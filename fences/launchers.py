"""Find the game launchers installed on this PC, for Console mode's System page."""
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import gog, steam
from .system import IS_WINDOWS, open_path

if IS_WINDOWS:
    import winreg

# Installing one of these registers a link type, and that link's command says where the program is.
# The third value is for launchers whose link goes to a small helper next to the real program.
WINDOWS_LAUNCHERS = (("Epic Games", "com.epicgames.launcher", None), ("EA app", "link2ea", "EADesktop.exe"),
                     ("Ubisoft Connect", "uplay", "UbisoftConnect.exe"), ("Battle.net", "battlenet", None),
                     ("Amazon Games", "amazon-games", None), ("itch", "itchio", None),
                     ("Heroic", "heroic", None), ("Playnite", "playnite", None))
XBOX_PACKAGE = "Microsoft.GamingApp_8wekyb3d8bbwe"
XBOX_APP = rf"shell:AppsFolder\{XBOX_PACKAGE}!Microsoft.Xbox.App"
LINUX_LAUNCHERS = (("Lutris", "lutris"), ("Heroic", "heroic"), ("Bottles", "bottles"))


def _link_program(scheme):
    """The program a link type opens, or None."""
    try:
        command = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command")
    except OSError:
        return None
    found = re.match(r'\s*"([^"]+)"|\s*(\S+)', command)
    path = Path(found.group(1) or found.group(2)) if found else None
    return path if path and path.is_file() else None


def _windows_launchers():
    found = [("Steam", steam.steam_exe()), ("GOG Galaxy", gog.galaxy_exe())]
    for name, scheme, real_name in WINDOWS_LAUNCHERS:
        program = _link_program(scheme)
        if program and real_name and (program.parent / real_name).is_file():
            program = program.parent / real_name
        found.append((name, program))
    if (Path(os.environ.get("LOCALAPPDATA", "")) / "Packages" / XBOX_PACKAGE).is_dir():
        found.append(("Xbox", XBOX_APP))
    return found


def _linux_launchers():
    return [("Steam", steam.steam_exe())] + [(name, shutil.which(command)) for name, command in LINUX_LAUNCHERS]


def installed():
    """[(name, program)] for the launchers on this PC, ready for start()."""
    found = _windows_launchers() if IS_WINDOWS else _linux_launchers()
    return [(name, str(program)) for name, program in found if program]


def start(program):
    if program.startswith("shell:"):  # store apps have no program file of their own
        open_path(program)
    else:
        subprocess.Popen([program], cwd=Path(program).parent if IS_WINDOWS else None)
