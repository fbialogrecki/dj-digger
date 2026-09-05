"""Download orchestration without widgets, using real pools and settlement."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from dj_digger.gate_models import GateProfileRequired
from dj_digger.models import Track
from dj_digger.services.downloads import DownloadRequest, DownloadWorkflow
from dj_digger.services.runtime import ApplicationServices
from dj_digger.state import TrackState


def tracks():
    return [
        Track(
            id=i,
            title=str(i),
            permalink_url=f"https://soundcloud.com/a/{i}",
            downloadable=True,
            has_downloads_left=True,
        )
        for i in (1, 2)
    ]


def test_profile_retry_only_repeats_approved_waiting_tracks(tmp_path):
    calls, events = Counter(), []
    state = TrackState(tmp_path / "library.db")
    with ApplicationServices(state=state) as services:

        class Client:
            def download_track(self, track, directory, **kwargs):
                calls[track.key] += 1
                if track.id == 2 and calls[track.key] == 1:
                    raise GateProfileRequired("name and email")
                path = directory / f"{track.id}.wav"
                path.write_bytes(b"audio")
                return path

        handle = services.operations.start("Downloading")
        workflow = DownloadWorkflow(
            services.downloads,
            DownloadRequest("", "initial", tmp_path, 20),
            handle,
            client=Client,
            config=services.config,
            emit=events.append,
            prerequisites=lambda profiles, auth: profiles,
        )
        workflow.run_batch([(track, None) for track in tracks()])
        services.operations.finish(handle)
        assert calls == {"1": 1, "2": 2}
        assert all(event.operation_id == handle.id for event in events)
        assert [event.key for event in events if event.kind == "downloaded"] == ["1", "2"]
        assert state.get("1") == state.get("2") == "got"
        summaries = [event.summary for event in events if event.kind == "summary"]
        assert [summary.pending for summary in summaries] == [1, 0]


def test_cancelled_pool_keeps_its_slot_and_resources_until_every_file_settles(tmp_path):
    entered, release = Event(), Event()
    state = TrackState(tmp_path / "library.db")
    services = ApplicationServices(state=state)
    handle = services.operations.start("Downloading")
    events = []

    class Client:
        def download_track(self, track, directory, **kwargs):
            entered.set()
            assert release.wait(3)
            # Publication has happened when cancellation arrives; persist it.
            path = directory / f"{track.id}.wav"
            path.write_bytes(b"audio")
            return path

    workflow = DownloadWorkflow(
        services.downloads,
        DownloadRequest("", "initial", tmp_path, 20),
        handle,
        client=Client,
        config=services.config,
        emit=events.append,
        prerequisites=lambda *args: [],
    )

    def run():
        with services.worker():
            try:
                workflow.run_batch([(tracks()[0], None)])
            finally:
                services.operations.finish(handle)

    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            task = worker.submit(run)
            try:
                assert entered.wait(2)
                services.operations.cancel(handle)
                assert services.operations.active() is handle
                assert not task.done()
                assert not state.db._closed
            finally:
                release.set()
            task.result()
        assert services.operations.active() is None
        assert state.local_file("1") == str(tmp_path / "1.wav")
        assert sum(event.kind == "downloaded" for event in events) == 1
    finally:
        release.set()
        services.stop()
