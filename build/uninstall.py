"""Uninstalls a DeskSpace installed by build\\install_without_setup.py (copied next to the install as
uninstall.py and run by its own DeskSpace.exe from Settings > Apps or the Start menu).

Your fences, notes and settings in %USERPROFILE%\\DeskSpace are kept."""
import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

APP_NAME = "DeskSpace"
TARGET = Path(__file__).resolve().parent
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"


def main():
    quiet = "/quiet" in sys.argv
    if not quiet and ctypes.windll.user32.MessageBoxW(
            None, "Remove DeskSpace from this PC?\n\nYour fences, notes and settings in your DeskSpace folder "
                  "are kept.", "Uninstall DeskSpace", 0x24 | 0x40000) != 6:  # Yes/No, question, topmost
        return
    subprocess.run(["taskkill", "/IM", f"{APP_NAME}.exe", "/F", "/FI", f"PID ne {os.getpid()}"],
                   capture_output=True)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        try:
            if str(TARGET).lower() in winreg.QueryValueEx(key, APP_NAME)[0].lower():
                winreg.DeleteValue(key, APP_NAME)
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    shutil.rmtree(Path(os.environ["APPDATA"]) / rf"Microsoft\Windows\Start Menu\Programs\{APP_NAME}",
                  ignore_errors=True)
    (Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk").unlink(missing_ok=True)
    # We're running from inside the folder, so let cmd delete it once we've exited.
    subprocess.Popen(f'cmd /c timeout /t 2 /nobreak > nul & rmdir /s /q "{TARGET}"', cwd=os.environ["TEMP"],
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    if not quiet:
        ctypes.windll.user32.MessageBoxW(None, "DeskSpace has been removed.", "Uninstall DeskSpace", 0x40 | 0x40000)


if __name__ == "__main__":
    main()
