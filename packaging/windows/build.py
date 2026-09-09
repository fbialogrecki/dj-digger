"""Build the offline Windows artifact from locked dependencies and verified tools."""
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def download(spec, destination):
    digest = hashlib.sha256()
    with urlopen(spec['url'], timeout=60) as source, destination.open('wb') as output:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != spec['sha256']:
        destination.unlink()
        raise ValueError('Downloaded tool checksum mismatch')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows installer on Windows x64.')
    os.chdir(ROOT)
    tools = json.loads((ROOT / 'packaging/windows/tools.json').read_text())
    staging = ROOT / 'build/desktop-tools'
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / 'ffmpeg.zip'
    download(tools['ffmpeg'], archive)
    compiler = staging / 'inno-setup.exe'
    download(tools['inno'], compiler)
    inno = staging / 'inno'
    subprocess.run([str(compiler), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', f'/DIR={inno}'], check=True)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'packaging/windows/desktop.spec'], check=True)
    bundle = ROOT / 'dist/dj-digger'
    if any('qtwebengine' in file.name.lower() or 'qt6webengine' in file.name.lower() for file in bundle.rglob('*')):
        raise RuntimeError('Unexpected embedded Qt WebEngine in desktop bundle')
    (bundle / 'tools').mkdir(exist_ok=True)
    (bundle / 'licenses/ffmpeg').mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            name = Path(member.filename).name
            if name in {'ffmpeg.exe', 'ffprobe.exe'}:
                (bundle / 'tools' / name).write_bytes(source.read(member))
            elif not member.is_dir() and ('license' in name.lower() or 'readme' in name.lower()):
                (bundle / 'licenses/ffmpeg' / name).write_bytes(source.read(member))
    if not all((bundle / 'tools' / name).is_file() for name in ('ffmpeg.exe', 'ffprobe.exe')):
        raise RuntimeError('FFmpeg archive did not contain both tools')
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(bundle / 'browsers'))
    subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], env=env, check=True)
    versions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name']
        versions[name] = distribution.version
        for entry in distribution.files or ():
            if '.dist-info/' in str(entry).replace('\\', '/') and any(x in str(entry).lower() for x in ('license', 'copying', 'notice')):
                target = bundle / 'licenses' / name / str(entry).split('.dist-info', 1)[1].lstrip('/\\')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(distribution.locate_file(entry), target)
    shutil.copyfile(ROOT / 'LICENSE', bundle / 'LICENSE')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    manifest = {'commit': commit, 'python': sys.version, 'platform': sys.platform, 'dependencies': versions, 'tools': tools}
    (bundle / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    subprocess.run([str(inno / 'ISCC.exe'), f'/DAppVersion={version}', 'packaging/windows/installer.iss'], check=True)
    for installer in (ROOT / 'dist/installer').glob('*.exe'):
        installer.with_suffix('.exe.sha256').write_text(hashlib.file_digest(installer.open('rb'), 'sha256').hexdigest() + '  ' + installer.name + '\n')


if __name__ == '__main__':
    main()
