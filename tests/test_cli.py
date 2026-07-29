from __future__ import annotations

import json

import pytest

from dj_digger import cli, library, links
from dj_digger.dig import TargetNotFound
from dj_digger.models import Crate, Track


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
    with pytest.raises(TargetNotFound, match="neither"):
        cli.handle_dig(args)


def test_main_turns_a_bad_target_into_a_clean_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="neither"):
        cli.main(["definitely-not-here.html", "--no-tui"])


def test_no_arguments_means_dig_with_nothing_to_dig_yet():
    args = cli.parse_cli_args([])
    assert args.command == "dig"
    assert args.target is None


def test_no_arguments_without_a_tui_is_an_error(monkeypatch):
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    args = cli.parse_cli_args(["--no-tui"])
    with pytest.raises(SystemExit, match="Nothing to dig"):
        cli.handle_dig(args)


def test_no_arguments_opens_the_tui_to_ask(monkeypatch):
    """Bare `dj-digger` should hand straight over to the browser, which prompts."""

    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    captured = {}

    def fake_run(self):
        captured["records"] = self.rows
        captured["options"] = self.dig_options

    monkeypatch.setattr("dj_digger.tui.DiggerApp.run", fake_run)
    assert cli.handle_dig(cli.parse_cli_args([])) == 0
    assert captured["records"] == []
    assert captured["options"].timeout == 20.0


def test_a_cli_dig_joins_the_library(tmp_path, monkeypatch):
    """The library is the source of truth, so both entry points feed it."""

    monkeypatch.chdir(tmp_path)
    crate = Crate(
        source="https://soundcloud.com/a/sets/b",
        title="From the CLI",
        tracks=[Track(title="T", permalink_url="https://soundcloud.com/a/t", id=7)],
    )
    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate)

    assert cli.main(["https://soundcloud.com/a/sets/b", "--no-tui", "-f", "none"]) == 0
    assert [record.title for record in library.list_crates()] == ["From the CLI"]


def test_a_cli_dig_respects_earlier_local_deletions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tracks = [
        Track(title="A", permalink_url="https://soundcloud.com/a/a", id=1),
        Track(title="B", permalink_url="https://soundcloud.com/a/b", id=2),
    ]
    crate = Crate(source="https://soundcloud.com/a/sets/b", title="Crate", tracks=tracks)

    record = library.CrateRecord.from_crate(crate)
    record.remove("2")
    library.save(record)

    monkeypatch.setattr("dj_digger.dig.dig", lambda target, **kwargs: crate)
    cli.main(["https://soundcloud.com/a/sets/b", "--no-tui", "-f", "json", "-o", "out.json"])

    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    titles = [item["title"] for items in written.values() for item in items]
    assert titles == ["A"]


def test_open_imports_the_summary_into_the_library_as_partial(tmp_path, monkeypatch):
    summary = tmp_path / "crate.json"
    summary.write_text(
        json.dumps(
            {
                "bandcamp": [
                    {
                        "title": "A track",
                        "track_url": "https://soundcloud.com/a/t",
                        "shop_link": "https://label.bandcamp.com/track/t",
                        "track_id": 9,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    # Stub the app, not run_tui, so a signature mismatch in between is caught.
    monkeypatch.setattr("dj_digger.tui.DiggerApp.run", lambda self: None)

    assert cli.main(["open", str(summary)]) == 0

    crates = library.list_crates()
    assert len(crates) == 1
    assert crates[0].partial is True
    # The link survived the round trip, so the crate still categorises.
    assert links.categorise(crates[0].tracks[0])[0].category == "bandcamp"


def test_dig_options_carry_the_cli_knobs():
    options = cli._dig_options(cli.parse_cli_args(["link", "-n", "5", "--timeout", "3"]))
    assert (options.limit, options.timeout) == (5, 3.0)
