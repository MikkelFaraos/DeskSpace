"""Shared constants, paths and file helpers."""
import ctypes
import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

APP_NAME = "DeskSpace"
OLD_NAME = "DeskFences"  # the app's name before 1.1: its data folder and startup entry get moved over
APP_VERSION = "1.2.1"  # keep in step with build\installer.iss
APP_DIR = Path(__file__).resolve().parent.parent
ENTRY_SCRIPT = APP_DIR / "deskspace.pyw"


def _desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return Path(buf.value)


DESKTOP = _desktop_dir()
PUBLIC_DESKTOP = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
OLD_DATA_ROOT = Path.home() / OLD_NAME


def _data_root():
    """%USERPROFILE%\\DeskSpace. deskspace.pyw moves the old DeskFences folder over at start-up; if it
    couldn't (a file in it was open somewhere), keep using the old folder until it can."""
    new = Path.home() / APP_NAME
    return OLD_DATA_ROOT if not new.exists() and OLD_DATA_ROOT.is_dir() else new


DATA_ROOT = _data_root()


def moved_path(path):
    """A path saved under the old data folder, pointed at where it lives now."""
    path = Path(path)
    if DATA_ROOT != OLD_DATA_ROOT:
        try:
            return DATA_ROOT / path.relative_to(OLD_DATA_ROOT)
        except ValueError:
            pass
    return path


# Kept beside the fence folders rather than in %APPDATA%: when launched from a packaged (MSIX)
# app, AppData writes are silently redirected into that app's private storage.
CONFIG_PATH = DATA_ROOT / "config.json"
LEGACY_CONFIG_PATH = Path(os.environ["APPDATA"]) / OLD_NAME / "config.json"  # from the very first versions
COVER_CACHE = DATA_ROOT / ".covers"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_TITLE_SIZE = 10  # points
TITLE_SIZES = [("Small", 8), ("Medium", 10), ("Large", 13), ("Extra large", 16), ("Huge", 20)]
EDGE = 6
DEFAULT_COLOR = [22, 24, 32, 150]
ACCENT = QColor(110, 165, 255)

# Order matters: it is also the order auto-sort lays out new fences.
CATEGORIES = [
    ("Apps", {".lnk", ".url", ".exe", ".appref-ms", ".msi", ".bat", ".cmd", ".ps1"}),
    ("Folders", set()),
    ("Documents", {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".xls", ".xlsx", ".csv",
                   ".ods", ".ppt", ".pptx", ".odp", ".pages", ".numbers", ".key", ".epub"}),
    ("Images", {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".heic", ".ico", ".tif",
                ".tiff", ".psd", ".raw"}),
    ("Media", {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"}),
    ("Archives", {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"}),
    ("Other", set()),
]

SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 70); border-radius: 3px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { height: 0; background: none; }
"""

LIST_STYLE = """
QListWidget { background: transparent; color: white; border: none; outline: 0; }
QListWidget::item { border-radius: 6px; padding: 2px; }
QListWidget::item:hover { background: rgba(255, 255, 255, 40); }
QListWidget::item:selected { background: rgba(110, 165, 255, 95); }
""" + SCROLLBAR_STYLE

EDIT_STYLE = """
QPlainTextEdit, QLineEdit { background: transparent; color: white; border: none;
                            selection-background-color: rgba(110, 165, 255, 120); }
QLineEdit { background: rgba(255, 255, 255, 22); border-radius: 5px; padding: 4px 6px; }
""" + SCROLLBAR_STYLE


def category_for(path):
    if path.is_dir():
        return "Folders"
    ext = path.suffix.lower()
    for name, exts in CATEGORIES:
        if ext in exts:
            return name
    return "Other"


def is_hidden(path):
    attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    return attrs != -1 and bool(attrs & 0x6)  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def safe_name(name):
    cleaned = "".join("_" if c in '<>:"/\\|?*' else c for c in name).strip(" .")
    return cleaned or "Fence"


def unique_path(folder, name, is_dir):
    dest = folder / name
    if not dest.exists():
        return dest
    stem, suffix = (name, "") if is_dir else (Path(name).stem, Path(name).suffix)
    i = 2
    while (folder / f"{stem} ({i}){suffix}").exists():
        i += 1
    return folder / f"{stem} ({i}){suffix}"


def move_into(src, folder):
    """Move src into folder. Returns True on success, False on failure, None if skipped."""
    src, folder = Path(src), Path(folder)
    if not src.exists() or src.parent.resolve() == folder.resolve():
        return None
    if src.is_dir() and folder.resolve().is_relative_to(src.resolve()):
        return None
    try:
        shutil.move(str(src), str(unique_path(folder, src.name, src.is_dir())))
        return True
    except OSError:
        return False


def display_name(path, limit=28):
    name = path.stem if path.suffix.lower() in (".lnk", ".url") else path.name
    return name if len(name) <= limit else name[:limit - 2] + "…"


def local_paths(mime):
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


def make_app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(52, 120, 220))
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setBrush(QColor(255, 255, 255, 235))
    for x, y in ((14, 14), (36, 14), (14, 36), (36, 36)):
        p.drawRoundedRect(x, y, 14, 14, 3, 3)
    p.end()
    return QIcon(pm)


def load_color(fraction):
    """Bar colour for a 0..1 load: blue, then amber, then red."""
    if fraction < 0.6:
        return ACCENT
    return QColor(245, 180, 60) if fraction < 0.85 else QColor(240, 90, 80)
