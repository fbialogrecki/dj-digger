import hashlib
import json
from pathlib import Path

import pytest

from dj_digger.gui import updates


def test_checks_only_a_newer_stable_release(monkeypatch):
    release = dict(tag_name='v1.10.0', assets=[])
    monkeypatch.setattr(updates, 'chunks', lambda *args: [json.dumps(release).encode()])
    assert updates.latest('1.9.0') == ('1.10.0', [])
    assert updates.latest('1.10.0') is None
    assert updates.latest('2.0.0') is None
    release['prerelease'] = True
    with pytest.raises(ValueError, match='stable'):
        updates.latest('1.9.0')


def test_uv_must_own_running_environment_and_pin_extras(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.sys, 'platform', 'linux')
    prefix = tmp_path / 'dj-sc-digger'
    prefix.mkdir()
    receipt = prefix / 'uv-receipt.toml'
    receipt.write_text('[tool]\nrequirements=[{name="dj-sc-digger",extras=["gui","analyze"]}]')
    monkeypatch.setattr(updates.sys, 'prefix', str(prefix))
    monkeypatch.setattr(updates.shutil, 'which', lambda name: '/bin/uv')
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, str(tmp_path)))
    assert updates.target('1.8.0') == ('uv', ['/bin/uv', 'tool', 'install', '--upgrade', 'dj-sc-digger[analyze,gui]==1.8.0'])
    receipt.write_text('[tool]\nrequirements=[{name="dj-sc-digger",directory="/src"}]')
    assert updates.target('1.8.0')[0] == 'manual'
    receipt.write_text('[tool]\nrequirements=[{name="dj-sc-digger",extras=["gui"]}]')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess([], 0, '/other'))
    assert updates.target('1.8.0')[0] == 'manual'


@pytest.mark.parametrize(('os', 'arch', 'suffix'), [('win32', 'AMD64', 'windows-x64-test.exe'),
                                                    ('darwin', 'arm64', 'macos-arm64-test.dmg'),
                                                    ('darwin', 'x86_64', 'macos-x86_64-test.dmg')])
def test_installer_verified_before_native_handoff(os, arch, suffix, tmp_path, monkeypatch):
    monkeypatch.setattr(updates.sys, 'platform', os)
    monkeypatch.setattr(updates.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updates.platform, 'machine', lambda: arch)
    monkeypatch.setattr(updates, 'data_dir', lambda: tmp_path)
    data = b'fixture'
    monkeypatch.setattr(updates, 'chunks', lambda *args: [data])
    mode, name = updates.target('1.8.0')
    assert mode == 'installer' and name == f'dj-digger-1.8.0-{suffix}'
    asset = dict(name=name, size=len(data), digest='sha256:' + '0' * 64,
                 browser_download_url=f'{updates.REPO}/releases/download/v1.8.0/{name}')
    with pytest.raises(ValueError, match='verification failed'):
        updates.installer(name, updates.installer_asset('1.8.0', name, [asset]), None)
    assert not list((tmp_path / 'updates').iterdir())
    asset['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()
    native = updates.installer(name, updates.installer_asset('1.8.0', name, [asset]), None)
    assert Path(native[-1]).read_bytes() == data
    assert native[:-1] == (['/usr/bin/open'] if os == 'darwin' else [])
    with pytest.raises(ValueError, match='matching installer'):
        updates.installer_asset('1.8.0', name, [{**asset, 'browser_download_url': 'https://other.test/a'}])


@pytest.mark.parametrize('status', [0, 1])
def test_uv_restart_uses_original_environment_after_success_or_failure(tmp_path, monkeypatch, status):
    import subprocess
    monkeypatch.setattr(updates.sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(subprocess, 'run', lambda command: subprocess.CompletedProcess(command, status))
    opened = []
    monkeypatch.setattr(subprocess, 'Popen', opened.append)
    updates.launch_update('uv', ['uv', 'tool', 'install', 'fixture'])
    assert opened == [[str(tmp_path / 'bin' / 'dj-digger-gui')]]


def test_pypi_requires_published_wheel_for_exact_version(monkeypatch):
    release = {'info': {'version': '2.0.0'}, 'urls': [{'packagetype': 'bdist_wheel', 'yanked': False}]}
    def fetch(url, limit):
        assert url == 'https://pypi.org/pypi/dj-sc-digger/2.0.0/json'
        return [json.dumps(release).encode()]
    monkeypatch.setattr(updates, 'chunks', fetch)
    updates.check_pypi('2.0.0')
    release['urls'][0]['yanked'] = True
    with pytest.raises(ValueError, match='PyPI'):
        updates.check_pypi('2.0.0')

@pytest.mark.parametrize(('running', 'kind'), [('1.8.0', 'message'), ('1.7.0', 'error')])
def test_next_desktop_start_checks_version_once_even_after_installer_cancellation(tmp_path, monkeypatch, running, kind):
    monkeypatch.setattr(updates, 'data_dir', lambda: tmp_path)
    updates.record_expected_version('1.8.0')
    event, payload = updates.update_result(running)
    assert event == kind and '1.8.0' in payload['args'] and running in payload['args']
    assert updates.update_result(running) is None
