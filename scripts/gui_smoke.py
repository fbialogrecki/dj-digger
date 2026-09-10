"""Run the desktop against an empty isolated profile; optional executable argument."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix='dj-digger-gui-test-') as temporary:
        root = Path(temporary)
        env = dict(os.environ, HOME=temporary, USERPROFILE=temporary,
                   LOCALAPPDATA=str(root / 'Local'), APPDATA=str(root / 'Roaming'),
                   QT_QPA_PLATFORM='offscreen')
        if len(sys.argv) > 1 and sys.platform == 'darwin':
            env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        for name in ('XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME'):
            env[name] = str(root / name)
        command = ([str(Path(sys.argv[1]).resolve())] if len(sys.argv) > 1 else
                   [sys.executable, '-c', 'from dj_digger.gui import main; raise SystemExit(main())'])
        result = subprocess.run([*command, '--smoke-test'], env=env, capture_output=True, text=True, timeout=30)
        if result.returncode or any(s in result.stderr for s in ('TypeError', 'ReferenceError', 'failed to load')):
            raise SystemExit(result.stderr or f'Desktop exit: {result.returncode}')
        if len(sys.argv) > 2 and sys.argv[2] == '--runtime':
            subprocess.run([*command, '--runtime-test', temporary], env=env, check=True, timeout=180)
            assert (root / 'runtime-smoke.json').is_file()
        print('Desktop started and closed using an isolated profile.')


if __name__ == '__main__':
    main()
