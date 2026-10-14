import json
import zipfile

from scripts.package_extension import EXCLUDE, SOURCE, build


def test_store_zips_carry_a_per_browser_manifest_and_the_extension_files(tmp_path):
    chrome_zip, firefox_zip = build(tmp_path)
    source = json.loads((SOURCE / "manifest.json").read_text())
    expected = sorted(
        p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*")
        if p.is_file() and p.name not in EXCLUDE and "__pycache__" not in p.parts
    )
    assert chrome_zip.name == f"dj-digger-chrome-{source['version']}.zip"
    with zipfile.ZipFile(chrome_zip) as zf:
        assert zf.namelist() == expected
        chrome = json.loads(zf.read("manifest.json"))
    assert chrome["version"] == source["version"]
    assert "key" not in chrome and "browser_specific_settings" not in chrome
    assert chrome["background"] == {"service_worker": "background.js"}
    assert {k: v for k, v in chrome.items() if k != "background"} == {
        k: v for k, v in source.items() if k not in ("key", "browser_specific_settings", "background")
    }
    with zipfile.ZipFile(firefox_zip) as zf:
        firefox = json.loads(zf.read("manifest.json"))
    assert "key" not in firefox and firefox["background"] == {"scripts": ["dom.js", "background.js"]}
    assert firefox["browser_specific_settings"] == source["browser_specific_settings"]
    first = chrome_zip.read_bytes()
    build(tmp_path)
    assert chrome_zip.read_bytes() == first  # deterministic
