"""Explicit offline smoke entry used only by the isolated packaging harness."""
import json
import math
import struct
import wave
from pathlib import Path


def run(directory):
    import miniaudio
    from playwright.sync_api import sync_playwright

    from ..analysis import analyze_spawned
    from ..media import probe

    root = Path(directory)
    source = root / 'synthetic.wav'
    with wave.open(str(source), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i * math.tau * 440 / 22050)))
                                   for i in range(22050 * 3)))
    assert probe(source)['channels'] == 1
    assert len(miniaudio.decode_file(str(source)).samples) > 0
    result = analyze_spawned(source)
    assert result['sha256'] and result['parameters']['rate'] == 22050
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content('<title>Offline bundled browser</title>')
            assert page.title() == 'Offline bundled browser'
        finally:
            browser.close()
    (root / 'runtime-smoke.json').write_text(json.dumps({'probe': True, 'decode': True, 'analysis': True, 'browser': True}), encoding='utf-8')
