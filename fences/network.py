"""Network widget: live download/upload speed with a graph, ping, Wi-Fi network and signal, and your
local IP address. Samples once a second while a network widget is open."""
import ctypes
import re
import socket
import struct
import subprocess
import time
from collections import deque
from ctypes import wintypes

import psutil
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from .common import ACCENT
from .panel import Panel
from .sysinfo import Background, fmt_bytes
from .system import IS_WINDOWS, run_output
from .widgets import DIM, FAINT, WHITE, font

HISTORY = 60  # seconds of speed shown in the graph
UP_COLOR = QColor(120, 220, 150)


class ICMP_ECHO_REPLY(ctypes.Structure):
    _fields_ = [("Address", wintypes.ULONG), ("Status", wintypes.ULONG), ("RoundTripTime", wintypes.ULONG),
                ("DataSize", wintypes.USHORT), ("Reserved", wintypes.USHORT), ("Data", ctypes.c_void_p),
                ("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]


if IS_WINDOWS:
    _icmp = ctypes.windll.iphlpapi
    _icmp.IcmpCreateFile.restype = wintypes.HANDLE
    _icmp.IcmpSendEcho.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _icmp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]


def ping(host, timeout_ms=1500):
    """Round trip to `host` in milliseconds, or None if it didn't answer. Uses Windows' own ICMP API,
    so no ping.exe window and no admin rights."""
    if not IS_WINDOWS:
        found = re.search(r"time[=<]([\d.]+)", run_output("ping", "-c", "1", "-W", "2", host, timeout=4))
        return round(float(found.group(1))) if found else None
    address = struct.unpack("<I", socket.inet_aton(socket.gethostbyname(host)))[0]
    payload = b"DeskSpace ping!!"
    buf = ctypes.create_string_buffer(ctypes.sizeof(ICMP_ECHO_REPLY) + len(payload) + 8)
    handle = _icmp.IcmpCreateFile()
    try:
        if not _icmp.IcmpSendEcho(handle, address, payload, len(payload), None, buf, len(buf), timeout_ms):
            return None
        reply = ICMP_ECHO_REPLY.from_buffer(buf)
        return reply.RoundTripTime if reply.Status == 0 else None
    finally:
        _icmp.IcmpCloseHandle(handle)


def wifi_info():
    """{"ssid", "signal" (percent), "rate"} for the connected Wi-Fi network, or None when not on Wi-Fi."""
    if not IS_WINDOWS:
        for line in run_output("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,RATE", "dev", "wifi").splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[0] == "yes":
                return {"ssid": parts[1], "signal": int(parts[2]) if parts[2].isdigit() else None, "rate": parts[3]}
        return None
    out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=10,
                         creationflags=0x08000000).stdout  # no window
    fields = {}
    for line in out.splitlines():
        key, sep, value = line.partition(" : ")
        if sep:
            fields.setdefault(key.strip().lower(), value.strip())
    if fields.get("state", "").lower() != "connected":
        return None
    signal = re.match(r"(\d+)", fields.get("signal", ""))
    return {"ssid": fields.get("ssid", "Wi-Fi"), "signal": int(signal.group(1)) if signal else None,
            "rate": fields.get("receive rate (mbps)", "")}


def local_ip():
    """The address this PC uses to reach the internet (no packet is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("1.1.1.1", 53))
        return s.getsockname()[0]


class NetworkStats(QObject):
    updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self, interval=1000, timeout=self.sample)
        self.down = deque([0.0] * HISTORY, maxlen=HISTORY)
        self.up = deque([0.0] * HISTORY, maxlen=HISTORY)
        self.pings = deque(maxlen=20)  # None = no answer
        self.host = "1.1.1.1"
        self.ping = Background(lambda: ping(self.host), 3)
        self.wifi = Background(wifi_info, 10)
        self.ip = Background(local_ip, 30)
        self.last = None
        self.session = [0, 0]  # bytes down/up since the widget opened
        self.ping_seen = None

    def start(self):
        if not self.timer.isActive():
            self.last = None
            self.timer.start()
            self.sample()

    def sample(self):
        now, io = time.monotonic(), psutil.net_io_counters()
        if self.last:
            then, recv, sent = self.last
            elapsed = max(now - then, 0.001)
            self.down.append(max(0, io.bytes_recv - recv) / elapsed)
            self.up.append(max(0, io.bytes_sent - sent) / elapsed)
            self.session[0] += max(0, io.bytes_recv - recv)
            self.session[1] += max(0, io.bytes_sent - sent)
        self.last = (now, io.bytes_recv, io.bytes_sent)
        if not self.ping.busy and self.ping.next_run and self.ping_seen != self.ping.next_run:
            self.pings.append(self.ping.value)  # a ping finished since last time
            self.ping_seen = self.ping.next_run
        for job in (self.ping, self.wifi, self.ip):
            job.poll()
        self.updated.emit()

    def latest_ping(self):
        return self.pings[-1] if self.pings else None


def ping_color(ms):
    if ms is None:
        return QColor(240, 90, 90)
    return QColor(120, 220, 150) if ms < 40 else QColor(240, 200, 90) if ms < 100 else QColor(240, 130, 80)


class NetworkView(QWidget):
    def __init__(self, fence):
        super().__init__()
        self.fence = fence

    def paintEvent(self, _):
        stats = self.fence.app.netstats
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Big numbers
        half = (w - 16) / 2
        for i, (arrow, values, color) in enumerate((("↓", stats.down, ACCENT), ("↑", stats.up, UP_COLOR))):
            x = 8 + i * half
            p.setFont(font(12))
            p.setPen(color)
            p.drawText(QRectF(x, 2, half, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       f"{arrow} {'Download' if i == 0 else 'Upload'}")
            p.setFont(font(20, light=True))
            p.setPen(WHITE)
            p.drawText(QRectF(x, 20, half, 30), Qt.AlignLeft | Qt.AlignVCenter, fmt_bytes(values[-1], True))

        # Speed graph, both directions on one shared scale
        top, bottom = 56, max(110, h - 74)
        graph = QRectF(8, top, w - 16, bottom - top)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(graph, 6, 6)
        peak = max(max(stats.down), max(stats.up), 64 * 1024)
        for values, color in ((stats.down, ACCENT), (stats.up, UP_COLOR)):
            path = QPainterPath()
            for i, v in enumerate(values):
                pt = QPointF(graph.left() + graph.width() * i / (HISTORY - 1),
                             graph.bottom() - 3 - (graph.height() - 8) * v / peak)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            fill = QPainterPath(path)
            fill.lineTo(graph.bottomRight())
            fill.lineTo(graph.bottomLeft())
            fill.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
            p.drawPath(fill)
            p.setPen(QPen(color, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setFont(font(10))
        p.setPen(QColor(255, 255, 255, 110))
        p.drawText(graph.adjusted(6, 3, -6, -3), Qt.AlignRight | Qt.AlignTop, fmt_bytes(peak, True))
        p.drawText(graph.adjusted(6, 3, -6, -3), Qt.AlignLeft | Qt.AlignBottom, f"last {HISTORY} s")

        # Ping, with the last 20 results as little bars
        y = bottom + 8
        last = stats.latest_ping()
        p.setFont(font(12))
        p.setPen(DIM)
        p.drawText(QRectF(8, y, 80, 18), Qt.AlignLeft | Qt.AlignVCenter, "Ping")
        p.setFont(font(12, bold=True))
        p.setPen(ping_color(last) if stats.pings else DIM)
        text = "…" if not stats.pings else "no reply" if last is None else "<1 ms" if last == 0 else f"{last} ms"
        p.drawText(QRectF(44, y, 80, 18), Qt.AlignLeft | Qt.AlignVCenter, text)
        bars_left, bar_w = w - 8 - 20 * 5, 4
        for i, ms in enumerate(stats.pings):
            height = 16 if ms is None else 4 + min(12, ms / 10)
            p.setPen(Qt.NoPen)
            p.setBrush(ping_color(ms))
            p.drawRoundedRect(QRectF(bars_left + i * 5, y + 17 - height, bar_w, height), 1, 1)

        # Connection
        y += 24
        wifi = stats.wifi.value
        p.setFont(font(12))
        p.setPen(DIM)
        p.drawText(QRectF(8, y, 80, 18), Qt.AlignLeft | Qt.AlignVCenter, "Wi-Fi" if wifi else "Connection")
        p.setFont(font(12, bold=True))
        p.setPen(WHITE)
        if wifi:
            label = QFontMetrics(p.font()).elidedText(wifi["ssid"], Qt.ElideRight, int(w - 140))
            p.drawText(QRectF(80, y, w - 140, 18), Qt.AlignLeft | Qt.AlignVCenter, label)
            signal = wifi["signal"] or 0
            for i in range(4):  # signal bars
                lit = signal >= (i + 1) * 22
                p.setPen(Qt.NoPen)
                p.setBrush(WHITE if lit else FAINT)
                bh = 5 + i * 3
                p.drawRoundedRect(QRectF(w - 52 + i * 6, y + 16 - bh, 4, bh), 1, 1)
            p.setFont(font(11))
            p.setPen(DIM)
            p.drawText(QRectF(w - 28, y, 22, 18), Qt.AlignRight | Qt.AlignVCenter, f"{signal}%")
        else:
            p.drawText(QRectF(80, y, w - 88, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       "Cable" if stats.ip.value else "Offline")
        y += 22
        p.setFont(font(11))
        p.setPen(QColor(255, 255, 255, 110))
        line = (f"IP {stats.ip.value or '–'}   ·   session ↓ {fmt_bytes(stats.session[0])}  "
                f"↑ {fmt_bytes(stats.session[1])}")
        p.drawText(QRectF(8, y, w - 16, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(line, Qt.ElideMiddle, int(w - 16)))


class NetworkFence(Panel):
    kind = "network"
    default_size = (320, 280)
    drag_anywhere = True

    def build_content(self, data):
        self.view = NetworkView(self)
        self.app.netstats.host = data.get("ping_host", "1.1.1.1")
        self.app.netstats.updated.connect(lambda: self.update() if self.rolled else self.view.update())
        self.app.netstats.start()
        self.view.setToolTip("Ping goes to the address set on this widget's Settings page.")
        return self.view

    def content_dict(self):
        return {"ping_host": self.app.netstats.host}

    def rolled_title(self):
        s = self.app.netstats
        last = s.latest_ping()
        return (f"{self.name}  ·  ↓ {fmt_bytes(s.down[-1], True)}  ↑ {fmt_bytes(s.up[-1], True)}"
                + ("" if last is None else f"  ·  {last} ms"))

    def build_settings(self, form):
        host = QLineEdit(self.app.netstats.host)
        host.setPlaceholderText("1.1.1.1")
        host.editingFinished.connect(lambda: self.set_host(host.text().strip() or "1.1.1.1"))
        form.addRow("Ping", host)
        hint = QLabel("An address or name to measure your connection's delay against - for example 1.1.1.1, "
                      "8.8.8.8, your router (often 192.168.1.1), or a game server.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        form.addRow("", hint)

    def set_host(self, host):
        stats = self.app.netstats
        if host != stats.host:
            stats.host = host
            stats.pings.clear()
            stats.ping.next_run = 0
            self.app.schedule_save()
