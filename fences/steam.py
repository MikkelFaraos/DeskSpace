"""Find installed Steam games and their cover art."""
import re
from pathlib import Path

from .common import COVER_CACHE
from .system import IS_WINDOWS

if IS_WINDOWS:
    import winreg

# Steam installs these alongside games; they aren't things you launch.
NOT_GAMES = {"228980"}  # Steamworks Common Redistributables
NOT_GAME_WORDS = ("redistributable", "runtime", "proton", "steamvr")
CDN_COVER = "https://shared.cloudflare.steamstatic.com/store_item_assets/steam/apps/{}/library_600x900.jpg"


LINUX_ROOTS = (".steam/steam", ".local/share/Steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam",
               "snap/steam/common/.local/share/Steam")


def steam_root():
    if not IS_WINDOWS:
        return next((Path.home() / r for r in LINUX_ROOTS if (Path.home() / r / "steamapps").is_dir()), None)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        return path if path.is_dir() else None
    except OSError:
        return None


def steam_exe():
    root = steam_root()
    if not IS_WINDOWS:
        return "steam" if root else None
    return root / "steam.exe" if root else None


def library_dirs():
    root = steam_root()
    if not root:
        return []
    dirs = [root / "steamapps"]
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        for path in re.findall(r'"path"\s+"(.+?)"', text):
            d = Path(path.replace("\\\\", "\\")) / "steamapps"
            if d.is_dir() and d not in dirs:
                dirs.append(d)
    except OSError:
        pass
    return dirs


def installed_games():
    """[(appid, name)] of installed Steam games, sorted by name."""
    return [(appid, name) for appid, name, _ in library()]


def running_game():
    """The app id Steam is running right now, or None."""
    if not IS_WINDOWS:
        try:
            text = (Path.home() / ".steam" / "registry.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        found = re.search(r'"RunningAppID"\s+"(\d+)"', text)
        return found.group(1) if found and found.group(1) != "0" else None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return str(winreg.QueryValueEx(key, "RunningAppID")[0] or "") or None
    except OSError:
        return None


def library():
    """[(appid, name, last played time)] of installed Steam games, sorted by name."""
    games = {}
    for lib in library_dirs():
        for manifest in lib.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"(.+?)"', text)
            if not appid or not name:
                continue
            appid, name = appid.group(1), name.group(1)
            if appid in NOT_GAMES or any(w in name.lower() for w in NOT_GAME_WORDS):
                continue
            played = re.search(r'"LastPlayed"\s+"(\d+)"', text)
            games[appid] = (appid, name, int(played.group(1)) if played else 0)
    return sorted(games.values(), key=lambda g: g[1].lower())


def appid_from_shortcut(path):
    """The Steam app id a .url shortcut launches (steam://rungameid/<id>), or None."""
    if path.suffix.lower() != ".url":
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"steam://rungameid/(\d+)", text, re.I)
    return match.group(1) if match else None


def _cached(appid, *patterns):
    root = steam_root()
    folder = root / "appcache" / "librarycache" / appid if root else None
    if folder and folder.is_dir():
        for pattern in patterns:
            found = next(folder.rglob(pattern), None)
            if found:
                return found
    return None


def cover_file(appid):
    """Portrait (600x900) cover art: Steam's own cache first, then ours.
    Older Steam clients call it library_600x900.jpg, newer ones library_capsule.jpg in a hashed subfolder."""
    found = _cached(appid, "library_600x900*.jpg", "library_capsule.jpg")
    if found:
        return found
    ours = COVER_CACHE / f"{appid}.jpg"
    return ours if ours.exists() else None


def header_file(appid):
    """Landscape header image, used while a portrait cover isn't available."""
    return _cached(appid, "header.jpg", "library_header.jpg")
