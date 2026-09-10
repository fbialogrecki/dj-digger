"""Mount the DMG and test a copied app without Homebrew on PATH."""
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

MACHO = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
         b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def check_dependencies(bundle):
    """A runner's Homebrew must not hide missing libraries in the shipped app."""
    for path in bundle.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open('rb') as stream:
            if stream.read(4) not in MACHO:
                continue
        output = subprocess.check_output(['otool', '-L', str(path)], text=True)
        for line in output.splitlines():
            if not line.startswith('\t'):
                continue
            dependency = line.strip().split(' (', 1)[0]
            if dependency.startswith('/') and not dependency.startswith(('/System/Library/', '/usr/lib/')):
                raise AssertionError(f'External runtime dependency: {path}: {dependency}')


def main():
    if sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run DMG acceptance only on a disposable macOS CI runner.')
    images = list(Path('dist/installer').glob(f'*-macos-{platform.machine()}-test.dmg'))
    assert len(images) == 1
    with tempfile.TemporaryDirectory(prefix='dj-digger-dmg-') as temporary:
        root = Path(temporary)
        mount = root / 'Mounted image'
        mount.mkdir()
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(images[0])], check=True)
        try:
            assert (mount / 'Applications').is_symlink()
            assert os.readlink(mount / 'Applications') == '/Applications'
            installed = root / 'Applications with spaces/dj-digger.app'
            subprocess.run(['ditto', str(mount / 'dj-digger.app'), str(installed)], check=True)
        finally:
            subprocess.run(['hdiutil', 'detach', str(mount)], check=True)
        info = plistlib.loads((installed / 'Contents/Info.plist').read_bytes())
        assert info['LSMinimumSystemVersion'] == '15.0'
        executable = installed / 'Contents/MacOS' / info['CFBundleExecutable']
        assert executable.is_file()
        assert platform.machine() in subprocess.check_output(['lipo', '-archs', str(executable)], text=True).split()
        check_dependencies(installed)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(installed)], check=True)
        subprocess.run([sys.executable, 'scripts/gui_smoke.py', str(executable), '--runtime'], check=True, timeout=240)
        print('DMG mounted, app copied, image ejected, isolated runtime passed.', flush=True)


if __name__ == '__main__':
    main()
