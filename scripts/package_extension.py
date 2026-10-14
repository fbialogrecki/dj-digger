"""Build store uploads of the browser extension: dist/extension/dj-digger-{chrome,firefox}-<version>.zip.

The extension folder carries a manifest for both browsers and the development
``key`` (a fixed ID for unpacked installs). The Chrome Web Store refuses a
``key`` and assigns its own ID, and Chrome warns about Firefox's
``background.scripts``; addons.mozilla.org has no use for either Chrome field.
Each zip gets its own manifest; every other file is copied unchanged except the
manual self-test page. Entries are sorted with fixed timestamps, so the same
sources always give the same bytes.

    python3 scripts/package_extension.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "dj_digger" / "webext" / "extension"
EXCLUDE = {"selftest.html", "selftest.js"}  # manual dom.js check, opened by hand only


def manifests(manifest: dict) -> dict[str, dict]:
    chrome = {k: v for k, v in manifest.items() if k not in ("key", "browser_specific_settings")}
    chrome["background"] = {"service_worker": manifest["background"]["service_worker"]}
    firefox = {k: v for k, v in manifest.items() if k != "key"}
    firefox["background"] = {"scripts": manifest["background"]["scripts"]}
    return {"chrome": chrome, "firefox": firefox}


def build(out: Path, source: Path = SOURCE) -> list[Path]:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    files = sorted(
        p for p in source.rglob("*")
        if p.is_file() and p.name not in EXCLUDE and p.name != "manifest.json" and "__pycache__" not in p.parts
    )
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for browser, body in manifests(manifest).items():
        entries = {p.relative_to(source).as_posix(): p.read_bytes() for p in files}
        entries["manifest.json"] = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode()
        path = out / f"dj-digger-{browser}-{manifest['version']}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(entries):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, entries[name])
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "extension")
    for path in build(parser.parse_args().out):
        print(path)


if __name__ == "__main__":
    main()
