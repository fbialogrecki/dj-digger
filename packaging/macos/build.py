"""Build an offline test DMG on a native macOS runner, without Developer ID."""
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    if sys.platform != 'darwin' or platform.machine() not in {'arm64', 'x86_64'}:
        raise SystemExit('Build the DMG natively on an Apple Silicon or Intel Mac.')
    os.chdir(ROOT)
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        executable = shutil.which(name)
        if not executable:
            raise SystemExit(f'Install {name} before building the DMG.')
        with open(executable, 'rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        tools[name] = {'sha256': digest, 'version': subprocess.check_output([executable, '-version'], text=True).splitlines()[0]}
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging/desktop.spec'], check=True)
    bundle = ROOT / 'dist/dj-digger.app'
    resources = bundle / 'Contents/Resources'
    if any('qtwebengine' in path.name.lower() or 'qt6webengine' in path.name.lower() for path in bundle.rglob('*')):
        raise RuntimeError('Unexpected embedded Qt WebEngine in desktop bundle')
    # Keep Playwright's native browser bundle and its existing internal layout.
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(resources / 'browsers'))
    subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], env=env, check=True)
    versions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        versions[name] = distribution.version
        for entry in distribution.files or ():
            if '.dist-info/' in str(entry) and any(word in str(entry).lower() for word in ('license', 'copying', 'notice')):
                target = resources / 'licenses' / name / str(entry).split('.dist-info/', 1)[1]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(entry), target)
    # Include available notices from the installed FFmpeg formula and dependencies.
    prefixes = subprocess.check_output(['brew', '--prefix', 'ffmpeg'], text=True).splitlines()
    formulae = subprocess.check_output(['brew', 'deps', '--installed', 'ffmpeg'], text=True).splitlines()
    if formulae:
        prefixes += subprocess.check_output(['brew', '--prefix', *formulae], text=True).splitlines()
    for prefix in prefixes:
        directory = Path(prefix)
        for pattern in ('LICENSE*', 'COPYING*', 'NOTICE*', 'share/doc/**/LICENSE*', 'share/doc/**/COPYING*'):
            for file in directory.glob(pattern):
                if file.is_file():
                    target = resources / 'licenses/homebrew' / directory.name / file.relative_to(directory)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, target)
    shutil.copyfile(ROOT / 'LICENSE', resources / 'LICENSE')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    architecture = platform.machine()
    manifest = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'python': sys.version, 'platform': 'darwin', 'architecture': architecture,
                'minimum_macos': '15.0', 'developer_id': False, 'notarized': False,
                'dependencies': versions, 'tools': tools}
    (resources / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    # PyInstaller signs native code ad-hoc. Reseal the outer app after adding
    # resources; do not replace Chromium's nested signatures or use credentials.
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
    output = ROOT / 'dist/installer'
    output.mkdir(parents=True, exist_ok=True)
    image = output / f'dj-digger-{version}-macos-{architecture}-test.dmg'
    with tempfile.TemporaryDirectory(prefix='dmg-', dir=ROOT / 'build') as temporary:
        staging = Path(temporary)
        subprocess.run(['ditto', str(bundle), str(staging / bundle.name)], check=True)
        (staging / 'Applications').symlink_to('/Applications')
        shutil.copyfile(ROOT / 'packaging/macos/INSTALL.txt', staging / 'INSTALL.txt')
        subprocess.run(['hdiutil', 'create', '-ov', '-format', 'UDZO', '-fs', 'HFS+',
                        '-volname', 'dj-digger', '-srcfolder', str(staging), str(image)], check=True)
    with image.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    image.with_suffix('.dmg.sha256').write_text(digest + '  ' + image.name + '\n', encoding='utf-8')
    image.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(f'Built test DMG: {image}', flush=True)


if __name__ == '__main__':
    main()
