"""Linux stand-ins for win32.py, so the rest of the app doesn't need to care which system it's on.

The app runs as an X11 client on both Linux Mint (X11) and Ubuntu (Wayland, through XWayland): Wayland
doesn't let apps place their own windows, and fences have to. Qt asks the window manager to keep
fences below other windows; there's no Linux equivalent of pinning them to Progman.
"""
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImageReader

from .snapping import SNAP_DISTANCE, SNAP_GAP, snap_moving, snap_sizing  # noqa: F401 (same names as win32.py)


class _NoWindowsApi:
    """Stands in for user32: code guarded by "is this a Windows window?" simply does nothing."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: 0


user32 = _NoWindowsApi()
WM_SIZING, WM_MOVING, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE = 0, 0, 0, 0


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom

    @classmethod
    def of(cls, g):
        return cls(g.left(), g.top(), g.left() + g.width(), g.top() + g.height())


def pin_to_desktop(hwnd):
    pass


def set_desktop_icons_visible(visible):
    pass


def desktop_file_field(path, field):
    """A value from the [Desktop Entry] part of a .desktop launcher, or ""."""
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"{field}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def shell_icon(path, jumbo=False):
    """A .desktop launcher's own icon; everything else falls back to Qt's icon for the file type."""
    if Path(path).suffix.lower() != ".desktop":
        return None
    name = desktop_file_field(path, "Icon")
    if not name:
        return None
    icon = QIcon(name) if name.startswith("/") else QIcon.fromTheme(name)
    return None if icon.isNull() else icon


def shell_thumbnail(path, size=320):
    """A scaled copy of an image file as a QImage, or None. No shared thumbnailer for other types."""
    reader = QImageReader(str(path))
    if not reader.canRead():
        return None
    original = reader.size()
    if original.isValid():
        reader.setScaledSize(original.scaled(QSize(size, size), Qt.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else image


def work_area(rect):
    """Work area (screen minus panels) of the monitor the rect's centre is on."""
    centre = QRectF(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top).center().toPoint()
    screen = QGuiApplication.screenAt(centre) or QGuiApplication.primaryScreen()
    return Rect.of(screen.availableGeometry())


def gpu_adapters():
    return []


class DesktopDoubleClick(QObject):
    """There's no way to watch clicks on the desktop from an X11 app; the tray icon toggles instead."""

    def __init__(self, callback):
        super().__init__()
