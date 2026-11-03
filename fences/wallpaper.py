"""Fence colours matched to the desktop wallpaper."""
import ctypes
import os
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage

# Windows keeps a copy of whatever is on the desktop right now (including slideshow and Spotlight).
TRANSCODED = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper"


def wallpaper_file():
    if TRANSCODED.exists():
        return TRANSCODED
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.SystemParametersInfoW(0x73, 512, buf, 0)  # SPI_GETDESKWALLPAPER
    return Path(buf.value) if buf.value and Path(buf.value).exists() else None


def tint_from_image(image, alpha=165):
    """A dark, translucent fence colour taken from the image's most prominent vivid hue."""
    small = image.scaled(48, 48, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    buckets = {}  # 30° hue slice -> [weight, r, g, b]
    avg = [0.0, 0.0, 0.0]
    n = small.width() * small.height()
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            r, g, b = c.redF(), c.greenF(), c.blueF()
            avg[0] += r / n
            avg[1] += g / n
            avg[2] += b / n
            hue, sat, val = c.hsvHueF(), c.hsvSaturationF(), c.valueF()
            if hue < 0 or sat < 0.15 or val < 0.12:
                continue  # greys and near-black don't say much about the picture's colour
            weight = sat * val
            slot = buckets.setdefault(int(hue * 12) % 12, [0.0, 0.0, 0.0, 0.0])
            slot[0] += weight
            slot[1] += r * weight
            slot[2] += g * weight
            slot[3] += b * weight
    best = max(buckets.values(), key=lambda s: s[0], default=None)
    if best and best[0] > n * 0.02:
        base = QColor.fromRgbF(best[1] / best[0], best[2] / best[0], best[3] / best[0])
    else:
        base = QColor.fromRgbF(*avg)  # a mostly grey picture: just use its average
    hue = max(base.hsvHueF(), 0.0)
    return QColor.fromHsvF(hue, min(base.hsvSaturationF(), 0.55), 0.22, alpha / 255)


class WallpaperWatcher(QObject):
    """Emits `changed` with a new fence colour whenever the wallpaper changes."""
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = None
        self.color = None
        self.timer = QTimer(self, interval=10000, timeout=self.check)

    def start(self):
        self.check()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def check(self):
        path = wallpaper_file()
        try:
            key = (str(path), path.stat().st_mtime) if path else None
        except OSError:
            key = None
        if key == self.key:
            return
        self.key = key
        image = QImage(str(path)) if path else QImage()
        self.color = None if image.isNull() else tint_from_image(image)
        self.changed.emit(self.color)
