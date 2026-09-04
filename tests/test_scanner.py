"""Matching a crate against files on disk, and putting a path on the clipboard."""

import os
import subprocess
import threading
from pathlib import Path

from dj_digger import clipboard, scanner
from dj_digger.clipboard import copy_to_clipboard
from dj_digger.db import Database
from dj_digger.models import Track
from dj_digger.scanner import LocalScanner, normalize_string


def test_normalize_string() -> None:
    assert normalize_string("Artist - Title (Original Mix)") == "artisttitleoriginalmix"


def scanner_over(tmp_path: Path, *filenames: str) -> LocalScanner:
    music = tmp_path / "Music"
    music.mkdir(exist_ok=True)
    for name in filenames:
        (music / name).write_text("audio content", encoding="utf-8")
    local = LocalScanner(directories=[music], db=Database(tmp_path / "test.db"))
    local.scan()
    return local


def test_artist_and_title_together_are_a_confident_match(tmp_path: Path) -> None:
    local = scanner_over(tmp_path, "Artist - Song.mp3")

    match = local.match_track(Track(title="Song", artist="Artist", permalink_url="http://sc/1"))

    assert match is not None
    assert "Artist - Song.mp3" in match.path
    assert match.confident is True


def test_a_title_on_its_own_matches_but_is_not_confident(tmp_path: Path) -> None:
    """Two artists can release a "Nightdrive"; only one of them is yours."""

    local = scanner_over(tmp_path, "Nightdrive.mp3")

    match = local.match_track(Track(title="Nightdrive", artist="Someone", permalink_url="http://sc/2"))

    assert match is not None
    assert "Nightdrive.mp3" in match.path
    assert match.confident is False


def test_a_short_title_is_not_evidence_of_anything(tmp_path: Path) -> None:
    local = scanner_over(tmp_path, "Intro.mp3")

    assert local.match_track(Track(title="Intro", artist="Someone", permalink_url="http://sc/3")) is None


def test_a_track_with_no_file_matches_nothing(tmp_path: Path) -> None:
    local = scanner_over(tmp_path, "Artist - Song.mp3")

    assert local.match_track(Track(title="Absent", artist="Nobody", permalink_url="http://sc/4")) is None


def test_exact_matches_use_the_scan_index(tmp_path: Path, monkeypatch) -> None:
    local = scanner_over(tmp_path, "Artist - Song.mp3")

    def unexpected_lookup(*_args):
        raise AssertionError("unexpected SQLite lookup")

    monkeypatch.setattr(
        local.db,
        "find_local_match",
        unexpected_lookup,
    )

    assert local.match_track(
        Track(title="Song", artist="Artist", permalink_url="http://sc/index")
    )


def test_the_scan_only_counts_files_it_has_not_seen(tmp_path: Path) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    (music / "Artist - Song.mp3").write_text("audio", encoding="utf-8")
    (music / "notes.txt").write_text("not audio", encoding="utf-8")
    local = LocalScanner(directories=[music], db=Database(tmp_path / "test.db"))

    assert local.scan() == 1
    assert local.scan() == 0, "an unchanged file should not be rewritten"


def test_a_scan_writes_in_batches_not_per_file(tmp_path: Path, monkeypatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    for index in range(5):
        (music / f"Artist - Song {index}.mp3").write_text("audio", encoding="utf-8")
    db = Database(tmp_path / "test.db")
    writes: list[int] = []
    real = db.upsert_local_files
    monkeypatch.setattr(db, "upsert_local_files", lambda rows: (writes.append(len(rows)), real(rows)))

    assert LocalScanner(directories=[music], db=db).scan() == 5
    assert writes == [5], "five files should be one transaction"


def test_a_cancelled_scan_stops_and_keeps_what_it_wrote(tmp_path: Path, monkeypatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    for index in range(4):
        (music / f"Artist - Song {index}.mp3").write_text("audio", encoding="utf-8")
    db = Database(tmp_path / "test.db")
    local = LocalScanner(directories=[music], db=db)
    cancel = threading.Event()
    seen = 0

    real_stat = os.stat

    def stat_then_cancel(path, *args, **kwargs):
        nonlocal seen
        if Path(path).suffix.lower() in scanner.AUDIO_EXTENSIONS:
            seen += 1
            if seen == 2:
                cancel.set()
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(scanner.os, "stat", stat_then_cancel)
    written = local.scan(cancel=cancel)

    assert 0 < written < 4
    assert len(db.get_cached_files()) == written, "the partial batch is flushed"
    assert local.scan() == 4 - written, "the next scan picks up the rest"


def test_non_audio_files_are_not_stated(tmp_path: Path, monkeypatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    (music / "Artist - Song.mp3").write_text("audio", encoding="utf-8")
    (music / "cover.jpg").write_text("not audio", encoding="utf-8")
    stated: list[str] = []
    real_stat = os.stat
    monkeypatch.setattr(
        scanner.os, "stat", lambda path, *a, **k: (stated.append(str(path)), real_stat(path, *a, **k))[1]
    )

    LocalScanner(directories=[music], db=Database(tmp_path / "test.db")).scan()

    assert any(path.endswith(".mp3") for path in stated)
    assert not any(path.endswith(".jpg") for path in stated)


def test_an_unreadable_folder_is_reported_not_swallowed(tmp_path: Path) -> None:
    if os.name == "nt" or os.geteuid() == 0:
        return  # root reads everything; nothing to assert
    music = tmp_path / "Music"
    locked = music / "locked"
    locked.mkdir(parents=True)
    (music / "Artist - Song.mp3").write_text("audio", encoding="utf-8")
    locked.chmod(0o000)
    try:
        local = LocalScanner(directories=[music], db=Database(tmp_path / "test.db"))
        assert local.scan() == 1
        assert local.errors and "locked" in local.errors[0]
    finally:
        locked.chmod(0o700)


def test_a_symlinked_folder_is_still_walked(tmp_path: Path) -> None:
    real = tmp_path / "Real"
    real.mkdir()
    (real / "Artist - Song.mp3").write_text("audio", encoding="utf-8")
    music = tmp_path / "Music"
    music.mkdir()
    (music / "linked").symlink_to(real, target_is_directory=True)

    local = LocalScanner(directories=[music], db=Database(tmp_path / "test.db"))
    assert local.scan() == 1


def test_a_decorated_track_in_a_nested_folder_is_found(tmp_path: Path) -> None:
    music = tmp_path / "Music"
    nested = music / "Bonheur EC"
    nested.mkdir(parents=True)
    audio = nested / "Actual Artist - Long Track Name (Original Mix).wav"
    audio.write_text("audio", encoding="utf-8")
    local = LocalScanner(directories=[music], db=Database(tmp_path / "test.db"))
    track = Track(
        title="Actual Artist - Long Track Name",
        artist="Uploader Label",
        permalink_url="http://sc/nested",
    )

    assert local.scan() == 1
    assert local.match_track(track) == (str(audio.resolve()), False)

    (nested / "Actual Artist - Long Track Name (Extended Mix).wav").write_text(
        "audio", encoding="utf-8"
    )
    assert local.scan() == 1
    assert local.match_track(track) is None, "two decorated versions are ambiguous"


def test_a_deleted_file_is_removed_from_the_cache_and_never_matches(
    tmp_path: Path,
) -> None:
    local = scanner_over(tmp_path, "Artist - Song.mp3")
    path = Path(
        local.match_track(
            Track(title="Song", artist="Artist", permalink_url="http://sc/1")
        ).path
    )
    path.unlink()

    assert local.scan() == 0
    assert local.match_track(
        Track(title="Song", artist="Artist", permalink_url="http://sc/1")
    ) is None
    assert str(path) not in local.db.get_cached_files()


def test_copy_to_clipboard_is_false_when_no_tool_exists(monkeypatch) -> None:
    """It used to return True even with nothing installed to copy with."""

    def missing(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(clipboard.subprocess, "run", missing)
    assert copy_to_clipboard("/path/to/file.mp3") is False


def test_copy_to_clipboard_stops_at_the_first_tool_that_takes_it(monkeypatch) -> None:
    tried = []

    def fake_run(command, **_kwargs):
        tried.append(command[0])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert copy_to_clipboard("/path/to/file.mp3") is True
    assert tried == ["wl-copy"]


def test_nothing_to_copy_is_not_a_copy() -> None:
    assert copy_to_clipboard("") is False
