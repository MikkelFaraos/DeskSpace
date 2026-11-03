"""Hover previews for files in fences: Explorer's own thumbnail (so photos, videos, PDFs and Office
files all work), the file's size and date, and the first lines of text files."""
import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .sysinfo import fmt_bytes
from .system import shell_thumbnail

TEXT_TYPES = {".txt", ".md", ".csv", ".log", ".json", ".ini", ".cfg", ".py", ".xml", ".yaml", ".yml", ".bat",
              ".ps1"}
MAX_IMAGE = 300
PAD = 12


class PreviewPopup(QWidget):
    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.image = QPixmap()
        self.title = ""
        self.details = ""
        self.text = ""

    def show_for(self, path, item_rect):
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return
        thumb = shell_thumbnail(path, MAX_IMAGE * 2)
        self.image = QPixmap.fromImage(thumb) if thumb else QPixmap()
        if not self.image.isNull():
            self.image = self.image.scaled(MAX_IMAGE, MAX_IMAGE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.title = path.name
        modified = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%d %b %Y  %H:%M")
        if path.is_dir():
            try:
                count = sum(1 for _ in path.iterdir())
            except OSError:
                count = 0
            self.details = f"Folder · {count} item(s) · {modified}"
        else:
            self.details = f"{fmt_bytes(st.st_size)} · {modified}"
        self.text = ""
        if path.suffix.lower() in TEXT_TYPES:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self.text = "\n".join(f.read(1500).splitlines()[:14])
            except OSError:
                pass
            if self.text:
                self.image = QPixmap()  # the text says more than a generic text-file icon

        width = max(self.image.width(), 240) if not self.text else 380
        fm_title = QFontMetrics(self.title_font())
        lines = 0 if not self.text else len(self.text.splitlines())
        height = (PAD + (self.image.height() + 10 if not self.image.isNull() else 0)
                  + fm_title.height() * 2 + 4 + 18 + (lines * 15 + 10 if lines else 0) + PAD)
        self.resize(width + PAD * 2, height)
        self.move(self.position_beside(item_rect))
        self.show()
        self.update()

    def position_beside(self, item_rect):
        screen = (QGuiApplication.screenAt(item_rect.center()) or QGuiApplication.primaryScreen()).availableGeometry()
        x = item_rect.right() + 8
        if x + self.width() > screen.right():
            x = item_rect.left() - 8 - self.width()
        y = min(max(item_rect.top() - 20, screen.top()), screen.bottom() - self.height())
        return QPoint(max(x, screen.left()), y)

    def title_font(self):
        f = QFont()
        f.setPointSizeF(10)
        f.setBold(True)
        return f

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.setPen(QColor(255, 255, 255, 50))
        p.setBrush(QColor(24, 26, 34, 238))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        y = PAD
        w = self.width() - PAD * 2
        if not self.image.isNull():
            p.drawPixmap(PAD + (w - self.image.width()) // 2, y, self.image)
            y += self.image.height() + 10
        p.setFont(self.title_font())
        p.setPen(QColor(255, 255, 255, 240))
        fm = QFontMetrics(self.title_font())
        rect = QRectF(PAD, y, w, fm.height() * 2)
        p.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self.title)
        y += fm.boundingRect(rect.toRect(), Qt.TextWordWrap, self.title).height() + 4
        small = QFont()
        small.setPointSizeF(8.5)
        p.setFont(small)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(PAD, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter, self.details)
        y += 24
        if self.text:
            mono = QFont("Consolas")
            mono.setPointSizeF(8.5)
            p.setFont(mono)
            p.setPen(QColor(255, 255, 255, 200))
            p.drawText(QRectF(PAD, y, w, self.height() - y - PAD), Qt.AlignLeft | Qt.AlignTop, self.text)


class PreviewController:
    """Shows the popup after the pointer rests on an item for a moment."""

    def __init__(self, app):
        self.app = app
        self.popup = PreviewPopup()
        self.pending = None
        self.timer = QTimer(singleShot=True, interval=650, timeout=self.fire)

    def hover(self, path, item_rect):
        if not self.app.previews:
            return
        if self.pending and self.pending[0] == path:
            return
        self.popup.hide()
        self.pending = (path, item_rect)
        self.timer.start()

    def cancel(self):
        self.timer.stop()
        self.pending = None
        self.popup.hide()

    def fire(self):
        if self.pending:
            self.popup.show_for(*self.pending)
