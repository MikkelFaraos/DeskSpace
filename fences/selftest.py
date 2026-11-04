"""`DeskSpace.exe --selftest <report file>`: checks a packaged build has everything it needs
(Qt, HTTPS, the WinRT media API, pywin32/COM, calendar parsing...) without starting the app.
Writes one line per check to the report and returns 0 when all pass."""
import importlib
import sys
import traceback


def run(report_path):
    results = []

    def check(name, func):
        try:
            detail = func()
            results.append(f"PASS {name}" + (f": {detail}" if detail else ""))
        except Exception:
            results.append(f"FAIL {name}: {traceback.format_exc().strip().splitlines()[-1]}")

    from PySide6.QtWidgets import QApplication
    app = QApplication([])

    def https():
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslSocket
        net = QNetworkAccessManager()
        reply = net.get(QNetworkRequest(QUrl("https://api.17track.net/")))
        loop = QEventLoop()
        reply.finished.connect(loop.quit)
        QTimer.singleShot(15000, loop.quit)
        loop.exec()
        if reply.error() in (QNetworkReply.SslHandshakeFailedError, QNetworkReply.UnknownNetworkError):
            raise RuntimeError(reply.errorString())
        return f"TLS backend {QSslSocket.activeBackend()}, HTTP {reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)}"

    def media():
        importlib.import_module("winrt.windows.media.control").GlobalSystemMediaTransportControlsSessionManager
        importlib.import_module("winrt.windows.storage.streams").DataReader

    def com():
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        win32com.client.Dispatch("ADODB.Connection")

    def calendar():
        from fences.calendar_feed import parse_events
        sample = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:1\r\nDTSTART:20300101T090000Z\r\n"
                  b"RRULE:FREQ=WEEKLY\r\nSUMMARY:Test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        return f"weekly event expands to {len(parse_events(sample, days=4000))} upcoming"

    def stats():
        from fences.sysinfo import GpuCounters
        return GpuCounters().name or "no GPU name"

    def launchers():
        from fences.launchers import installed
        return ", ".join(name for name, _ in installed()) or "none here"

    def updates():
        from fences.updates import read_release
        tag = {"tag_name": "v99.0", "assets": [{"name": "DeskSpace-Setup-99.0.exe", "browser_download_url":
               "https://github.com/MikkelFaraos/DeskSpace/releases/download/v99.0/DeskSpace-Setup-99.0.exe"}]}
        if not read_release(tag)["url"] or read_release({"tag_name": "v0.1"}):
            raise ValueError("version comparison")

    def modules():
        importlib.import_module("send2trash")
        importlib.import_module("fences.app")  # imports every feature module...
        for name in ("settings", "tidy", "cleanup", "console"):  # ...except the windows it opens on demand
            importlib.import_module(f"fences.{name}")

    checks = [("Qt + all DeskSpace modules", modules), ("HTTPS", https), ("Calendar parsing", calendar),
              ("Game launchers", launchers), ("Updates", updates)]
    if sys.platform == "win32":
        checks += [("Media session API", media), ("COM / Windows Search", com), ("GPU counters", stats)]
    for name, func in checks:
        check(name, func)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(results) + "\n")
    app.quit()
    return 0 if all(r.startswith("PASS") for r in results) else 1
