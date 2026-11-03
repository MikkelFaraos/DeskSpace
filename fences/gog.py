"""Find installed GOG games and their cover art.

Both GOG Galaxy and GOG's offline installers register each game under
HKLM\\SOFTWARE\\WOW6432Node\\GOG.com\\Games and leave a goggame-<id>.info file in the game folder.
Shortcuts DeskSpace makes start the game through GOG Galaxy when it's installed (so play time and
cloud saves work), otherwise the game's own exe. Covers come from GOG's games database.
"""
import json
import re
import winreg
from pathlib import Path

from .common import COVER_CACHE

GAMES_KEY = r"SOFTWARE\WOW6432Node\GOG.com\Games"
GALAXY_KEY = r"SOFTWARE\WOW6432Node\GOG.com\GalaxyClient\paths"
GAMESDB = "https://gamesdb.gog.com/platforms/gog/external_releases/{}"
DESCRIPTION = "GOG game {}"  # stored in the shortcuts we make, so we know which game it is


def _value(key, name):
    try:
        return str(winreg.QueryValueEx(key, name)[0]).strip()
    except OSError:
        return ""


def galaxy_exe():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GALAXY_KEY) as key:
            exe = Path(_value(key, "client")) / "GalaxyClient.exe"
        return exe if exe.is_file() else None
    except OSError:
        return None


def _primary_task(folder, game_id):
    """The main "play" entry from the game's goggame-<id>.info: (exe, arguments, working dir)."""
    try:
        info = json.loads((Path(folder) / f"goggame-{game_id}.info").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    for task in info.get("playTasks", []):
        if task.get("isPrimary") and task.get("type") == "FileTask" and task.get("path"):
            exe = Path(folder) / task["path"]
            work = Path(folder) / task["workingDir"] if task.get("workingDir") else exe.parent
            return exe, task.get("arguments", ""), work
    return None


def installed_games():
    """[{"id", "name", "folder", "exe", "args", "workdir"}] for installed GOG games, sorted by name."""
    games = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, GAMES_KEY)
    except OSError:
        return []
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(root, sub) as key:
                game_id = _value(key, "gameID") or sub
                folder = _value(key, "path")
                if _value(key, "dependsOn") or not folder or not Path(folder).is_dir():  # DLC, or uninstalled
                    continue
                task = _primary_task(folder, game_id)
                if task:
                    exe, args, work = task
                else:
                    exe = Path(_value(key, "exe"))
                    args, work = _value(key, "launchParam"), Path(_value(key, "workingDir") or exe.parent)
                if not exe.is_file():
                    continue
                games[game_id] = {"id": game_id, "name": _value(key, "gameName") or _value(key, "startMenu") or sub,
                                  "folder": Path(folder), "exe": exe, "args": args, "workdir": work}
    return sorted(games.values(), key=lambda g: g["name"].lower())


def _shell_link():
    import pythoncom
    from win32com.shell import shell
    return pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)


def make_shortcut(path, game):
    """A .lnk that starts the game (through GOG Galaxy if it's installed)."""
    import pythoncom
    link = _shell_link()
    galaxy = galaxy_exe()
    if galaxy:
        link.SetPath(str(galaxy))
        link.SetArguments(f'/command=runGame /gameId={game["id"]} /path="{game["folder"]}"')
        link.SetWorkingDirectory(str(galaxy.parent))
    else:
        link.SetPath(str(game["exe"]))
        link.SetArguments(game["args"])
        link.SetWorkingDirectory(str(game["workdir"]))
    link.SetIconLocation(str(game["exe"]), 0)
    link.SetDescription(DESCRIPTION.format(game["id"]))
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)


_ids = {}  # shortcut path -> (modified time, game id or None)


def id_from_shortcut(path):
    """The GOG game id a .lnk starts, or None. Recognises our own shortcuts, GOG Galaxy's
    ("/gameId=..."), and plain shortcuts to an exe in a GOG game folder (the offline installers' kind)."""
    if path.suffix.lower() != ".lnk":
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _ids.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    game_id = None
    try:
        import pythoncom
        link = _shell_link()
        link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
        found = (re.search(r"GOG game (\d+)", link.GetDescription())
                 or re.search(r"/gameId=(\d+)", link.GetArguments(), re.I))
        if found:
            game_id = found.group(1)
        else:
            target = Path(link.GetPath(0)[0])
            for folder in list(target.parents)[:3]:
                info = next(folder.glob("goggame-*.info"), None) if folder.is_dir() else None
                if info:
                    game_id = re.search(r"goggame-(\d+)", info.name).group(1)
                    break
    except Exception:  # not a readable shortcut
        game_id = None
    _ids[str(path)] = (mtime, game_id)
    return game_id


def cover_file(game_id):
    path = COVER_CACHE / f"gog_{game_id}.jpg"
    return path if path.exists() else None


def cover_url(gamesdb_reply):
    """The portrait cover image URL from a GOG games database reply, or None."""
    try:
        game = json.loads(gamesdb_reply)["game"]
        fmt = (game.get("vertical_cover") or game.get("cover") or {})["url_format"]
    except (ValueError, KeyError, TypeError):
        return None
    return fmt.replace("{formatter}", "").replace("{ext}", "jpg")
