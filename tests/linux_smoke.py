"""Runs the real app on Linux against a throwaway home folder and exercises each part of it.

Needs an X display and a window manager (the GitHub workflow provides both). Writes screenshots and
log.txt to the folder given, and exits non-zero if any check failed. Nothing here runs real power
actions, and the throwaway home keeps it away from any real files.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
home = Path(tempfile.mkdtemp())
os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), QT_QPA_PLATFORM="xcb",
                  XDG_RUNTIME_DIR=str(home))
# DESKSPACE_APP tests an installed copy (the .deb) rather than this checkout.
sys.path.insert(0, os.environ.get("DESKSPACE_APP", str(Path(__file__).resolve().parent.parent)))

# What a real system has lying around: a Steam game, a launcher, downloads, a screenshot
steam = home / ".steam/steam/steamapps"
steam.mkdir(parents=True)
(steam / "appmanifest_413150.acf").write_text('"AppState"\n{\n"appid" "413150"\n"name" "Stardew Valley"\n'
                                              '"LastPlayed" "1700000000"\n}\n')
(home / "Desktop").mkdir()
(home / "Desktop/notes.txt").write_text("hello")
(home / "Desktop/Firefox.desktop").write_text("[Desktop Entry]\nName=Firefox\nIcon=firefox\nExec=firefox\n")
(home / "Downloads").mkdir()
(home / "Downloads/report.pdf").write_bytes(b"x" * 3000)
os.utime(home / "Downloads/report.pdf", (time.time() - 9 * 86400,) * 2)
shots = home / "Pictures/Screenshots"
shots.mkdir(parents=True)
(shots / "old.png").write_bytes(b"p" * 2000)
os.utime(shots / "old.png", (time.time() - 90 * 86400,) * 2)

data = home / "DeskSpace"
(data / "Work").mkdir(parents=True)
(data / "Work/plan.txt").write_text("plan")
(data / "Games").mkdir()
kinds = [("note", 700, 100), ("todo", 1000, 100), ("clock", 1300, 100), ("monitor", 1620, 100), ("network", 700, 400),
         ("media", 1050, 400), ("packages", 1400, 400)]
fences = [{"id": "w", "kind": "folder", "name": "Work", "folder": str(data / "Work"), "x": 100, "y": 100,
           "w": 300, "h": 200},
          {"id": "g", "kind": "folder", "name": "Games", "folder": str(data / "Games"), "view": "tiles", "x": 100,
           "y": 420, "w": 300, "h": 220}]
fences += [{"id": k, "kind": k, "name": k.title(), "x": x, "y": y, "w": 300, "h": 280} for k, x, y in kinds]
(data / "config.json").write_text(json.dumps({
    "startup_configured": True, "fences": fences,
    "pets": [{"kind": "Bear", "color": "Brown", "name": "Honey"}, {"kind": "Rabbit", "color": "White", "name": "Bun"}]}))

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qt = QApplication([])
qt.setQuitOnLastWindowClosed(False)

import fences.console as con  # noqa: E402
import fences.pet as petmod  # noqa: E402
import fences.system as system  # noqa: E402
import fences.tidy as tidy  # noqa: E402
from fences import common, gog  # noqa: E402
from fences import steam as steam_module  # noqa: E402
from fences.app import DeskSpace  # noqa: E402
from fences.cleanup import CleanupWindow, recycle_bin  # noqa: E402
from fences.network import local_ip, ping, wifi_info  # noqa: E402

log, failed = [], [0]


def check(cond, text):
    log.append(("PASS " if cond else "FAIL ") + text)
    failed[0] += not cond


def shot(name):
    qt.primaryScreen().grabWindow(0).save(str(out / f"{name}.png"))
    log.append(f"     None references at {name}: {sys.getrefcount(None)}")


def none_leak(seconds=6):
    """References to None lost per second while the app sits idle. Below Python 3.12 they're counted,
    and a steady loss crashes the process when it reaches zero, so this has to be about nothing."""
    before = sys.getrefcount(None)
    pump(seconds)
    return (before - sys.getrefcount(None)) / seconds


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)


def spontaneous_close():
    """A close event from the system, which Qt doesn't let Python build directly."""
    class Close(QCloseEvent):
        def spontaneous(self):
            return True
    return Close()


def window_state(widget):
    return subprocess.run(["xprop", "-id", str(int(widget.winId()))], capture_output=True, text=True).stdout


def steps():
    check(common.DATA_ROOT == data, f"data folder: {common.DATA_ROOT}")
    check(common.DESKTOP == home / "Desktop", f"desktop folder: {common.DESKTOP}")
    check(system.is_hidden(Path("/x/.hidden")) and not system.is_hidden(Path("/x/shown")), "dot files count as hidden")
    check(steam_module.steam_root() is not None, "Steam found")
    check([g[:2] for g in steam_module.library()] == [("413150", "Stardew Valley")], "Steam library read")
    check(gog.installed_games() == [], "no GOG on Linux, without errors")
    check(system.single_instance(), "single-instance lock taken")

    panels = {f.kind: f for f in app.fences}
    pump(4)
    check(len(panels) == 8, f"every kind of panel opened: {sorted(panels)}")
    check(all(f.isVisible() for f in app.fences), "all fences are on screen")
    work = next(f for f in app.fences if f.name == "Work")
    check(work.list.count() == 1, "folder fence listed its files")
    leak = none_leak()
    if sys.version_info < (3, 12):
        check(leak < 5, f"no steady leak of None while idle: {leak:.1f} a second")
    state = window_state(app.fences[0])
    log.append("     window manager state: " + " | ".join(l for l in state.splitlines() if "_NET_WM_STATE" in l
                                                         or "WINDOW_TYPE" in l))
    check("_NET_WM_STATE_BELOW" in state, "the window manager keeps fences below other windows")
    check("_NET_WM_DESKTOP(CARDINAL) = 4294967295" in state, "fences are on every workspace")
    check("_NET_WM_WINDOW_TYPE_DESKTOP" in state, "fences sit in the desktop layer")
    shot("01_desktop")

    # Moving, snapping and resizing, driven through the same code as a real mouse drag
    fence = app.fences[0]
    start = fence.geometry()
    fence.manual_drag = (Qt.Edge(0), start, QPoint(150, 110))
    fence.drag_to(QPoint(200, 130))
    fence.manual_drag = None
    pump(0.4)
    check((fence.x(), fence.y()) == (start.x() + 50, start.y() + 20), f"a fence can be dragged: {fence.geometry()}")
    fence.manual_drag = (Qt.Edge(0), fence.geometry(), QPoint(0, 0))
    fence.drag_to(QPoint(-fence.x() + 6, 0))
    fence.manual_drag = None
    pump(0.4)
    check(fence.x() == 0, f"it snaps to the screen edge: x={fence.x()}")
    fence.manual_drag = (Qt.RightEdge, fence.geometry(), QPoint(0, 0))
    fence.drag_to(QPoint(40, 0))
    fence.manual_drag = None
    pump(0.4)
    check(fence.width() == 340, f"it can be resized: {fence.width()}")

    # Tabs: dragging a fence over another one's title bar
    target = next(f for f in app.fences if f.kind == "note")
    mover = next(f for f in app.fences if f.kind == "todo")
    mover.manual_drag = (Qt.Edge(0), mover.geometry(), QPoint(0, 0))
    centre = target.geometry().center()
    mover.drag_to(QPoint(centre.x(), target.y() + 5))
    check(mover.merge_target is target, "dragging over a title bar highlights it")
    mover.mouseReleaseEvent(None)
    pump(0.5)
    check(any(f.kind == "tabs" for f in app.fences), "dropping there makes tabs")

    app.toggle_hidden()
    pump(0.3)
    check(not any(f.isVisible() for f in app.fences), "fences hide")
    app.toggle_hidden()
    pump(0.5)
    check(all(f.isVisible() for f in app.fences if f.shown), "fences come back")

    # Widgets' data sources
    rows = {r["label"]: r["value"] for r in app.stats.rows}
    log.append(f"     system monitor: {rows}")
    check("CPU" in rows and "RAM" in rows and "Disk" in rows and "Uptime" in rows, "system monitor has its rows")
    log.append(f"     ping 127.0.0.1: {ping('127.0.0.1')} ms, ip {local_ip()}, wifi {wifi_info()}")
    check(len(app.netstats.down) == 60, "network monitor runs")
    system.linux_get_volume()  # neither of these may crash on a machine with no speakers or player
    app.media.command("toggle")

    # Settings: every page must build
    app.open_settings()
    win = app.settings_window
    for row in range(win.nav.count()):
        win.nav.setCurrentRow(row)
        pump(0.05)
    win.nav.setCurrentRow(0)
    pump(0.3)
    shot("02_settings")
    win.hide()
    check(True, f"all {win.nav.count()} Settings pages built")

    app.set_startup(True)
    check(app.startup_enabled() and "deskspace.pyw" in system.autostart_file("DeskSpace").read_text(),
          "start with the desktop: autostart file written")
    log.append("     " + [l for l in system.autostart_file("DeskSpace").read_text().splitlines()
                          if l.startswith("Exec")][0])
    app.set_startup(False)
    check(not app.startup_enabled(), "and removed")

    # Search: apps come from .desktop files
    app.open_search()
    pump(0.3)
    names = [c[0] for c in app.search.catalog]
    check("Firefox" in names and "notes.txt" in names, f"search knows desktop items: {names[:6]}")
    app.search.hide()

    # Tidy and cleanup
    tidy.DOWNLOADS, tidy.SCREENSHOTS = home / "Downloads", shots
    moves = [(s.name, d.parent.name) for s, d in tidy.plan_sort(2)]
    check(moves == [("report.pdf", "Documents")], f"tidy plans the download: {moves}")
    check(len(tidy.plan_screenshots()) == 1, "tidy plans the old screenshot")
    trash = home / ".local/share/Trash/files"
    trash.mkdir(parents=True)
    (trash / "junk.txt").write_bytes(b"j" * 500)
    check(recycle_bin() == (1, 500), f"Trash is measured: {recycle_bin()}")
    cleaner = CleanupWindow()
    cleaner.open()
    pump(1)
    shot("03_cleanup")
    cleaner.hide()

    # Pets
    pets = app.pet_windows
    pump(2)
    check(all(p.isVisible() for p in pets), "pets are out")
    pets[0].give_treat()
    pump(5)
    log.append(f"     pet states: {[p.state for p in pets]}")
    check(petmod.idle_seconds() >= 0, "idle time works without Windows")
    shot("04_pets")

    # Console mode
    launched = []
    con.launch = lambda game: launched.append(game["name"])
    con.is_running = lambda game: False
    con.GAME_START_TIMEOUT = 0
    powers = []
    con.shutdown_pc = lambda: powers.append("shutdown")
    app.enter_console()
    pump(2.5)
    c = app.console
    check(c.isFullScreen(), f"console is full screen: {c.geometry()}")
    check(all(p.isVisible() and p.scale > 1 for p in pets), "pets are big on the console")
    check([g["name"] for g in c.games] == ["Stardew Valley"], "console lists the Steam game")
    shot("05_console")
    # With one game, the strip above the bottom bar is plain dark background, so anything bright
    # really on the screen there is a pet. (A test that only asked Qt was fooled once already.)
    image = qt.primaryScreen().grabWindow(0).toImage()
    top = c.y() + int(c.bar_top())
    bright = sum(image.pixelColor(x, y).lightness() > 90 for x in range(0, image.width(), 3)
                 for y in range(top - 60, top - 4, 3))
    check(bright > 60, f"pets really are on the console screen: {bright} bright samples")

    def key(k):
        c.keyPressEvent(QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))
    key(Qt.Key_Tab)
    pump(0.3)
    check(c.page == 1, "Tab opens the System page")
    shot("06_system")
    con.launchers.installed = lambda: [("Steam", "steam"), ("Lutris", "lutris")]
    opened = []
    con.launchers.start = opened.append
    c.row = 1
    key(Qt.Key_Return)
    check([r[0] for r in c.system_rows()] == ["Steam", "Lutris", "Back"], "game launchers are listed")
    pump(0.2)
    shot("07_launchers")
    key(Qt.Key_Down)
    key(Qt.Key_Return)
    pump(0.5)
    check(opened == ["lutris"] and app.console_active, "a launcher opens and the console keeps running")
    c.come_back()
    pump(0.5)
    check(c.isVisible() and not c.isMinimized(), "the console comes back after a launcher")
    key(Qt.Key_Escape)
    check(c.launchers is None and c.page == 1, "Esc goes back from the launcher list")
    c.closeEvent(spontaneous_close())
    pump(0.5)
    check(app.console_active and c.isMinimized(), "closing the window only minimizes the console")
    app.toggle_hidden()
    pump(0.5)
    check(not c.isMinimized(), "the tray icon brings the console back")
    c.row = 5
    key(Qt.Key_Return)
    key(Qt.Key_Return)
    check(powers == ["shutdown"], "shut down asks, then goes through (faked here)")
    key(Qt.Key_Escape)
    c.do("ok")
    check(launched == ["Stardew Valley"], "a game starts")
    pump(3.5)
    check(c.playing is None and c.isVisible(), "the console comes back afterwards")

    # A controller, through the kernel joystick format
    read, write = os.pipe()
    os.set_blocking(read, False)
    js = con.LinuxJoystick()
    js.file = read
    event = struct.Struct("IhBB")
    os.write(write, event.pack(0, 1, 1, 0))  # A down
    check(js.read() == con.A, "joystick: A button")
    os.write(write, event.pack(0, 0, 1, 0) + event.pack(0, 30000, 2, 0))  # A up, stick right
    check(js.read() == con.RIGHT, "joystick: stick right")
    os.write(write, event.pack(0, 0, 2, 0) + event.pack(0, -32767, 2, 7))  # stick back, D-pad up
    check(js.read() == con.UP, "joystick: D-pad up")
    os.write(write, event.pack(0, 0, 2, 7) + event.pack(0, 1, 129, 6) + event.pack(0, 1, 1, 7))
    check(js.read() == con.BACK | con.START, "joystick: Back + Start, including the initial-state flag")
    os.close(write)
    check(js.read() is None, "joystick: unplugged is noticed")

    leak = none_leak()
    if sys.version_info < (3, 12):
        check(leak < 5, f"no steady leak of None in Console mode: {leak:.1f} a second")
    app.exit_console()
    pump(1)
    check(all(f.isVisible() for f in app.fences if f.shown), "exit: fences are back")
    check(all(p.scale == 1 and p.parent() is None and p.isWindow() for p in pets), "exit: pets are back to size")
    check(all(p.isVisible() for p in pets), "exit: pets are back on the desktop")
    shot("07_back_on_desktop")
    app.save()
    saved = json.loads((data / "config.json").read_text())
    check(len(saved["fences"]) == len(app.fences), "settings save")


def run():
    try:
        steps()
    except Exception:
        log.append(traceback.format_exc())
        failed[0] += 1
    (out / "log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(log), flush=True)
    os._exit(1 if failed[0] else 0)


app = DeskSpace(qt)
QTimer.singleShot(1500, run)
QTimer.singleShot(240000, lambda: (log.append("FAIL timed out"), failed.__setitem__(0, 1), run()))
qt.exec()
