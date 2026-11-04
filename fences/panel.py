"""The translucent desktop window every fence and widget lives in: title bar, move/resize with
edge snapping, roll-up, colour and title size. Subclasses supply the content."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from . import dialogs
from .common import APP_NAME, DEFAULT_COLOR, DEFAULT_TITLE_SIZE, EDGE
from .system import (
    IS_WINDOWS, SNAP_GAP, WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, WM_MOVING, WM_SIZING, pin_to_desktop, snap_moving,
    snap_sizing, user32, work_area,
)

if IS_WINDOWS:
    from .system import cursor_pos
else:
    from .system import Rect


class Panel(QWidget):
    kind = None  # stored in the config to pick the class again on load
    default_size = (340, 230)
    drag_anywhere = False  # display-only panels can be dragged by their body, not just the title

    def __init__(self, app, data):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint)
        self.app = app
        self.id = data["id"]
        self.name = data["name"]
        self.color = QColor(*data.get("color", DEFAULT_COLOR))
        # A colour picked by hand wins over the wallpaper-matched colour.
        self.custom_color = data.get("custom_color", list(data.get("color", DEFAULT_COLOR)) != DEFAULT_COLOR)
        self.group = None  # the TabGroup this panel is a tab of, if any
        self.merge_target = None
        self.rolled = data.get("rolled", False)
        self.shown = data.get("visible", True)  # can be switched off in Settings without removing it
        self.full_h = data.get("h", self.default_size[1])
        self.drop_hover = False
        self.drag_start = None
        self.manual_drag = None  # Linux: (edges, geometry and cursor when the drag began)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setWindowTitle(f"{APP_NAME} - {self.name}")

        self.layout_ = QVBoxLayout(self)
        self.content = self.build_content(data)
        # Own cursor, so the panel's resize cursor (set while crossing its edge) isn't inherited.
        self.content.setCursor(Qt.ArrowCursor)
        self.layout_.addWidget(self.content)
        self.apply_title_size(data.get("title_size", DEFAULT_TITLE_SIZE))

        self.setGeometry(data.get("x", 60), data.get("y", 60), data.get("w", self.default_size[0]),
                         self.title_h if self.rolled else self.full_h)
        self.content.setVisible(not self.rolled)

    # -- for subclasses

    def build_content(self, data):
        raise NotImplementedError

    def content_dict(self):
        return {}

    def build_settings(self, form):
        """Add this panel's own options to its page in the Settings window (a QFormLayout)."""

    def rolled_title(self):
        return self.name

    def on_renamed(self):
        pass

    def confirm_remove(self):
        """Ask before removing; return True to go ahead. Subclasses may clean up here."""
        return dialogs.ask(f"Remove '{self.name}'?")

    # -- state

    def to_dict(self):
        g = self.geometry()
        data = {"id": self.id, "kind": self.kind, "name": self.name, "x": g.x(), "y": g.y(), "w": g.width(),
                "h": self.full_h, "rolled": self.rolled, "color": list(self.color.getRgb()),
                "custom_color": self.custom_color, "title_size": self.title_size, "visible": self.shown}
        data.update(self.content_dict())
        return data

    def apply_title_size(self, size):
        """Set the title font size in points; the title bar's height follows it."""
        self.title_size = max(7, min(32, size))
        self.title_font = QFont(self.font())
        self.title_font.setPointSizeF(self.title_size)
        self.title_font.setBold(True)
        self.title_h = max(24, QFontMetrics(self.title_font).height() + 12)
        self.layout_.setContentsMargins(EDGE, self.title_h, EDGE, EDGE)
        self.setMinimumSize(150, self.title_h)

    def set_title_size(self, size):
        self.apply_title_size(size)
        self.full_h = max(self.full_h, self.title_h + 60)
        if self.rolled:
            self.resize(self.width(), self.title_h)
        self.update()
        self.app.schedule_save()

    def show_pinned(self):
        self.show()
        pin_to_desktop(int(self.winId()))

    # -- painting

    def effective_color(self):
        wallpaper = self.app.wallpaper_color if self.app.match_wallpaper else None
        return wallpaper if wallpaper is not None and not self.custom_color else self.color

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setBrush(self.effective_color())
        border = QColor(120, 175, 255, 220) if self.drop_hover else QColor(255, 255, 255, 45)
        p.setPen(QPen(border, 2 if self.drop_hover else 1))
        p.drawRoundedRect(r, 8, 8)
        if not self.rolled:
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.drawLine(r.left() + 10, self.title_h - 2, r.right() - 10, self.title_h - 2)
        self.paint_title(p, r)

    def paint_title(self, p, r):
        p.setFont(self.title_font)
        p.setPen(QColor(255, 255, 255, 230))
        title = self.rolled_title() if self.rolled else self.name
        text = QFontMetrics(self.title_font).elidedText(title, Qt.ElideRight, r.width() - 24)
        p.drawText(QRect(r.left(), 0, r.width(), self.title_h), Qt.AlignCenter, text)

    # -- moving / resizing / roll-up

    def edges_at(self, pos):
        e = Qt.Edge(0)
        if self.app.locked:
            return e
        if pos.x() <= EDGE:
            e |= Qt.LeftEdge
        elif pos.x() >= self.width() - EDGE:
            e |= Qt.RightEdge
        if not self.rolled:
            if pos.y() <= 3:
                e |= Qt.TopEdge
            elif pos.y() >= self.height() - EDGE:
                e |= Qt.BottomEdge
        return e

    def mouseMoveEvent(self, e):
        if self.manual_drag:
            self.drag_to(QCursor.pos())
            return
        edges = self.edges_at(e.position().toPoint())
        horiz = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        vert = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if horiz and vert:
            diag = bool(edges & Qt.LeftEdge) == bool(edges & Qt.TopEdge)
            self.setCursor(Qt.SizeFDiagCursor if diag else Qt.SizeBDiagCursor)
        elif horiz:
            self.setCursor(Qt.SizeHorCursor)
        elif vert:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def nativeEvent(self, event_type, message):
        # Windows sends these continuously during a move/resize drag; adjusting the proposed
        # rect in place makes the panel snap to monitor edges and other panels live.
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_ENTERSIZEMOVE:
            rc = wintypes.RECT()
            user32.GetWindowRect(msg.hWnd, ctypes.byref(rc))
            self.drag_start = (rc, self.cursor_pos())
        elif msg.message == WM_EXITSIZEMOVE:
            self.drag_start = None
            target = self.merge_target
            self.set_merge_target(None)
            if target is not None:  # dropped onto another panel's title bar: combine them as tabs
                QTimer.singleShot(0, lambda: self.app.merge(self, target))
        elif msg.message in (WM_MOVING, WM_SIZING) and self.drag_start:
            if msg.message == WM_MOVING:
                self.set_merge_target(self.app.merge_target_at(self, self.cursor_pos()))
            if not self.app.snap:
                return False, 0
            # Windows builds each proposed rect from the previous (already snapped) one, so snapping
            # that would hold the panel forever. Rebuild the unsnapped rect from where the drag
            # started plus the total mouse movement, then snap that.
            r = wintypes.RECT.from_address(msg.lParam)
            start, start_pt = self.drag_start
            pt = self.cursor_pos()
            dx, dy = pt.x - start_pt.x, pt.y - start_pt.y
            side = msg.wParam
            if msg.message == WM_MOVING:
                r.left, r.top, r.right, r.bottom = start.left + dx, start.top + dy, start.right + dx, start.bottom + dy
            else:
                if side in (1, 4, 7):
                    r.left = start.left + dx
                if side in (2, 5, 8):
                    r.right = start.right + dx
                if side in (3, 4, 5):
                    r.top = start.top + dy
                if side in (6, 7, 8):
                    r.bottom = start.bottom + dy
            dpi = user32.GetDpiForWindow(msg.hWnd)
            dist, gap = self.app.snap_distance * dpi // 96, SNAP_GAP * dpi // 96
            others = []
            for f in self.app.fences:
                if f is not self and f.isVisible():
                    rc = wintypes.RECT()
                    user32.GetWindowRect(int(f.winId()), ctypes.byref(rc))
                    others.append(rc)
            if msg.message == WM_MOVING:
                snap_moving(r, work_area(r), others, dist, gap)
            else:
                snap_sizing(r, side, work_area(r), others, dist, gap)
                # Our rebuilt rect skipped Windows' minimum-size check, so enforce it on the dragged edge.
                min_w, min_h = self.minimumWidth() * dpi // 96, self.minimumHeight() * dpi // 96
                if r.right - r.left < min_w:
                    if side in (1, 4, 7):
                        r.left = r.right - min_w
                    else:
                        r.right = r.left + min_w
                if r.bottom - r.top < min_h:
                    if side in (3, 4, 5):
                        r.top = r.bottom - min_h
                    else:
                        r.bottom = r.top + min_h
            return True, 1
        return False, 0

    def cursor_pos(self):
        return cursor_pos() if IS_WINDOWS else QCursor.pos()

    def drag_to(self, cursor):
        """Linux has no message to adjust a window-manager drag with, so we move the window ourselves."""
        edges, start, start_cursor = self.manual_drag
        r = Rect.of(start)
        dx, dy = cursor.x() - start_cursor.x(), cursor.y() - start_cursor.y()
        side = 0
        if edges == Qt.Edge(0):
            r.left, r.right, r.top, r.bottom = r.left + dx, r.right + dx, r.top + dy, r.bottom + dy
            self.set_merge_target(self.app.merge_target_at(self, cursor))
        else:
            left, right = bool(edges & Qt.LeftEdge), bool(edges & Qt.RightEdge)
            top, bottom = bool(edges & Qt.TopEdge), bool(edges & Qt.BottomEdge)
            side = (1 if left else 2 if right else 0) + (6 if bottom else 3 if top else 0)  # WMSZ_* codes
            r.left += dx if left else 0
            r.right += dx if right else 0
            r.top += dy if top else 0
            r.bottom += dy if bottom else 0
        if self.app.snap:
            others = [Rect.of(f.geometry()) for f in self.app.fences if f is not self and f.isVisible()]
            snap = (lambda *a: snap_moving(r, *a)) if not side else (lambda *a: snap_sizing(r, side, *a))
            snap(work_area(r), others, self.app.snap_distance, SNAP_GAP)
        if side:
            if r.right - r.left < self.minimumWidth():
                if side in (1, 4, 7):
                    r.left = r.right - self.minimumWidth()
                else:
                    r.right = r.left + self.minimumWidth()
            if r.bottom - r.top < self.minimumHeight():
                if side in (3, 4, 5):
                    r.top = r.bottom - self.minimumHeight()
                else:
                    r.bottom = r.top + self.minimumHeight()
        self.setGeometry(r.left, r.top, r.right - r.left, r.bottom - r.top)

    def mouseReleaseEvent(self, e):
        if self.manual_drag:
            self.manual_drag = None
            target = self.merge_target
            self.set_merge_target(None)
            if target is not None:
                QTimer.singleShot(0, lambda: self.app.merge(self, target))

    def set_merge_target(self, target):
        if target is self.merge_target:
            return
        for panel, on in ((self.merge_target, False), (target, True)):
            if panel is not None:
                panel.drop_hover = on
                panel.update()
        self.merge_target = target

    def leaveEvent(self, _):
        self.unsetCursor()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        edges = self.edges_at(pos)
        movable = (pos.y() < self.title_h or self.drag_anywhere) and not self.app.locked
        if not IS_WINDOWS:
            if edges != Qt.Edge(0) or movable:
                self.manual_drag = (edges, self.geometry(), QCursor.pos())
        elif edges != Qt.Edge(0):
            self.windowHandle().startSystemResize(edges)
        elif movable:
            self.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < self.title_h:
            self.toggle_roll()

    def wheelEvent(self, e):
        # Scrolling over the title bar resizes the title text.
        if e.position().y() < self.title_h and e.angleDelta().y():
            self.set_title_size(self.title_size + (1 if e.angleDelta().y() > 0 else -1))

    def contextMenuEvent(self, e):
        self.show_menu(e.globalPos())

    def show_menu(self, global_pos, menu=None):
        menu = menu or QMenu()
        self.fill_menu(menu)
        menu.exec(global_pos)

    def toggle_roll(self):
        self.set_rolled(not self.rolled)

    def set_rolled(self, rolled):
        self.rolled = rolled
        self.content.setVisible(not self.rolled)
        self.resize(self.width(), self.title_h if self.rolled else self.full_h)
        self.update()
        self.app.schedule_save()

    def moveEvent(self, _):
        self.app.schedule_save()

    def resizeEvent(self, _):
        if not self.rolled and self.height() > self.title_h:
            self.full_h = self.height()
        self.app.schedule_save()

    # -- menu actions

    def fill_menu(self, menu):
        if self.group is not None:  # a tab: its group's menu, aimed at this tab
            self.group.fill_menu(menu, tab=self)
            return
        # Everyday actions only; every other option lives in the Settings window.
        self.app.fill_new_menu(menu.addMenu("New"), at_cursor=True)
        menu.addAction("Change color…", self.pick_color)
        menu.addAction("Settings…", lambda: self.app.open_settings(self))
        menu.addSeparator()
        menu.addAction(self.remove_label(), lambda: self.app.remove_panel(self))

    def remove_label(self):
        return "Remove"

    def set_shown(self, shown):
        self.shown = shown
        if shown and not self.app.hidden:
            self.show_pinned()
        else:
            self.hide()
        self.app.schedule_save()

    def set_name(self, name):
        name = name.strip()
        if not name or name == self.name:
            return
        self.name = name
        self.setWindowTitle(f"{APP_NAME} - {name}")
        self.on_renamed()
        self.update()
        self.app.schedule_save()

    def pick_color(self):
        color = dialogs.get_color(self.effective_color(), f"{self.name} color")
        if color:
            self.color = color
            self.custom_color = True
            self.update()
            self.app.schedule_save()


if not IS_WINDOWS:
    # Qt would call this for every X11 event, and each call costs Python a reference to None: on
    # Python below 3.12 those run out within minutes and the whole app crashes.
    del Panel.nativeEvent
