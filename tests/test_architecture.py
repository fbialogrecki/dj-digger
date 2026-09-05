"""Executable dependency boundaries; no UI or device is needed by services."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""
            if not node.module:
                yield from (alias.name for alias in node.names)


def test_services_import_and_construct_without_ui_audio_or_browser():
    code = '''
import builtins, importlib, pkgutil
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert name.split('.')[0] not in {'textual', 'miniaudio', 'playwright'}, name
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import dj_digger.services
for module in pkgutil.iter_modules(dj_digger.services.__path__):
    importlib.import_module('dj_digger.services.' + module.name)
from dj_digger.services.runtime import ApplicationServices
services = ApplicationServices()
services.collection
assert services._state is services._client is services._player is services._cart is None
services.stop()
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_services_and_models_keep_their_dependency_boundaries():
    for path in (ROOT / "dj_digger/services").glob("*.py"):
        assert not any("textual" in name or "tui" in name.split(".") for name in imports(path)), path
    for filename in ("models.py", "crate_models.py", "gate_models.py", "cart_models.py", "store_match.py", "store_urls.py"):
        path = ROOT / "dj_digger" / filename
        assert not set(imports(path)) & {"sqlite3", "requests", "textual", "rich", "db", "browser_session"}, path
    for path in (ROOT / "dj_digger/tui").glob("*.py"):
        forbidden = {"soundcloud", "gates", "stores", "auth", "browser", "db", "scanner", "beatport_playlist"}
        assert not any(set(name.split(".")) & forbidden for name in imports(path)), path
    assert not set(imports(ROOT / "dj_digger/player.py")) & {"textual", "rich"}
