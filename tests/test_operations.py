from threading import Event, Thread

import pytest

from dj_digger.services.operations import OperationBusy, OperationCoordinator


def test_cancel_keeps_slot_until_thread_settles_and_late_events_are_ignored():
    operations = OperationCoordinator()
    handle = operations.start('Downloading')
    released = Event()
    worker = Thread(target=lambda: (released.wait(), operations.finish(handle)))
    worker.start()
    try:
        operations.cancel(handle)
        operations.cancel(handle)
        assert handle.state == 'cancelling'
        with pytest.raises(OperationBusy):
            operations.start('Digging')
    finally:
        released.set()
        worker.join()
    newer = operations.start('Digging')
    assert not operations.finish(handle)
    assert not operations.progress(handle, 10)
    assert newer.done == 0
    assert operations.visible is newer


def test_scan_is_independent_and_visible_again_after_main_settles():
    operations = OperationCoordinator()
    scan = operations.start('Scanning', lane='scan')
    main = operations.start('Digging')
    assert operations.visible is main
    with pytest.raises(OperationBusy):
        operations.start('Scanning', lane='scan')
    operations.finish(main)
    assert operations.visible is scan
    operations.stop_accepting()
    assert scan.cancel.is_set()
    with pytest.raises(OperationBusy):
        operations.start('Downloading')


def test_cancelled_async_io_keeps_operation_until_the_thread_really_finishes():
    import asyncio

    from dj_digger.services.runtime import ApplicationServices

    services = ApplicationServices()
    started, release = Event(), Event()
    effects = []
    handle = services.operations.start("Copying")

    def copy():
        started.set()
        release.wait()
        effects.append("published")

    async def operation():
        try:
            await services.io(copy)
        finally:
            services.operations.finish(handle)

    async def scenario():
        task = asyncio.create_task(operation())
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.01)
        assert services.operations.active() is handle
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert effects == ["published"]
        assert services.operations.active() is None
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        services.stop()


def test_shutdown_closes_database_last_even_if_another_close_fails():
    from types import SimpleNamespace

    from dj_digger.services.runtime import ApplicationServices

    closed = []

    class Device:
        def close(self):
            closed.append("audio")
            raise OSError("unavailable")
    services = ApplicationServices(state=SimpleNamespace(db=SimpleNamespace(close=lambda: closed.append("database"))))
    services._player = Device()
    services._client = SimpleNamespace(close=lambda: closed.append("client"))
    services.stop()
    services.stop()
    assert closed == ["audio", "client", "database"]


def test_adopted_login_observes_later_replacement_and_logout(monkeypatch):
    from dj_digger import auth
    from dj_digger.services.runtime import ApplicationServices

    stored = ["first"]
    monkeypatch.delenv("SOUNDCLOUD_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(auth, "get_stored_token", lambda: stored[0])
    services = ApplicationServices()
    try:
        services.adopt_login("first")
        assert services.client.oauth_token == "first"
        stored[0] = "second"
        assert services.client.oauth_token == "second"
        stored[0] = None
        assert services.client.oauth_token is None
    finally:
        services.stop()
