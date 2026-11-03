"""Icon fences: an owned fence keeps its items in its own folder under DeskSpace; a folder
portal shows any existing folder (like Downloads) without moving anything."""
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QFileSystemWatcher, QMimeData, QRect, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDrag, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
)

from . import dialogs, gog, steam
from .common import (
    DATA_ROOT, DESKTOP, LIST_STYLE, display_name, is_hidden, local_paths, move_into, moved_path, safe_name,
    unique_path,
)
from .panel import Panel
from .win32 import shell_icon

TILE = QSize(104, 156)  # 2:3, the shape of Steam's library covers


def button(text, slot):
    b = QPushButton(text)
    b.clicked.connect(slot)
    return b


class FenceList(QListWidget):
    def __init__(self, fence):
        super().__init__(fence)
        self.fence = fence
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideRight)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setFrameShape(QListWidget.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(LIST_STYLE)
        self.setMouseTracking(True)
        self.itemActivated.connect(lambda it: os.startfile(it.data(Qt.UserRole)))

    # -- hover previews (icon view only; game tiles already are pictures)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        item = self.itemAt(e.position().toPoint())
        preview = self.fence.app.preview
        if item is None or self.fence.view != "icons" or e.buttons():
            preview.cancel()
            return
        rect = self.visualItemRect(item)
        top_left = self.viewport().mapToGlobal(rect.topLeft())
        preview.hover(item.data(Qt.UserRole), QRect(top_left, rect.size()))

    def viewportEvent(self, e):
        if e.type() in (QEvent.Leave, QEvent.Wheel, QEvent.MouseButtonPress):
            self.fence.app.preview.cancel()
        return super().viewportEvent(e)

    def set_view(self, view):
        if view == "tiles":
            self.setIconSize(TILE)
            self.setGridSize(QSize(TILE.width() + 10, TILE.height() + 10))
        else:
            self.setIconSize(QSize(40, 40))
            self.setGridSize(QSize(88, 86))

    def selected_paths(self):
        return [it.data(Qt.UserRole) for it in self.selectedItems()]

    def startDrag(self, actions):
        paths = self.selected_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.currentItem().icon().pixmap(36, 36))
        drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)
        self.fence.schedule_refresh()

    def dragEnterEvent(self, e):
        self.fence.handle_drag_enter(e)

    def dragMoveEvent(self, e):
        self.fence.handle_drag_enter(e)

    def dragLeaveEvent(self, e):
        self.fence.set_drop_hover(False)

    def dropEvent(self, e):
        self.fence.handle_drop(e)

    def contextMenuEvent(self, e):
        item = self.itemAt(e.pos())
        menu = QMenu()
        if item:
            if not item.isSelected():
                self.clearSelection()
                item.setSelected(True)
            paths = self.selected_paths()
            menu.addAction("Open", lambda: [os.startfile(p) for p in paths])
            menu.addAction("Show in Explorer", lambda: subprocess.Popen(f'explorer /select,"{paths[0]}"'))
            menu.addAction("Move to desktop", lambda: self.fence.move_out(paths, DESKTOP))
            others = [f for f in self.fence.app.folder_fences() if f is not self.fence]
            if others:
                sub = menu.addMenu("Move to fence")
                for other in others:
                    sub.addAction(other.name, lambda o=other: self.fence.move_out(paths, o.folder, o))
            menu.addSeparator()
        self.fence.show_menu(e.globalPos(), menu)


def make_tile(name, dpr, cover=None, header=None, icon=None):
    """A 2:3 tile: the game's cover art, or its icon and name on a dark card."""
    pm = QPixmap(TILE * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    w, h = TILE.width(), TILE.height()
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
    p.setClipPath(clip)
    art = QPixmap(str(cover or header)) if (cover or header) else QPixmap()
    if cover and not art.isNull():
        scaled = art.scaled(TILE * dpr, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        p.drawPixmap(int((w - scaled.width() / dpr) / 2), int((h - scaled.height() / dpr) / 2), scaled)
    else:
        p.fillRect(0, 0, w, h, QColor(255, 255, 255, 28))
        text_top = h - 46
        if not art.isNull():  # landscape header: full width across the top
            scaled = art.scaledToWidth(int(w * dpr), Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            p.drawPixmap(0, 0, scaled)
            text_top = int(scaled.height() / dpr) + 8
        elif icon:
            p.drawPixmap(int((w - 64) / 2), 26, icon.pixmap(QSize(64, 64), dpr))
        font = QFont()
        font.setPointSizeF(9)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 230))
        p.drawText(QRectF(6, text_top, w - 12, h - text_top - 6), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, name)
    p.end()
    return pm


class FolderFence(Panel):
    kind = "folder"

    def build_content(self, data):
        self.folder = moved_path(data["folder"])  # saved before the data folder was renamed?
        self.portal = data.get("kind") == "portal"
        self.kind = "portal" if self.portal else "folder"
        self.view = data.get("view", "icons")
        self.rules = data.get("rules", [])
        self.count = 0
        if not self.portal:
            self.folder.mkdir(parents=True, exist_ok=True)
        self.setAcceptDrops(True)

        self.list = FenceList(self)
        self.list.set_view(self.view)
        self.refresh_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.schedule_refresh)
        self.refresh()
        return self.list

    def content_dict(self):
        return {"folder": str(self.folder), "view": self.view, "rules": self.rules}

    def rolled_title(self):
        return f"{self.name}  ({self.count})"

    def schedule_refresh(self, *_):
        self.refresh_timer.start()

    def refresh(self):
        if not self.portal:
            self.folder.mkdir(parents=True, exist_ok=True)
        if not self.folder.is_dir():  # a portal whose folder was deleted or is on an unplugged drive
            self.list.clear()
            self.count = 0
            self.update()
            return
        if str(self.folder) not in self.watcher.directories():
            self.watcher.addPath(str(self.folder))
        entries = sorted((p for p in self.folder.iterdir() if not is_hidden(p)),
                         key=lambda p: (not p.is_dir(), p.name.lower()))
        self.list.clear()
        dpr = self.devicePixelRatioF()
        for p in entries:
            item = QListWidgetItem()
            if self.view == "tiles":
                item.setIcon(QIcon(self.tile_for(p, dpr)))
            else:
                item.setIcon(shell_icon(p) or self.app.icon_provider.icon(QFileInfo(str(p))))
                item.setText(display_name(p))
            item.setData(Qt.UserRole, str(p))
            item.setToolTip(p.name)
            self.list.addItem(item)
        self.count = len(entries)
        self.update()

    def tile_for(self, path, dpr):
        name = display_name(path, limit=40)
        appid = steam.appid_from_shortcut(path)
        if appid:
            cover = steam.cover_file(appid)
            if cover:
                return make_tile(name, dpr, cover=cover)
            self.app.fetch_cover(appid)
            header = steam.header_file(appid)
            if header:
                return make_tile(name, dpr, header=header)
        game_id = gog.id_from_shortcut(path)
        if game_id:
            cover = gog.cover_file(game_id)
            if cover:
                return make_tile(name, dpr, cover=cover)
            self.app.fetch_gog_cover(game_id)
        icon = shell_icon(path, jumbo=True) or self.app.icon_provider.icon(QFileInfo(str(path)))
        return make_tile(name, dpr, icon=icon)

    # -- moving files

    def receive(self, paths):
        results = [move_into(p, self.folder) for p in paths]
        self.refresh()
        if False in results:
            self.app.warn("Some items could not be moved.\n\nShortcuts shared by all users "
                          "(Public Desktop) need admin rights, and open files can't be moved.")

    def move_out(self, paths, target, target_fence=None):
        results = [move_into(p, target) for p in paths]
        self.refresh()
        if target_fence:
            target_fence.refresh()
        if False in results:
            self.app.warn("Some items could not be moved (they may be open in another program).")

    # -- drag & drop

    def handle_drag_enter(self, e):
        if e.mimeData().hasUrls() and e.source() is not self.list and self.folder.is_dir():
            e.setDropAction(Qt.MoveAction)
            e.accept()
            self.set_drop_hover(True)
        else:
            e.ignore()

    def handle_drop(self, e):
        self.set_drop_hover(False)
        if e.source() is self.list:
            e.ignore()
            return
        paths = local_paths(e.mimeData())
        # We move the files ourselves; report "copy" so Explorer doesn't also try to delete the source.
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if isinstance(e.source(), FenceList):
            QTimer.singleShot(0, lambda: self.receive(paths))
        else:
            self.receive(paths)

    def set_drop_hover(self, on):
        if self.drop_hover != on:
            self.drop_hover = on
            self.update()
            if self.group is not None:  # as a tab, it's the group's border that's visible
                self.group.drop_hover = on
                self.group.update()

    def dragEnterEvent(self, e):
        self.handle_drag_enter(e)

    def dragMoveEvent(self, e):
        self.handle_drag_enter(e)

    def dragLeaveEvent(self, e):
        self.set_drop_hover(False)

    def dropEvent(self, e):
        self.handle_drop(e)

    # -- settings

    def build_settings(self, form):
        folder_row = QHBoxLayout()
        path = QLineEdit(str(self.folder))
        path.setReadOnly(True)
        folder_row.addWidget(path, 1)
        folder_row.addWidget(button("Open", lambda: os.startfile(self.folder)))
        if self.portal:
            def change():
                chosen = dialogs.get_folder("Choose a folder to show", self.folder)
                if chosen:
                    self.set_folder(chosen)
                    path.setText(chosen)
            folder_row.addWidget(button("Change…", change))
        form.addRow("Folder", folder_row)

        view = QComboBox()
        view.addItem("Icons", "icons")
        view.addItem("Game tiles (cover art)", "tiles")
        view.setCurrentIndex(view.findData(self.view))
        view.currentIndexChanged.connect(lambda: self.set_view(view.currentData()))
        form.addRow("View", view)

        rules = QLineEdit(", ".join(self.rules))
        rules.setPlaceholderText("e.g.  *.xlsx, *.docx, invoice*")
        rules.editingFinished.connect(lambda: self.set_rules(rules.text()))
        form.addRow("Auto-sort rules", rules)
        hint = QLabel("Files that land on the desktop and match these patterns move into this fence "
                      "automatically (including matching files already there). Leave empty to turn off.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        form.addRow("", hint)
        if steam.steam_root():
            form.addRow("Steam", button("Add installed Steam games", self.add_steam_games))
        form.addRow("GOG", button("Add installed GOG games", self.add_gog_games))

    def remove_label(self):
        return "Remove portal (files stay)" if self.portal else "Remove fence (items go back to desktop)"

    def set_view(self, view):
        self.view = view
        self.list.set_view(view)
        self.refresh()
        self.app.schedule_save()

    def add_steam_games(self):
        have = {steam.appid_from_shortcut(p) for p in self.folder.iterdir()} if self.folder.is_dir() else set()
        exe = steam.steam_exe()
        added = []
        for appid, name in steam.installed_games():
            if appid in have:
                continue
            path = unique_path(self.folder, safe_name(name) + ".url", False)
            path.write_text(f"[InternetShortcut]\nURL=steam://rungameid/{appid}\nIconIndex=0\nIconFile={exe}\n",
                            encoding="utf-8")
            added.append(name)
        self.refresh()
        if added:
            self.app.warn(f"Added {len(added)} game(s):\n\n" + "\n".join(added), info=True)
        else:
            self.app.warn("All your installed Steam games are already in this fence.", info=True)

    def add_gog_games(self):
        games = gog.installed_games()
        if not games:
            self.app.warn("No installed GOG games were found on this PC.\n\nGames installed with GOG Galaxy or "
                          "GOG's offline installers show up here. You can also drag a GOG game's shortcut into "
                          "the fence.", info=True)
            return
        have = {gog.id_from_shortcut(p) for p in self.folder.iterdir()} if self.folder.is_dir() else set()
        added = []
        for game in games:
            if game["id"] in have:
                continue
            gog.make_shortcut(unique_path(self.folder, safe_name(game["name"]) + ".lnk", False), game)
            added.append(game["name"])
        self.refresh()
        if added:
            self.app.warn(f"Added {len(added)} game(s):\n\n" + "\n".join(added), info=True)
        else:
            self.app.warn("All your installed GOG games are already in this fence.", info=True)

    def set_rules(self, text):
        rules = [r.strip() for r in text.replace(";", ",").split(",") if r.strip()]
        if rules == self.rules:
            return
        self.rules = rules
        self.app.schedule_save()
        self.app.apply_rules()

    def set_folder(self, folder):
        self.watcher.removePath(str(self.folder))
        self.folder = Path(folder)
        self.refresh()
        self.app.schedule_save()

    def on_renamed(self):
        if self.portal:
            return
        new_folder = DATA_ROOT / safe_name(self.name)
        if not new_folder.exists():
            self.watcher.removePath(str(self.folder))
            try:
                self.folder.rename(new_folder)
                self.folder = new_folder
            except OSError:
                pass  # keep the old folder name; the fence still works
        self.refresh()

    def confirm_remove(self):
        if self.portal:
            return dialogs.ask(f"Remove the portal '{self.name}'?\n\nThe folder {self.folder} and its files "
                               "are left untouched.")
        if not dialogs.ask(f"Remove the fence '{self.name}'?\n\n"
                           f"Its {self.count} item(s) will be moved back to your desktop."):
            return False
        self.watcher.removePath(str(self.folder))
        failed = [p for p in list(self.folder.iterdir()) if move_into(p, DESKTOP) is False]
        if failed:
            self.refresh()
            self.app.warn("Some items could not be moved back (they may be open). The fence was kept.")
            return False
        try:
            self.folder.rmdir()
        except OSError:
            pass  # hidden files left behind; harmless
        return True
