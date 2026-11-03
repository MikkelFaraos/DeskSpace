"""The DeskSpace application: tray icon, creating/removing panels, tabs, auto-sort, desktop rules,
quick-hide, quick search, wallpaper colours, the desktop pets and persistence."""
import copy
import ctypes
import fnmatch
import json
import random
import sys
import time
import uuid
import winreg
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QRect, QTimer, QUrl
from PySide6.QtGui import QCursor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QFileIconProvider, QMenu, QSystemTrayIcon

from . import dialogs, gog, steam
from .common import (
    APP_NAME, CATEGORIES, CONFIG_PATH, COVER_CACHE, DATA_ROOT, DESKTOP, ENTRY_SCRIPT, LEGACY_CONFIG_PATH, OLD_NAME,
    PUBLIC_DESKTOP, RUN_KEY, category_for, is_hidden, make_app_icon, move_into, safe_name,
)
from .folder import FolderFence
from .media import MediaBackend, MediaFence
from .network import NetworkFence, NetworkStats
from .packages import PackageFence
from .pet import PALETTES, SPECIES, Pet
from .preview import PreviewController
from .search import HotkeyListener, SearchPopup
from .sysinfo import SystemStats
from .tabs import TabGroup
from .wallpaper import WallpaperWatcher
from .widgets import ClockFence, MonitorFence, NoteFence, TodoFence
from .win32 import DesktopDoubleClick, pin_to_desktop, set_desktop_icons_visible, user32

PANEL_TYPES = {"folder": FolderFence, "portal": FolderFence, "note": NoteFence, "todo": TodoFence,
               "clock": ClockFence, "monitor": MonitorFence, "media": MediaFence, "packages": PackageFence,
               "network": NetworkFence, "tabs": TabGroup}
NEW_ITEMS = [("folder", "Fence"), ("portal", "Folder portal…"), ("note", "Sticky note"),
             ("todo", "To-do list"), ("clock", "Clock && calendar"), ("monitor", "System monitor"),
             ("media", "Media controls"), ("packages", "Package tracker"), ("network", "Network monitor")]
DEFAULT_NAMES = {"note": "Note", "todo": "To-do", "clock": "Clock", "monitor": "System", "media": "Now playing",
                 "packages": "Packages", "network": "Network"}
# App-wide settings and their defaults (all saved in config.json).
OPTIONS = {"locked": False, "snap": True, "snap_distance": 16, "quick_hide": True, "hide_icons": True,
           "match_wallpaper": True, "previews": True, "search_enabled": True, "search_hotkey": "Ctrl+Shift+Space",
           "pet_enabled": True, "pets": [], "pet_outfits": True, "pet_meetings": True, "track_key": "",
           "keep_delivered_days": 2, "tidy_auto": False, "tidy_days": 2, "tidy_last": 0,
           "console_autostart": False}
PET_NAMES = ["Mochi", "Biscuit", "Pickles", "Nugget", "Pebble", "Clover", "Waffles", "Hazel", "Peanut", "Bramble",
             "Tofu", "Maple", "Juniper", "Muffin", "Pip", "Sprout"]
# Files still being written by a browser or Office; rules leave them alone until they're final.
IN_PROGRESS = (".crdownload", ".part", ".partial", ".download", ".tmp")


class DeskSpace:
    def __init__(self, qapp):
        self.qapp = qapp
        self.icon_provider = QFileIconProvider()
        self.net = QNetworkAccessManager()
        self.stats = SystemStats()
        self.netstats = NetworkStats()
        self.media = MediaBackend()
        self.preview = PreviewController(self)
        self.fences = []  # top-level panels (a tab group counts once; its tabs live inside it)
        self.hidden = False
        self.cover_requests = set()
        self.settings_window = None
        self.cleanup_window = None
        self.tidy_window = None
        self.ball = None
        self.console = None
        self.console_active = False
        self.stage_floor = None  # where pets walk in Console mode
        self.alerted = set()  # calendar events the pets already announced
        self.wallpaper_color = None
        self.save_timer = QTimer(singleShot=True, interval=600, timeout=self.save)

        cfg = {}
        source = CONFIG_PATH if CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
        if source.exists():
            try:
                cfg = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cfg = {}
        for name, default in OPTIONS.items():
            setattr(self, name, cfg.get(name, copy.deepcopy(default)))
        if not self.pets:  # older versions had a single pet, kept in pet_kind/pet_color/pet_name
            self.pets = [{"kind": cfg.get("pet_kind", "Cat"), "color": cfg.get("pet_color", "Orange"),
                          "name": cfg.get("pet_name", "Mochi")}]
        if cfg.get("icons_hidden"):
            set_desktop_icons_visible(True)  # we exited with the desktop icons hidden; bring them back
        self.icons_hidden = False
        DATA_ROOT.mkdir(parents=True, exist_ok=True)

        self.wallpaper = WallpaperWatcher()
        self.wallpaper.changed.connect(self.on_wallpaper)
        if self.match_wallpaper:
            self.wallpaper.start()

        self.loading = True
        for data in cfg.get("fences", []):
            self._add(data)
        self.loading = False

        # Like Fences, start with Windows by default; after the first run it's the user's choice.
        if self.drop_old_startup_entry():  # it was on under the old DeskFences name: keep it on
            self.set_startup(True)
        elif not cfg.get("startup_configured"):
            self.set_startup(True)
        self._build_tray()
        self.save()

        # At login we may start before Explorer's desktop exists, and Explorer can restart later
        # (taking its owned windows with it), so keep re-attaching fences to the desktop.
        self.desktop_timer = QTimer(interval=3000, timeout=self.check_desktop)
        self.desktop_timer.start()
        self.chores_timer = QTimer(interval=30000, timeout=lambda: (self.check_meetings(), self.daily_tidy()))
        self.chores_timer.start()
        QTimer.singleShot(60000, self.daily_tidy)
        if self.console_autostart:
            QTimer.singleShot(2500, self.enter_console)
        # Monitors can still be coming up at login; only rescue off-screen fences once they're settled.
        QTimer.singleShot(15000, self.rescue_offscreen)

        self.double_click = DesktopDoubleClick(self.on_desktop_double_click)
        self.search = SearchPopup(self)
        self.hotkey = HotkeyListener(self.open_search)
        self.apply_hotkey()
        self.pet_windows = [Pet(self, info) for info in self.pets]
        if self.pet_enabled:
            QTimer.singleShot(1500, self.start_pets)  # once the fences they walk on are in place

        # Auto-sort rules: watch the desktop and move matching files once they've finished arriving.
        self.seen = {}  # path -> (size, mtime, failed attempts)
        self.rules_timer = QTimer(singleShot=True, interval=2000, timeout=self.apply_rules)
        self.desktop_watcher = QFileSystemWatcher([str(DESKTOP)])
        self.desktop_watcher.directoryChanged.connect(lambda _: self.rules_timer.start())
        self.rules_timer.start()

        if not self.fences:
            self.new_fence("Drop files here")
            self.tray.showMessage(APP_NAME, "Drag icons from your desktop into a fence, or right-click "
                                  "the tray icon and choose 'Auto-sort desktop'.", self.tray.icon())

    # -- tray

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_app_icon())
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.fill_new_menu(menu.addMenu("New"), at_cursor=False)
        menu.addAction("Search…", self.open_search)
        menu.addAction("Auto-sort desktop into fences…", self.auto_sort)
        menu.addAction("Tidy files…", self.open_tidy)
        menu.addAction("Console mode", self.enter_console)
        self.hide_action = menu.addAction("Hide fences", self.toggle_hidden)
        menu.addSeparator()
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Quit", self.quit)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_hidden() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def fill_new_menu(self, menu, at_cursor):
        for kind, label in NEW_ITEMS:
            menu.addAction(label, lambda k=kind: self.new_panel_interactive(k, at_cursor))

    def open_settings(self, panel=None):
        from .settings import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)
        self.settings_window.open_for(panel)

    def enter_console(self):
        """Full-screen home screen for a PC on a TV; the fences step aside while it's open."""
        if self.console_active:
            self.console.come_back()
            return
        from .console import ConsoleWindow
        if self.console is None:
            self.console = ConsoleWindow(self)
        self.console_active = True
        for f in self.fences:
            f.hide()
        self.stop_pets()
        self.console.open()

    def exit_console(self):
        self.console_active = False
        self.console.close_console()
        if not self.hidden:
            for f in self.fences:
                if f.shown:
                    f.show_pinned()
            if self.pet_enabled:
                self.start_pets()

    def open_tidy(self):
        from .tidy import TidyWindow
        if self.tidy_window is None:
            self.tidy_window = TidyWindow(self)
        self.tidy_window.open()

    def daily_tidy(self):
        if not self.tidy_auto or time.time() - self.tidy_last < 20 * 3600:
            return
        from .tidy import auto_tidy
        self.set_option("tidy_last", time.time())
        moved = auto_tidy(self.tidy_days)
        if moved:
            self.tray.showMessage(APP_NAME, f"Tidied {moved} files in Downloads and Screenshots. "
                                  "Right-click the tray icon → Tidy files… to undo.", self.tray.icon())

    def check_meetings(self):
        """A pet pipes up a minute before a timed event from a clock widget's calendar."""
        if not (self.pet_enabled and self.pet_meetings and self.pet_windows) or self.hidden:
            return
        now = datetime.now().astimezone()
        for panel in self.all_panels():
            for start, all_day, title in getattr(getattr(panel, "feed", None), "events", []):
                key = (start.isoformat(), title)
                if not all_day and 0 <= (start - now).total_seconds() <= 75 and key not in self.alerted:
                    self.alerted.add(key)
                    for i, pet in enumerate(self.pet_windows):
                        pet.announce(f"{title} starts in a minute!" if i == 0 else "!")

    def throw_ball(self):
        from .pet import Ball
        if self.ball is None:
            self.ball = Ball(self)
        self.ball.throw_in()

    def put_ball_away(self):
        if self.ball is not None:
            self.ball.hide()
            self.ball.deleteLater()
            self.ball = None

    def open_cleanup(self):
        from .cleanup import CleanupWindow
        if self.cleanup_window is None:
            self.cleanup_window = CleanupWindow()
        self.cleanup_window.open()

    def open_search(self):
        if self.search_enabled:
            self.search.open()

    def apply_hotkey(self):
        """(Re)register the quick-search hotkey; returns False if Windows refused it (taken by another app)."""
        if not self.search_enabled:
            self.hotkey.unregister()
            return True
        return self.hotkey.register(self.search_hotkey)

    def panels_changed(self):
        """A panel was added or removed: keep the Settings window's list in step."""
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.reload_later()

    def toggle_hidden(self):
        if self.console_active:
            return
        self.hidden = not self.hidden
        for f in self.fences:
            if self.hidden:
                f.hide()
            elif f.shown:
                f.show_pinned()
        if self.pet_enabled:
            self.stop_pets() if self.hidden else self.start_pets()
        if self.hide_icons or self.icons_hidden:
            self.icons_hidden = self.hidden and self.hide_icons
            set_desktop_icons_visible(not self.icons_hidden)
        self.hide_action.setText("Show fences" if self.hidden else "Hide fences")
        self.save()

    def on_desktop_double_click(self):
        if self.quick_hide:
            self.toggle_hidden()

    def set_option(self, name, value):
        """Change one of the app-wide settings (from the Settings window or a widget)."""
        setattr(self, name, value)
        if name == "hide_icons" and self.hidden:
            self.icons_hidden = value
            set_desktop_icons_visible(not value)
        elif name == "match_wallpaper":
            self.wallpaper.start() if value else self.wallpaper.stop()
            self.repaint_all()
        elif name == "pet_enabled":
            self.start_pets() if value and not self.hidden else self.stop_pets()
        elif name == "pet_outfits":
            for pet in self.pet_windows:
                pet.update()
        elif name == "previews" and not value:
            self.preview.cancel()
        self.schedule_save()

    # -- pets

    def start_pets(self):
        for pet in self.pet_windows:
            pet.start()

    def stop_pets(self):
        for pet in self.pet_windows:
            pet.stop()
        self.put_ball_away()

    def add_pet(self):
        """Another pet joins, picking an animal and colour that aren't taken yet where possible."""
        used_kinds = {p["kind"] for p in self.pets}
        used_colors = {p["color"] for p in self.pets}
        kind = random.choice([k for k in SPECIES if k not in used_kinds] or list(SPECIES))
        color = random.choice([c for c in PALETTES if c not in used_colors] or list(PALETTES))
        names = [n for n in PET_NAMES if n not in {p["name"] for p in self.pets}] or PET_NAMES
        info = {"kind": kind, "color": color, "name": random.choice(names)}
        self.pets.append(info)
        pet = Pet(self, info)
        self.pet_windows.append(pet)
        if self.pet_enabled and not self.hidden:
            pet.start()
        self.schedule_save()
        return info

    def remove_pet(self, info):
        if len(self.pets) <= 1:
            return
        index = next(i for i, p in enumerate(self.pets) if p is info)
        del self.pets[index]
        pet = self.pet_windows.pop(index)
        pet.stop()
        pet.deleteLater()
        self.schedule_save()
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.reload_later()

    def update_pet(self, info, key, value):
        """Change one pet's animal, colour or name (from the Settings window)."""
        info[key] = value
        for pet in self.pet_windows:
            if pet.info is info:
                pet.update()
        self.schedule_save()

    def on_wallpaper(self, color):
        self.wallpaper_color = color
        self.repaint_all()

    def repaint_all(self):
        for panel in self.fences:
            panel.update()

    def reveal_panel(self, panel):
        """Bring a panel into view (from quick search): unhide, switch to its tab, flash its border."""
        if self.hidden:
            self.toggle_hidden()
        if panel.group is not None:
            panel.group.set_active(panel.group.tabs.index(panel))
            panel = panel.group
        if not panel.shown:
            panel.set_shown(True)
        if panel.rolled:
            panel.set_rolled(False)
        panel.drop_hover = True
        panel.update()
        QTimer.singleShot(1200, lambda: (setattr(panel, "drop_hover", False), panel.update()))

    def startup_command(self):
        # Installed: runtime\DeskSpace.exe (a renamed, signed pythonw.exe). Dev: the venv's pythonw.exe.
        exe = Path(sys.executable)
        if exe.name.lower() == "python.exe":
            exe = exe.with_name("pythonw.exe")  # no console window at sign-in
        return f'"{exe}" "{ENTRY_SCRIPT}"'

    def startup_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        except OSError:
            return False

    def drop_old_startup_entry(self):
        """Remove the start-with-Windows entry from before the rename; True if there was one."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, OLD_NAME)
            return True
        except OSError:
            return False

    def set_startup(self, on):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, self.startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except OSError:
                    pass

    # -- panels

    def all_panels(self):
        """Every panel, including the tabs inside tab groups (but not the groups themselves)."""
        for f in self.fences:
            if isinstance(f, TabGroup):
                yield from f.tabs
            else:
                yield f

    def folder_fences(self):
        return [f for f in self.all_panels() if isinstance(f, FolderFence)]

    def make_panel(self, data):
        return PANEL_TYPES.get(data.get("kind", "folder"), FolderFence)(self, data)

    def _add(self, data):
        panel = self.make_panel(data)
        self.fences.append(panel)
        if not self.hidden and panel.shown:
            panel.show_pinned()
        if not self.loading:
            self.panels_changed()
        return panel

    def check_desktop(self):
        progman = user32.FindWindowW("Progman", None)
        if not progman or self.hidden or self.console_active:
            return
        for i, panel in enumerate(list(self.fences)):
            hwnd = int(panel.winId())
            if not user32.IsWindow(hwnd):  # destroyed along with an old Explorer: rebuild it
                data = panel.to_dict()
                if isinstance(panel, TabGroup):
                    panel.dispose()
                panel.deleteLater()
                self.fences[i] = self.make_panel(data)
                if self.fences[i].shown:
                    self.fences[i].show_pinned()
            elif panel.shown and user32.GetWindowLongPtrW(hwnd, -8) != progman:
                pin_to_desktop(hwnd)

    def rescue_offscreen(self):
        for panel in self.fences:
            if self.qapp.screenAt(panel.geometry().center()) is None:
                panel.move(60, 60)  # saved position is on a monitor that's gone

    def free_spot(self, w=340, h=230):
        """First free slot, filling from the top-right so default desktop icons stay visible."""
        scr = self.qapp.primaryScreen().availableGeometry()
        taken = [f.geometry().adjusted(-10, -10, 10, 10) for f in self.fences]
        for x in range(scr.right() - w - 20, scr.left(), -(w + 20)):
            for y in range(scr.top() + 20, scr.bottom() - h, h + 20):
                if not any(QRect(x, y, w, h).intersects(t) for t in taken):
                    return QRect(x, y, w, h)
        n = len(self.fences)
        return QRect(scr.left() + 60 + 30 * n, scr.top() + 60 + 30 * n, w, h)

    def new_panel(self, kind, name, rect=None, **extra):
        cls = PANEL_TYPES[kind]
        w, h = cls.default_size
        rect = rect or self.free_spot(w, h)
        panel = self._add({"id": uuid.uuid4().hex, "kind": kind, "name": name, "x": rect.x(), "y": rect.y(),
                           "w": rect.width(), "h": rect.height(), **extra})
        self.schedule_save()
        return panel

    def new_fence(self, name, rect=None):
        used = {f.folder.resolve() for f in self.folder_fences()}
        folder = DATA_ROOT / safe_name(name)
        i = 2
        while folder.resolve() in used:
            folder = DATA_ROOT / f"{safe_name(name)} {i}"
            i += 1
        return self.new_panel("folder", name, rect, folder=str(folder))

    def new_panel_interactive(self, kind, at_cursor):
        w, h = PANEL_TYPES[kind].default_size
        pos = QCursor.pos()
        rect = QRect(pos.x() - w // 2, pos.y() - 14, w, h) if at_cursor else None
        if kind == "folder":
            name, ok = dialogs.get_text("Name for the new fence:", "New fence")
            if ok and name.strip():
                self.new_fence(name.strip(), rect)
        elif kind == "portal":
            folder = dialogs.get_folder("Choose a folder to show on the desktop", Path.home() / "Downloads")
            if folder:
                self.new_panel("portal", Path(folder).name or folder, rect, folder=folder)
        else:
            self.new_panel(kind, DEFAULT_NAMES[kind], rect)

    def remove_panel(self, panel):
        if not panel.confirm_remove():
            return
        group = panel.group
        if group is not None:
            group.take(panel)
            panel.deleteLater()
            group.after_change()
        else:
            self.fences.remove(panel)
            panel.close()
            panel.deleteLater()
        if not any(isinstance(f, MonitorFence) for f in self.all_panels()):
            self.stats.stop()
        self.save()
        self.panels_changed()

    # -- tabs

    def merge_target_at(self, moving, pt):
        """The panel whose title bar is under the cursor (physical pixels) while `moving` is dragged."""
        from ctypes import wintypes
        for panel in self.fences:
            if panel is moving or not panel.isVisible():
                continue
            rc = wintypes.RECT()
            user32.GetWindowRect(int(panel.winId()), ctypes.byref(rc))
            title = panel.title_h * user32.GetDpiForWindow(int(panel.winId())) // 96
            if rc.left <= pt.x < rc.right and rc.top <= pt.y < rc.top + title:
                return panel
        return None

    def merge(self, source, target):
        """Drop `source` (a panel or a whole tab group) into `target` as tabs."""
        if source is target or source not in self.fences or target not in self.fences:
            return
        if isinstance(target, TabGroup):
            group = target
        else:
            g = target.geometry()
            group = self.make_panel({"id": uuid.uuid4().hex, "kind": "tabs", "name": "Tabs", "x": g.x(), "y": g.y(),
                                     "w": g.width(), "h": target.full_h, "color": list(target.color.getRgb()),
                                     "custom_color": target.custom_color, "title_size": target.title_size})
            self.fences[self.fences.index(target)] = group
            group.add(target)
        incoming = list(source.tabs) if isinstance(source, TabGroup) else [source]
        for panel in incoming:
            if isinstance(source, TabGroup):
                source.take(panel)
            group.add(panel)
        self.fences.remove(source)
        if isinstance(source, TabGroup):
            source.close()
            source.deleteLater()
        group.set_active(len(group.tabs) - 1)
        if not self.hidden:
            group.show_pinned()
        self.save()
        self.panels_changed()

    def drop_group(self, group):
        if group in self.fences:
            self.fences.remove(group)
        group.close()
        group.deleteLater()

    # -- sorting

    def auto_sort(self):
        items = [p for p in DESKTOP.iterdir() if not is_hidden(p)]
        plan = {}
        for p in items:
            plan.setdefault(category_for(p), []).append(p)
        public = [p for p in PUBLIC_DESKTOP.iterdir() if not is_hidden(p)] if PUBLIC_DESKTOP.exists() else []
        if not plan:
            self.warn("Your desktop is already empty - nothing to sort.", info=True)
            return
        order = [name for name, _ in CATEGORIES if name in plan]
        lines = "\n".join(f"  • {name}: {len(plan[name])}" for name in order)
        note = (f"\n\n{len(public)} shortcut(s) shared by all users will stay on the desktop "
                "(moving them needs admin rights)." if public else "")
        if not dialogs.ask(f"Move {len(items)} desktop item(s) into fences?\n\n{lines}\n\n"
                           f"Files are stored in {DATA_ROOT}. Removing a fence puts its items back on the "
                           f"desktop.{note}"):
            return
        failed = 0
        for name in order:
            fence = next((f for f in self.folder_fences() if f.name.lower() == name.lower()), None) \
                or self.new_fence(name)
            failed += sum(move_into(p, fence.folder) is False for p in plan[name])
            fence.refresh()
        if failed:
            self.warn(f"{failed} item(s) could not be moved (they may be open in another program).")

    def rule_target(self, path):
        name = path.name.lower()
        for fence in self.folder_fences():
            if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in fence.rules):
                return fence
        return None

    def apply_rules(self):
        """Move desktop files matching a fence's rules into it, once they've stopped changing."""
        if not any(f.rules for f in self.folder_fences()):
            return
        waiting = False
        for path in list(DESKTOP.iterdir()):
            name = path.name.lower()
            if is_hidden(path) or name.endswith(IN_PROGRESS) or name.startswith("~$"):
                continue
            target = self.rule_target(path)
            if not target or not target.folder.is_dir():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            size, mtime, failures = self.seen.get(path, (None, None, 0))
            if (size, mtime) != (st.st_size, st.st_mtime) or time.time() - st.st_mtime < 2:
                self.seen[path] = (st.st_size, st.st_mtime, failures)  # still arriving: check again shortly
                waiting = True
                continue
            if move_into(path, target.folder) is False:
                if failures < 10:  # probably open in another program; keep trying for a while
                    self.seen[path] = (size, mtime, failures + 1)
                    waiting = True
                continue
            self.seen.pop(path, None)
            target.refresh()
        if waiting:
            self.rules_timer.start()

    # -- game covers (Steam, GOG)

    def fetch_cover(self, appid):
        """Download a game's portrait cover from Steam once, if Steam hasn't cached one locally."""
        if appid in self.cover_requests:
            return
        self.cover_requests.add(appid)
        reply = self.net.get(QNetworkRequest(QUrl(steam.CDN_COVER.format(appid))))
        reply.finished.connect(lambda: self._cover_done(appid, reply))

    def _cover_done(self, appid, reply, filename=None):
        reply.deleteLater()
        data = bytes(reply.readAll())
        if reply.error() != QNetworkReply.NoError or not data:
            return
        COVER_CACHE.mkdir(parents=True, exist_ok=True)
        ctypes.windll.kernel32.SetFileAttributesW(str(COVER_CACHE), 0x2)  # hidden
        (COVER_CACHE / (filename or f"{appid}.jpg")).write_bytes(data)
        if self.console is not None:
            self.console.covers_changed()
        for fence in self.folder_fences():
            if fence.view == "tiles":
                fence.schedule_refresh()

    def fetch_gog_cover(self, game_id):
        """Look up a GOG game's portrait cover in GOG's games database, then download it once."""
        key = f"gog_{game_id}"
        if key in self.cover_requests:
            return
        self.cover_requests.add(key)
        reply = self.net.get(QNetworkRequest(QUrl(gog.GAMESDB.format(game_id))))

        def looked_up():
            reply.deleteLater()
            url = gog.cover_url(bytes(reply.readAll())) if reply.error() == QNetworkReply.NoError else None
            if url:
                image = self.net.get(QNetworkRequest(QUrl(url)))
                image.finished.connect(lambda: self._cover_done(game_id, image, f"{key}.jpg"))
        reply.finished.connect(looked_up)

    # -- persistence

    def schedule_save(self):
        if not getattr(self, "loading", False):
            self.save_timer.start()

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = {name: getattr(self, name) for name in OPTIONS}
        cfg.update({"startup_configured": True, "icons_hidden": self.icons_hidden,
                    "fences": [f.to_dict() for f in self.fences]})
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    def warn(self, text, info=False):
        dialogs.tell(text, warning=not info)

    def restore_icons(self):
        if self.icons_hidden:
            set_desktop_icons_visible(True)
            self.icons_hidden = False

    def quit(self):
        if self.console_active:
            self.exit_console()
        self.restore_icons()
        self.save()
        self.hotkey.unregister()
        self.search.shutdown()
        self.media.stop()
        self.tray.hide()
        self.qapp.quit()


def main():
    # Only one instance at a time; keep the handle alive for the life of the process.
    global _mutex
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, "Local\\DeskSpaceSingleInstance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(make_app_icon())
    app = DeskSpace(qapp)

    def on_quit():
        app.restore_icons()
        app.save()
    qapp.aboutToQuit.connect(on_quit)
    sys.exit(qapp.exec())
