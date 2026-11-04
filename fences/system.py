"""The one place that knows which operating system we're on. Everything else imports its
system-specific helpers from here: win32.py's on Windows, linux.py's on Linux, plus the small jobs
below that every platform does differently."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # subprocess creationflags: no console window
ROOT_DISK = "C:\\" if IS_WINDOWS else "/"
FILE_MANAGER = "Explorer" if IS_WINDOWS else "file manager"

if IS_WINDOWS:
    import ctypes
    import winreg

    from .win32 import *  # noqa: F401,F403
    from .win32 import SNAP_GAP, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, WM_MOVING, WM_SIZING  # noqa: F401
    from .win32 import user32  # noqa: F401
else:
    from .linux import *  # noqa: F401,F403
    from .linux import Rect, SNAP_GAP, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, WM_MOVING, WM_SIZING  # noqa: F401
    from .linux import desktop_file_field, user32  # noqa: F401

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"


def run_output(*command, timeout=10):
    """A command's output, or "" if it isn't installed or fails."""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def user_dir(name, fallback):
    """A standard Linux folder like DESKTOP or PICTURES, in the user's own language."""
    path = run_output("xdg-user-dir", name).strip()
    return Path(path) if path and path != str(Path.home()) else Path.home() / fallback


def desktop_dir():
    if IS_WINDOWS:
        buf = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
        return Path(buf.value)
    return user_dir("DESKTOP", "Desktop")


def is_hidden(path):
    if IS_WINDOWS:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attrs != -1 and bool(attrs & 0x6)  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
    return Path(path).name.startswith(".")


def hide_path(path):
    if IS_WINDOWS:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN


def open_path(target):
    """Open a file, folder or web address the way double-clicking it would."""
    target = str(target)
    if IS_WINDOWS:
        os.startfile(target)
    elif target.endswith(".desktop"):
        subprocess.Popen(["gio", "launch", target])
    elif target.lower().endswith(".url"):  # our Steam shortcuts; Linux doesn't know this format
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        found = re.search(r"^URL=(.+)$", text, re.M)
        if found:
            subprocess.Popen(["xdg-open", found.group(1).strip()])
    else:
        subprocess.Popen(["xdg-open", target])


def show_in_folder(path):
    if IS_WINDOWS:
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def single_instance():
    """True if we're the only DeskSpace running. The handle has to live as long as the process."""
    global _instance
    if IS_WINDOWS:
        kernel32 = ctypes.windll.kernel32
        _instance = kernel32.CreateMutexW(None, False, "Local\\DeskSpaceSingleInstance")
        return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    from PySide6.QtCore import QLockFile
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    _instance = QLockFile(str(Path(runtime) / "deskspace.lock"))
    _instance.setStaleLockTime(0)
    return _instance.tryLock(0)


# -- start with the system

def autostart_file(name):
    return AUTOSTART / f"{name.lower()}.desktop"


def startup_enabled(name):
    if not IS_WINDOWS:
        return autostart_file(name).exists()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def set_startup(name, command, on):
    if not IS_WINDOWS:
        path = autostart_file(name)
        if on:
            AUTOSTART.mkdir(parents=True, exist_ok=True)
            # TryExec: if the app is removed, the desktop skips this file instead of failing at sign-in.
            path.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={command}\n"
                            f"TryExec={shlex.split(command)[0]}\nIcon=deskspace\nX-GNOME-Autostart-enabled=true\n",
                            encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, name)
            except OSError:
                pass


def drop_startup(name):
    """Remove a start-with-Windows entry from before a rename; True if there was one."""
    if not IS_WINDOWS:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except OSError:
        return False


# -- volume and power on Linux (console.py has the Windows side)

def linux_get_volume():
    """(level 0..1, muted), or None. wpctl on newer systems, pactl on older ones."""
    out = run_output("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    found = re.search(r"Volume:\s*([\d.]+)", out)
    if found:
        return float(found.group(1)), "MUTED" in out
    out = run_output("pactl", "get-sink-volume", "@DEFAULT_SINK@")
    found = re.search(r"(\d+)%", out)
    if found:
        return int(found.group(1)) / 100, "yes" in run_output("pactl", "get-sink-mute", "@DEFAULT_SINK@")
    return None


def linux_set_volume(level):
    level = max(0.0, min(1.0, level))
    if shutil.which("wpctl"):
        run_output("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
        run_output("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level:.2f}")
    else:
        run_output("pactl", "set-sink-mute", "@DEFAULT_SINK@", "0")
        run_output("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{round(level * 100)}%")


def quote_command(exe, script):
    """The command line that starts the app, for autostart files and the registry."""
    return f'"{exe}" "{script}"' if IS_WINDOWS else f"{shlex.quote(str(exe))} {shlex.quote(str(script))}"
