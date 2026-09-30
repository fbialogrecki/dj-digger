import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_icons_carry_every_size():
    png = (ROOT / 'dj_digger/gui/qml/icons/app.png').read_bytes()
    assert png[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack('>II', png[16:24]) == (256, 256)

    ico = (ROOT / 'packaging/windows/icon.ico').read_bytes()
    reserved, kind, count = struct.unpack('<HHH', ico[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for index in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack('<BBBBHHII', ico[6 + 16 * index:22 + 16 * index])
        assert ico[offset:offset + 8] == png[:8] and offset + length <= len(ico)
        sizes.append(width or 256)
    assert sizes == [16, 24, 32, 48, 64, 128, 256]

    icns = (ROOT / 'packaging/macos/icon.icns').read_bytes()
    assert icns[:4] == b'icns' and struct.unpack('>I', icns[4:8])[0] == len(icns)
    kinds, position = [], 8
    while position < len(icns):
        kinds.append(icns[position:position + 4])
        position += struct.unpack('>I', icns[position + 4:position + 8])[0]
    assert kinds == [b'icp4', b'icp5', b'icp6', b'ic07', b'ic08', b'ic09', b'ic10']
