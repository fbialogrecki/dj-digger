"""Stable GitHub releases for the desktop, installed only after confirmation."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

from ..models import check_cancelled
from ..paths import data_dir
from ..private_json import write_private_json

REPO = 'https://github.com/fbialogrecki/dj-digger'
LATEST = 'https://api.github.com/repos/fbialogrecki/dj-digger/releases/latest'
VERSION = re.compile(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}')
MAX_INSTALLER = 1024 * 1024 * 1024


def chunks(url, limit, cancel=None):
    with requests.Session() as session:
        session.trust_env = False  # Public release reads must never attach .netrc credentials.
        for _ in range(5):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.netloc not in {
                'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                'objects.githubusercontent.com', 'pypi.org',
            }:
                raise ValueError('Unexpected release download host')
            with session.get(url, stream=True, timeout=(4, 10), allow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['Location'])
                    continue
                response.raise_for_status()
                size = 0
                for block in response.iter_content(256 * 1024):
                    check_cancelled(cancel)
                    size += len(block)
                    if size > limit:
                        raise ValueError('Release download exceeds its size limit')
                    yield block
                return
        raise ValueError('Too many release redirects')


def latest(installed):
    release = json.loads(b''.join(chunks(LATEST, 1024 * 1024)))
    version = str(release.get('tag_name', '')).removeprefix('v')
    if release.get('draft') or release.get('prerelease') or not VERSION.fullmatch(version):
        raise ValueError('Invalid stable release')
    if not VERSION.fullmatch(installed):
        raise ValueError('This development version must be updated from its checkout')
    if tuple(map(int, version.split('.'))) <= tuple(map(int, installed.split('.'))):
        return None
    return version, release.get('assets', [])


def check_pypi(version):
    metadata = json.loads(b''.join(chunks(f'https://pypi.org/pypi/dj-sc-digger/{version}/json', 1024 * 1024)))
    if (metadata.get('info', {}).get('version') != version
            or not any(f.get('packagetype') == 'bdist_wheel' and not f.get('yanked') for f in metadata.get('urls', []))):
        raise ValueError('This desktop version is not available on PyPI yet')


def record_expected_version(version):
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid release version')
    write_private_json(data_dir() / 'updates' / 'expected-version.json', version)


def update_result(installed):
    """Consume the handoff once while the desktop owns the data-directory lock."""
    path = data_dir() / 'updates' / 'expected-version.json'
    try:
        with path.open('rb') as stream:
            expected = json.loads(stream.read(64))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        expected = None
    path.unlink()
    if not isinstance(expected, str) or not VERSION.fullmatch(expected):
        return None
    if installed == expected:
        return 'message', {'text': 'Updated to version {0}', 'args': [installed]}
    return 'error', {'text': 'The update to {0} did not finish. Running version {1}.', 'args': [expected, installed]}


def launch_update(mode, command):
    if mode != 'uv':
        subprocess.Popen(command)
        return
    try:
        subprocess.run(command)
    except OSError:
        pass
    # uv keeps the environment when resolution fails; reopen it and report the failure there.
    launcher = Path(sys.prefix) / 'bin' / 'dj-digger-gui'
    subprocess.Popen([str(launcher)])


def target(version):
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid release version')
    arch = platform.machine().lower()
    if getattr(sys, 'frozen', False):
        if sys.platform == 'darwin' and arch in ('arm64', 'x86_64'):
            return 'installer', f'dj-digger-{version}-macos-{arch}-test.dmg'
        if sys.platform == 'win32' and arch in ('amd64', 'x86_64'):
            return 'installer', f'dj-digger-{version}-windows-x64-test.exe'
    elif sys.platform.startswith('linux') and (uv := shutil.which('uv')):
        prefix = Path(sys.prefix).resolve()
        receipt = prefix / 'uv-receipt.toml'
        if prefix.name == 'dj-sc-digger' and receipt.is_file():
            try:
                base = subprocess.run([uv, 'tool', 'dir'], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
                requirements = tomllib.loads(receipt.read_text(encoding='utf-8'))['tool']['requirements']
                if base and prefix == (Path(base) / 'dj-sc-digger').resolve() and len(requirements) == 1:
                    requirement = requirements[0]
                    if requirement['name'] == 'dj-sc-digger' and set(requirement) <= {'name', 'extras', 'specifier'}:
                        extras = set(requirement.get('extras', ())) | {'gui'}
                        if extras <= {'gui', 'analyze'}:
                            package = f"dj-sc-digger[{','.join(sorted(extras))}]=={version}"
                            return 'uv', [uv, 'tool', 'install', '--upgrade', package]
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                pass  # An unrecognized installation uses the manual instructions.
    return 'manual', f'{REPO}/releases/tag/v{version}'


def installer_asset(version, name, assets):
    url = f'{REPO}/releases/download/v{version}/{name}'
    asset = next((a for a in assets if a.get('name') == name and a.get('browser_download_url') == url), {})
    size = asset.get('size')
    digest = str(asset.get('digest') or '')
    if type(size) is not int or not 0 < size <= MAX_INSTALLER or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('The release has no matching installer with a verified SHA-256 digest')
    return url, size, digest


def installer(name, asset, cancel):
    url, size, digest = asset
    directory = data_dir() / 'updates'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = directory / name
    fd, temp_name = tempfile.mkstemp(dir=directory)
    temporary = Path(temp_name)
    try:
        checksum = hashlib.sha256()
        with os.fdopen(fd, 'wb') as stream:
            for block in chunks(url, size, cancel):
                checksum.update(block)
                stream.write(block)
            if stream.tell() != size or digest != 'sha256:' + checksum.hexdigest():
                raise ValueError('Installer verification failed')
        check_cancelled(cancel)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return ['/usr/bin/open', str(output)] if sys.platform == 'darwin' else [str(output)]
