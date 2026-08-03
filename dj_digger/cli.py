"""Command line entry point.

The headline change from v0.1: ``dj-digger <link>`` is all you need. The link can
be a playlist, an artist profile, someone's /likes or a single track, and there is
no subcommand to remember - ``dig`` is assumed when the first argument is not one.
A saved HTML file still works in the same position, and running ``dj-digger`` with
no arguments at all opens the browser and asks for a link.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__
from . import browser as browser_module
from . import dig as dig_module
from . import library, links
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

    dig_cmd = subparsers.add_parser(
        "dig",
        help="Dig a SoundCloud link (or a saved playlist HTML file). Assumed by default.",
    )
    dig_cmd.add_argument(
        "target",
        nargs="?",
        help=(
            "SoundCloud URL (playlist, profile, /likes, track) or a saved HTML file. "
            "Omit it and you will be asked."
        ),
    )
    dig_cmd.add_argument(
        "-f",
        "--format",
        "--export",
        dest="export_format",
        choices=links.EXPORT_FORMATS,
        default="json",
        help="Export format for the categorised links (default: json)",
    )
    dig_cmd.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Where to write the export. Defaults to soundcloud_links.<ext>",
    )
    dig_cmd.add_argument(
        "-n",
        "--limit",
        "--max-tracks",
        dest="limit",
        type=int,
        help="Process only the first N tracks",
    )
    dig_cmd.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP request timeout in seconds (default: 20)",
    )
    dig_cmd.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests, only used by the slow HTML fallback (default: 0.5)",
    )
    _add_shared_arguments(dig_cmd)

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
    """Let ``dj-digger <link>`` mean ``dj-digger dig <link>``, and bare mean ``dig``."""

    tokens = list(argv)
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


def _dig_with_progress(target: str, args: argparse.Namespace, console: Console) -> Crate:
    with _progress(console) as progress:
        task = progress.add_task(dig_module.STAGE_LINK, total=None)

        def on_progress(stage: str, done: int, total: Optional[int]) -> None:
            progress.update(task, description=stage, completed=done, total=total)

        return dig_module.dig(
            target,
            limit=args.limit,
            timeout=args.timeout,
            delay=args.delay,
            on_progress=on_progress,
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
    # A dozen categories with most of them empty is noise, so only show the hits.
    for category in links.present_categories(records):
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


def _dig_options(args: argparse.Namespace) -> dig_module.DigOptions:
    return dig_module.DigOptions(limit=args.limit, timeout=args.timeout, delay=args.delay)


def handle_dig(args: argparse.Namespace) -> int:
    console = Console(stderr=True)

    if args.target is None:
        if not _should_use_tui(args):
            raise SystemExit(
                "Nothing to dig. Pass a SoundCloud link, or run without --no-tui "
                "to be asked for one."
            )
        from .tui import run_tui

        run_tui(
            [],
            state=TrackState(),
            browser=args.browser,
            export_format=args.export_format,
            export_path=args.output,
            dig_options=_dig_options(args),
        )
        return 0

    crate = _dig_with_progress(str(args.target), args, console)

    if not crate.tracks:
        LOGGER.warning("No tracks found behind '%s'.", args.target)
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

    # The library is the source of truth, so a CLI dig joins it too.
    record = library.remember(crate)
    records = links.categorise_all(record.active_tracks)
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
            dig_options=_dig_options(args),
            crate_record=record,
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
    path = Path(args.summary_file)
    records = links.load_summary(path)
    _print_summary(console, records)

    if args.no_open:
        return 0

    if args.category or not _should_use_tui(args):
        _batch_open(args, records)
        return 0

    # An export carries fewer fields than the API does, so the crate joins the
    # library marked partial - refreshing it fills in genre and the rest.
    record = library.remember(
        Crate(
            source=str(path),
            title=path.stem,
            tracks=links.tracks_from_records(records),
        ),
        partial=True,
    )

    from .tui import run_tui

    run_tui(
        # Re-derived from the URLs rather than trusting the category names in
        # the file, so a summary written by an older version still groups the
        # way this one does.
        links.categorise_all(record.active_tracks),
        state=TrackState(),
        crate_title=record.title,
        browser=args.browser,
        export_format="json",
        export_path=path,
        crate_record=record,
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
    except dig_module.TargetNotFound as exc:
        raise SystemExit(str(exc)) from exc
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("Interrupted.")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
