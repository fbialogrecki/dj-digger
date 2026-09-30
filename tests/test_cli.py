
import argparse
import contextlib
import io
import json
import logging

import library_db as library
import pytest

from dj_digger import cli, crate_models
from dj_digger.models import Crate, LinkRecord, Track
from dj_digger.services.collection import TargetNotFound


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["https://soundcloud.com/a/sets/b"], ["dig", "https://soundcloud.com/a/sets/b"]),
        (["playlist.html"], ["dig", "playlist.html"]),
        (["dig", "playlist.html"], ["dig", "playlist.html"]),
        (["open", "crate.json"], ["open", "crate.json"]),
        (["--log-level", "DEBUG", "link"], ["dig", "--log-level", "DEBUG", "link"]),
        # No arguments at all still means dig - it just has nothing to dig yet.
        ([], ["dig"]),
        # Help and version must reach the top-level parser, not the dig subparser.
        (["--help"], ["--help"]),
        (["-h"], ["-h"]),
        (["--version"], ["--version"]),
        (["-v"], ["-v"]),
    ],
)
def test_default_command_injection(argv, expected):
    assert cli.inject_default_command(argv) == expected


@pytest.mark.parametrize("flag", ["-v", "--version"])
def test_version_flag_prints_version(flag, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.parse_cli_args([flag])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert cli.__version__ in out


def test_a_bare_link_is_dug():
    args = cli.parse_cli_args(["https://soundcloud.com/a/sets/b"])
    assert args.command == "dig"
    assert args.target == "https://soundcloud.com/a/sets/b"
    assert args.export_format == "json"
    assert args.limit is None


def test_log_level_works_before_a_bare_link():
    args = cli.parse_cli_args(["--log-level", "DEBUG", "https://soundcloud.com/a/sets/b"])
    assert args.command == "dig"
    assert args.log_level == "DEBUG"


def test_v01_flag_names_still_work():
    args = cli.parse_cli_args(["dig", "playlist.html", "--export", "csv", "--max-tracks", "5"])
    assert args.export_format == "csv"
    assert args.limit == 5


def test_short_flags():
    args = cli.parse_cli_args(["link", "-f", "csv", "-o", "out.csv", "-n", "3"])
    assert (args.export_format, str(args.output), args.limit) == ("csv", "out.csv", 3)


def test_open_still_takes_its_v01_flags():
    args = cli.parse_cli_args(
        ["open", "crate.json", "--category", "bandcamp", "--skip", "2", "--limit", "4"]
    )
    assert args.command == "open"
    assert (args.category, args.skip, args.limit) == ("bandcamp", 2, 4)


def test_top_level_help_lists_the_subcommands(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.parse_cli_args(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "dig" in out and "open" in out and "auth" in out


def test_auth_subcommand_parsing():
    args = cli.parse_cli_args(["auth", "login", "--token", "test-token"])
    assert args.command == "auth"
    assert args.auth_action == "login"
    assert args.token == "test-token"

    args = cli.parse_cli_args(["auth", "status"])
    assert args.command == "auth"
    assert args.auth_action == "status"

    args = cli.parse_cli_args(["auth", "logout"])
    assert args.command == "auth"
    assert args.auth_action == "logout"


def test_soundcloud_login_opens_managed_chromium_after_detection_fails(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.auth_module, "get_stored_token", lambda: None)
    monkeypatch.setattr(cli.auth_module, "auto_detect_and_verify", lambda _id: None)
    monkeypatch.setattr(
        cli.auth_module,
        "login_with_chromium",
        lambda client_id, **_kwargs: ("secret", "DJ Test", 42),
    )
    monkeypatch.setattr(
        cli.soundcloud.SoundCloudClient,
        "client_id",
        property(lambda _self: "client-id"),
    )

    assert cli.handle_auth(argparse.Namespace(auth_action="login", token=None)) == 0
    output = capsys.readouterr().out
    assert "DJ Test" in output
    assert "secret" not in output


def test_soundcloud_login_uses_hidden_manual_fallback_when_browser_cannot_start(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.auth_module, "get_stored_token", lambda: None)
    monkeypatch.setattr(cli.auth_module, "auto_detect_and_verify", lambda _id: None)
    monkeypatch.setattr(
        cli.auth_module,
        "login_with_chromium",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            cli.auth_module.SoundCloudAuthError("browser unavailable")
        ),
    )
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "pasted-token")
    monkeypatch.setattr(
        cli.auth_module,
        "verify_and_save",
        lambda token, client_id: (token, "Manual DJ", 8),
    )
    monkeypatch.setattr(
        cli.soundcloud.SoundCloudClient,
        "client_id",
        property(lambda _self: "client-id"),
    )

    assert cli.handle_auth(argparse.Namespace(auth_action="login", token=None)) == 0
    output = capsys.readouterr().out
    assert "Manual DJ" in output
    assert "pasted-token" not in output


def test_soundcloud_login_does_not_wait_for_browser_without_a_tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.auth_module, "get_stored_token", lambda: None)
    monkeypatch.setattr(cli.auth_module, "auto_detect_and_verify", lambda _id: None)
    monkeypatch.setattr(
        cli.auth_module,
        "login_with_chromium",
        lambda *_args, **_kwargs: pytest.fail("must not open interactive browser"),
    )
    monkeypatch.setattr(
        cli.soundcloud.SoundCloudClient,
        "client_id",
        property(lambda _self: "client-id"),
    )

    assert cli.handle_auth(argparse.Namespace(auth_action="login", token=None)) == 1


def test_invalid_soundcloud_environment_override_is_reported_before_wizard(
    monkeypatch, capsys
):
    monkeypatch.setenv("SOUNDCLOUD_OAUTH_TOKEN", "stale-env-token")
    monkeypatch.setattr(cli.auth_module, "verify_token", lambda *_args: None)
    monkeypatch.setattr(
        cli.auth_module,
        "login_with_chromium",
        lambda *_args, **_kwargs: pytest.fail("shadowed login must not be offered"),
    )
    monkeypatch.setattr(
        cli.soundcloud.SoundCloudClient,
        "client_id",
        property(lambda _self: "client-id"),
    )

    assert cli.handle_auth(argparse.Namespace(auth_action="login", token=None)) == 1
    assert "SOUNDCLOUD_OAUTH_TOKEN" in capsys.readouterr().out


def test_unknown_export_format_is_rejected():
    with pytest.raises(SystemExit):
        cli.parse_cli_args(["link", "-f", "sqlite"])


def test_dig_rejects_a_target_that_is_not_a_soundcloud_link(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = cli.parse_cli_args(["definitely-not-here.html"])
    with pytest.raises(TargetNotFound, match="not a soundcloud.com link"):
        cli.handle_dig(args)


def test_main_turns_a_bad_target_into_a_clean_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="not a soundcloud.com link"):
        cli.main(["definitely-not-here.html"])


def test_no_arguments_means_dig_with_nothing_to_dig_yet():
    args = cli.parse_cli_args([])
    assert args.command == "dig"
    assert args.target is None


def test_no_arguments_is_an_error_when_handling_a_dig():
    args = cli.parse_cli_args([])
    with pytest.raises(SystemExit, match="Nothing to dig"):
        cli.handle_dig(args)


def test_a_cli_dig_joins_the_library(tmp_path, monkeypatch):
    """The library is the source of truth, so both entry points feed it."""

    monkeypatch.chdir(tmp_path)
    crate = Crate(
        source="https://soundcloud.com/a/sets/b",
        title="From the CLI",
        tracks=[Track(title="T", permalink_url="https://soundcloud.com/a/t", id=7)],
    )
    monkeypatch.setattr("dj_digger.services.collection.dig", lambda target, **kwargs: crate)

    assert cli.main(["https://soundcloud.com/a/sets/b", "-f", "none"]) == 0
    assert [record.title for record in library.list_crates()] == ["From the CLI"]


def test_a_cli_dig_respects_earlier_local_deletions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracks = [
        Track(title="A", permalink_url="https://soundcloud.com/a/a", id=1),
        Track(title="B", permalink_url="https://soundcloud.com/a/b", id=2),
    ]
    crate = Crate(source="https://soundcloud.com/a/sets/b", title="Crate", tracks=tracks)

    record = crate_models.CrateRecord.from_crate(crate)
    record.remove("2")
    library.save(record)

    monkeypatch.setattr("dj_digger.services.collection.dig", lambda target, **kwargs: crate)
    cli.main(["https://soundcloud.com/a/sets/b", "-f", "json", "-o", "out.json"])

    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    titles = [item["title"] for items in written.values() for item in items]
    assert titles == ["A"]


def test_dig_options_carry_the_cli_knobs():
    options = cli._dig_options(cli.parse_cli_args(["link", "-n", "5", "--timeout", "3"]))
    assert (options.limit, options.timeout) == (5, 3.0)


def test_the_browser_flag_is_gone():
    """Deprecated in 0.6, removed in 0.8. The browser is a setting now."""

    with pytest.raises(SystemExit):
        cli.parse_cli_args(["--browser", "firefox", "https://soundcloud.com/a/sets/b"])


def test_batch_open_uses_the_browser_from_settings(monkeypatch, tmp_path):
    """The CLI and the desktop must not disagree about which browser you meant."""

    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps({"browser": "firefox"}), encoding="utf-8")
    monkeypatch.setattr("dj_digger.config.default_config_path", lambda: config_path)

    used = []
    monkeypatch.setattr(
        "dj_digger.browser.open_urls",
        lambda urls, chosen="", **kwargs: used.append(chosen) or len(list(urls)),
    )

    record = LinkRecord(
        category="bandcamp",
        track=Track(title="T", permalink_url="https://soundcloud.com/a/t"),
        link_url="https://label.bandcamp.com/track/t",
        link_text="Buy",
    )
    args = argparse.Namespace(category="bandcamp", skip=0, limit=None)
    cli._batch_open(args, [record])

    assert used == ["firefox"]


def test_default_log_is_a_file_and_excludes_dependency_noise():
    """basicConfig configured the root logger, so urllib3 came out with us.

    A dig across 484 tracks printed dozens of "Retrying (Retry(total=1..." lines,
    one per dead link in the playlist, before it printed a single result.
    """

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        # Inside the redirect: StreamHandler binds sys.stderr when it is built.
        path = cli.configure_logging("INFO")
        logging.getLogger("urllib3.connectionpool").warning("Retrying (Retry(total=1...))")
        logging.getLogger("dj_digger.dig").info("Collected 484 tracks.")
    written = path.read_text(encoding='utf-8')

    assert "Retrying" not in written
    assert "Collected 484 tracks." in written
    assert stderr.getvalue() == ''


def test_debug_still_shows_everything():
    """The one level where somebody does want the library's side of the story."""

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        path = cli.configure_logging("DEBUG")
        logging.getLogger("urllib3.connectionpool").warning("Retrying (Retry(total=1...))")

    assert "Retrying" in path.read_text(encoding='utf-8')
    assert stderr.getvalue() == ''


def test_a_log_file_takes_the_log_off_the_screen(tmp_path):
    """A --log-file replaces the stderr stream handler."""

    path = tmp_path / "logs" / "dj.log"
    stderr = io.StringIO()
    logger = logging.getLogger("dj_digger")
    try:
        with contextlib.redirect_stderr(stderr):
            cli.configure_logging("INFO", str(path))
            logging.getLogger("dj_digger.dig").info("Collected 484 tracks.")
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    assert stderr.getvalue() == ""
    assert "INFO dj_digger.dig: Collected 484 tracks." in path.read_text(encoding="utf-8")


