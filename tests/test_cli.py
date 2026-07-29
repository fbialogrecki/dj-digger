from __future__ import annotations

import pytest

from dj_digger import cli


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["https://soundcloud.com/a/sets/b"], ["dig", "https://soundcloud.com/a/sets/b"]),
        (["playlist.html"], ["dig", "playlist.html"]),
        (["dig", "playlist.html"], ["dig", "playlist.html"]),
        (["open", "crate.json"], ["open", "crate.json"]),
        (["--log-level", "DEBUG", "link"], ["dig", "--log-level", "DEBUG", "link"]),
        ([], []),
        # Help and version must reach the top-level parser, not the dig subparser.
        (["--help"], ["--help"]),
        (["-h"], ["-h"]),
        (["--version"], ["--version"]),
    ],
)
def test_default_command_injection(argv, expected):
    assert cli.inject_default_command(argv) == expected


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
    args = cli.parse_cli_args(["dig", "playlist.html", "--export", "yaml", "--max-tracks", "5"])
    assert args.export_format == "yaml"
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
    assert "{dig,open}" in capsys.readouterr().out


def test_unknown_export_format_is_rejected():
    with pytest.raises(SystemExit):
        cli.parse_cli_args(["link", "-f", "sqlite"])


def test_no_tui_is_honoured(monkeypatch):
    args = cli.parse_cli_args(["link", "--no-tui"])
    assert cli._should_use_tui(args) is False


def test_tui_is_skipped_when_output_is_piped(monkeypatch):
    args = cli.parse_cli_args(["link"])
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    assert cli._should_use_tui(args) is False


def test_dig_rejects_a_target_that_is_neither_link_nor_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = cli.parse_cli_args(["definitely-not-here.html", "--no-tui"])
    with pytest.raises(SystemExit, match="neither"):
        cli.handle_dig(args)
