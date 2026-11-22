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
    from dj_digger.state import FileMatch, TrackState

    state = TrackState(tmp_path / "library.db")
    state.set("one", "skip")
    original = state.db.set_track_state

    def fail_second(key, status, path):
        if key == "two":
            raise OSError("write failed")
        original(key, status, path)

    monkeypatch.setattr(state.db, "set_track_state", fail_second)
    with pytest.raises(OSError):
        state.apply_file_matches([FileMatch("one", "/one.wav", True, False, 0), FileMatch("two", "/two.wav", True, False, 0)])
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


@pytest.mark.parametrize("newer", ["download", "manual"])
def test_missing_file_scan_cannot_clear_a_later_completed_decision(tmp_path, newer):
    from dj_digger.models import GOT, Track
    from dj_digger.services.library import LibraryService
    from dj_digger.state import TrackState

    state = TrackState(tmp_path / "library.db")
    state.set_local_file("1", tmp_path / "missing.wav")
    complete = tmp_path / "new.wav"
    complete.write_bytes(b"audio")

    class Scanner:
        def match_track(self, track):
            if newer == "download":
                state.set_local_file(track.key, complete)
            else:
                state.set(track.key, GOT)
            return None

        def had_stale_match(self, track):
            return False

    paths = LibraryService(state).match_tracks([Track(id=1, title="one", permalink_url="https://soundcloud.com/a/one")], Scanner())
    assert state.get("1") == GOT
    assert state.local_file("1") == (str(complete) if newer == "download" else None)
    assert paths["1"] == state.local_file("1")


def test_first_refresh_keeps_concurrent_row_removal_without_discarding_arrivals(tmp_path):
    from dj_digger.db import Database

    db = Database(tmp_path / "library.db")
    crate = dict(source="crate", title="before", tracks=[dict(id=1)], partial=False)
    db.save_crate(crate)
    snapshot = db.snapshot_generations()
    db.set_removed_tracks("crate", db.crate_generation("crate"), ["1"], True)
    updated = db.remember_collection(dict(crate, title="after", tracks=[dict(id=1), dict(id=2)]), snapshot)
    assert updated["title"] == "after"
    assert updated["removed_track_keys"] == ["1"]
    assert updated["new_track_keys"] == ["2"]


def test_a_title_only_path_is_never_taken_as_an_owned_file(tmp_path):
    from dj_digger.models import GOT, Track
    from dj_digger.services.library import LibraryService
    from dj_digger.state import TrackState

    state = TrackState(tmp_path / "library.db")
    loose = tmp_path / "maybe.wav"
    loose.write_bytes(b"audio")
    track = Track(id=1, title="one", permalink_url="https://soundcloud.com/a/one", local_path=str(loose))
    assert not LibraryService(state).mark_existing(track)
    assert state.get("1") != GOT
    state.set_local_file("1", loose)
    assert LibraryService(state).mark_existing(track)


def test_partial_provider_alias_and_deletion_preserve_shared_track(tmp_path):
    from dj_digger.services.library import LibraryService
    from dj_digger.state import TrackState
    state = TrackState(tmp_path / 'library.db')
    db = state.db
    full = dict(source='original', title='full', partial=False, provider_id=22,
                tracks=[dict(id=1), dict(id=2)], removed_track_keys=['2'])
    db.remember_collection(full)
    db.save_crate(dict(full, source='another', provider_id=None))
    state.set_local_file('1', tmp_path / 'library.wav')
    saved = db.load_crate('original')
    slice_ = db.remember_collection(dict(full, source='renamed-url', partial=True, tracks=[dict(id=1)]))
    assert slice_['source'] == 'original' and slice_['tracks'] == [dict(id=1)]
    assert db.load_crate('original') == saved and db.load_crate('renamed-url') is None
    LibraryService(state).delete('original')
    assert db.load_crate('another') and db.all_track_statuses()['1'] == 'got'
    assert db.all_track_local_files()['1'] == str(tmp_path / 'library.wav')


def test_failed_csv_keeps_prior_contents_and_permissions(tmp_path):
    import stat

    from dj_digger.private_json import atomic_csv
    tmp_path = tmp_path / 'output'
    tmp_path.mkdir()
    path = tmp_path / 'export.csv'
    path.write_text('old')
    path.chmod(0o600)
    with pytest.raises(ValueError), atomic_csv(path) as stream:
        stream.write('partial')
        raise ValueError('bad row')
    assert path.read_text() == 'old' and list(tmp_path.iterdir()) == [path]
    mode = stat.S_IMODE(path.stat().st_mode)
    with atomic_csv(path) as stream:
        stream.write('new')
    assert path.read_text() == 'new' and stat.S_IMODE(path.stat().st_mode) == mode


def test_empty_browser_download_keeps_original_and_long_collision_fits(tmp_path):
    from dj_digger.files import save_browser_download
    from dj_digger.models import Track
    from dj_digger.paths import unique_target
    from dj_digger.soundcloud_errors import SoundCloudError
    tmp_path = tmp_path / 'downloads'
    tmp_path.mkdir()
    discarded = []
    class Download:
        suggested_filename = 'file.wav'
        def save_as(self, path):
            Path(path).write_bytes(b'')
        def discard(self):
            discarded.append(True)
    with pytest.raises(SoundCloudError, match='empty'):
        save_browser_download(Download(), Track('one', ''), tmp_path)
    assert not discarded and not list(tmp_path.iterdir())
    stem = 'CON.' + '音' * 200
    first = unique_target(tmp_path, stem, '.wav')
    first.touch()
    second = unique_target(tmp_path, stem, '.wav')
    assert second != first and second.name.startswith('_CON.') and second.name.endswith(' (1).wav')
    assert len(second.name.encode()) <= 255


@pytest.mark.parametrize('confident', [True, False])
def test_scan_suggestion_cannot_replace_a_later_manual_decision(tmp_path, confident):
    from dj_digger.state import FileMatch, TrackState

    state = TrackState(tmp_path / 'library.db')
    observed = state.observe_file('one')
    state.set('one', 'skip')
    paths = state.apply_file_matches([FileMatch('one', '/stale.wav', confident, False, observed.revision)])
    assert paths == {'one': None}
    assert state.get('one') == 'skip' and state.db.all_track_local_files() == {}


def test_preview_revision_follows_the_scanners_own_stale_path_cleanup(tmp_path):
    from dj_digger.state import FileMatch, TrackState

    state = TrackState(tmp_path / 'library.db')
    state.set_local_file('one', tmp_path / 'missing.wav')
    observed = state.observe_file('one')
    previews = {}
    state.apply_file_matches([FileMatch('one', 'maybe.wav', False, True, observed.revision)], previews=previews)
    assert state.local_file('one') is None and previews['one'] == ('maybe.wav', state.observe_file('one').revision)
    state.set('one', 'skip')
    assert state.observe_file('one').revision != previews['one'][1]
