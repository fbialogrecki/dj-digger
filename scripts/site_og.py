"""Draw the website's Open Graph image (1200x630): the headline beside the app icon.

    uv run --extra gui --with fonttools --with brotli python scripts/site_og.py

Writes ``site/public/og.png`` from the icon's own drawing routine (``app_icon.draw``)
and the site's font, so the preview card cannot drift from either.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from app_icon import draw  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402
from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, MARGIN, ICON = 1200, 630, 72, 400
LINES = ('FROM A SOUNDCLOUD', 'PLAYLIST TO', 'FILES ON YOUR DECKS')  # the last one takes the gradient


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    with tempfile.TemporaryDirectory() as temporary:
        # Qt loads TrueType; the site ships the same font as WOFF2.
        source = TTFont(ROOT / 'site/src/assets/fonts/archivo-latin.woff2')
        source.flavor = None
        source.save(f'{temporary}/archivo.ttf')
        family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(f'{temporary}/archivo.ttf'))[0]

        def font(size, weight, width):
            face = QFont(family)
            face.setPixelSize(size)
            face.setVariableAxis(QFont.Tag('wght'), weight)
            face.setVariableAxis(QFont.Tag('wdth'), width)
            return face

        image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
        image.fill(QColor('#0b0b0e'))
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

        centre = QPointF(WIDTH - MARGIN - ICON / 2, HEIGHT / 2)
        glow = QRadialGradient(centre, ICON * .95)
        glow.setColorAt(0, QColor(255, 47, 168, 90))
        glow.setColorAt(.55, QColor(30, 155, 255, 40))
        glow.setColorAt(1, QColor(11, 11, 14, 0))
        painter.fillRect(image.rect(), QBrush(glow))
        painter.drawImage(QRectF(centre.x() - ICON / 2, centre.y() - ICON / 2, ICON, ICON), draw(ICON))

        painter.setPen(QColor('#a3a6ad'))
        small = font(22, 600, 125)
        small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        painter.setFont(small)
        painter.drawText(QPointF(MARGIN, MARGIN + 22), 'DJ-DIGGER  /  CRATE DIGGING FOR DJS')

        # The headline is as large as its longest line allows beside the icon.
        room = WIDTH - 2 * MARGIN - ICON - 48
        size = 120
        while max(QFontMetricsF(font(size, 800, 68)).horizontalAdvance(line) for line in LINES) > room:
            size -= 2
        head = font(size, 800, 68)
        painter.setFont(head)
        top = (HEIGHT - size * .98 * len(LINES)) / 2 + size * .84
        gradient = QLinearGradient(MARGIN, 0, MARGIN + room, 0)
        gradient.setColorAt(0, QColor('#ff2fa8'))
        gradient.setColorAt(1, QColor('#1e9bff'))
        for index, line in enumerate(LINES):
            painter.setPen(QPen(QBrush(gradient), 1) if index == len(LINES) - 1 else QColor('#ececef'))
            painter.drawText(QPointF(MARGIN, top + index * size * .98), line)

        painter.setPen(QColor('#a3a6ad'))
        painter.setFont(font(24, 500, 100))
        painter.drawText(QPointF(MARGIN, HEIGHT - MARGIN), 'Free and open source. Windows, macOS, Linux.')
        painter.end()
        image.save(str(ROOT / 'site/public/og.png'), 'PNG')
    del app
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
