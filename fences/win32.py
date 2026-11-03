"""Win32 helpers: desktop pinning, shell icons, edge snapping, the desktop icon view,
GPU adapter info and raw mouse input."""
import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import QWidget

from .snapping import SNAP_DISTANCE, SNAP_GAP, snap_moving, snap_sizing  # noqa: F401

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
HWND = wintypes.HWND

user32.FindWindowW.restype = HWND
user32.FindWindowExW.argtypes = [HWND, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = HWND
user32.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.IsWindow.argtypes = [HWND]
user32.IsWindowVisible.argtypes = [HWND]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.GetParent.argtypes = [HWND]
user32.GetParent.restype = HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = HWND
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetDpiForWindow.argtypes = [HWND]
user32.SendMessageTimeoutW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                       wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.RegisterRawInputDevices.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.UINT]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def guid(d1, d2, d3, *d4):
    return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))


def vtable(com_ptr):
    return ctypes.cast(com_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def com_release(com_ptr):
    ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable(com_ptr)[2])(com_ptr)


# ---------------------------------------------------------------- desktop windows

def pin_to_desktop(hwnd):
    """Make Progman the window's owner so it survives Win+D / 'Show desktop'."""
    progman = user32.FindWindowW("Progman", None)
    if progman:
        user32.SetWindowLongPtrW(hwnd, -8, progman)  # GWL_HWNDPARENT


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def desktop_listview():
    """The SysListView32 that shows the desktop icons (under Progman, or a WorkerW on some setups)."""
    def in_parent(parent):
        defview = user32.FindWindowExW(parent, None, "SHELLDLL_DefView", None) if parent else None
        return user32.FindWindowExW(defview, None, "SysListView32", None) if defview else None

    found = in_parent(user32.FindWindowW("Progman", None))
    worker = None
    while not found:
        worker = user32.FindWindowExW(None, worker, "WorkerW", None)
        if not worker:
            return None
        found = in_parent(worker)
    return found


def is_desktop_window(hwnd):
    cls = class_name(hwnd)
    if cls == "SysListView32":
        return class_name(user32.GetParent(hwnd)) == "SHELLDLL_DefView"
    return cls in ("SHELLDLL_DefView", "WorkerW", "Progman")


def listview_selected_count(hwnd):
    result = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(hwnd, 0x1032, 0, 0, 0x2, 200, ctypes.byref(result))  # LVM_GETSELECTEDCOUNT
    return result.value if ok else 0


def set_desktop_icons_visible(visible):
    lv = desktop_listview()
    if lv:
        user32.ShowWindow(lv, 5 if visible else 0)  # SW_SHOW / SW_HIDE


# ---------------------------------------------------------------- shell icons

class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


IID_IImageList = guid(0x46EB5926, 0x582E, 0x4017, 0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50)
_IMAGE_LIST_GET_ICON = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_int, wintypes.UINT,
                                          ctypes.POINTER(wintypes.HICON))
_image_lists = {}


def shell_icon(path, jumbo=False):
    """The icon Explorer shows for this exact file, at 48px (or 256px when jumbo).

    QFileIconProvider caches icons per file extension, so every .url (e.g. Steam games) would
    get the same generic icon; asking the shell per file picks up each one's own IconFile.
    """
    sfi = SHFILEINFOW()
    if not shell32.SHGetFileInfoW(str(path), 0, ctypes.byref(sfi), ctypes.sizeof(sfi), 0x4000):  # SYSICONINDEX
        return None
    which = 4 if jumbo else 2  # SHIL_JUMBO / SHIL_EXTRALARGE
    if which not in _image_lists:
        il = ctypes.c_void_p()
        if shell32.SHGetImageList(which, ctypes.byref(IID_IImageList), ctypes.byref(il)):
            return None
        _image_lists[which] = il
    il = _image_lists[which]
    hicon = wintypes.HICON()
    if _IMAGE_LIST_GET_ICON(vtable(il)[10])(il, sfi.iIcon, 0x1, ctypes.byref(hicon)) or not hicon:  # ILD_TRANSPARENT
        return None
    try:
        image = QImage.fromHICON(hicon.value)
    finally:
        user32.DestroyIcon(hicon)
    return None if image.isNull() else QIcon(QPixmap.fromImage(image))


IID_IShellItemImageFactory = guid(0xBCC18B79, 0xBA16, 0x442F, 0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B)
shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                                ctypes.POINTER(ctypes.c_void_p)]
_GET_IMAGE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.SIZE, ctypes.c_int,
                                ctypes.POINTER(wintypes.HBITMAP))


def shell_thumbnail(path, size=320):
    """Explorer's thumbnail for a file (photos, videos, PDFs, Office files...) as a QImage, or None.
    Files without a thumbnail come back as their large icon."""
    factory = ctypes.c_void_p()
    if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(IID_IShellItemImageFactory),
                                           ctypes.byref(factory)):
        return None
    hbitmap = wintypes.HBITMAP()
    try:
        failed = _GET_IMAGE(vtable(factory)[3])(factory, wintypes.SIZE(size, size), 0, ctypes.byref(hbitmap))
    finally:
        com_release(factory)
    if failed or not hbitmap:
        return None
    try:
        return _bitmap_to_image(hbitmap)
    finally:
        ctypes.windll.gdi32.DeleteObject(hbitmap)


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _bitmap_to_image(hbitmap):
    """Copy an HBITMAP's pixels into a QImage, keeping its alpha. (QImage.fromHBITMAP drops alpha,
    which turns icons-as-thumbnails into icons on a black square.)"""
    gdi32 = ctypes.windll.gdi32
    bm = BITMAP()
    if not gdi32.GetObjectW(hbitmap, ctypes.sizeof(bm), ctypes.byref(bm)) or bm.bmWidth <= 0:
        return None
    w, h = bm.bmWidth, abs(bm.bmHeight)
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,  # top-down rows
                              biPlanes=1, biBitCount=32)
    buf = ctypes.create_string_buffer(w * h * 4)
    dc = user32.GetDC(None)
    try:
        lines = gdi32.GetDIBits(dc, hbitmap, 0, h, buf, ctypes.byref(header), 0)
    finally:
        user32.ReleaseDC(None, dc)
    if lines != h:
        return None
    # Photos come back with alpha 0 everywhere (meaning "no alpha"); icons carry real premultiplied alpha.
    has_alpha = any(buf.raw[i] for i in range(3, len(buf.raw), 4 * max(1, (w * h) // 512)))
    fmt = QImage.Format_ARGB32_Premultiplied if has_alpha else QImage.Format_RGB32
    return QImage(buf.raw, w, h, w * 4, fmt).copy()  # copy: the buffer goes away when we return


# ---------------------------------------------------------------- snapping

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
user32.MonitorFromRect.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]

WM_SIZING, WM_MOVING, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE = 0x0214, 0x0216, 0x0231, 0x0232

def work_area(rect):
    """Work area (screen minus taskbar) of the monitor the rect is mostly on, in physical pixels."""
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromRect(ctypes.byref(rect), 2), ctypes.byref(info))  # NEAREST
    return info.rcWork


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt


# ---------------------------------------------------------------- GPU adapters (DXGI)

class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC(ctypes.Structure):
    _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT), ("DeviceId", wintypes.UINT),
                ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID)]


IID_IDXGIFactory1 = guid(0x770AAE78, 0xF26F, 0x4DBA, 0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87)


def gpu_adapters():
    """[(name, dedicated VRAM bytes, PDH luid key)] for each graphics adapter."""
    factory = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory)):
        return []
    enum_adapters = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p))(vtable(factory)[7])
    adapters = []
    i = 0
    while True:
        adapter = ctypes.c_void_p()
        if enum_adapters(factory, i, ctypes.byref(adapter)):  # DXGI_ERROR_NOT_FOUND ends the list
            break
        desc = DXGI_ADAPTER_DESC()
        get_desc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                      ctypes.POINTER(DXGI_ADAPTER_DESC))(vtable(adapter)[8])
        if get_desc(adapter, ctypes.byref(desc)) == 0:
            luid = f"luid_0x{desc.AdapterLuid.HighPart & 0xFFFFFFFF:08x}_0x{desc.AdapterLuid.LowPart:08x}"
            adapters.append((desc.Description, desc.DedicatedVideoMemory, luid))
        com_release(adapter)
        i += 1
    com_release(factory)
    return adapters


# ---------------------------------------------------------------- desktop double-click

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                ("hwndTarget", HWND)]


WM_INPUT = 0x00FF
RAW_HEADER_SIZE = 24  # sizeof(RAWINPUTHEADER) on 64-bit


class DesktopDoubleClick(QWidget):
    """Calls callback when the user double-clicks an empty spot on the desktop.

    Listens with raw input (RIDEV_INPUTSINK) rather than a low-level mouse hook, so a busy
    app can never stall the system's mouse.
    """

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.last_click = (0.0, 0, 0)
        self.buf = ctypes.create_string_buffer(64)
        dev = RAWINPUTDEVICE(1, 2, 0x100, int(self.winId()))  # generic desktop / mouse, RIDEV_INPUTSINK
        self.ok = bool(user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(dev)))

    def nativeEvent(self, event_type, message):
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_INPUT:
            size = wintypes.UINT(len(self.buf))
            got = user32.GetRawInputData(msg.lParam, 0x10000003, self.buf, ctypes.byref(size), RAW_HEADER_SIZE)
            # RAWINPUTHEADER.dwType == RIM_TYPEMOUSE and RAWMOUSE.usButtonFlags has LEFT_BUTTON_DOWN
            if got != 0xFFFFFFFF and self.buf.raw[0] == 0 and self.buf.raw[28] & 0x1:
                self.on_left_down()
        return False, 0

    def on_left_down(self):
        pt = cursor_pos()
        now = time.monotonic()
        then, x, y = self.last_click
        self.last_click = (now, pt.x, pt.y)
        if (now - then > user32.GetDoubleClickTime() / 1000
                or abs(pt.x - x) > user32.GetSystemMetrics(36) or abs(pt.y - y) > user32.GetSystemMetrics(37)):
            return
        self.last_click = (0.0, 0, 0)  # a third click starts over rather than counting as another double
        if is_desktop_window(user32.WindowFromPoint(pt)):
            QTimer.singleShot(150, self.confirm)

    def confirm(self):
        # Double-clicking an icon selects (and opens) it; double-clicking empty space clears the selection.
        lv = desktop_listview()
        if lv and user32.IsWindowVisible(lv) and listview_selected_count(lv):
            return
        self.callback()
