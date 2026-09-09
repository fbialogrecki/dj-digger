"""Installer lifecycle integration check, restricted to disposable Windows CI."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run only on a disposable Windows GitHub Actions runner.')
    installers = list(Path('dist/installer').glob('*.exe'))
    assert len(installers) == 1
    with tempfile.TemporaryDirectory(prefix='dj-digger-install-') as temporary:
        root = Path(temporary)
        destination = root / 'Program files with spaces'
        env = dict(os.environ)
        for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
            env[key] = str(root / key)
        sentinel = root / 'XDG_DATA_HOME/dj-digger/user-data.txt'
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text('preserve this data', encoding='utf-8')
        command = [str(installers[0].resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                   '/TASKS=desktopicon', f'/DIR={destination}']
        for _ in range(2):  # initial install and upgrade/reinstall
            subprocess.run(command, env=env, check=True, timeout=180)
            assert (destination / 'dj-digger-gui.exe').is_file()
            assert sentinel.read_text(encoding='utf-8') == 'preserve this data'
        subprocess.run([sys.executable, 'scripts/gui_smoke.py', str(destination / 'dj-digger-gui.exe'), '--runtime'],
                       env=env, check=True, timeout=240)
        subprocess.run([str(destination / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                       env=env, check=True, timeout=120)
        assert not (destination / 'dj-digger-gui.exe').exists()
        assert sentinel.read_text(encoding='utf-8') == 'preserve this data'
        print('Install, reinstall, bundled runtime and uninstall passed; user data preserved.')


if __name__ == '__main__':
    main()
