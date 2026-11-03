"""Console mode: a full-screen home screen for a PC on a TV, made to be used from the sofa.

Your Steam and GOG games as a big cover grid (recently played first), a System page with volume
and power, and your pets walking along the bottom bar. Works with an Xbox-style controller, the
keyboard and the mouse. Starting a game gets out of the way; when the game ends the console comes
back by itself, and holding Back + Start brings it back at any time.
"""
import ctypes
import os
import struct
import subprocess
import time
from ctypes import wintypes
from datetime import datetime

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from . import gog, pet, steam
from .common import ACCENT, APP_NAME
from .dialogs import bring_to_front
from .folder import make_tile
from .system import IS_WINDOWS, linux_get_volume, linux_set_volume, open_path, run_output, user32

WHITE = QColor(255, 255, 255, 240)
DIM = QColor(255, 255, 255, 150)
CARD = QColor(255, 255, 255, 22)
PAGES = ("Games", "System")
GAME_START_TIMEOUT = 180  # seconds to wait for a game to appear before coming back anyway
CHORD_SECONDS = 1.0


# -- game library

def load_games():
    """Installed Steam and GOG games, most recently played first, then by name."""
    games = [{"source": "Steam", "id": appid, "name": name, "played": played}
             for appid, name, played in steam.library()]
    games += [{"source": "GOG", "id": g["id"], "name": g["name"], "played": 0, "game": g}
              for g in gog.installed_games()]
    return sorted(games, key=lambda g: (-g["played"], g["name"].lower()))


def launch(game):
    if game["source"] == "Steam":
        open_path(f"steam://rungameid/{game['id']}")
    else:
        gog.run_game(game["game"])


def is_running(game):
    if game["source"] == "Steam":
        return steam.running_game() == game["id"]
    return gog.is_running(game["game"])


# -- volume, through Windows' Core Audio COM interfaces

class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        h = text.replace("-", "")
        super().__init__(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                         (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


CLSID_ENUMERATOR = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_ENUMERATOR = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_VOLUME = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")
HRESULT = ctypes.c_long


def com_call(obj, index, *signature):
    """The method at this place in a COM object's table, ready to call with its arguments."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *signature)(table[index])
    return lambda *args: method(obj, *args)


def release(obj):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(obj)


def _endpoint_volume():
    """IAudioEndpointVolume of the current default speakers (they can change while we run)."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    enumerator, device, volume = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(CLSID_ENUMERATOR), None, 23, ctypes.byref(IID_ENUMERATOR),
                              ctypes.byref(enumerator)):
        return None
    try:
        get_default = com_call(enumerator, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
        if get_default(0, 1, ctypes.byref(device)):  # eRender, eMultimedia
            return None
        try:
            activate = com_call(device, 3, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p,
                                ctypes.POINTER(ctypes.c_void_p))
            return None if activate(ctypes.byref(IID_VOLUME), 23, None, ctypes.byref(volume)) else volume
        finally:
            release(device)
    finally:
        release(enumerator)


def get_volume():
    """(level 0..1, muted), or None if there are no speakers."""
    if not IS_WINDOWS:
        return linux_get_volume()
    volume = _endpoint_volume()
    if not volume:
        return None
    try:
        level, muted = ctypes.c_float(), wintypes.BOOL()
        com_call(volume, 9, ctypes.POINTER(ctypes.c_float))(ctypes.byref(level))
        com_call(volume, 15, ctypes.POINTER(wintypes.BOOL))(ctypes.byref(muted))
        return level.value, bool(muted.value)
    finally:
        release(volume)


def set_volume(level):
    if not IS_WINDOWS:
        linux_set_volume(level)
        return
    volume = _endpoint_volume()
    if volume:
        try:
            com_call(volume, 14, wintypes.BOOL, ctypes.c_void_p)(False, None)  # changing it un-mutes
            com_call(volume, 7, ctypes.c_float, ctypes.c_void_p)(max(0.0, min(1.0, level)), None)
        finally:
            release(volume)


# -- power

def enable_shutdown_privilege():
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                           win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
    privilege = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)
    win32security.AdjustTokenPrivileges(token, False, [(privilege, win32con.SE_PRIVILEGE_ENABLED)])


def sleep_pc():
    if not IS_WINDOWS:
        subprocess.Popen(["systemctl", "suspend"])
        return
    enable_shutdown_privilege()
    if not ctypes.windll.powrprof.SetSuspendState(False, False, False):
        # PCs with Modern Standby can't be told to sleep this way, but do once the screen goes off.
        user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)  # WM_SYSCOMMAND, SC_MONITORPOWER, off


def restart_pc():
    subprocess.Popen(["shutdown", "/r", "/t", "0"] if IS_WINDOWS else ["systemctl", "reboot"],
                     creationflags=0x08000000 if IS_WINDOWS else 0)


def shutdown_pc():
    subprocess.Popen(["shutdown", "/s", "/t", "0"] if IS_WINDOWS else ["systemctl", "poweroff"],
                     creationflags=0x08000000 if IS_WINDOWS else 0)


# -- controller

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


UP, DOWN, LEFT, RIGHT = 0x1, 0x2, 0x4, 0x8
START, BACK, LB, RB = 0x10, 0x20, 0x100, 0x200
A, B, X, Y = 0x1000, 0x2000, 0x4000, 0x8000
PRESSES = ((A, "ok"), (B, "back"), (LB, "prev"), (RB, "next"), (START, "menu"))
MOVES = ((UP, "up"), (DOWN, "down"), (LEFT, "left"), (RIGHT, "right"))
STICK = 16000
REPEAT_FIRST, REPEAT_NEXT = 0.38, 0.11


class LinuxJoystick:
    """The kernel's joystick device: reads the controller's button and stick changes as they arrive.
    Button and axis numbers are the ones Xbox-style controllers get from Linux's xpad driver."""
    BUTTONS = {0: A, 1: B, 2: X, 3: Y, 4: LB, 5: RB, 6: BACK, 7: START}
    EVENT = struct.Struct("IhBB")  # time, value, type, number
    PATH = "/dev/input/js{}"

    def __init__(self):
        self.file = None
        self.buttons = 0
        self.axes = {}

    def connect(self):
        for i in range(4):
            try:
                self.file = os.open(self.PATH.format(i), os.O_RDONLY | os.O_NONBLOCK)
                self.buttons, self.axes = 0, {}
                return True
            except OSError:
                continue
        return False

    def read(self):
        """The buttons held now, or None once the controller is gone."""
        while True:
            try:
                data = os.read(self.file, self.EVENT.size)
            except BlockingIOError:
                break
            except OSError:
                data = b""
            if len(data) < self.EVENT.size:
                os.close(self.file)
                self.file = None
                return None
            _, value, kind, number = self.EVENT.unpack(data)
            kind &= 0x7f  # without the "initial state" flag
            if kind == 1 and number in self.BUTTONS:
                self.buttons = self.buttons | self.BUTTONS[number] if value else self.buttons & ~self.BUTTONS[number]
            elif kind == 2:
                self.axes[number] = value
        lx, ly, hat_x, hat_y = (self.axes.get(n, 0) for n in (0, 1, 6, 7))
        held = self.buttons | (LEFT if lx < -STICK or hat_x < 0 else 0) | (RIGHT if lx > STICK or hat_x > 0 else 0)
        return held | (UP if ly < -STICK or hat_y < 0 else 0) | (DOWN if ly > STICK or hat_y > 0 else 0)


class Gamepad(QObject):
    """Polls the first connected Xbox-style controller."""
    action = Signal(str)
    home = Signal()  # Back + Start held

    def __init__(self, parent=None):
        super().__init__(parent)
        self.xinput = None
        self.joystick = None if IS_WINDOWS else LinuxJoystick()
        for name in ("xinput1_4", "xinput9_1_0") if IS_WINDOWS else ():
            try:
                self.xinput = getattr(ctypes.windll, name)
                break
            except OSError:
                pass
        self.slot = None
        self.next_scan = 0.0
        self.last = 0
        self.held_since = {}
        self.chord_since = None
        self.last_awake = 0.0
        self.timer = QTimer(self, interval=25, timeout=self.poll)

    def start(self):
        if self.xinput or self.joystick:
            self.timer.start()

    def read(self, slot):
        state = XINPUT_STATE()
        return state if self.xinput.XInputGetState(slot, ctypes.byref(state)) == 0 else None

    def find(self):
        # Asking an empty slot is slow, so only look for a controller now and then.
        now = time.monotonic()
        if now < self.next_scan:
            return None
        self.next_scan = now + 2
        return next((i for i in range(4) if self.read(i)), None)

    def poll_linux(self):
        js = self.joystick
        if js.file is None:
            now = time.monotonic()
            if now < self.next_scan or not js.connect():
                self.next_scan = max(self.next_scan, now + 2) if now >= self.next_scan else self.next_scan
                return
        buttons = js.read()
        if buttons is None:
            self.last, self.held_since, self.chord_since = 0, {}, None
            return
        self.handle(buttons, time.monotonic())

    def poll(self):
        if not IS_WINDOWS:
            self.poll_linux()
            return
        if self.slot is None:
            self.slot = self.find()
            if self.slot is None:
                return
        state = self.read(self.slot)
        if state is None:  # switched off
            self.slot = None
            self.last, self.held_since, self.chord_since = 0, {}, None
            return
        pad = state.Gamepad
        buttons = pad.wButtons
        buttons |= (UP if pad.sThumbLY > STICK else 0) | (DOWN if pad.sThumbLY < -STICK else 0)
        buttons |= (LEFT if pad.sThumbLX < -STICK else 0) | (RIGHT if pad.sThumbLX > STICK else 0)
        self.handle(buttons, time.monotonic())

    def keep_awake(self, now):
        """Controllers don't count as input to the system, so keep the TV and PC awake ourselves."""
        if IS_WINDOWS:
            ctypes.windll.kernel32.SetThreadExecutionState(0x3)
        elif now - self.last_awake > 30:
            self.last_awake = now
            run_output("xdg-screensaver", "reset")

    def handle(self, buttons, now):
        if buttons:
            pet.pad_activity = now
            self.keep_awake(now)
        if buttons & BACK and buttons & START:
            if self.chord_since is None:
                self.chord_since = now
            elif now - self.chord_since >= CHORD_SECONDS:
                self.chord_since = float("inf")  # once per hold
                self.home.emit()
            self.last = buttons
            return
        self.chord_since = None
        for button, name in PRESSES:
            if buttons & button and not self.last & button:
                self.action.emit(name)
        for button, name in MOVES:
            if not buttons & button:
                self.held_since.pop(button, None)
            elif button not in self.held_since:
                self.held_since[button] = now + REPEAT_FIRST
                self.action.emit(name)
            elif now >= self.held_since[button]:
                self.held_since[button] = now + REPEAT_NEXT
                self.action.emit(name)
        self.last = buttons


KEYS = {Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
        Qt.Key_Return: "ok", Qt.Key_Enter: "ok", Qt.Key_Space: "ok", Qt.Key_Escape: "back",
        Qt.Key_Backspace: "back", Qt.Key_Tab: "next", Qt.Key_Backtab: "prev", Qt.Key_PageDown: "next",
        Qt.Key_PageUp: "prev"}


# -- the screen

class ConsoleWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Window)
        self.app = app
        self.setWindowTitle(f"{APP_NAME} console")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.games = []
        self.covers = {}
        self.page = 0
        self.index = 0
        self.row = 0  # selected row on the System page
        self.scroll = 0.0
        self.target = 0.0
        self.confirm = None  # (question, action) waiting for a yes
        self.using_pad = False
        self.volume = None
        self.hits = []  # (rect, what) for the mouse
        self.minute = ""
        self.playing = None  # (game, time started, has it appeared yet)

        self.anim = QTimer(self, interval=16, timeout=self.animate)
        self.clock = QTimer(self, interval=1000, timeout=self.on_clock)
        self.watch = QTimer(self, interval=2000, timeout=self.watch_game)
        self.pad = Gamepad(self)
        self.pad.action.connect(self.on_pad)
        self.pad.home.connect(self.come_back)

    # -- sizes: everything is laid out for 1080p and scaled to the screen

    def u(self, n):
        return n * self.height() / 1080

    def tile_size(self):
        return self.u(230), self.u(345)

    def gap(self):
        return self.u(34)

    def margin(self):
        return self.u(80)

    def columns(self):
        w = self.tile_size()[0]
        return max(2, int((self.width() - 2 * self.margin() + self.gap()) // (w + self.gap())))

    def bar_top(self):
        return self.height() - self.u(96)

    def grid_top(self):
        return self.u(140)

    def tile_rect(self, i):
        w, h = self.tile_size()
        cols = self.columns()
        row, col = divmod(i, cols)
        total = cols * w + (cols - 1) * self.gap()
        left = (self.width() - total) / 2
        return QRectF(left + col * (w + self.gap()), self.grid_top() + row * (h + self.gap()) - self.scroll, w, h)

    def show_selected(self):
        """Scroll so the selected game's row is fully on screen."""
        h = self.tile_size()[1] + self.gap()
        row = self.index // self.columns()
        room = self.bar_top() - self.grid_top() - self.u(24)
        top = row * h
        if top < self.target:
            self.target = top
        elif top + self.tile_size()[1] > self.target + room:
            self.target = top + self.tile_size()[1] - room
        self.anim.start()

    # -- opening and closing

    def open(self):
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.reload_games()
        self.show_home()
        self.pad.start()
        self.clock.start()
        # Steam and friends may still be opening their windows after sign-in.
        QTimer.singleShot(8000, lambda: self.isVisible() and not self.playing and bring_to_front(self))

    def show_home(self):
        self.showFullScreen()
        bring_to_front(self)
        self.setFocus()
        self.refresh_volume()
        self.pets_on()

    def close_console(self):
        self.pets_off()
        self.pad.timer.stop()
        self.clock.stop()
        self.watch.stop()
        self.playing = None
        self.setCursor(Qt.ArrowCursor)
        self.hide()

    def pets_on(self):
        app = self.app
        if not app.pet_enabled:
            return
        app.stage_floor = int(self.y() + self.bar_top())
        for p in app.pet_windows:
            p.set_stage(int(self.winId()), self.height() / 1080 * 2, app.stage_floor)
        app.start_pets()

    def pets_off(self):
        app = self.app
        app.stop_pets()
        app.stage_floor = None
        floor = QApplication.primaryScreen().availableGeometry().bottom() + 1
        for p in app.pet_windows:
            p.set_stage(None, 1.0, floor)

    def reload_games(self):
        chosen = self.games[self.index] if self.games and self.index < len(self.games) else None
        self.games = load_games()
        self.covers.clear()
        key = (chosen["source"], chosen["id"]) if chosen else None
        keys = [(g["source"], g["id"]) for g in self.games]
        self.index = keys.index(key) if key in keys else 0
        self.index = min(self.index, max(0, len(self.games) - 1))
        self.show_selected()
        self.update()

    # -- playing

    def play(self, game):
        launch(game)
        self.playing = [game, time.monotonic(), False]
        self.app.stop_pets()
        self.showMinimized()
        self.watch.start()

    def watch_game(self):
        game, started, seen = self.playing
        running = is_running(game)
        if running:
            self.playing[2] = True
        elif seen or time.monotonic() - started > GAME_START_TIMEOUT:
            self.come_back()

    def come_back(self):
        """A game ended, or Back + Start was held: back to the home screen."""
        if not self.app.console_active:
            return
        self.watch.stop()
        was_away = self.playing is not None or self.isMinimized() or not self.isActiveWindow()
        self.playing = None
        if was_away:
            self.reload_games()  # what you just played moves to the front
            self.show_home()

    # -- input

    def on_pad(self, action):
        if self.isActiveWindow() and not self.playing:
            self.set_pad(True)
            self.do(action)

    def set_pad(self, on):
        if on != self.using_pad:
            self.using_pad = on
            self.update()
        self.setCursor(Qt.BlankCursor)

    def keyPressEvent(self, e):
        action = KEYS.get(e.key())
        if action:
            self.set_pad(False)
            self.do(action)
        else:
            super().keyPressEvent(e)

    def do(self, action):
        if self.confirm:
            question, yes = self.confirm
            self.confirm = None
            if action == "ok":
                yes()
            self.update()
        elif action in ("prev", "next"):
            self.set_page(self.page + (1 if action == "next" else -1))
        elif action == "menu":
            self.set_page(1)
        elif self.page == 0:
            self.do_games(action)
        else:
            self.do_system(action)

    def set_page(self, page):
        self.page = page % len(PAGES)
        self.row = 0
        self.refresh_volume()
        self.update()

    def do_games(self, action):
        if not self.games:
            return
        cols = self.columns()
        last = len(self.games) - 1
        if action == "left" and self.index % cols:
            self.index -= 1
        elif action == "right" and self.index % cols < cols - 1 and self.index < last:
            self.index += 1
        elif action == "up" and self.index >= cols:
            self.index -= cols
        elif action == "down" and self.index // cols < last // cols:
            self.index = min(self.index + cols, last)
        elif action == "ok":
            self.play(self.games[self.index])
            return
        self.show_selected()
        self.update()

    def system_rows(self):
        return [("Volume", None),
                ("Sleep", self.do_sleep),
                ("Restart", lambda: self.ask("Restart the PC?", restart_pc)),
                ("Shut down", lambda: self.ask("Shut down the PC?", shutdown_pc)),
                ("Exit to desktop", self.app.exit_console)]

    def do_sleep(self):
        sleep_pc()

    def ask(self, question, action):
        self.confirm = (question, action)
        self.update()

    def do_system(self, action):
        rows = self.system_rows()
        if action == "back":
            self.set_page(0)
        elif action == "up":
            self.row = max(0, self.row - 1)
        elif action == "down":
            self.row = min(len(rows) - 1, self.row + 1)
        elif action in ("left", "right") and self.row == 0:
            self.change_volume(0.05 if action == "right" else -0.05)
        elif action == "ok" and rows[self.row][1]:
            rows[self.row][1]()
        self.update()

    def change_volume(self, step):
        if self.volume:
            set_volume(round(self.volume[0] / 0.05) * 0.05 + step)
            self.refresh_volume()

    def refresh_volume(self):
        self.volume = get_volume()
        self.update()

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.ArrowCursor)
        if self.using_pad:
            self.using_pad = False
            self.update()
        for rect, what in self.hits:
            if rect.contains(e.position()):
                self.hover(what)
                break

    def hover(self, what):
        if what[0] == "game" and what[1] != self.index:
            self.index = what[1]
            self.update()
        elif what[0] == "row" and what[1] != self.row:
            self.row = what[1]
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        for rect, what in self.hits:
            if rect.contains(e.position()):
                self.click(what, e.position())
                return

    def click(self, what, pos):
        if self.confirm:
            self.do("ok" if what[0] == "yes" else "back")
        elif what[0] == "page":
            self.set_page(what[1])
        elif what[0] == "game":
            self.index = what[1]
            self.do_games("ok")
        elif what[0] == "row":
            self.row = what[1]
            self.do_system("ok")
        elif what[0] == "volume":
            set_volume((pos.x() - what[1].left()) / what[1].width())
            self.refresh_volume()

    def wheelEvent(self, e):
        if self.page == 0:
            self.do_games("up" if e.angleDelta().y() > 0 else "down")

    # -- timers

    def animate(self):
        distance = self.target - self.scroll
        self.scroll = self.target if abs(distance) < 1 else self.scroll + distance * 0.22
        if self.scroll == self.target:
            self.anim.stop()
        self.update()

    def on_clock(self):
        now = datetime.now().strftime("%H:%M")
        if now != self.minute:
            self.minute = now
            self.update()

    def covers_changed(self):
        self.covers.clear()
        self.update()

    # -- drawing

    def font_(self, size, bold=False, light=False):
        f = QFont("Segoe UI Light" if light else "Segoe UI")
        f.setPixelSize(max(1, int(self.u(size))))
        f.setBold(bold)
        return f

    def cover(self, game):
        key = (game["source"], game["id"])
        if key not in self.covers:
            w, h = self.tile_size()
            dpr = w / 104 * self.devicePixelRatioF()
            if game["source"] == "Steam":
                cover = steam.cover_file(game["id"])
                header = None if cover else steam.header_file(game["id"])
                if not cover:
                    self.app.fetch_cover(game["id"])
            else:
                cover, header = gog.cover_file(game["id"]), None
                if not cover:
                    self.app.fetch_gog_cover(game["id"])
            self.covers[key] = make_tile(game["name"], dpr, cover=cover, header=header)
        return self.covers[key]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        back = QLinearGradient(0, 0, 0, self.height())
        back.setColorAt(0, QColor("#141a2b"))
        back.setColorAt(1, QColor("#0b0e16"))
        p.fillRect(self.rect(), back)
        self.hits = []
        if self.page == 0:
            self.paint_games(p)
        else:
            self.paint_system(p)
        self.paint_header(p)
        self.paint_bar(p)
        if self.confirm:
            self.paint_confirm(p)

    def paint_header(self, p):
        back = QLinearGradient(0, 0, 0, self.grid_top())
        back.setColorAt(0, QColor("#141a2b"))
        back.setColorAt(0.8, QColor(20, 26, 43, 235))
        back.setColorAt(1, QColor(20, 26, 43, 0))
        p.fillRect(QRectF(0, 0, self.width(), self.grid_top()), back)
        x = self.margin()
        for i, name in enumerate(PAGES):
            p.setFont(self.font_(36, bold=i == self.page))
            width = QFontMetrics(p.font()).horizontalAdvance(name)
            rect = QRectF(x, self.u(36), width, self.u(60))
            p.setPen(WHITE if i == self.page else DIM)
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, name)
            if i == self.page:
                p.fillRect(QRectF(x, rect.bottom() + self.u(4), width, self.u(4)), ACCENT)
            self.hits.append((rect, ("page", i)))
            x += width + self.u(48)
        p.setFont(self.font_(38, light=True))
        p.setPen(WHITE)
        p.drawText(QRectF(0, self.u(36), self.width() - self.margin(), self.u(60)),
                   Qt.AlignRight | Qt.AlignVCenter, datetime.now().strftime("%H:%M"))

    def paint_games(self, p):
        if not self.games:
            p.setFont(self.font_(30))
            p.setPen(DIM)
            p.drawText(self.rect().adjusted(0, 0, 0, -int(self.u(100))), Qt.AlignCenter,
                       "No games found yet.\nInstall a game with Steam or GOG Galaxy and it will show up here.")
            return
        p.save()
        p.setClipRect(QRectF(0, self.grid_top() - self.u(24), self.width(),
                             self.bar_top() - self.grid_top() + self.u(24)))
        for i, game in enumerate(self.games):
            rect = self.tile_rect(i)
            if rect.bottom() < 0 or rect.top() > self.bar_top():
                continue
            self.hits.append((rect, ("game", i)))
            if i == self.index:
                grow = self.u(12)
                big = rect.adjusted(-grow, -grow * 1.5, grow, grow * 1.5)
                p.drawPixmap(big.toRect(), self.cover(game))
                p.setPen(QPen(ACCENT, self.u(5)))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(big.adjusted(-self.u(3), -self.u(3), self.u(3), self.u(3)), self.u(14), self.u(14))
            else:
                p.setOpacity(0.82)
                p.drawPixmap(rect.toRect(), self.cover(game))
                p.setOpacity(1)
        p.restore()

    def paint_system(self, p):
        width = min(self.u(900), self.width() - 2 * self.margin())
        left = (self.width() - width) / 2
        y = self.grid_top() + self.u(20)
        height = self.u(96)
        for i, (name, _) in enumerate(self.system_rows()):
            rect = QRectF(left, y, width, height)
            chosen = i == self.row
            p.setPen(QPen(ACCENT, self.u(4)) if chosen else Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 40) if chosen else CARD)
            p.drawRoundedRect(rect, self.u(14), self.u(14))
            p.setFont(self.font_(32))
            p.setPen(WHITE)
            p.drawText(rect.adjusted(self.u(32), 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, name)
            self.hits.append((rect, ("row", i)))
            if i == 0:
                self.paint_volume(p, rect)
            y += height + self.u(18)

    def paint_volume(self, p, rect):
        track = QRectF(rect.left() + self.u(210), rect.center().y() - self.u(7), rect.width() - self.u(350), self.u(14))
        if not self.volume:
            p.setFont(self.font_(26))
            p.setPen(DIM)
            p.drawText(track.adjusted(0, -self.u(20), 0, self.u(20)), Qt.AlignLeft | Qt.AlignVCenter, "No speakers found")
            return
        level, muted = self.volume
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 40))
        p.drawRoundedRect(track, self.u(7), self.u(7))
        p.setBrush(DIM if muted else ACCENT)
        p.drawRoundedRect(QRectF(track.left(), track.top(), max(track.height(), track.width() * level), track.height()),
                          self.u(7), self.u(7))
        p.setFont(self.font_(28))
        p.setPen(WHITE)
        p.drawText(QRectF(track.right(), rect.top(), rect.right() - track.right() - self.u(28), rect.height()),
                   Qt.AlignRight | Qt.AlignVCenter, "Muted" if muted else f"{round(level * 100)} %")
        self.hits.insert(0, (track.adjusted(0, -self.u(24), 0, self.u(24)), ("volume", track)))

    def paint_bar(self, p):
        rect = QRectF(0, self.bar_top(), self.width(), self.height() - self.bar_top())
        p.fillRect(rect, QColor(8, 10, 16, 235))
        p.fillRect(QRectF(0, rect.top(), self.width(), max(1, self.u(2))), QColor(255, 255, 255, 30))
        if self.page == 0 and self.games:
            game = self.games[self.index]
            p.setFont(self.font_(30, bold=True))
            p.setPen(WHITE)
            room = int(self.width() * 0.45)
            name = QFontMetrics(p.font()).elidedText(game["name"], Qt.ElideRight, room)
            p.drawText(rect.adjusted(self.margin(), 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, name)
            x = self.margin() + QFontMetrics(p.font()).horizontalAdvance(name) + self.u(24)
            p.setFont(self.font_(24))
            p.setPen(DIM)
            p.drawText(QRectF(x, rect.top(), self.u(120), rect.height()), Qt.AlignLeft | Qt.AlignVCenter, game["source"])
        x = self.width() - self.margin()
        for button, text in reversed(self.hints()):
            x = self.paint_hint(p, x, rect, button, text)

    def hints(self):
        pad = self.using_pad
        if self.page == 0:
            return [("A" if pad else "Enter", "Play"), ("LB/RB" if pad else "Tab", "System")]
        return [("A" if pad else "Enter", "Select"), ("B" if pad else "Esc", "Games")]

    def paint_hint(self, p, right, rect, button, text):
        """One button hint, ending at `right`; returns where the next one should end."""
        p.setFont(self.font_(24))
        width = QFontMetrics(p.font()).horizontalAdvance(text)
        p.setPen(DIM)
        p.drawText(QRectF(right - width, rect.top(), width, rect.height()), Qt.AlignVCenter, text)
        right -= width + self.u(12)
        p.setFont(self.font_(20, bold=True))
        key_w = max(self.u(40), QFontMetrics(p.font()).horizontalAdvance(button) + self.u(20))
        key = QRectF(right - key_w, rect.center().y() - self.u(20), key_w, self.u(40))
        colors = {"A": QColor("#5fb04a"), "B": QColor("#d8483f")}
        p.setPen(Qt.NoPen)
        p.setBrush(colors.get(button, QColor(255, 255, 255, 55)))
        p.drawRoundedRect(key, self.u(20) if button in colors else self.u(8), self.u(20) if button in colors else self.u(8))
        p.setPen(QColor(255, 255, 255))
        p.drawText(key, Qt.AlignCenter, button)
        return key.left() - self.u(36)

    def paint_confirm(self, p):
        self.hits = []
        p.fillRect(self.rect(), QColor(0, 0, 0, 170))
        rect = QRectF(0, 0, self.u(760), self.u(280))
        rect.moveCenter(QRectF(self.rect()).center())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1d2436"))
        p.drawRoundedRect(rect, self.u(18), self.u(18))
        p.setFont(self.font_(38))
        p.setPen(WHITE)
        p.drawText(rect.adjusted(0, self.u(40), 0, -self.u(120)), Qt.AlignCenter, self.confirm[0])
        row = QRectF(rect.left(), rect.bottom() - self.u(100), rect.width() / 2 + self.u(120), self.u(60))
        pad = self.using_pad
        x = self.paint_hint(p, row.right(), row, "B" if pad else "Esc", "No")
        self.paint_hint(p, x, row, "A" if pad else "Enter", "Yes")
        half = rect.width() / 2
        self.hits = [(QRectF(rect.left(), rect.top(), half, rect.height()), ("yes",)),
                     (QRectF(rect.left() + half, rect.top(), half, rect.height()), ("no",))]

