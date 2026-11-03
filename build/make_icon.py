"""Render the app icon (the same design as the tray icon) into a multi-size Windows .ico."""
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter


def render(size):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)  # same geometry as fences.common.make_app_icon, which draws at 64px
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(52, 120, 220))
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setBrush(QColor(255, 255, 255, 235))
    for x, y in ((14, 14), (36, 14), (14, 36), (36, 36)):
        p.drawRoundedRect(x, y, 14, 14, 3, 3)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    from io import BytesIO
    return Image.open(BytesIO(bytes(buf.data())))


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    render(256).save(out, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128),
                                 (256, 256)])
    print(f"wrote {out}")
