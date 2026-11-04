"""Linux stand-ins for win32.py, so the rest of the app doesn't need to care which system it's on.

The app runs as an X11 client on both Linux Mint (X11) and Ubuntu (Wayland, through XWayland): Wayland
doesn't let apps place their own windows, and fences have to. Qt asks the window manager to keep
fences below other windows; pin_to_desktop adds what that leaves out, the closest Linux gets to
pinning them to Progman.
"""
import ctypes
import ctypes.util
from pathlib import Path

import psutil
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


_DESKTOP_ICONS = ("nemo-desktop", "caja", "xfdesktop", "pcmanfm", "ding@rastersoft.com")
_ALL_WORKSPACES = 0xFFFFFFFF
_XA_ATOM, _XA_CARDINAL = 4, 6
_CLIENT_MESSAGE, _EVENT_TO_WINDOW_MANAGER = 33, (1 << 19) | (1 << 20)


class _ClientMessage(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int), ("data", ctypes.c_long * 5), ("pad", ctypes.c_long * 12)]


class _X11:
    """Our own small connection to the X server: Qt has no call for these two window properties."""

    def __init__(self):
        name = ctypes.util.find_library("X11")
        self.lib = lib = ctypes.CDLL(name or "libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        # Xlib's default handler ends the whole app on any error, such as a window that has just gone.
        self.ignore_errors = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(lambda *args: 0)
        lib.XSetErrorHandler(self.ignore_errors)
        self.display = lib.XOpenDisplay(None)

    def atom(self, name):
        return self.lib.XInternAtom(self.display, name, 0)

    def set_property(self, window, name, kind, value):
        data = ctypes.c_ulong(value)
        self.lib.XChangeProperty(self.display, window, self.atom(name), kind, 32, 0, ctypes.byref(data), 1)

    def send_to_workspace(self, window, workspace):
        event = _ClientMessage(type=_CLIENT_MESSAGE, window=window, format=32,
                               message_type=self.atom(b"_NET_WM_DESKTOP"))
        event.data[0] = workspace
        event.data[1] = 1  # 1: this comes from an ordinary app
        root = self.lib.XDefaultRootWindow(self.display)
        self.lib.XSendEvent(self.display, root, 0, _EVENT_TO_WINDOW_MANAGER, ctypes.byref(event))


_x11 = None


def _connection():
    global _x11
    if _x11 is None:
        try:
            _x11 = _X11()
        except OSError:
            _x11 = False  # no libX11 here: fences just stay on one workspace
    return _x11 if _x11 and _x11.display else None


def _desktop_icons_running():
    for process in psutil.process_iter(["cmdline"]):
        if any(name in " ".join(process.info["cmdline"] or ()) for name in _DESKTOP_ICONS):
            return True
    return False


def pin_to_desktop(hwnd):
    """Puts a window on every workspace and, on a bare GNOME desktop, into the window manager's desktop
    layer, where apps always cover it and "Show desktop" leaves it alone. Where a file manager draws the
    desktop icons that layer is its own, and a fence there can end up behind the icons, so skip it."""
    x11 = _connection()
    if x11 is None:
        return
    if not _desktop_icons_running():
        x11.set_property(hwnd, b"_NET_WM_WINDOW_TYPE", _XA_ATOM, x11.atom(b"_NET_WM_WINDOW_TYPE_DESKTOP"))
    # The window manager reads the property if it hasn't taken the window on yet, the message if it has.
    x11.set_property(hwnd, b"_NET_WM_DESKTOP", _XA_CARDINAL, _ALL_WORKSPACES)
    x11.send_to_workspace(hwnd, _ALL_WORKSPACES)
    x11.lib.XFlush(x11.display)


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
