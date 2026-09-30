"""Draw the application icon and write it for every platform.

A record carrying the same kind of barcode the desktop draws on a track's
artwork, with a white label, on a blue-to-bordeaux tile. Writes the window icon
(``dj_digger/gui/qml/icons/app.png``), the Windows ``icon.ico`` and the macOS
``icon.icns``. Run with the desktop extra:

    uv run --extra gui python scripts/app_icon.py
"""
import math
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

ROOT = Path(__file__).resolve().parents[1]
SEED = 'dj-digger'
BLUE, BORDEAUX = QColor('#2f63ea'), QColor('#c02a4e')
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_TYPES = ((b'icp4', 16), (b'icp5', 32), (b'icp6', 64), (b'ic07', 128), (b'ic08', 256), (b'ic09', 512), (b'ic10', 1024))


def seed_hash(text: str) -> int:
    """``cover.hash`` in Main.qml: 32-bit wrapping multiply-add, then its magnitude."""
    value = 7
    for char in text:
        value = (value * 31 + ord(char)) & 0xFFFFFFFF
    return abs(value - (1 << 32) if value & 0x80000000 else value)


def mulberry32(state: int):
    """The generator of the artwork's barcode, with JavaScript's 32-bit arithmetic."""
    def imul(a, b):
        return (a * b) & 0xFFFFFFFF

    def random():
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = imul(state ^ (state >> 15), 1 | state)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return random


# ponytail: a copy of the Cover barcode in Main.qml; the icon is a frozen asset and
# need not follow later changes to the artwork.
def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, BLUE)
    gradient.setColorAt(1, BORDEAUX)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * .22, size * .22)

    width = size * .84
    centre = QPointF(size / 2, size / 2)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawEllipse(centre, width / 2, width / 2)

    random = mulberry32(seed_hash(SEED))
    inner, outer = width * .19, width * .48
    patterns = [[(0, 1)], [(0, .5)], [(.5, 1)], [(.3, .7)], [(0, 1 / 3), (2 / 3, 1)], []]
    # Small sizes get bolder strokes and no dots, or the code turns to noise.
    line = max(1.0, width / (40 if size < 48 else 110))
    pen = QPen(QColor(255, 255, 255, 170), line)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    count, turn = 18 + math.floor(random() * 10), random() * math.pi * 2
    for i in range(count):
        angle = turn + (i + .15 + random() * .7) / count * math.pi * 2
        dx, dy = math.cos(angle), math.sin(angle)
        pattern = patterns[math.floor(random() * len(patterns))]
        painter.setPen(pen)
        for start, end in pattern:
            a, b = inner + start * (outer - inner), inner + end * (outer - inner)
            painter.drawLine(QPointF(centre.x() + dx * a, centre.y() + dy * a), QPointF(centre.x() + dx * b, centre.y() + dy * b))
        if not pattern or random() < .3:
            for _ in range(1 + math.floor(random() * 3)):
                radius = inner + random() * (outer - inner)
                if size >= 48:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 170))
                    painter.drawEllipse(QPointF(centre.x() + dx * radius, centre.y() + dy * radius), line * 1.2, line * 1.2)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawEllipse(centre, width * .17, width * .17)
    painter.end()
    return image


def png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


def ico(images: dict[int, bytes]) -> bytes:
    """An ICO of PNG entries, which Windows reads at every size since Vista."""
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b'', b''
    for size, data in images.items():
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    return header + entries + payload


def icns(images: list[tuple[bytes, bytes]]) -> bytes:
    body = b''.join(kind + struct.pack('>I', 8 + len(data)) + data for kind, data in images)
    return b'icns' + struct.pack('>I', 8 + len(body)) + body


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    rendered = {size: png(draw(size)) for size in {*ICO_SIZES, *(size for _, size in ICNS_TYPES)}}
    (ROOT / 'dj_digger/gui/qml/icons/app.png').write_bytes(rendered[256])
    (ROOT / 'packaging/windows/icon.ico').write_bytes(ico({size: rendered[size] for size in ICO_SIZES}))
    (ROOT / 'packaging/macos/icon.icns').write_bytes(icns([(kind, rendered[size]) for kind, size in ICNS_TYPES]))
    del app
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
