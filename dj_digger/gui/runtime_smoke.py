"""Explicit offline smoke entry used only by the isolated packaging harness."""
import json
import math
import struct
import wave
from pathlib import Path


def run(directory):
    import miniaudio

    from .. import __version__
    from ..analysis import analyze_spawned
    from ..media import probe

    root = Path(directory)
    assert __version__ != '0.0.0+unknown', 'Application version metadata is missing'
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
    # The packaged extension and its relay must be present; no browser is started.
    from ..webext import host, link, register
    extension_ids = register.extension_ids(register.read_extension_manifest())
    assert all(extension_ids) and (register.EXTENSION_SOURCE / 'allowlist.json').is_file()
    assert callable(host.main) and link.PROTOCOL
    (root / 'runtime-smoke.json').write_text(json.dumps({'probe': True, 'decode': True, 'analysis': True, 'extension': True}), encoding='utf-8')
