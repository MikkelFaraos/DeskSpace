"""Checks GitHub for a newer DeskSpace and, on an installed Windows copy, installs it.

The check asks GitHub for the latest release and sends nothing about you. Pre-releases don't count.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from .common import APP_NAME, APP_VERSION
from .dialogs import ask, tell
from .system import IS_WINDOWS, open_path

REPO = "MikkelFaraos/DeskSpace"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"
SETUP_NAME = re.compile(r"DeskSpace-Setup-[\d.]+\.exe$")
CHECK_EVERY = 24 * 60 * 60 * 1000
FIRST_CHECK = 60000
NO_WINDOW, DETACHED = 0x08000000, 0x00000008


def version_numbers(text):
    return tuple(int(n) for n in re.findall(r"\d+", text))


def read_release(data):
    """The newer version in a GitHub release, as {"version", "page", "url", "sha256"}, or None."""
    version = str(data.get("tag_name", "")).lstrip("v")
    if not version_numbers(version) or version_numbers(version) <= version_numbers(APP_VERSION):
        return None
    release = {"version": version, "page": data.get("html_url", f"https://github.com/{REPO}/releases"),
               "url": None, "sha256": None}
    for asset in data.get("assets", []):
        url = asset.get("browser_download_url", "")
        if SETUP_NAME.search(asset.get("name", "")) and url.startswith(DOWNLOADS):
            digest = asset.get("digest") or ""
            release.update(url=url, sha256=digest.split(":", 1)[1] if digest.startswith("sha256:") else None)
    return release


def installed_copy():
    """The running install's own program, or None when run from source or on Linux."""
    exe = Path(sys.executable)
    return exe if IS_WINDOWS and exe.name.lower() == "deskspace.exe" else None


def install_command(setup, exe, script):
    """Waits for us to quit, runs the setup, and restarts this copy if the setup was blocked or failed.
    The setup restarts the new version itself."""
    return (f'cmd /s /c "ping -n 3 127.0.0.1 >nul & "{setup}" /SILENT || start "" "{exe}" "{script}""')


class Updater(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.available = None
        self.timer = QTimer(self, interval=CHECK_EVERY, timeout=self.check)
        self.timer.start()
        QTimer.singleShot(FIRST_CHECK, self.check)

    def check(self, quiet=True):
        if quiet and not self.app.update_check:
            return
        request = QNetworkRequest(QUrl(LATEST))
        request.setRawHeader(b"User-Agent", APP_NAME.encode())
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setTransferTimeout(30000)
        reply = self.app.net.get(request)
        reply.finished.connect(lambda: self._checked(reply, quiet))

    def _checked(self, reply, quiet):
        reply.deleteLater()
        try:
            if reply.error() != QNetworkReply.NoError:
                raise ValueError(reply.errorString())
            release = read_release(json.loads(bytes(reply.readAll())))
        except ValueError as e:
            if not quiet:
                tell(f"Couldn't check for updates.\n\n{e}", warning=True)
            return
        if not release:
            if not quiet:
                tell(f"{APP_NAME} {APP_VERSION} is the latest version.")
            return
        known = self.available and self.available["version"] == release["version"]
        self.available = release
        self.app.update_action.setText(f"Install update {release['version']}…")
        self.app.update_action.setVisible(True)
        if not quiet:
            self.install()
        elif not known:
            self.app.tray.showMessage(APP_NAME, f"Version {release['version']} is available. Click to install it.")

    def install(self):
        release = self.available
        if not release:
            return
        exe = installed_copy()
        if not exe or not release["url"]:
            open_path(release["page"])  # Linux, or running from source: nothing here we can replace
            return
        if ask(f"Install {APP_NAME} {release['version']} now?\n\n{APP_NAME} closes while it updates and then "
               "starts again. Your fences and settings are kept."):
            request = QNetworkRequest(QUrl(release["url"]))
            request.setRawHeader(b"User-Agent", APP_NAME.encode())
            reply = self.app.net.get(request)
            reply.finished.connect(lambda: self._downloaded(reply, release, exe))

    def _downloaded(self, reply, release, exe):
        reply.deleteLater()
        data = bytes(reply.readAll())
        if reply.error() != QNetworkReply.NoError or not data:
            tell(f"The download failed.\n\n{reply.errorString()}", warning=True)
            return
        if release["sha256"] and hashlib.sha256(data).hexdigest() != release["sha256"].lower():
            tell("The download was damaged, so it wasn't installed.", warning=True)
            return
        setup = Path(tempfile.gettempdir()) / f"DeskSpace-Setup-{release['version']}.exe"
        try:
            setup.write_bytes(data)
            subprocess.Popen(install_command(setup, exe, Path(sys.argv[0]).resolve()),
                             creationflags=NO_WINDOW | DETACHED)
        except OSError:
            open_path(release["page"])
            return
        self.app.quit()
