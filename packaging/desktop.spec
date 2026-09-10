"""Two entry points, one collection of Python/Qt libraries. Run from repo root."""
import platform
import shutil
import sys
import tomllib
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
macos = sys.platform == "darwin"
datas = [(str(root / 'dj_digger' / 'gui' / 'qml'), 'dj_digger/gui/qml'),
         (str(root / 'dj_digger' / 'gui' / 'translations'), 'dj_digger/gui/translations')]
binaries, hiddenimports = [], []
if macos:
    for tool in ("ffmpeg", "ffprobe"):
        executable = shutil.which(tool)
        if not executable:
            raise RuntimeError(f"Install {tool} before building the DMG")
        binaries.append((executable, "tools"))
for package in ('librosa', 'numba', 'llvmlite', 'soundfile', 'playwright'):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden

def analyze(entry):
    return Analysis([str(root / 'packaging' / entry)], pathex=[str(root)],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    hookspath=[str(root / 'packaging/hooks')],
                    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebView'], noarchive=False)

gui = analyze('gui_entry.py')
helper = analyze('analysis_entry.py')
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='dj-digger-gui', console=False, upx=False,
              icon=str(root / ('packaging/macos/icon.icns' if macos else 'packaging/windows/icon.ico')),
              target_arch=platform.machine() if macos else None, codesign_identity=None)
helper_exe = EXE(PYZ(helper.pure), helper.scripts, [], exclude_binaries=True,
                 name='dj-digger-analysis', console=True, upx=False,
                 target_arch=platform.machine() if macos else None, codesign_identity=None)
collection = COLLECT(gui_exe, helper_exe, gui.binaries, gui.datas, helper.binaries, helper.datas,
        name='dj-digger', upx=False)

if macos:
    version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    # COLLECT sorts both executables; explicitly inherit the windowed GUI entry.
    BUNDLE(gui_exe, collection.toc, name='dj-digger.app', icon=str(root / 'packaging/macos/icon.icns'),
           bundle_identifier='com.fbialogrecki.dj-digger', version=version,
           info_plist={'CFBundleDisplayName': 'dj-digger', 'LSMinimumSystemVersion': '15.0',
                       'NSHighResolutionCapable': True})
