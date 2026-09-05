#!/usr/bin/env python3
"""Compare table updates on identical offline inputs, including the 1.0 checkout."""

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter


def measure(repo, count):
    sys.path.insert(0, str(repo.resolve()))
    import requests
    from textual.widgets import DataTable

    from dj_digger.config import AppConfig
    from dj_digger.models import LinkRecord, Track
    from dj_digger.state import TrackState
    from dj_digger.tui import DiggerApp

    def offline(*args, **kwargs):
        raise AssertionError("Benchmark attempted a network request")
    requests.Session.send = offline
    config = AppConfig()
    empty = Path(os.environ["XDG_DATA_HOME"]) / "empty"
    empty.mkdir(parents=True)
    config.scan_directories = [str(empty)]
    config.download_directory = str(empty)
    config.first_run = False
    config.save()
    records = [LinkRecord(
        "bandcamp", Track(id=i + 1, title=f"Track {i:04d}", artist=f"Artist {i % 50}",
                          permalink_url=f"https://soundcloud.com/benchmark/{i}"),
        f"https://benchmark.bandcamp.com/track/{i}", "Buy",
    ) for i in range(count)]
    state = TrackState()
    app = DiggerApp(records, state=state)
    elapsed, clears, db_calls = [], [], []
    original_connection = state.db.connection

    @contextmanager
    def connection(*args, **kwargs):
        db_calls.append(1)
        with original_connection(*args, **kwargs) as value:
            yield value

    async def scenario():
        async with app.run_test(size=(140, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#tracks", DataTable)
            clear = table.clear

            def counted_clear(*args, **kwargs):
                clears.append(1)
                return clear(*args, **kwargs)
            table.clear = counted_clear
            state.db.connection = connection
            presentation = getattr(app, "playlist_state", app)
            rendering = getattr(app, "table_controller", app)
            for query in ("track", "artist 12", "track 099", "artist", "") * 3:
                presentation.search_term = query
                started = perf_counter()
                rendering.refresh_rows()
                elapsed.append((perf_counter() - started) * 1000)
                await pilot.pause()
            # Exclude shutdown from the render-only database count.
            state.db.connection = original_connection
    asyncio.run(scenario())
    print(json.dumps({"tracks": count, "updates": len(elapsed), "median_update_ms": round(median(elapsed), 3),
                      "table_clears": len(clears), "database_calls_during_updates": len(db_calls)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tracks", type=int, default=1000)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="digger-benchmark-") as temporary:
        for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
            os.environ[key] = str(Path(temporary) / key.lower())
        os.environ.pop("SOUNDCLOUD_OAUTH_TOKEN", None)
        os.environ["TEXTUAL_ANIMATIONS"] = "none"
        measure(args.repo, args.tracks)


if __name__ == "__main__":
    main()
