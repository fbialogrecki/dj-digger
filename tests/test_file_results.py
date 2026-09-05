from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dj_digger.files import copy_local_file
from dj_digger.services.downloads import DownloadService, PublishedFileUnrecorded


def test_published_file_survives_database_failure_without_second_transfer(tmp_path):
    target = tmp_path / 'done.wav'
    class Client:
        calls = 0
        def download_track(self, *args, **kwargs):
            self.calls += 1
            target.write_bytes(b'audio')
            return target
    class State:
        def set_local_file(self, *args):
            raise OSError('locked')
    class Track:
        key = 'one'
    client = Client()
    with pytest.raises(PublishedFileUnrecorded) as failure:
        DownloadService(State()).fetch(client, Track(), tmp_path)
    assert failure.value.result.path == target
    assert not failure.value.result.recorded
    assert target.read_bytes() == b'audio'
    assert client.calls == 1


def test_copy_collisions_publish_distinct_complete_files(tmp_path):
    sources = []
    for i in range(12):
        folder = tmp_path / str(i)
        folder.mkdir()
        source = folder / 'same.wav'
        source.write_bytes(str(i).encode())
        sources.append(source)
    with ThreadPoolExecutor(max_workers=12) as pool:
        targets = list(pool.map(lambda source: copy_local_file(source, tmp_path/'out'), sources))
    assert len(set(targets)) == 12
    assert {Path(path).read_bytes() for path in targets} == {str(i).encode() for i in range(12)}
    assert not list((tmp_path/'out').glob('*.part'))


def test_metadata_patch_preserves_new_marks_and_deletions_and_generation(tmp_path):
    from dj_digger.db import Database
    db = Database(tmp_path / 'library.db')
    record = {'source': 'crate', 'tracks': [{'id': 1, 'title': 'one'}],
              'new_track_keys': ['1'], 'removed_track_keys': []}
    db.save_crate(record)
    generation = db.crate_generation('crate')
    assert db.merge_track_metadata('crate', generation, {'1': {'purchase_url': 'https://shop.test'}})
    assert db.load_crate('crate')['new_track_keys'] == ['1']
    record['removed_track_keys'] = ['1']
    db.save_crate(record)
    db.merge_track_metadata('crate', generation, {'1': {'local_path': '/done.wav'}})
    assert 'local_path' not in db.load_crate('crate')['tracks'][0]
    db.delete_crate('crate')
    db.save_crate(record)
    assert not db.merge_track_metadata('crate', generation, {'1': {'local_path': '/done.wav'}})
    assert 'local_path' not in db.load_crate('crate')['tracks'][0]


@pytest.mark.parametrize("transport", ["http", "browser"])
def test_cancellation_at_publication_removes_only_own_temporary_file(tmp_path, monkeypatch, transport):
    from threading import Event

    from dj_digger import files
    from dj_digger.models import Cancelled, Track

    keep = tmp_path / "existing.wav"
    keep.write_bytes(b"existing audio")
    cancel = Event()
    original = files._claim_target

    def cancel_before_claim(*args):
        cancel.set()
        return original(*args)

    monkeypatch.setattr(files, "_claim_target", cancel_before_claim)
    track = Track(id=1, title="existing", permalink_url="https://soundcloud.com/a/1")

    class Response:
        def iter_content(self, **kwargs):
            yield b"new audio"

    class Download:
        suggested_filename = "existing.wav"
        def save_as(self, target):
            Path(target).write_bytes(b"new audio")

    with pytest.raises(Cancelled):
        if transport == "http":
            files._save_stream(Response(), track, tmp_path, ".wav", None, None, cancel)
        else:
            files.save_browser_download(Download(), track, tmp_path, cancel)
    assert list(tmp_path.glob("*.wav")) == [keep]
    assert not list(tmp_path.glob("*.part"))
    assert keep.read_bytes() == b"existing audio"


def test_scanner_batch_rolls_back_status_and_provenance_mirrors(tmp_path, monkeypatch):
    from dj_digger.state import TrackState

    state = TrackState(tmp_path / "library.db")
    state.set("one", "skip")
    original = state.db.set_track_state

    def fail_second(key, status, path):
        if key == "two":
            raise OSError("write failed")
        original(key, status, path)

    monkeypatch.setattr(state.db, "set_track_state", fail_second)
    with pytest.raises(OSError):
        state.apply_file_matches([("one", "/one.wav", True, False), ("two", "/two.wav", True, False)])
    assert state.get("one") == "skip"
    assert state.local_file("one") is None
    assert state.db.all_track_statuses() == {"one": "skip"}
    assert state.db.all_track_local_files() == {}


def test_browser_batch_records_other_files_after_one_database_failure(tmp_path, monkeypatch):
    from dj_digger.config import AppConfig
    from dj_digger.gate_models import HypedditBrowserBatchResult

    files = [tmp_path / "one.wav", tmp_path / "two.wav"]
    for path in files:
        path.write_bytes(b"audio")
    monkeypatch.setattr("dj_digger.gates.browser.download_hypeddit_batch_in_browser",
                        lambda *a, **k: HypedditBrowserBatchResult(completed=(("one", files[0]), ("two", files[1]))))
    recorded = []

    class State:
        def set_local_file(self, key, path):
            if key == "one":
                raise OSError("database busy")
            recorded.append((key, path))

    result = DownloadService(State()).finish_gates([], tmp_path, None, config=AppConfig())
    assert [(r.key, r.path) for r in result.completed] == [("two", files[1])]
    assert result.failures[0][0] == "one"
    assert isinstance(result.failures[0][1], PublishedFileUnrecorded)
    assert recorded == [("two", files[1])]
    assert all(path.exists() for path in files)
