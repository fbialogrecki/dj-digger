"""The record label: a picture cut to a circle (or a plain square), colours untouched."""
import base64
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

LABEL_PIXELS = 256
SQUARE_PIXELS = 384  # The plain artwork fills the whole cover, at twice the label's size.
BROWSER_PIXELS = 192  # The browser extension's panel: a small JPEG, well under link.MAX_ARTWORK.


def label_image(data: bytes, size: int = LABEL_PIXELS, circle: bool = True, jpeg: bool = False, crop: float = .5) -> str:
    """A PNG (``jpeg``: JPEG, for a square) data URL of the picture's cropped square, in a
    circle unless ``circle`` is False; '' when it is not an image.

    QImage works off the GUI thread. Cutting here keeps the label free of shaders,
    which the software renderer does not run.
    """

    image = QImage.fromData(data)
    if image.isNull():
        return ''
    side = min(image.width(), image.height())
    source = QRectF(round((image.width() - side) * crop), round((image.height() - side) * crop), side, side)
    label = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    label.fill(Qt.transparent)
    painter = QPainter(label)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    if circle:
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, size, size))
        painter.setClipPath(path)
    painter.drawImage(QRectF(0, 0, size, size), image, source)
    painter.end()
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QBuffer.WriteOnly)
    label.save(buffer, 'JPEG' if jpeg else 'PNG', 80 if jpeg else -1)
    kind = 'jpeg' if jpeg else 'png'
    return f'data:image/{kind};base64,' + base64.b64encode(bytes(encoded)).decode('ascii')


def default_picture(path):
    if path:
        try:
            file = Path(path).expanduser()
            if not file.is_file():
                return b''
            with file.open('rb') as stream:
                data = stream.read(16 * 1024 * 1024 + 1)
            if len(data) <= 16 * 1024 * 1024:
                return data
        except (OSError, RuntimeError, ValueError):
            pass
    return b''
