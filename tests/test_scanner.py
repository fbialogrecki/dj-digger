"""Tests for LocalScanner and clipboard path copying."""

from pathlib import Path

from dj_digger.db import Database
from dj_digger.models import Track
from dj_digger.scanner import LocalScanner, copy_to_clipboard, normalize_string


def test_normalize_string() -> None:
    assert normalize_string("Artist - Title (Original Mix)") == "artisttitleoriginalmix"


def test_local_scanner_match(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    track_file = music_dir / "Artist - Song.mp3"
    track_file.write_text("audio content")

    scanner = LocalScanner(directories=[music_dir], db=db)
    scanned = scanner.scan()
    assert scanned == 1

    track = Track(title="Song", artist="Artist", permalink_url="http://sc.com/song")
    match = scanner.match_track(track)
    assert match is not None
    assert "Artist - Song.mp3" in match


def test_copy_to_clipboard() -> None:
    # Verify copy_to_clipboard executes without error
    assert copy_to_clipboard("/path/to/local/file.mp3") is True
