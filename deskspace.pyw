"""DeskSpace - translucent fences and widgets on the Windows desktop.

Entry point (used by the Start-with-Windows entry, the launcher shortcut and the packaged
DeskSpace.exe); the app lives in the `fences` package next to this file.
"""
import sys
from pathlib import Path

# The Linux package keeps its Python libraries next to the app.
_bundled = Path(__file__).resolve().parent.parent / "lib"
if _bundled.is_dir():
    sys.path.insert(0, str(_bundled))

if __name__ == "__main__" and "--selftest" in sys.argv:
    # Used by the build: checks a packaged exe bundled everything, without starting the app.
    from fences.selftest import run
    sys.exit(run(sys.argv[sys.argv.index("--selftest") + 1]))

# The app was called DeskFences before 1.1. Move its data folder over before anything opens a file
# in it (the error log below would, and an open file stops the folder being renamed).
_new, _old = Path.home() / "DeskSpace", Path.home() / "DeskFences"
if not _new.exists() and _old.is_dir():
    try:
        _old.rename(_new)
    except OSError:
        pass  # something in it is open right now; the app keeps using the old folder and tries next time

if sys.stderr is None:  # pythonw / the windowed exe have no console; keep errors somewhere readable
    log = (_old if _old.is_dir() and not _new.exists() else _new) / "error.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr = open(log, "a", encoding="utf-8", buffering=1)

from fences.app import main  # noqa: E402  (after the log is set up, so import errors land in it)

if __name__ == "__main__":
    main()
