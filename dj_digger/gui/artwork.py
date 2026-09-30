"""The record label: a picture cut to a circle, colours untouched."""
import base64

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

LABEL_PIXELS = 256


def label_image(data: bytes, size: int = LABEL_PIXELS) -> str:
    """A PNG data URL of the picture's centre square in a circle; '' when it is not an image.

    QImage works off the GUI thread. Cutting here keeps the label free of shaders,
    which the software renderer does not run.
    """

    image = QImage.fromData(data)
    if image.isNull():
        return ''
    scaled = image.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    label = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    label.fill(Qt.transparent)
    painter = QPainter(label)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    circle = QPainterPath()
    circle.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(circle)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    buffer.open(QBuffer.WriteOnly)
    label.save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(bytes(encoded)).decode('ascii')
