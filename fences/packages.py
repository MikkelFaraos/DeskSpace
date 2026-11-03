"""Package tracker: keeps tracking numbers until the parcel is delivered.

The carrier is guessed from the number and its tracking page is one click away. Live status for
every carrier comes from 17TRACK when a (free) API key is added in Settings - Danish carriers
(PostNord, GLS, DAO, Bring) no longer offer open tracking APIs of their own.
"""
import json
import re
import time
from datetime import datetime

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSpinBox, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from . import dialogs
from .common import EDIT_STYLE, LIST_STYLE
from .panel import Panel
from .system import open_path

CARRIERS = {
    "postnord": ("PostNord", "https://www.postnord.dk/varktojer/track-trace?shipmentId={}"),
    "gls": ("GLS", "https://gls-group.com/DK/da/find-pakke?match={}"),
    "dao": ("DAO", "https://www.dao.as/privat/find-din-pakke?stregkode={}"),
    "bring": ("Bring", "https://tracking.bring.com/tracking/{}"),
    "dhl": ("DHL", "https://www.dhl.com/dk-da/home/tracking.html?tracking-id={}"),
    "ups": ("UPS", "https://www.ups.com/track?tracknum={}"),
    "fedex": ("FedEx", "https://www.fedex.com/fedextrack/?trknbr={}"),
    "other": ("Other", "https://t.17track.net/en#nums={}"),
}
STATUS_TEXT = {"NotFound": "Not found yet", "InfoReceived": "Label created", "InTransit": "On its way",
               "Expired": "Tracking expired", "AvailableForPickup": "Ready for pickup",
               "OutForDelivery": "Out for delivery", "DeliveryFailure": "Delivery failed",
               "Delivered": "Delivered", "Exception": "Problem with delivery"}
STATUS_COLOR = {"Delivered": QColor(90, 200, 120), "OutForDelivery": QColor(110, 165, 255),
                "AvailableForPickup": QColor(245, 180, 60), "DeliveryFailure": QColor(240, 90, 80),
                "Exception": QColor(240, 90, 80), "Expired": QColor(160, 160, 160)}
API = "https://api.17track.net/track/v2.2/"
ROLE_PKG = Qt.UserRole + 1


def guess_carrier(number):
    n = number.upper().replace(" ", "")
    if re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        return "ups"
    if re.fullmatch(r"[A-Z]{2}\d{9}(DK|SE|FI)", n) or re.fullmatch(r"(00)?3\d{17,19}", n):
        return "postnord"  # UPU numbers from the Nordic posts, and SSCC barcodes (00370...)
    if re.fullmatch(r"[A-Z]{2}\d{9}NO", n) or n.startswith("CS"):
        return "bring"
    if re.fullmatch(r"(JJD|JVGL|GM)\w+", n) or re.fullmatch(r"\d{10}", n):
        return "dhl"
    if re.fullmatch(r"\d{11,12}", n):
        return "gls"
    if re.fullmatch(r"\d{14}", n):
        return "dao"
    if re.fullmatch(r"\d{15}", n):
        return "fedex"
    return "other"


def tracking_url(pkg):
    return CARRIERS.get(pkg["carrier"], CARRIERS["other"])[1].format(pkg["number"])


class Tracker(QObject):
    """Talks to the 17TRACK API: registers new numbers, then fetches their latest status."""
    done = Signal()

    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net

    def post(self, endpoint, key, body, callback):
        req = QNetworkRequest(QUrl(API + endpoint))
        req.setRawHeader(b"17token", key.encode())
        req.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        reply = self.net.post(req, json.dumps(body).encode())

        def finished():
            reply.deleteLater()
            try:
                data = json.loads(bytes(reply.readAll()) or b"{}") if reply.error() == QNetworkReply.NoError else {}
            except ValueError:
                data = {}
            callback(data)
        reply.finished.connect(finished)

    def refresh(self, packages, key):
        active = [p for p in packages if not p.get("delivered_at")]
        if not key or not active:
            self.done.emit()
            return
        new = [p for p in active if not p.get("registered")]

        def fetch(_=None):
            self.post("gettrackinfo", key, [{"number": p["number"]} for p in active[:40]], apply)

        def registered(data):
            accepted = {a["number"] for a in data.get("data", {}).get("accepted", [])}
            # -18019901: already registered (e.g. by an earlier run) - that's fine too.
            accepted |= {r["number"] for r in data.get("data", {}).get("rejected", [])
                         if r.get("error", {}).get("code") == -18019901}
            for p in new:
                if p["number"] in accepted:
                    p["registered"] = True
            fetch()

        def apply(data):
            if data.get("code") not in (0, None) or "data" not in data:
                for p in active:
                    p["error"] = "Couldn't reach 17TRACK - check the API key in Settings"
            for entry in data.get("data", {}).get("accepted", []):
                pkg = next((p for p in active if p["number"] == entry.get("number")), None)
                info = entry.get("track_info") or {}
                if pkg is None:
                    continue
                pkg.pop("error", None)
                status = (info.get("latest_status") or {}).get("status") or "NotFound"
                event = info.get("latest_event") or {}
                pkg["status"] = status
                pkg["event"] = event.get("description") or ""
                pkg["event_time"] = event.get("time_iso") or ""
                if status == "Delivered" and not pkg.get("delivered_at"):
                    pkg["delivered_at"] = time.time()
            self.done.emit()

        if new:
            self.post("register", key, [{"number": p["number"]} for p in new], registered)
        else:
            fetch()


class PackageDelegate(QStyledItemDelegate):
    def paint(self, p, option, index):
        pkg = index.data(ROLE_PKG)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(2, 2, -2, -2)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(r, 6, 6)
        status = pkg.get("status", "")
        color = STATUS_COLOR.get(status, QColor(255, 255, 255, 170))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(r.left() + 6, r.top() + 10, 9, 9)
        title = QFont(option.font)
        title.setPointSizeF(10)
        title.setBold(True)
        carrier = CARRIERS.get(pkg["carrier"], CARRIERS["other"])[0]
        small = QFont(option.font)
        small.setPointSizeF(8.5)
        cw = QFontMetrics(small).horizontalAdvance(carrier) + 4
        p.setFont(title)
        p.setPen(QColor(255, 255, 255, 240) if not pkg.get("delivered_at") else QColor(255, 255, 255, 140))
        label = pkg.get("label") or pkg["number"]
        p.drawText(QRect(r.left() + 22, r.top() + 2, r.width() - 26 - cw, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(title).elidedText(label, Qt.ElideRight, r.width() - 30 - cw))
        p.setFont(small)
        p.setPen(QColor(255, 255, 255, 120))
        p.drawText(QRect(r.right() - cw - 4, r.top() + 2, cw, 22), Qt.AlignRight | Qt.AlignVCenter, carrier)
        p.setPen(color if status else QColor(255, 255, 255, 140))
        p.drawText(QRect(r.left() + 22, r.top() + 22, r.width() - 26, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(small).elidedText(index.data(Qt.DisplayRole), Qt.ElideRight, r.width() - 30))
        p.restore()

    def sizeHint(self, option, index):
        return QSize(0, 46)


class PackageFence(Panel):
    kind = "packages"
    default_size = (320, 260)

    def build_content(self, data):
        self.packages = data.get("packages", [])
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setStyleSheet(LIST_STYLE)
        self.list.setItemDelegate(PackageDelegate())
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(lambda it: self.open_page(it.data(ROLE_PKG)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.item_menu)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tracking number (and a name), then Enter")
        self.input.setStyleSheet(EDIT_STYLE)
        self.input.returnPressed.connect(self.add_from_input)
        layout.addWidget(self.list)
        layout.addWidget(self.input)

        self.tracker = Tracker(self.app.net, self)
        self.tracker.done.connect(self.on_tracked)
        self.timer = QTimer(self, interval=30 * 60 * 1000, timeout=self.refresh)
        self.timer.start()
        self.populate()
        QTimer.singleShot(2000, self.refresh)
        return box

    def content_dict(self):
        return {"packages": self.packages}

    def rolled_title(self):
        waiting = sum(not p.get("delivered_at") for p in self.packages)
        return f"{self.name}  ({waiting} on the way)"

    # -- data

    def add_from_input(self):
        text = self.input.text().strip()
        if not text:
            return
        number, _, label = text.partition(" ")
        number = number.strip().upper()
        if any(p["number"] == number for p in self.packages):
            self.input.clear()
            return
        self.packages.append({"number": number, "label": label.strip(), "carrier": guess_carrier(number),
                              "added": time.time()})
        self.input.clear()
        self.populate()
        self.app.schedule_save()
        self.refresh()

    def refresh(self):
        self.tracker.refresh(self.packages, self.app.track_key)

    def on_tracked(self):
        # Delivered parcels stay for a while so you see it happened, then clear themselves.
        keep = self.app.keep_delivered_days * 86400
        self.packages = [p for p in self.packages if not p.get("delivered_at") or time.time() - p["delivered_at"] < keep]
        self.populate()
        self.app.schedule_save()

    def status_line(self, pkg):
        if pkg.get("delivered_at"):
            when = datetime.fromtimestamp(pkg["delivered_at"]).strftime("%d/%m")
            return f"Delivered {when}" + (f" · {pkg['event']}" if pkg.get("event") else "")
        if pkg.get("error"):
            return pkg["error"]
        if pkg.get("status"):
            text = STATUS_TEXT.get(pkg["status"], pkg["status"])
            return text + (f" · {pkg['event']}" if pkg.get("event") else "")
        if not self.app.track_key:
            return "Double-click to open the tracking page"
        return "Checking…"

    def populate(self):
        self.list.clear()
        order = sorted(self.packages, key=lambda p: (bool(p.get("delivered_at")), -p.get("added", 0)))
        for pkg in order:
            item = QListWidgetItem(self.status_line(pkg))
            item.setData(ROLE_PKG, pkg)
            item.setToolTip(f"{pkg['number']}\n{tracking_url(pkg)}")
            self.list.addItem(item)
        self.update()

    # -- actions

    def open_page(self, pkg):
        open_path(tracking_url(pkg))

    def item_menu(self, pos):
        item = self.list.itemAt(pos)
        menu = QMenu()
        if item:
            pkg = item.data(ROLE_PKG)
            menu.addAction("Open tracking page", lambda: self.open_page(pkg))
            menu.addAction("Copy tracking number", lambda: QGuiApplication.clipboard().setText(pkg["number"]))
            menu.addAction("Rename…", lambda: self.rename_package(pkg))
            carriers = menu.addMenu("Carrier")
            for key, (label, _) in CARRIERS.items():
                action = carriers.addAction(label, lambda k=key: self.set_carrier(pkg, k))
                action.setCheckable(True)
                action.setChecked(pkg["carrier"] == key)
            if pkg.get("delivered_at"):
                menu.addAction("Mark as not delivered", lambda: self.set_delivered(pkg, False))
            else:
                menu.addAction("Mark as delivered", lambda: self.set_delivered(pkg, True))
            menu.addAction("Remove", lambda: self.remove_package(pkg))
            menu.addSeparator()
        menu.addAction("Refresh now", self.refresh)
        self.show_menu(self.list.mapToGlobal(pos), menu)

    def find(self, pkg):
        return next(p for p in self.packages if p["number"] == pkg["number"])

    def rename_package(self, pkg):
        label, ok = dialogs.get_text("Name for this parcel:", pkg.get("label", ""))
        if ok:
            self.find(pkg)["label"] = label.strip()
            self.populate()
            self.app.schedule_save()

    def set_carrier(self, pkg, carrier):
        self.find(pkg)["carrier"] = carrier
        self.populate()
        self.app.schedule_save()

    def set_delivered(self, pkg, delivered):
        p = self.find(pkg)
        if delivered:
            p["delivered_at"] = time.time()
        else:
            p.pop("delivered_at", None)
        self.populate()
        self.app.schedule_save()

    def remove_package(self, pkg):
        self.packages = [p for p in self.packages if p["number"] != pkg["number"]]
        self.populate()
        self.app.schedule_save()

    # -- settings

    def build_settings(self, form):
        key = QLineEdit(self.app.track_key)
        key.setPlaceholderText("Paste your 17TRACK API key")
        key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        key.editingFinished.connect(lambda: self.set_key(key.text()))
        form.addRow("17TRACK API key", key)
        how = QLabel('Live status for all carriers (PostNord, GLS, DAO, Bring, DHL, UPS…). The free plan is '
                     'plenty for personal use: sign up at <a href="https://api.17track.net">api.17track.net</a>, '
                     'then copy the key from Settings → Security. Without a key, parcels are still saved and '
                     'double-clicking one opens the carrier\'s tracking page.')
        how.setWordWrap(True)
        how.setOpenExternalLinks(True)
        form.addRow("", how)
        keep = QSpinBox()
        keep.setRange(0, 30)
        keep.setSuffix(" day(s)")
        keep.setValue(self.app.keep_delivered_days)
        keep.valueChanged.connect(lambda v: (self.app.set_option("keep_delivered_days", v), self.on_tracked()))
        form.addRow("Keep delivered for", keep)
        form.addRow("", QPushButton("Refresh now", clicked=self.refresh))

    def set_key(self, key):
        if key.strip() != self.app.track_key:
            self.app.set_option("track_key", key.strip())
            for p in self.packages:
                p.pop("error", None)
            self.refresh()
