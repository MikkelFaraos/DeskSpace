"""File auto-tidy: sorts Downloads into folders by type, files old screenshots into month folders, and
finds duplicate downloads.

Nothing is deleted: files are only moved within their own folder (every move is logged, so the
last tidy can be undone), and the extra copies of duplicates go to the Recycle Bin. Opened from the
tray or Settings; sorting and screenshots can also run on their own once a day.
"""
import hashlib
import json
import os
import shutil
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from . import dialogs
from .common import APP_NAME, CATEGORIES, DATA_ROOT, is_hidden, make_app_icon, unique_path
from .dialogs import bring_to_front
from .sysinfo import fmt_bytes

DAY = 86400
DOWNLOADS = Path.home() / "Downloads"
LOG_PATH = DATA_ROOT / "tidy-log.json"
IN_PROGRESS = (".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload")
# Downloads folders by type. Installers get their own: they're the thing you most often look for again.
FOLDERS = {"Apps": "Installers", "Documents": "Documents", "Images": "Images", "Media": "Videos & music",
           "Archives": "Archives"}
SORT_FOLDERS = set(FOLDERS.values())


def screenshots_dir():
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
    # FOLDERID_Screenshots {b7bede81-df94-4682-a7d8-57a52620b86f}
    gid = GUID(0xb7bede81, 0xdf94, 0x4682, (ctypes.c_ubyte * 8)(0xa7, 0xd8, 0x57, 0xa5, 0x26, 0x20, 0xb8, 0x6f))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(gid), 0, None, ctypes.byref(out)) == 0:
        path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    return Path.home() / "Pictures" / "Screenshots"


SCREENSHOTS = screenshots_dir()


def plural(n, word, words=None):
    return f"{n} {word if n == 1 else words or word + 's'}"


def folder_for(path):
    ext = path.suffix.lower()
    for category, exts in CATEGORIES:
        if ext in exts and category in FOLDERS:
            return FOLDERS[category]
    return None  # unknown types stay where they are


def plan_sort(min_age_days):
    """(source, destination) moves that sort loose files in Downloads into type folders."""
    if not DOWNLOADS.is_dir():
        return []
    cutoff = time.time() - min_age_days * DAY
    moves = []
    for p in DOWNLOADS.iterdir():
        try:
            if (not p.is_file() or is_hidden(p) or p.name.lower().endswith(IN_PROGRESS) or p.name.startswith("~$")
                    or p.stat().st_mtime > cutoff):
                continue
        except OSError:
            continue
        folder = folder_for(p)
        if folder:
            moves.append((p, DOWNLOADS / folder / p.name))
    return moves


def plan_screenshots():
    """Screenshots from before this month into "2026-09"-style folders; this month's stay at hand."""
    if not SCREENSHOTS.is_dir():
        return []
    this_month = datetime.now().strftime("%Y-%m")
    moves = []
    for p in SCREENSHOTS.iterdir():
        try:
            if not p.is_file() or is_hidden(p):
                continue
            month = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m")
        except OSError:
            continue
        if month != this_month:
            moves.append((p, SCREENSHOTS / month / p.name))
    return moves


def file_hash(path):
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def find_duplicates():
    """Groups of identical files in Downloads (including its type folders): [(keep, [extra copies])].
    The copy kept is the one with the plainest name - "setup.exe" rather than "setup (1).exe"."""
    by_size = defaultdict(list)
    if not DOWNLOADS.is_dir():
        return []
    for root, _, files in os.walk(DOWNLOADS):
        for name in files:
            p = Path(root) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size >= 1024 and not name.lower().endswith(IN_PROGRESS) and not is_hidden(p):
                by_size[size].append(p)
    groups = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_hash = defaultdict(list)
        for p in same_size:
            try:
                by_hash[file_hash(p)].append(p)
            except OSError:
                pass
        for copies in by_hash.values():
            if len(copies) > 1:
                copies.sort(key=lambda p: (" (" in p.stem, len(p.name), p.stat().st_mtime))
                groups.append((copies[0], copies[1:]))
    return groups


def do_moves(moves):
    """Carry out the moves; returns the ones that happened, with their final names."""
    done = []
    for src, dst in moves:
        try:
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = unique_path(dst.parent, dst.name, False)
            shutil.move(str(src), str(dst))
            done.append((src, dst))
        except OSError:
            pass  # open in another program right now: next time
    return done


def save_log(done):
    if done:
        LOG_PATH.write_text(json.dumps({"when": datetime.now().isoformat(timespec="seconds"),
                                        "moves": [[str(s), str(d)] for s, d in done]}, indent=1), encoding="utf-8")


def load_log():
    try:
        return json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def undo_last():
    """Move everything from the last tidy back; returns how many files went back."""
    log = load_log()
    if not log:
        return 0
    back = 0
    for src, dst in reversed(log["moves"]):
        src, dst = Path(src), Path(dst)
        if dst.exists() and not src.exists():
            try:
                shutil.move(str(dst), str(src))
                back += 1
                if dst.parent != src.parent and not any(dst.parent.iterdir()):
                    dst.parent.rmdir()  # a folder the tidy made, now empty again
            except OSError:
                pass
    LOG_PATH.unlink(missing_ok=True)
    return back


def auto_tidy(min_age_days):
    """The daily run: sorting and screenshots only (duplicates always wait for you to look)."""
    done = do_moves(plan_sort(min_age_days) + plan_screenshots())
    save_log(done)
    return len(done)


class Scanner(QObject):
    done = Signal(dict)

    def scan(self, min_age_days):
        def run():
            self.done.emit({"sort": plan_sort(min_age_days), "shots": plan_screenshots(),
                            "dupes": find_duplicates()})
        threading.Thread(target=run, daemon=True).start()


class TidyWindow(QWidget):
    finished = Signal(int, int)

    def __init__(self, app):
        super().__init__(None, Qt.Window)
        self.app = app
        self.setWindowTitle(f"{APP_NAME} - Tidy files")
        self.setWindowIcon(make_app_icon())
        self.resize(620, 700)
        self.result = {}
        layout = QVBoxLayout(self)

        self.sections = {}
        for key, title, note, checked in (
                ("sort", "Sort Downloads into folders",
                 "Moves loose files into Installers, Documents, Images, Videos & music and Archives inside "
                 "Downloads. Files from the last {days} days and unknown types stay put.", True),
                ("shots", "File screenshots by month",
                 f"Screenshots from before this month go into month folders in {SCREENSHOTS}.", True),
                ("dupes", "Remove duplicate downloads",
                 "Identical copies (like \"setup (1).exe\") go to the Recycle Bin; one copy of each is kept.", False)):
            box = QGroupBox()
            col = QVBoxLayout(box)
            head = QHBoxLayout()
            check = QCheckBox(title)
            check.setChecked(checked)
            check.toggled.connect(self.update_button)
            head.addWidget(check, 1)
            count = QLabel("…")
            head.addWidget(count)
            col.addLayout(head)
            hint = QLabel(note)
            hint.setEnabled(False)
            hint.setWordWrap(True)
            col.addWidget(hint)
            items = QListWidget()
            items.setMaximumHeight(150)
            items.itemChanged.connect(self.update_button)
            col.addWidget(items)
            layout.addWidget(box)
            self.sections[key] = (check, count, items, hint, note)

        buttons = QHBoxLayout()
        self.undo = QPushButton("Undo last tidy")
        self.undo.clicked.connect(self.undo_last)
        buttons.addWidget(self.undo)
        buttons.addStretch(1)
        self.go = QPushButton("Tidy up")
        self.go.clicked.connect(self.tidy)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)

        self.scanner = Scanner()
        self.scanner.done.connect(self.show_result)
        self.finished.connect(self.on_finished)

    def open(self):
        bring_to_front(self)
        self.rescan()

    def rescan(self):
        days = self.app.tidy_days
        hint, note = self.sections["sort"][3], self.sections["sort"][4]
        hint.setText(note.replace("{days}", str(days)))
        self.go.setEnabled(False)
        self.go.setText("Looking…")
        for _, count, items, _, _ in self.sections.values():
            count.setText("…")
            items.clear()
        log = load_log()
        self.undo.setEnabled(bool(log))
        self.undo.setToolTip(f"Moves the {len(log['moves'])} files from the tidy on {log['when'].replace('T', ' ')} "
                             "back where they were." if log else "")
        self.scanner.scan(days)

    def show_result(self, result):
        self.result = result
        for key in ("sort", "shots"):
            _, count, items, _, _ = self.sections[key]
            items.blockSignals(True)
            for src, dst in result[key]:
                item = QListWidgetItem(f"{src.name}   →   {dst.parent.name}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, (src, dst))
                items.addItem(item)
            items.blockSignals(False)
            count.setText(plural(len(result[key]), "file"))
        _, count, items, _, _ = self.sections["dupes"]
        items.blockSignals(True)
        wasted = 0
        for keep, extras in result["dupes"]:
            for extra in extras:
                size = extra.stat().st_size if extra.exists() else 0
                wasted += size
                item = QListWidgetItem(f"{extra.relative_to(DOWNLOADS)}   (same as {keep.name}, {fmt_bytes(size)})")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                item.setData(Qt.UserRole, extra)
                items.addItem(item)
        items.blockSignals(False)
        count.setText(f"{plural(items.count(), 'copy', 'copies')}, {fmt_bytes(wasted)}")
        self.update_button()

    def chosen(self, key):
        check, _, items, _, _ = self.sections[key]
        items.setEnabled(check.isChecked())
        if not check.isChecked():
            return []
        return [items.item(i).data(Qt.UserRole) for i in range(items.count())
                if items.item(i).checkState() == Qt.Checked]

    def update_button(self, *_):
        if not self.result:
            return
        n = sum(len(self.chosen(k)) for k in self.sections)
        self.go.setEnabled(n > 0)
        self.go.setText(f"Tidy up {plural(n, 'file')}" if n else "Nothing to tidy")

    def tidy(self):
        trash = self.chosen("dupes")
        # A duplicate that's going to the Recycle Bin isn't also sorted into a folder first.
        moves = [m for m in self.chosen("sort") + self.chosen("shots") if m[0] not in trash]
        lines = []
        if moves:
            lines.append(f"move {len(moves)} files into folders (you can undo this)")
        if trash:
            lines.append(f"move {len(trash)} duplicate copies to the Recycle Bin")
        if not lines or not dialogs.ask("This will:\n\n• " + "\n• ".join(lines) + "\n\nContinue?"):
            return
        self.go.setEnabled(False)
        self.go.setText("Tidying…")

        def run():
            done = do_moves(moves)
            save_log(done)
            recycled = 0
            if trash:
                from send2trash import send2trash
                for path in trash:
                    try:
                        send2trash(str(path))
                        recycled += 1
                    except OSError:
                        pass
            self.finished.emit(len(done), recycled)
        threading.Thread(target=run, daemon=True).start()

    def on_finished(self, moved, recycled):
        self.rescan()
        dialogs.tell(f"Done - moved {plural(moved, 'file')}"
                     + (f" and recycled {plural(recycled, 'duplicate')}." if recycled else "."))

    def undo_last(self):
        log = load_log()
        if log and dialogs.ask(f"Put the {len(log['moves'])} files from the last tidy back where they were?"):
            back = undo_last()
            self.rescan()
            dialogs.tell(f"Moved {back} files back.")
