"""Two entry points, one collection of Python/Qt libraries. Run from repo root."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parents[1]
datas = [(str(root / 'dj_digger' / 'gui' / 'qml'), 'dj_digger/gui/qml'),
         (str(root / 'dj_digger' / 'gui' / 'translations'), 'dj_digger/gui/translations')]
binaries, hiddenimports = [], []
for package in ('librosa', 'numba', 'llvmlite', 'soundfile', 'playwright'):
    data, binary, hidden = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += hidden

def analyze(entry):
    return Analysis([str(root / 'packaging/windows' / entry)], pathex=[str(root)],
                    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
                    hookspath=[str(root / 'packaging/windows/hooks')],
                    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebView'], noarchive=False)

gui = analyze('gui_entry.py')
helper = analyze('analysis_entry.py')
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True,
              name='dj-digger-gui', console=False, upx=False,
              icon=str(root / 'packaging/windows/icon.ico'))
helper_exe = EXE(PYZ(helper.pure), helper.scripts, [], exclude_binaries=True,
                 name='dj-digger-analysis', console=True, upx=False)
COLLECT(gui_exe, helper_exe, gui.binaries, gui.datas, helper.binaries, helper.datas,
        name='dj-digger', upx=False)
