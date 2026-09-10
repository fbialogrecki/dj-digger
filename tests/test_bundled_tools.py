from pathlib import Path

import pytest

from dj_digger import bundled


def test_source_install_does_not_override_tools(monkeypatch):
    monkeypatch.delattr(bundled.sys, 'frozen', raising=False)
    assert bundled.tool('ffmpeg') is None


def test_frozen_tools_are_absolute_and_missing_tool_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled.sys, 'platform', 'win32')
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


def test_macos_bundle_uses_resources_and_native_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled.sys, 'platform', 'darwin')
    monkeypatch.setattr(bundled.sys, 'frozen', True, raising=False)
    contents = tmp_path / 'Applications with spaces/dj-digger.app/Contents'
    executable = contents / 'MacOS/dj-digger-gui'
    executable.parent.mkdir(parents=True)
    monkeypatch.setattr(bundled.sys, 'executable', str(executable))
    resources = contents / 'Resources'
    (resources / 'tools').mkdir(parents=True)
    ffmpeg = resources / 'tools/ffmpeg'
    ffmpeg.write_bytes(b'test')
    helper = executable.with_name('dj-digger-analysis')
    helper.write_bytes(b'test')
    assert bundled.resource_root() == resources
    assert bundled.tool('ffmpeg') == ffmpeg
    assert bundled.tool('dj-digger-analysis') == helper
    with pytest.raises(FileNotFoundError, match='reinstall'):
        bundled.tool('ffprobe')


@pytest.mark.parametrize('dependency,allowed', [
    ('/usr/lib/libSystem.B.dylib', True),
    ('/System/Library/Frameworks/AppKit.framework/AppKit', True),
    ('@rpath/libavcodec.dylib', True),
    ('/opt/homebrew/opt/ffmpeg/lib/libavcodec.dylib', False),
    ('/usr/local/Cellar/ffmpeg/9/lib/libavcodec.dylib', False),
])
def test_dmg_rejects_external_build_machine_libraries(tmp_path, monkeypatch, dependency, allowed):
    import runpy
    import subprocess

    check = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'packaging/macos/test_dmg.py'))['check_dependencies']
    (tmp_path / 'ffmpeg').write_bytes(b'\xcf\xfa\xed\xfe' + bytes(28))
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: f'ffmpeg:\n\t{dependency} (compatibility version 1.0.0)\n')
    if allowed:
        check(tmp_path)
    else:
        with pytest.raises(AssertionError, match='External runtime dependency'):
            check(tmp_path)
