"""Disk clean-up window, opened from the System widget.

Shows what's taking space in a few safe-to-clean places, lets you choose, and always asks first.
Old downloads go to the Recycle Bin (recoverable); the other categories are deleted for good.
"""
import ctypes
import os
import shutil
import tempfile
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from . import dialogs
from .common import APP_NAME, make_app_icon
from .dialogs import bring_to_front
from .sysinfo import fmt_bytes
from .system import IS_WINDOWS, ROOT_DISK, open_path

DAY = 86400
TEMP = Path(tempfile.gettempdir())
LOCAL = Path(os.environ.get("LOCALAPPDATA", Path.home()))
LINUX_TRASH = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"
DOWNLOADS = Path.home() / "Downloads"
CRASH_DIRS = ([LOCAL / "CrashDumps", LOCAL / "Microsoft" / "Windows" / "WER" / "ReportArchive",
               LOCAL / "Microsoft" / "Windows" / "WER" / "ReportQueue"] if IS_WINDOWS
              else [Path.home() / ".local/share/apport/coredump", Path("/var/crash")])


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]


def recycle_bin():
    if not IS_WINDOWS:
        files = entries(LINUX_TRASH / "files")
        return len(files), sum(size_of(p) for p in files)
    info = SHQUERYRBINFO(cbSize=ctypes.sizeof(SHQUERYRBINFO))
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return info.i64NumItems, info.i64Size


def empty_recycle_bin():
    if IS_WINDOWS:
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)  # no confirm/progress/sound
        return
    for folder in ("files", "info"):
        for p in entries(LINUX_TRASH / folder):
            delete(p)


def size_of(path):
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for root, _, files in os.walk(path, onerror=lambda e: None):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def entries(folder, older_than=0):
    """Top-level items in a folder last changed more than `older_than` seconds ago."""
    if not folder.is_dir():
        return []
    cutoff = time.time() - older_than
    found = []
    for p in folder.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                found.append(p)
        except OSError:
            pass
    return found


def delete(path):
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except OSError:
        pass  # in use by a program right now: skip it


class Scanner(QObject):
    """Measures the categories on a worker thread (big folders take a moment)."""
    done = Signal(dict)

    def scan(self):
        def run():
            result = {"recycle": recycle_bin()}
            temp = entries(TEMP, DAY)
            result["temp"] = (temp, sum(size_of(p) for p in temp))
            crash = [p for d in CRASH_DIRS for p in entries(d)]
            result["crash"] = (crash, sum(size_of(p) for p in crash))
            old = sorted(((p, size_of(p)) for p in entries(DOWNLOADS, 30 * DAY)), key=lambda x: -x[1])
            result["downloads"] = old
            self.done.emit(result)
        threading.Thread(target=run, daemon=True).start()


class CleanupWindow(QWidget):
    finished_cleaning = Signal(int)

    def __init__(self):
        super().__init__(None, Qt.Window)
        self.setWindowTitle(f"{APP_NAME} - Clean up disk")
        self.setWindowIcon(make_app_icon())
        self.resize(560, 600)
        self.result = {}

        layout = QVBoxLayout(self)
        self.free = QLabel()
        f = self.free.font()
        f.setPointSizeF(11)
        f.setBold(True)
        self.free.setFont(f)
        layout.addWidget(self.free)

        self.checks = {}
        self.sizes = {}
        for key, title, note in (
                ("recycle", "Recycle Bin", "Empties the Recycle Bin. Permanent."),
                ("temp", "Temporary files", "Leftovers in your Temp folder older than a day. Files in use are skipped."),
                ("crash", "Crash dumps && error reports", "Saved after programs crash; only useful for debugging.")):
            box = QGroupBox()
            row = QHBoxLayout(box)
            check = QCheckBox(title)
            check.setChecked(True)
            check.toggled.connect(self.update_total)
            text = QVBoxLayout()
            text.addWidget(check)
            hint = QLabel(note)
            hint.setEnabled(False)
            hint.setWordWrap(True)
            text.addWidget(hint)
            row.addLayout(text, 1)
            size = QLabel("…")
            row.addWidget(size)
            self.checks[key], self.sizes[key] = check, size
            layout.addWidget(box)

        box = QGroupBox()
        col = QVBoxLayout(box)
        head = QHBoxLayout()
        self.checks["downloads"] = QCheckBox("Old downloads (not changed for 30+ days)")
        self.checks["downloads"].setChecked(False)  # your own files: opt in
        self.checks["downloads"].toggled.connect(self.update_total)
        head.addWidget(self.checks["downloads"], 1)
        self.sizes["downloads"] = QLabel("…")
        head.addWidget(self.sizes["downloads"])
        col.addLayout(head)
        hint = QLabel("Moved to the Recycle Bin, so you can still get them back. Untick anything you want to keep.")
        hint.setEnabled(False)
        hint.setWordWrap(True)
        col.addWidget(hint)
        self.download_list = QListWidget()
        self.download_list.itemChanged.connect(self.update_total)
        col.addWidget(self.download_list)
        layout.addWidget(box, 1)

        buttons = QHBoxLayout()
        if IS_WINDOWS:
            storage = QPushButton("Windows storage settings…")
            storage.clicked.connect(lambda: open_path("ms-settings:storagesense"))
            buttons.addWidget(storage)
        buttons.addStretch(1)
        self.clean_button = QPushButton("Clean up")
        self.clean_button.clicked.connect(self.clean)
        buttons.addWidget(self.clean_button)
        layout.addLayout(buttons)

        self.scanner = Scanner()
        self.scanner.done.connect(self.show_result)
        self.finished_cleaning.connect(self.on_cleaned)

    def open(self):
        bring_to_front(self)
        self.rescan()

    def rescan(self):
        self.clean_button.setEnabled(False)
        self.clean_button.setText("Measuring…")
        for label in self.sizes.values():
            label.setText("…")
        disk = psutil.disk_usage(ROOT_DISK)
        self.free.setText(f"{'Drive C:' if IS_WINDOWS else 'Disk:'} {fmt_bytes(disk.free)} free of "
                          f"{fmt_bytes(disk.total)}")
        self.scanner.scan()

    def show_result(self, result):
        self.result = result
        count, size = result["recycle"]
        self.sizes["recycle"].setText(f"{fmt_bytes(size)}  ({count} items)")
        for key in ("temp", "crash"):
            items, size = result[key]
            self.sizes[key].setText(f"{fmt_bytes(size)}  ({len(items)} items)")
        self.download_list.blockSignals(True)
        self.download_list.clear()
        for path, size in result["downloads"]:
            when = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %Y") if path.exists() else ""
            item = QListWidgetItem(f"{path.name}    {fmt_bytes(size)}  ·  {when}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, (str(path), size))
            self.download_list.addItem(item)
        self.download_list.blockSignals(False)
        self.update_total()

    def chosen_downloads(self):
        if not self.checks["downloads"].isChecked():
            return []
        return [self.download_list.item(i).data(Qt.UserRole) for i in range(self.download_list.count())
                if self.download_list.item(i).checkState() == Qt.Checked]

    def total(self):
        if not self.result:
            return 0
        t = self.result["recycle"][1] if self.checks["recycle"].isChecked() else 0
        for key in ("temp", "crash"):
            if self.checks[key].isChecked():
                t += self.result[key][1]
        return t + sum(size for _, size in self.chosen_downloads())

    def update_total(self, *_):
        downloads = sum(size for _, size in self.result.get("downloads", []))
        self.sizes["downloads"].setText(f"{fmt_bytes(downloads)}  ({len(self.result.get('downloads', []))} items)"
                                        if self.result else "…")
        self.download_list.setEnabled(self.checks["downloads"].isChecked())
        total = self.total()
        self.clean_button.setEnabled(total > 0)
        self.clean_button.setText(f"Clean up {fmt_bytes(total)}" if self.result else "Measuring…")

    def clean(self):
        chosen = [title for key, title in (("recycle", "empty the Recycle Bin"), ("temp", "delete old temporary files"),
                                           ("crash", "delete crash dumps and error reports"))
                  if self.checks[key].isChecked()]
        downloads = self.chosen_downloads()
        if downloads:
            chosen.append(f"move {len(downloads)} old download(s) to the Recycle Bin")
        if not chosen or not dialogs.ask("This will:\n\n• " + "\n• ".join(chosen) +
                                         f"\n\nAbout {fmt_bytes(self.total())} in total. Continue?"):
            return
        before = psutil.disk_usage(ROOT_DISK).free
        self.clean_button.setEnabled(False)
        self.clean_button.setText("Cleaning…")
        plan = {key: self.checks[key].isChecked() for key in ("recycle", "temp", "crash")}
        temp, crash = self.result["temp"][0], self.result["crash"][0]

        def run():
            # Empty the Recycle Bin *before* old downloads go into it, so they stay recoverable.
            if plan["recycle"]:
                empty_recycle_bin()
            if plan["temp"]:
                for p in temp:
                    delete(p)
            if plan["crash"]:
                for p in crash:
                    delete(p)
            if downloads:
                from send2trash import send2trash
                for path, _ in downloads:
                    try:
                        send2trash(path)
                    except OSError:
                        pass
            self.finished_cleaning.emit(psutil.disk_usage(ROOT_DISK).free - before)
        threading.Thread(target=run, daemon=True).start()

    def on_cleaned(self, freed):
        self.rescan()
        dialogs.tell(f"Done - freed about {fmt_bytes(max(freed, 0))}.")
