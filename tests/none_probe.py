"""Finds what leaks references to None: opens each part of the app in turn on Python below 3.12 and
prints how many references that part loses per second. Printed live, since the leak ends in a crash."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

home = Path(tempfile.mkdtemp())
os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), QT_QPA_PLATFORM="xcb",
                  XDG_RUNTIME_DIR=str(home))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
data = home / "DeskSpace"
(data / "Work").mkdir(parents=True)
(data / "Work/plan.txt").write_text("plan")
(data / "config.json").write_text(json.dumps({"startup_configured": True, "pet_enabled": False, "fences": [],
                                              "pets": [{"kind": "Bear", "color": "Brown", "name": "H"}]}))

from PySide6.QtWidgets import QApplication  # noqa: E402

qt = QApplication([])
qt.setQuitOnLastWindowClosed(False)
from fences.app import DeskSpace  # noqa: E402

app = DeskSpace(qt)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.005)


def measure(name, seconds=3):
    before = sys.getrefcount(None)
    pump(seconds)
    after = sys.getrefcount(None)
    print(f"{name:28} {(before - after) / seconds:9.1f} a second   (None refs now {after})", flush=True)


print("start", sys.version.split()[0], sys.getrefcount(None), flush=True)
measure("nothing open")
for i, kind in enumerate(("note", "todo", "clock", "folder", "monitor", "network", "media", "packages")):
    fence = {"id": kind, "kind": kind, "name": kind, "x": 20 + i * 60, "y": 20 + i * 40, "w": 300, "h": 280}
    if kind == "folder":
        fence["folder"] = str(data / "Work")
    panel = app.make_panel(fence)
    app.fences.append(panel)
    panel.show_pinned()
    measure(f"+ {kind}")
    panel.hide()
    measure(f"  after hiding {kind}")
    panel.deleteLater()
    app.fences.remove(panel)
    measure(f"  after removing {kind}")
app.set_option("pet_enabled", True)
measure("+ one pet")
app.set_option("pet_enabled", False)
measure("  after pets off")
print("done", flush=True)
os._exit(0)
