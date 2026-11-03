"""Desktop widgets that live in a fence: sticky note, to-do list, clock & calendar, system monitor."""
import calendar
from datetime import datetime

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from .calendar_feed import CalendarFeed
from .common import ACCENT, EDIT_STYLE, LIST_STYLE, SCROLLBAR_STYLE, load_color
from .panel import Panel

WHITE = QColor(255, 255, 255, 235)
DIM = QColor(255, 255, 255, 140)
FAINT = QColor(255, 255, 255, 30)
TEXT_SIZES = [("Small", 9), ("Medium", 11), ("Large", 14), ("Extra large", 18)]


def dim_placeholder(widget):
    pal = widget.palette()
    pal.setColor(QPalette.PlaceholderText, QColor(255, 255, 255, 100))
    widget.setPalette(pal)


def font(size, bold=False, light=False):
    f = QFont()
    f.setPixelSize(max(1, int(size)))
    f.setBold(bold)
    if light:
        f.setFamily("Segoe UI Light")
    return f


# ---------------------------------------------------------------- sticky note

class NoteFence(Panel):
    kind = "note"
    default_size = (280, 220)

    def build_content(self, data):
        self.edit = QPlainTextEdit()
        self.edit.setPlainText(data.get("text", ""))
        self.edit.setPlaceholderText("Type a note…")
        self.edit.setStyleSheet(EDIT_STYLE)
        self.edit.setFrameShape(QPlainTextEdit.NoFrame)
        dim_placeholder(self.edit)
        self.set_text_size(data.get("text_size", 11), save=False)
        self.edit.textChanged.connect(self.app.schedule_save)
        self.edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.edit.customContextMenuRequested.connect(
            lambda pos: self.show_menu(self.edit.mapToGlobal(pos), self.edit.createStandardContextMenu()))
        return self.edit

    def content_dict(self):
        return {"text": self.edit.toPlainText(), "text_size": self.text_size}

    def rolled_title(self):
        first = self.edit.toPlainText().strip().splitlines()
        return f"{self.name}  ·  {first[0]}" if first else self.name

    def set_text_size(self, size, save=True):
        self.text_size = size
        f = self.edit.font()
        f.setPointSizeF(size)
        self.edit.setFont(f)
        if save:
            self.app.schedule_save()

    def build_settings(self, form):
        sizes = QComboBox()
        for label, size in TEXT_SIZES:
            sizes.addItem(label, size)
        sizes.setCurrentIndex(max(0, sizes.findData(self.text_size)))
        sizes.currentIndexChanged.connect(lambda: self.set_text_size(sizes.currentData()))
        form.addRow("Text size", sizes)


# ---------------------------------------------------------------- to-do list

class TodoFence(Panel):
    kind = "todo"
    default_size = (280, 260)

    def build_content(self, data):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(6)
        self.items = QListWidget()
        self.items.setStyleSheet(LIST_STYLE + "QListWidget::item { padding: 3px 2px; }")
        self.items.setFrameShape(QListWidget.NoFrame)
        self.items.setDragDropMode(QListWidget.InternalMove)  # drag to reorder
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.items.setWordWrap(True)
        for task in data.get("items", []):
            self.add_task(task["text"], task.get("done", False))
        self.items.itemChanged.connect(self.on_changed)
        self.items.model().rowsMoved.connect(lambda *_: self.app.schedule_save())
        self.items.setContextMenuPolicy(Qt.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self.item_menu)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a task and press Enter")
        self.input.setStyleSheet(EDIT_STYLE)
        dim_placeholder(self.input)
        self.input.returnPressed.connect(self.add_from_input)
        layout.addWidget(self.items)
        layout.addWidget(self.input)
        return box

    def content_dict(self):
        return {"items": [{"text": it.text(), "done": it.checkState() == Qt.Checked} for it in self.all_items()]}

    def all_items(self):
        return [self.items.item(i) for i in range(self.items.count())]

    def rolled_title(self):
        left = sum(it.checkState() != Qt.Checked for it in self.all_items())
        return f"{self.name}  ({left} left)"

    def add_task(self, text, done=False):
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
        item.setCheckState(Qt.Checked if done else Qt.Unchecked)
        self.items.addItem(item)
        self.style_item(item)

    def style_item(self, item):
        done = item.checkState() == Qt.Checked
        f = item.font()
        f.setStrikeOut(done)
        self.items.blockSignals(True)  # restyling fires itemChanged again
        item.setFont(f)
        item.setForeground(QColor(255, 255, 255, 110 if done else 235))
        self.items.blockSignals(False)

    def on_changed(self, item):
        if not item.text().strip():
            self.items.takeItem(self.items.row(item))
        else:
            self.style_item(item)
        self.update()
        self.app.schedule_save()

    def add_from_input(self):
        text = self.input.text().strip()
        if text:
            self.add_task(text)
            self.input.clear()
            self.items.scrollToBottom()
            self.app.schedule_save()

    def remove_items(self, items):
        for item in items:
            self.items.takeItem(self.items.row(item))
        self.update()
        self.app.schedule_save()

    def item_menu(self, pos):
        menu = QMenu()
        item = self.items.itemAt(pos)
        if item:
            menu.addAction("Edit", lambda: self.items.editItem(item))
            menu.addAction("Delete", lambda: self.remove_items([item]))
            menu.addSeparator()
        self.show_menu(self.items.mapToGlobal(pos), menu)

    def build_settings(self, form):
        done = [it for it in self.all_items() if it.checkState() == Qt.Checked]
        clear = QPushButton(f"Clear completed tasks ({len(done)})")
        clear.setEnabled(bool(done))
        clear.clicked.connect(lambda: (self.remove_items(done), clear.setText("Clear completed tasks (0)"),
                                       clear.setEnabled(False)))
        form.addRow("Tasks", clear)


# ---------------------------------------------------------------- clock & calendar

class ClockFace(QWidget):
    def __init__(self, fence):
        super().__init__()
        self.fence = fence

    def paintEvent(self, _):
        c = self.fence
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        now = datetime.now()

        # Time, as large as the width allows.
        fmt = ("%H:%M" if c.h24 else "%I:%M") + (":%S" if c.seconds else "")
        text = now.strftime(fmt) if c.h24 else now.strftime(fmt).lstrip("0")
        tf = font(100, light=True)
        width = QFontMetrics(tf).horizontalAdvance(text)
        tf = font(min(100 * w * 0.86 / max(width, 1), h * 0.32, 96), light=True)
        th = QFontMetrics(tf).height()
        p.setFont(tf)
        p.setPen(WHITE)
        p.drawText(QRectF(0, 0, w, th), Qt.AlignCenter, text + ("" if c.h24 else now.strftime(" %p")))
        y = th

        week = now.isocalendar()[1]
        p.setFont(font(14))
        p.setPen(DIM)
        p.drawText(QRectF(0, y, w, 22), Qt.AlignCenter, f"{now:%A} {now.day} {now:%B}  ·  Week {week}")
        y += 32

        event_days = {start.date() for start, _, _ in c.feed.events}
        if c.show_month and h - y > 150:
            y = self.paint_month(p, now.date(), y, w, event_days)
        self.paint_events(p, now, y + 6, w, h)

    def paint_month(self, p, today, y, w, event_days):
        cell = min(w / 7, 30)
        left = (w - cell * 7) / 2
        p.setFont(font(11, bold=True))
        p.setPen(DIM)
        for i, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            p.drawText(QRectF(left + i * cell, y, cell, 18), Qt.AlignCenter, name)
        y += 20
        p.setFont(font(12))
        for week in calendar.Calendar(0).monthdatescalendar(today.year, today.month):
            for i, day in enumerate(week):
                rect = QRectF(left + i * cell, y, cell, cell * 0.8)
                if day == today:
                    p.setPen(Qt.NoPen)
                    p.setBrush(ACCENT)
                    d = min(rect.width(), rect.height()) - 2
                    p.drawEllipse(QRectF(rect.center().x() - d / 2, rect.center().y() - d / 2, d, d))
                p.setPen(WHITE if day.month == today.month else QColor(255, 255, 255, 60))
                p.drawText(rect, Qt.AlignCenter, str(day.day))
                if day in event_days and day != today:
                    p.setPen(Qt.NoPen)
                    p.setBrush(ACCENT)
                    p.drawEllipse(QRectF(rect.center().x() - 2, rect.bottom() - 4, 4, 4))
            y += cell * 0.8
        return y

    def paint_events(self, p, now, y, w, h):
        c = self.fence
        if not c.feed.url:
            p.setFont(font(11))
            p.setPen(QColor(255, 255, 255, 90))
            p.drawText(QRectF(8, y, w - 16, 40), Qt.AlignCenter | Qt.TextWordWrap,
                       "Add a calendar link in Settings to show your upcoming events")
            return
        if c.feed.error or not c.feed.events:
            p.setFont(font(11))
            p.setPen(QColor(255, 255, 255, 90))
            p.drawText(QRectF(8, y, w - 16, 20), Qt.AlignCenter, c.feed.error or "Nothing coming up")
            return
        p.setPen(FAINT)
        p.drawLine(int(10), int(y), int(w - 10), int(y))
        y += 8
        row = 22
        today = now.date()
        for start, all_day, title in c.feed.events:
            if y + row > h:
                break
            day = start.date()
            when = "Today" if day == today else "Tomorrow" if (day - today).days == 1 else f"{start:%a} {day.day}/{day.month}"
            when += "" if all_day else f" {start:%H:%M}" if c.h24 else f" {start:%I:%M %p}".replace(" 0", " ")
            p.setFont(font(12, bold=True))
            p.setPen(ACCENT if day == today else DIM)
            p.drawText(QRectF(10, y, 120, row), Qt.AlignLeft | Qt.AlignVCenter, when)
            p.setFont(font(12))
            p.setPen(WHITE)
            p.drawText(QRectF(130, y, w - 140, row), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(p.font()).elidedText(title, Qt.ElideRight, int(w - 140)))
            y += row


class ClockFence(Panel):
    kind = "clock"
    default_size = (300, 380)
    drag_anywhere = True

    def build_content(self, data):
        self.h24 = data.get("h24", True)
        self.seconds = data.get("seconds", False)
        self.show_month = data.get("month", True)
        self.face = ClockFace(self)
        self.feed = CalendarFeed(self.app.net, self)
        self.feed.updated.connect(self.face.update)
        self.tick = QTimer(self, interval=1000, timeout=self.on_tick)
        self.tick.start()
        self.refresh_feed = QTimer(self, interval=15 * 60 * 1000, timeout=lambda: self.feed.load(self.feed.url))
        self.refresh_feed.start()
        self.feed.load(data.get("ics_url", ""))
        return self.face

    def on_tick(self):
        if self.rolled:
            self.update()  # the time is in the title while rolled up
        else:
            self.face.update()

    def content_dict(self):
        return {"h24": self.h24, "seconds": self.seconds, "month": self.show_month, "ics_url": self.feed.url}

    def rolled_title(self):
        return f"{self.name}  ·  {datetime.now():%H:%M}"

    def build_settings(self, form):
        for label, attr in (("24-hour clock", "h24"), ("Show seconds", "seconds"), ("Show month", "show_month")):
            box = QCheckBox(label)
            box.setChecked(getattr(self, attr))
            box.toggled.connect(lambda on, a=attr: self.set_option(a, on))
            form.addRow("" if attr != "h24" else "Clock", box)
        link = QLineEdit(self.feed.url)
        link.setPlaceholderText("https://…/basic.ics")
        link.editingFinished.connect(lambda: self.set_calendar(link.text()))
        form.addRow("Calendar link", link)
        hint = QLabel("Paste your calendar's iCal link to show upcoming events. Leave empty to hide them.\n"
                      "Google Calendar: Settings → your calendar → \"Secret address in iCal format\"\n"
                      "Outlook.com: Settings → Calendar → Shared calendars → Publish a calendar → ICS link")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        form.addRow("", hint)

    def set_option(self, attr, on):
        setattr(self, attr, on)
        self.face.update()
        self.app.schedule_save()

    def set_calendar(self, url):
        if url.strip() != self.feed.url:
            self.feed.load(url)
            self.app.schedule_save()


# ---------------------------------------------------------------- system monitor

class MonitorView(QWidget):
    def __init__(self, fence):
        super().__init__()
        self.fence = fence
        self.hits = []  # (rect, action) for clickable rows

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        if not self.fence.app.stats.rows:
            p.setPen(DIM)
            p.drawText(self.rect(), Qt.AlignCenter, "Measuring…")
            return
        rows = [r for r in self.fence.app.stats.rows if r["group"] not in self.fence.hidden_rows]
        self.hits = []
        y = 4
        for r in rows:
            top = y
            p.setFont(font(12))
            p.setPen(DIM)
            p.drawText(QRectF(8, y, w - 16, 18), Qt.AlignLeft | Qt.AlignVCenter, r["label"])
            p.setFont(font(12, bold=True))
            p.setPen(ACCENT if r["action"] else WHITE)
            p.drawText(QRectF(8, y, w - 16, 18), Qt.AlignRight | Qt.AlignVCenter, r["value"])
            y += 21
            fraction = r["fraction"]
            if fraction is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(FAINT)
                p.drawRoundedRect(QRectF(8, y, w - 16, 5), 2.5, 2.5)
                p.setBrush(load_color(1 - fraction if r["invert"] else fraction))
                p.drawRoundedRect(QRectF(8, y, max(5, (w - 16) * min(fraction, 1)), 5), 2.5, 2.5)
                y += 13
            else:
                y += 4
            if r["action"]:
                self.hits.append((QRectF(0, top, w, y - top), r["action"]))
        self.setMinimumHeight(int(y))

    def mousePressEvent(self, e):
        for rect, action in self.hits:
            if rect.contains(e.position()) and e.button() == Qt.LeftButton:
                import os
                os.startfile(action)
                e.accept()
                return
        e.ignore()  # let the panel drag

    def mouseMoveEvent(self, e):
        over = any(rect.contains(e.position()) for rect, _ in self.hits)
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        e.ignore()


class MonitorFence(Panel):
    kind = "monitor"
    default_size = (300, 400)
    drag_anywhere = True

    ROWS = ("CPU", "RAM", "GPU", "VRAM", "Network", "Disk C:", "Uptime", "Updates", "Devices")

    def build_content(self, data):
        self.hidden_rows = set(data.get("hidden_rows", []))
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 4)
        self.view = MonitorView(self)
        self.view.setMouseTracking(True)
        scroll = QScrollArea()
        scroll.setWidget(self.view)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                             + SCROLLBAR_STYLE)
        layout.addWidget(scroll, 1)
        clean = QPushButton("Clean up disk…")
        clean.setCursor(Qt.PointingHandCursor)
        clean.setStyleSheet("QPushButton { background: rgba(255,255,255,26); color: white; border: none; "
                            "border-radius: 6px; padding: 6px; } QPushButton:hover { background: rgba(255,255,255,45); }")
        clean.clicked.connect(self.app.open_cleanup)
        layout.addWidget(clean)
        self.app.stats.updated.connect(lambda: self.update() if self.rolled else self.view.update())
        self.app.stats.start()
        if self.app.stats.gpu and self.app.stats.gpu.name:
            self.view.setToolTip(f"GPU: {self.app.stats.gpu.name}\n\nTemperatures appear when "
                                 "LibreHardwareMonitor is running.")
        return box

    def content_dict(self):
        return {"hidden_rows": sorted(self.hidden_rows)}

    def build_settings(self, form):
        for i, row in enumerate(self.ROWS):
            box = QCheckBox(row)
            box.setChecked(row not in self.hidden_rows)
            box.toggled.connect(lambda on, r=row: self.set_row_shown(r, on))
            form.addRow("Show" if i == 0 else "", box)
        hint = QLabel("Devices: laptop battery, Bluetooth devices that report their battery to Windows, and "
                      "Xbox-style controllers. CPU and GPU temperatures appear when LibreHardwareMonitor is running.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        form.addRow("", hint)

    def set_row_shown(self, row, shown):
        (self.hidden_rows.discard if shown else self.hidden_rows.add)(row)
        self.view.update()
        self.app.schedule_save()

    def rolled_title(self):
        rows = {r["label"]: r["value"] for r in self.app.stats.rows}
        return f"CPU {rows.get('CPU', '–')}  ·  GPU {rows.get('GPU', '–')}"