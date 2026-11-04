"""Quick search: a global hotkey opens a search box over everything. It finds fence items, desktop
items and Start-menu apps instantly, files from the Windows search index in the background, and
offers a web search as the last resort."""
import os
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QFileInfo, QObject, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QFileIconProvider, QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from .common import DESKTOP, PUBLIC_DESKTOP, display_name, is_hidden
from .dialogs import bring_to_front
from .system import IS_WINDOWS, open_path, run_output, shell_icon, show_in_folder, user32

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
if IS_WINDOWS:
    START_MENUS = [Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                   Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu"
                   / "Programs"]
else:
    START_MENUS = [Path.home() / ".local/share/applications", Path("/usr/share/applications"),
                   Path("/var/lib/flatpak/exports/share/applications"), Path("/var/lib/snapd/desktop/applications")]
APP_FILES = (".lnk", ".url") if IS_WINDOWS else (".desktop",)
ROLE_SUB, ROLE_ACTION = Qt.UserRole + 1, Qt.UserRole + 2


def hotkey_to_win32(text):
    """'Ctrl+Shift+Space' -> (modifiers, virtual key), or None if Windows can't register it."""
    seq = QKeySequence(text)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        vk = int(key)  # same as ASCII
    elif key == Qt.Key_Space:
        vk = 0x20
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        vk = 0x70 + (key - Qt.Key_F1)
    else:
        return None
    win_mods = ((MOD_CONTROL if mods & Qt.ControlModifier else 0) | (MOD_SHIFT if mods & Qt.ShiftModifier else 0)
                | (MOD_ALT if mods & Qt.AltModifier else 0) | (MOD_WIN if mods & Qt.MetaModifier else 0))
    if not win_mods:
        return None  # a bare key would swallow normal typing everywhere
    return win_mods | MOD_NOREPEAT, vk


class HotkeyListener(QWidget):
    """Hidden window that owns the global hotkey."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = False
        self.winId()

    def register(self, text):
        self.unregister()
        parsed = hotkey_to_win32(text)
        if parsed:
            self.registered = bool(user32.RegisterHotKey(int(self.winId()), 1, *parsed))
        return self.registered

    def unregister(self):
        if self.registered:
            user32.UnregisterHotKey(int(self.winId()), 1)
            self.registered = False

    def nativeEvent(self, event_type, message):
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            QTimer.singleShot(0, self.callback)
        return False, 0


if not IS_WINDOWS:  # see the note at the end of panel.py
    del HotkeyListener.nativeEvent


class IndexSearcher(QObject):
    """Queries the Windows search index on a worker thread (the first query can take seconds)."""
    results = Signal(int, list)

    def __init__(self):
        super().__init__()
        self.conn = None

    def search(self, seq, text):
        if not IS_WINDOWS:  # locate's index, where it's installed; only files in your home folder
            lines = run_output("locate", "-i", "-l", "40", "--", text).splitlines()
            home = str(Path.home())
            self.results.emit(seq, [p for p in lines if p.startswith(home + "/") and "/." not in p[len(home):]][:12])
            return
        try:
            import pythoncom
            import win32com.client
            if self.conn is None:
                pythoncom.CoInitialize()
                self.conn = win32com.client.Dispatch("ADODB.Connection")
                self.conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
            like = text.replace("'", "''").replace("%", "").replace("[", "")
            scope = Path.home().as_posix()
            rs, _ = self.conn.Execute(
                f"SELECT TOP 12 System.ItemPathDisplay FROM SystemIndex WHERE SCOPE='file:{scope}' "
                f"AND System.ItemNameDisplay LIKE '%{like}%' ORDER BY System.DateModified DESC")
            paths = []
            while not rs.EOF:
                paths.append(rs.Fields.Item(0).Value)
                rs.MoveNext()
            self.results.emit(seq, paths)
        except Exception:  # Windows Search off, or a query it didn't like: just no index results
            self.results.emit(seq, [])


def score(name, query):
    name, query = name.lower(), query.lower()
    if name == query:
        return 100
    if name.startswith(query):
        return 80
    if any(w.startswith(query) for w in name.replace("-", " ").replace("_", " ").split()):
        return 60
    if query in name:
        return 40
    it = iter(name)
    return 15 if all(ch in it for ch in query) else 0  # letters in order, e.g. "vrop" -> "Vareoprettelse"


class ResultDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        p.save()
        selected = option.state & QStyle.State_Selected
        if selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(110, 165, 255, 90))
            p.setRenderHint(QPainter.Antialiasing)
            p.drawRoundedRect(option.rect.adjusted(2, 1, -2, -1), 6, 6)
        r = option.rect
        icon = index.data(Qt.DecorationRole)
        if icon:
            icon.paint(p, QRect(r.left() + 10, r.top() + (r.height() - 28) // 2, 28, 28))
        title = QFont(option.font)
        title.setPointSizeF(10.5)
        p.setFont(title)
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QRect(r.left() + 48, r.top() + 5, r.width() - 56, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   index.data(Qt.DisplayRole))
        sub = QFont(option.font)
        sub.setPointSizeF(8.5)
        p.setFont(sub)
        p.setPen(QColor(255, 255, 255, 130))
        p.drawText(QRect(r.left() + 48, r.top() + 24, r.width() - 56, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   index.data(ROLE_SUB) or "")
        p.restore()

    def sizeHint(self, option, index):
        return QSize(0, 46)


class SearchPopup(QWidget):
    search_index = Signal(int, str)

    def __init__(self, app):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(620, 440)
        self.icons = {}
        self.catalog = []
        self.seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Search apps, fences and files…")
        f = self.input.font()
        f.setPointSizeF(15)
        self.input.setFont(f)
        self.input.setStyleSheet("QLineEdit { background: rgba(255,255,255,20); color: white; border: none; "
                                 "border-radius: 8px; padding: 8px 12px; }")
        self.input.textChanged.connect(self.update_results)
        self.input.installEventFilter(self)
        self.list = QListWidget()
        self.list.setItemDelegate(ResultDelegate())
        self.list.setStyleSheet("QListWidget { background: transparent; border: none; outline: 0; }")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(self.activate)
        layout.addWidget(self.input)
        layout.addWidget(self.list)

        self.index_timer = QTimer(self, singleShot=True, interval=220, timeout=self.query_index)
        self.thread = QThread(self)
        self.searcher = IndexSearcher()
        self.searcher.moveToThread(self.thread)
        self.search_index.connect(self.searcher.search)
        self.searcher.results.connect(self.add_index_results)
        self.thread.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(255, 255, 255, 45))
        p.setBrush(QColor(22, 24, 32, 242))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 14, 14)

    # -- opening

    def open(self):
        self.catalog = self.build_catalog()
        screen = (QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()).availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.top() + screen.height() // 4)
        self.input.clear()
        self.update_results("")
        bring_to_front(self)
        self.input.setFocus()

    def build_catalog(self):
        """Everything searchable without the index: (name, subtitle, path or action)."""
        items, seen = [], set()

        def add(path, where):
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                items.append((display_name(path, 80), where, str(path)))

        for fence in self.app.folder_fences():
            if fence.folder.is_dir():
                for p in fence.folder.iterdir():
                    if not is_hidden(p):
                        add(p, f"In {fence.name}")
        for folder in (DESKTOP, PUBLIC_DESKTOP):
            if folder.is_dir():
                for p in folder.iterdir():
                    if not is_hidden(p):
                        add(p, "Desktop")
        for root in START_MENUS:
            if root.is_dir():
                for p in root.rglob("*"):
                    if p.suffix.lower() in APP_FILES and "uninstall" not in p.name.lower():
                        add(p, "App")
        for panel in self.app.all_panels():
            items.append((panel.name, "Fence / widget - show it", ("panel", panel)))
        return items

    # -- results

    def update_results(self, text):
        self.seq += 1
        self.list.clear()
        text = text.strip()
        if not text:
            self.index_timer.stop()
            return
        ranked = sorted(((score(name, text), name, sub, target) for name, sub, target in self.catalog),
                        key=lambda r: (-r[0], len(r[1])))
        for s, name, sub, target in ranked[:9]:
            if s:
                self.add_item(name, sub, target)
        self.web_item = self.add_item(f"Search the web for “{text}”", "Opens your browser", ("web", text))
        self.list.setCurrentRow(0)
        self.index_timer.start()

    def add_item(self, name, sub, target, row=None):
        item = QListWidgetItem(name)
        item.setData(ROLE_SUB, sub)
        item.setData(ROLE_ACTION, target)
        item.setIcon(self.icon_for(target))
        if row is None:
            self.list.addItem(item)
        else:
            self.list.insertItem(row, item)
        return item

    def icon_for(self, target):
        if isinstance(target, str):
            if target not in self.icons:
                self.icons[target] = shell_icon(target) or QFileIconProvider().icon(QFileInfo(target))
            return self.icons[target]
        style = self.style()
        return style.standardIcon(QStyle.SP_DesktopIcon if target[0] == "panel" else QStyle.SP_FileDialogContentsView)

    def query_index(self):
        if len(self.input.text().strip()) >= 2:
            self.search_index.emit(self.seq, self.input.text().strip())

    def add_index_results(self, seq, paths):
        if seq != self.seq:
            return  # results for an older query
        shown = {str(self.list.item(i).data(ROLE_ACTION)).lower() for i in range(self.list.count())}
        row = self.list.row(self.web_item)
        for path in paths:
            if path.lower() not in shown:
                self.add_item(Path(path).name, str(Path(path).parent), path, row)
                row += 1

    def activate(self, item=None):
        item = item or self.list.currentItem()
        if item is None:
            return
        target = item.data(ROLE_ACTION)
        self.hide()
        if isinstance(target, str):
            open_path(target)
        elif target[0] == "web":
            open_path("https://www.google.com/search?q=" + urllib.parse.quote(target[1]))
        elif target[0] == "panel":
            self.app.reveal_panel(target[1])

    def show_in_folder(self):
        item = self.list.currentItem()
        target = item.data(ROLE_ACTION) if item else None
        if isinstance(target, str):
            self.hide()
            show_in_folder(target)

    # -- keyboard / focus

    def eventFilter(self, obj, e):
        if obj is self.input and e.type() == e.Type.KeyPress:
            key = e.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow() + (1 if key == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.show_in_folder() if e.modifiers() & Qt.ControlModifier else self.activate()
                return True
            if key == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow():
            self.hide()  # clicked somewhere else
        super().changeEvent(e)

    def shutdown(self):
        self.thread.quit()
        self.thread.wait(2000)
