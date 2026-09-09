from pathlib import Path

import pytest

from dj_digger import bundled


def test_source_install_does_not_override_tools(monkeypatch):
    monkeypatch.delattr(bundled.sys, 'frozen', raising=False)
    assert bundled.tool('ffmpeg') is None


def test_frozen_tools_are_absolute_and_missing_tool_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(bundled.sys, 'executable', str(tmp_path / 'dj-digger-gui.exe'))
    (tmp_path / 'tools').mkdir()
    expected = tmp_path / 'tools/ffmpeg.exe'
    expected.write_bytes(b'test')
    assert bundled.tool('ffmpeg') == expected
    with pytest.raises(FileNotFoundError, match='reinstall'):
        bundled.tool('ffprobe')
    helper = tmp_path / 'dj-digger-analysis.exe'
    helper.write_bytes(b'test')
    assert bundled.tool('dj-digger-analysis') == helper
    assert Path(bundled.tool('ffmpeg')).is_absolute()
