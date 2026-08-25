"""Command line entry point.

The headline change from v0.1: ``dj-digger <link>`` is all you need. The link can
be a playlist, an artist profile, someone's /likes or a single track, and there is
no subcommand to remember - ``dig`` is assumed when the first argument is not one.
A saved HTML file still works in the same position, and running ``dj-digger`` with
no arguments at all opens the browser and asks for a link.
"""

import argparse
import getpass
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import __version__, library, links, soundcloud, spotify
from . import auth as auth_module
from . import browser as browser_module
from . import dig as dig_module
from .config import AppConfig
from .models import Crate, LinkRecord
from .state import TrackState

SUBCOMMANDS = {"dig", "open", "auth"}
HELP_FLAGS = {"-h", "--help", "-v", "--version"}
LOG_LEVELS = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

LOGGER = logging.getLogger("dj_digger")


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"dj-digger {__version__}",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=LOG_LEVELS,
        help="Logging verbosity (default: INFO)",
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

    auth_cmd = subparsers.add_parser(
        "auth",
        help="Manage authentication for direct downloads.",
    )
    auth_sub = auth_cmd.add_subparsers(dest="auth_action")

    auth_login = auth_sub.add_parser("login", help="Log in to SoundCloud.")
    auth_login.add_argument(
        "--token",
        help="Paste an OAuth token directly instead of browser detection.",
    )

    auth_sub.add_parser("logout", help="Remove saved credentials.")
    auth_sub.add_parser("status", help="Show current authentication status.")

    spotify_auth = auth_sub.add_parser(
        "spotify", help="Manage Spotify gate authentication."
    )
    spotify_sub = spotify_auth.add_subparsers(dest="spotify_action", required=True)
    spotify_login = spotify_sub.add_parser("login", help="Log in to Spotify with PKCE.")
    spotify_login.add_argument(
        "--client-id", help="Spotify developer app client ID."
    )
    spotify_sub.add_parser("status", help="Show Spotify authentication status.")
    spotify_sub.add_parser("logout", help="Remove saved Spotify credentials.")
    _add_shared_arguments(auth_cmd)

    return parser


def _run_tui(*args, **kwargs) -> None:
    # Imported here rather than at module top on purpose: textual and its
    # dependency tree stay entirely off the --no-tui and export-only paths.
    from .tui import run_tui

    run_tui(*args, **kwargs)


def inject_default_command(argv: Sequence[str]) -> list[str]:
    """Let ``dj-digger <link>`` mean ``dj-digger dig <link>``, and bare mean ``dig``."""

    tokens = list(argv)
    if set(tokens) & (set(SUBCOMMANDS) | set(HELP_FLAGS)):
        return tokens
    return ["dig", *tokens]


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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

        def on_progress(stage: str, done: int, total: int | None) -> None:
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
    crate: Crate | None = None,
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
        _run_tui(
            [],
            state=TrackState(),
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
        _run_tui(
            records,
            state=TrackState(),
            crate_title=crate.title,
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
    by_lower = {option.lower(): option for option in links.CATEGORY_CHOICES}
    while True:
        choice = input(prompt).strip().lower()
        if not choice:
            return "all"
        if choice in by_lower:
            return by_lower[choice]
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

    # The same setting the crate browser opens links with, so --no-tui and the
    # interactive path do not disagree about which browser you meant.
    chosen = AppConfig().browser
    opened = browser_module.open_urls([record.link_url for record in selected], chosen)
    LOGGER.info("Opened %s links in browser '%s'.", opened, chosen or "the system default")


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

    _run_tui(
        # Re-derived from the URLs rather than trusting the category names in
        # the file, so a summary written by an older version still groups the
        # way this one does.
        links.categorise_all(record.active_tracks),
        state=TrackState(),
        crate_title=record.title,
        export_format="json",
        export_path=path,
        crate_record=record,
    )
    return 0


def handle_auth(args: argparse.Namespace) -> int:
    console = Console()
    # No action given means status; argparse leaves auth_action as None then.
    action = getattr(args, "auth_action", None) or "status"
    if action == "spotify":
        return _auth_spotify(args, console)
    if action == "login":
        return _auth_login(args, console)
    if action == "logout":
        auth_module.clear_token()
        console.print("[green]Logged out. Saved SoundCloud credentials removed.[/green]")
        return 0
    return _auth_status(console)


def _auth_spotify(args: argparse.Namespace, console: Console) -> int:
    spotify_action = args.spotify_action
    if spotify_action == "login":
        config = AppConfig()
        client_id = (getattr(args, "client_id", None) or "").strip()
        if not client_id:
            client_id = str(spotify.load_credentials().get("client_id") or "").strip()
        if not client_id:
            if not sys.stdin.isatty():
                console.print(
                    "[red]Spotify Client ID is required without an interactive "
                    "terminal; use --client-id CLIENT_ID.[/red]"
                )
                return 1
            console.print("Create a Spotify Web API app in the Developer Dashboard.")
            console.print("Add this exact Redirect URI:")
            console.print(f"  [bold]{spotify.REDIRECT_URI}[/bold]")
            console.print("Client Secret is not needed.")
            browser_module.open_url(spotify.DASHBOARD_URL, config.browser)
            client_id = input("Paste Spotify Client ID: ").strip()
            if not client_id:
                console.print("[red]Spotify Client ID cannot be empty.[/red]")
                return 1
        spotify.login(client_id, browser=config.browser)
        console.print("[green]Spotify login saved.[/green]")
        return 0
    if spotify_action == "logout":
        spotify.clear_credentials()
        console.print("[green]Spotify credentials removed.[/green]")
        return 0
    credentials = spotify.load_credentials()
    if credentials.get("refresh_token"):
        console.print("[green]Spotify authentication: configured.[/green]")
    else:
        console.print("[yellow]Spotify authentication: not configured.[/yellow]")
    return 0


def _auth_login(args: argparse.Namespace, console: Console) -> int:
    token = getattr(args, "token", None)
    if token and token.strip():
        env_token = os.environ.get("SOUNDCLOUD_OAUTH_TOKEN", "").strip()
        if env_token and env_token != token.strip():
            console.print(
                "[red]SOUNDCLOUD_OAUTH_TOKEN overrides --token. Unset or "
                "update the environment variable first.[/red]"
            )
            return 1
        with soundcloud.SoundCloudClient(oauth_token=token.strip()) as client:
            try:
                _, username, user_id = auth_module.verify_and_save(
                    token.strip(), client.client_id
                )
                console.print(f"[green]Logged in as [bold]{username}[/bold] (ID: {user_id or 'N/A'}). Credentials saved securely.[/green]")
                return 0
            except auth_module.SoundCloudAuthError:
                console.print("[red]Verification failed. The provided OAuth token is invalid.[/red]")
                return 1
    else:
        with soundcloud.SoundCloudClient() as client:
            stored_token = auth_module.get_stored_token()
            if stored_token:
                user_data = auth_module.verify_token(stored_token, client.client_id)
                if user_data:
                    username = user_data.get("username") or "User"
                    console.print(
                        f"[green]Already logged in as [bold]{username}[/bold].[/green]"
                    )
                    return 0
                if os.environ.get("SOUNDCLOUD_OAUTH_TOKEN", "").strip():
                    console.print(
                        "[red]SOUNDCLOUD_OAUTH_TOKEN is invalid and overrides "
                        "saved logins. Unset or update it before logging in.[/red]"
                    )
                    return 1

            console.print("Scanning Firefox for an active SoundCloud session...")
            res = auth_module.auto_detect_and_verify(client.client_id)
            if res:
                _, username, user_id = res
                console.print(f"[green]Successfully detected session for [bold]{username}[/bold] (ID: {user_id or 'N/A'}). Credentials saved securely.[/green]")
                return 0

            if not sys.stdin.isatty():
                console.print(
                    "[red]Interactive SoundCloud login needs a terminal; use "
                    "--token TOKEN in scripts.[/red]"
                )
                return 1

            try:
                _, username, user_id = auth_module.login_with_chromium(
                    client.client_id,
                    status=lambda message: console.print(message),
                )
            except auth_module.SoundCloudAuthError as exc:
                console.print(f"[yellow]{exc}[/yellow]")
                token = getpass.getpass(
                    "Paste oauth_token instead (input is hidden; blank cancels): "
                ).strip()
                if not token:
                    console.print("[yellow]SoundCloud login cancelled.[/yellow]")
                    return 1
                try:
                    _, username, user_id = auth_module.verify_and_save(
                        token, client.client_id
                    )
                except auth_module.SoundCloudAuthError:
                    console.print(
                        "[red]Verification failed. The provided OAuth token is invalid.[/red]"
                    )
                    return 1
            console.print(
                f"[green]Logged in as [bold]{username}[/bold] "
                f"(ID: {user_id or 'N/A'}). Credentials saved securely.[/green]"
            )
            return 0


def _auth_status(console: Console) -> int:
    info = auth_module.get_stored_auth_info()
    token = info.get("oauth_token")
    if not token:
        console.print("[yellow]Authentication status: Not logged in.[/yellow]")
        return 0

    console.print(f"Stored user: [bold]{info.get('username') or 'Unknown'}[/bold]")
    with soundcloud.SoundCloudClient(oauth_token=token) as client:
        user_data = auth_module.verify_token(token, client.client_id)
        if user_data:
            console.print("[green]Token status: Valid & active.[/green]")
        else:
            console.print("[red]Token status: Expired or invalid.[/red]")
    return 0


def _configure_logging(level_name: str) -> None:
    """Put our own log on screen, and nobody else's.

    ``logging.basicConfig`` configures the root logger, so urllib3's retry
    warnings came out with ours: a dig across 484 tracks printed dozens of
    ``Retrying (Retry(total=1, connect=5...))`` lines - one per dead link in the
    playlist - before it printed a single result. Those are a library talking to
    itself about a host it is about to give up on, which we already report.

    ``--log-level DEBUG`` is the one case where somebody does want to see them,
    so that level lets them back through.
    """

    level = getattr(logging, level_name.upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    root = logging.getLogger()
    ours = logging.getLogger("dj_digger")
    ours.handlers.clear()

    if level <= logging.DEBUG:
        # Debugging is the one time somebody does want the whole picture. Wired
        # by hand rather than through basicConfig, which does nothing at all when
        # the root logger already has a handler - and by then so would we.
        ours.propagate = True
        ours.setLevel(logging.NOTSET)
        root.addHandler(handler)
        root.setLevel(level)
        return

    ours.addHandler(handler)
    ours.setLevel(level)
    ours.propagate = False
    # Without a handler on the root logger Python falls back to logging.lastResort,
    # which prints WARNING and above to stderr - so leaving root bare would not
    # have silenced urllib3, it would only have taken the formatting away.
    if not root.handlers:
        root.addHandler(logging.NullHandler())


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_cli_args(argv)

    _configure_logging(args.log_level)

    try:
        if args.command == "dig":
            return handle_dig(args)
        if args.command == "open":
            return handle_open(args)
        if args.command == "auth":
            return handle_auth(args)
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
