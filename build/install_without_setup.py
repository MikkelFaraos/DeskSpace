"""Installs (or updates) DeskSpace from dist\\stage without the setup exe.

For PCs where Windows' Smart App Control blocks the unsigned DeskSpace-Setup exe: this does the same
job as build\\installer.iss, but runs on the PSF-signed Python that build.ps1 puts in dist\\stage, which
Smart App Control allows. It must run outside any app sandbox (e.g. started from Explorer):

    dist\\stage\\runtime\\python.exe build\\install_without_setup.py

Also takes over an install made by the old DeskFences installer: its program folder, shortcuts,
startup entry and uninstall entry are replaced. Your fences and settings folder is left alone (the
app itself moves %USERPROFILE%\\DeskFences to %USERPROFILE%\\DeskSpace when it starts).
"""
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "dist" / "stage"
sys.path.insert(0, str(STAGE / "app"))
from fences.common import APP_NAME, APP_VERSION, OLD_NAME  # noqa: E402

PROGRAMS = Path(os.environ["LOCALAPPDATA"]) / "Programs"
TARGET = PROGRAMS / APP_NAME
START_MENU = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
OLD_INNO_ID = "{6F1C0E2A-5B7D-4C3E-9A8F-2D4B6E8A1C90}_is1"  # the setup exe's uninstall entry
EXE = TARGET / "runtime" / f"{APP_NAME}.exe"
SCRIPT = TARGET / "app" / "deskspace.pyw"
ICON = TARGET / "deskspace.ico"


def log(text):
    print(text, flush=True)


def shortcut(path, target, arguments="", workdir="", icon=None):
    import pythoncom
    from win32com.shell import shell
    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)
    link.SetPath(str(target))
    link.SetArguments(arguments)
    link.SetWorkingDirectory(str(workdir))
    if icon:
        link.SetIconLocation(str(icon), 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)


def run_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def main():
    startup = run_value(APP_NAME) is not None or run_value(OLD_NAME) is not None or not TARGET.exists()
    desktop_icon = (DESKTOP / f"{OLD_NAME}.lnk").exists() or (DESKTOP / f"{APP_NAME}.lnk").exists()

    log("closing the running app")
    for exe in (f"{OLD_NAME}.exe", f"{APP_NAME}.exe"):
        subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True)
    time.sleep(2)

    log(f"copying files to {TARGET}")
    shutil.rmtree(TARGET / "app", ignore_errors=True)  # an update replaces the code wholesale
    shutil.copytree(STAGE / "runtime", TARGET / "runtime", dirs_exist_ok=True)
    shutil.copytree(STAGE / "app", TARGET / "app", dirs_exist_ok=True)
    shutil.copy2(STAGE / "deskspace.ico", ICON)
    shutil.copy2(Path(__file__).with_name("uninstall.py"), TARGET / "uninstall.py")

    log("shortcuts")
    shortcut(START_MENU / APP_NAME / f"{APP_NAME}.lnk", EXE, f'"{SCRIPT}"', TARGET / "app", ICON)
    shortcut(START_MENU / APP_NAME / f"Uninstall {APP_NAME}.lnk", EXE, f'"{TARGET / "uninstall.py"}"', TARGET)
    if desktop_icon:
        shortcut(DESKTOP / f"{APP_NAME}.lnk", EXE, f'"{SCRIPT}"', TARGET / "app", ICON)

    log("startup entry and uninstall entry")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if startup:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{EXE}" "{SCRIPT}"')
        try:
            winreg.DeleteValue(key, OLD_NAME)
        except OSError:
            pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{UNINSTALL_KEY}\{APP_NAME}") as key:
        size_kb = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file()) // 1024
        uninstall = f'"{EXE}" "{TARGET / "uninstall.py"}"'
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", APP_VERSION), ("Publisher", APP_NAME),
                            ("DisplayIcon", str(ICON)), ("InstallLocation", str(TARGET)),
                            ("UninstallString", uninstall), ("QuietUninstallString", uninstall + " /quiet")):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{UNINSTALL_KEY}\{OLD_INNO_ID}")
    except OSError:
        pass

    log("removing what's left of the old DeskFences install")
    shutil.rmtree(PROGRAMS / OLD_NAME, ignore_errors=True)
    shutil.rmtree(START_MENU / OLD_NAME, ignore_errors=True)
    (DESKTOP / f"{OLD_NAME}.lnk").unlink(missing_ok=True)

    log("starting DeskSpace")
    subprocess.Popen([str(EXE), str(SCRIPT)], cwd=TARGET / "app", close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS)
    log("installed")


if __name__ == "__main__":
    main()
