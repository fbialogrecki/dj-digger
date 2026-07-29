"""Command line entry point.

The headline change from v0.1: ``dj-digger <link>`` is all you need. The link can
be a playlist, an artist profile, someone's /likes or a single track, and there is
no subcommand to remember - ``dig`` is assumed when the first argument is not one.
A saved HTML file still works in the same position.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from . import browser as browser_module
from . import html_fallback, links, soundcloud
from .models import Crate, LinkRecord
from .state import TrackState

SUBCOMMANDS = {"dig", "open"}
HELP_FLAGS = {"-h", "--help", "--version"}
LOG_LEVELS = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

LOGGER = logging.getLogger("dj_digger")


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=LOG_LEVELS,
        help="Logging verbosity (default: INFO)",
    )
    parser.add_argument(
        "--browser",
        choices=browser_module.BROWSER_CHOICES,
        default="default",
        help="Browser used to open links (default: system default)",
    )
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Skip the interactive browser and just report the results",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dj-digger",
        description=(
            "Dig purchase and free-download links out of SoundCloud playlists, "
            "likes and profiles."
        ),
        epilog="Example: dj-digger https://soundcloud.com/someone/sets/a-playlist",
    )
    parser.add_argument("--version", action="version", version=f"dj-digger {__version__}")
    _add_shared_arguments(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)

    dig = subparsers.add_parser(
        "dig",
        help="Dig a SoundCloud link (or a saved playlist HTML file). Assumed by default.",
    )
    dig.add_argument(
        "target",
        help="SoundCloud URL (playlist, profile, /likes, track) or a saved HTML file",
    )
    dig.add_argument(
        "-f",
        "--format",
        "--export",
        dest="export_format",
        choices=links.EXPORT_FORMATS,
        default="json",
        help="Export format for the categorised links (default: json)",
    )
    dig.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Where to write the export. Defaults to soundcloud_links.<ext>",
    )
    dig.add_argument(
        "-n",
        "--limit",
        "--max-tracks",
        dest="limit",
        type=int,
        help="Process only the first N tracks",
    )
    dig.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP request timeout in seconds (default: 20)",
    )
    dig.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests, only used by the slow HTML fallback (default: 0.5)",
    )
    _add_shared_arguments(dig)

    open_cmd = subparsers.add_parser(
        "open",
        help="Reopen a previously exported summary.",
    )
    open_cmd.add_argument("summary_file", type=Path, help="Path to an exported JSON/YAML summary")
    open_cmd.add_argument(
        "--category",
        choices=links.CATEGORY_CHOICES,
        help="Open one category straight away, without the interactive browser",
    )
    open_cmd.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Number of links to skip before opening (default: 0)",
    )
    open_cmd.add_argument(
        "--limit",
        type=int,
        help="Maximum number of links to open",
    )
    open_cmd.add_argument(
        "--no-open",
        action="store_true",
        help="Only display the summary without opening anything",
    )
    _add_shared_arguments(open_cmd)

    return parser


def inject_default_command(argv: Sequence[str]) -> List[str]:
    """Let ``dj-digger <link>`` mean ``dj-digger dig <link>``."""

    tokens = list(argv)
    if not tokens:
        return tokens
    if any(token in SUBCOMMANDS for token in tokens):
        return tokens
    if any(token in HELP_FLAGS for token in tokens):
        return tokens
    return ["dig", *tokens]


def parse_cli_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw = list(sys.argv[1:] if argv is None else argv)
    return build_parser().parse_args(inject_default_command(raw))


def _progress(console: Console) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )


def _dig_url(url: str, args: argparse.Namespace, console: Console) -> Crate:
    with _progress(console) as progress:
        task = progress.add_task("Reading the link", total=None)

        def on_progress(done: int, total: Optional[int]) -> None:
            progress.update(task, description="Fetching tracks", completed=done, total=total)

        return soundcloud.collect_tracks(
            url,
            limit=args.limit,
            timeout=args.timeout,
            on_progress=on_progress,
        )


def _dig_html(path: Path, args: argparse.Namespace, console: Console) -> Crate:
    track_ids, track_urls, declared = html_fallback.load_playlist(path)
    if args.limit is not None:
        track_ids = track_ids[: args.limit]
        track_urls = track_urls[: args.limit]

    if track_ids:
        LOGGER.info("Found %s track ids in %s - hydrating through the API", len(track_ids), path)
        with _progress(console) as progress:
            task = progress.add_task("Fetching tracks", total=len(track_ids))

            def on_progress(done: int, total: Optional[int]) -> None:
                progress.update(task, completed=done, total=total)

            tracks = soundcloud.hydrate_ids(
                track_ids, timeout=args.timeout, on_progress=on_progress
            )
    elif track_urls:
        LOGGER.info(
            "No track ids in %s - falling back to scraping %s track pages", path, len(track_urls)
        )
        session = soundcloud.create_requests_session()
        tracks = []
        try:
            with _progress(console) as progress:
                task = progress.add_task("Scraping track pages", total=len(track_urls))
                for track_url in track_urls:
                    tracks.append(
                        html_fallback.scrape_track_page(track_url, session, args.timeout)
                    )
                    progress.advance(task)
                    if args.delay > 0:
                        time.sleep(args.delay)
        finally:
            session.close()
    else:
        tracks = []

    return Crate(
        source=str(path),
        tracks=tracks,
        title=path.stem,
        declared_count=declared,
    )


def _print_summary(
    console: Console,
    records: Sequence[LinkRecord],
    crate: Optional[Crate] = None,
) -> None:
    counts = links.count_by_category(records)
    table = Table(title=crate.title if crate else None, title_justify="left")
    table.add_column("Store")
    table.add_column("Links", justify="right")
    for category in links.CATEGORY_NAMES:
        table.add_row(category, str(counts[category]))
    table.add_section()
    table.add_row("total", str(len(records)))
    console.print(table)


def _should_use_tui(args: argparse.Namespace) -> bool:
    if args.no_tui:
        return False
    if not sys.stdout.isatty():
        LOGGER.info("Not a terminal - skipping the interactive browser")
        return False
    return True


def handle_dig(args: argparse.Namespace) -> int:
    console = Console(stderr=True)
    target = str(args.target)

    if soundcloud.is_soundcloud_url(target):
        crate = _dig_url(target, args, console)
    else:
        path = Path(target)
        if not path.exists():
            raise SystemExit(
                f"'{target}' is neither a soundcloud.com link nor an existing file."
            )
        crate = _dig_html(path, args, console)

    if not crate.tracks:
        LOGGER.warning("No tracks found behind '%s'.", target)
        if not soundcloud.is_soundcloud_url(target):
            LOGGER.info(
                "Tip: pass the playlist link directly instead - it does not need the page "
                "to be scrolled or saved."
            )
        return 1

    if args.limit is not None:
        LOGGER.info("Collected %s tracks (limited to %s).", len(crate.tracks), args.limit)
    elif crate.declared_count and len(crate.tracks) != crate.declared_count:
        LOGGER.warning(
            "Collected %s tracks but the source declares %s.",
            len(crate.tracks),
            crate.declared_count,
        )
    else:
        LOGGER.info("Collected %s tracks.", len(crate.tracks))

    records = links.categorise_all(crate.tracks)
    export_path = links.export_records(records, args.export_format, args.output)
    _print_summary(console, records, crate)

    if _should_use_tui(args):
        from .tui import run_tui

        run_tui(
            records,
            state=TrackState(),
            crate_title=crate.title,
            browser=args.browser,
            export_format=args.export_format,
            export_path=export_path or args.output,
        )
    return 0


def prompt_category_selection() -> str:
    prompt = (
        "Open which category? Enter one of: "
        + ", ".join(links.CATEGORY_CHOICES)
        + " (default: all): "
    )
    while True:
        choice = input(prompt).strip().lower()
        if not choice:
            return "all"
        for option in links.CATEGORY_CHOICES:
            if option.lower() == choice:
                return option
        print("Please choose a valid category name.")


def _batch_open(args: argparse.Namespace, records: Sequence[LinkRecord]) -> None:
    category = args.category or prompt_category_selection()
    selected = [
        record for record in records if category == "all" or record.category == category
    ]
    if args.skip:
        selected = selected[max(0, args.skip) :]
    if args.limit is not None:
        selected = selected[: args.limit]

    if not selected:
        LOGGER.info("No links left to open for category '%s'.", category)
        return

    opened = browser_module.open_urls(
        [record.link_url for record in selected], args.browser
    )
    LOGGER.info("Opened %s links in browser '%s'.", opened, args.browser)


def handle_open(args: argparse.Namespace) -> int:
    console = Console(stderr=True)
    records = links.load_summary(args.summary_file)
    _print_summary(console, records)

    if args.no_open:
        return 0

    if args.category or not _should_use_tui(args):
        _batch_open(args, records)
        return 0

    from .tui import run_tui

    run_tui(
        records,
        state=TrackState(),
        crate_title=str(args.summary_file),
        browser=args.browser,
        export_format="json",
        export_path=Path(args.summary_file),
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_cli_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(message)s",
    )

    try:
        if args.command == "dig":
            return handle_dig(args)
        if args.command == "open":
            return handle_open(args)
    except soundcloud.SoundCloudError as exc:
        LOGGER.error("%s", exc)
        return 2
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Interrupted.")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
