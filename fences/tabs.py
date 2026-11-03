"""Fence tabs: several fences/widgets stacked in one window, with tabs in the title bar.

Drop a panel onto another panel's title bar to combine them. Each tab is still a full panel
object (its own refresh, drops, settings); only its content widget is moved into this window.
"""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QMenu, QStackedWidget

from . import dialogs
from .panel import Panel


class TabGroup(Panel):
    kind = "tabs"

    def build_content(self, data):
        self.stack = QStackedWidget()
        self.tabs = []
        self.active = 0
        self.setAcceptDrops(True)
        for tab_data in data.get("tabs", []):
            self.add(self.app.make_panel(tab_data))
        self.active = max(0, min(data.get("active", 0), len(self.tabs) - 1))
        if self.tabs:
            self.stack.setCurrentIndex(self.active)
        return self.stack

    def content_dict(self):
        return {"tabs": [t.to_dict() for t in self.tabs], "active": self.active}

    @property
    def drag_anywhere(self):  # a clock tab can be dragged by its face, like a standalone clock
        return bool(self.tabs) and self.current().drag_anywhere

    def current(self):
        return self.tabs[self.active]

    # -- tabs

    def add(self, panel):
        panel.group = self
        panel.hide()
        self.stack.addWidget(panel.content)
        panel.content.show()
        self.tabs.append(panel)
        self.update()

    def take(self, panel):
        """Remove a tab from this group (without placing it anywhere)."""
        index = self.tabs.index(panel)
        self.tabs.pop(index)
        self.stack.removeWidget(panel.content)
        panel.group = None
        if self.active >= len(self.tabs) or index < self.active:
            self.active = max(0, self.active - 1)
        if self.tabs:
            self.stack.setCurrentIndex(self.active)
        self.update()

    def set_active(self, index):
        self.active = index
        self.stack.setCurrentIndex(index)
        self.update()
        self.app.schedule_save()

    def detach(self, panel, geometry=None):
        """Turn a tab back into its own window, beside the group (or exactly in its place)."""
        self.take(panel)
        panel.layout_.addWidget(panel.content)
        panel.content.setVisible(not panel.rolled)
        g = geometry or self.geometry().translated(30, 30)
        panel.full_h = self.full_h
        panel.setGeometry(g.x(), g.y(), g.width(), panel.title_h if panel.rolled else self.full_h)
        self.app.fences.append(panel)
        if panel.shown and not self.app.hidden:
            panel.show_pinned()
        self.after_change()
        self.app.save()
        self.app.panels_changed()

    def after_change(self):
        # A group of one is just a fence again; a group of none is gone.
        if len(self.tabs) == 1:
            self.detach(self.tabs[0], self.geometry())
        elif not self.tabs:
            self.app.drop_group(self)

    def dispose(self):
        for tab in self.tabs:
            tab.deleteLater()

    # -- title bar

    def tab_rects(self):
        fm = QFontMetrics(self.title_font)
        widths = [fm.horizontalAdvance(t.name) + 24 for t in self.tabs]
        scale = min(1.0, (self.width() - 20) / max(sum(widths), 1))
        rects, x = [], 10
        for w in widths:
            w = int(w * scale)
            rects.append(QRect(x, 3, w, self.title_h - 7))
            x += w
        return rects

    def tab_at(self, pos):
        if pos.y() >= self.title_h:
            return None
        return next((i for i, r in enumerate(self.tab_rects()) if r.contains(pos)), None)

    def paint_title(self, p, r):
        fm = QFontMetrics(self.title_font)
        p.setFont(self.title_font)
        for i, (tab, rect) in enumerate(zip(self.tabs, self.tab_rects())):
            if i == self.active:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 38))
                p.drawRoundedRect(rect, 6, 6)
            p.setPen(QColor(255, 255, 255, 235 if i == self.active else 130))
            p.drawText(rect, Qt.AlignCenter, fm.elidedText(tab.name, Qt.ElideRight, rect.width() - 10))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            i = self.tab_at(e.position().toPoint())
            if i is not None and i != self.active:
                self.set_active(i)
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        i = self.tab_at(e.pos())
        self.show_menu(e.globalPos(), tab=self.tabs[i] if i is not None else None)

    def show_menu(self, global_pos, menu=None, tab=None):
        menu = menu or QMenu()
        self.fill_menu(menu, tab)
        menu.exec(global_pos)

    def fill_menu(self, menu, tab=None):
        tab = tab or self.current()
        self.app.fill_new_menu(menu.addMenu("New"), at_cursor=True)
        menu.addAction("Change color…", self.pick_color)
        menu.addAction(f"Settings for '{tab.name}'…", lambda: self.app.open_settings(tab))
        menu.addSeparator()
        menu.addAction(f"Detach '{tab.name}' into its own fence", lambda: self.detach(tab))
        menu.addAction(f"{tab.remove_label()}: '{tab.name}'", lambda: self.app.remove_panel(tab))

    def remove_label(self):
        return "Split into separate fences"

    def confirm_remove(self):
        # Removing a group never deletes its tabs: they become separate fences again.
        if dialogs.ask("Split this tab group back into separate fences?"):
            g = self.geometry()
            for i, tab in enumerate(list(self.tabs)):
                if tab in self.tabs:
                    self.detach(tab, g.translated(30 * i, 30 * i))
        return False

    # -- drops go to the active tab; hovering a tab while dragging switches to it

    def dragEnterEvent(self, e):
        self.forward_drag(e)

    def dragMoveEvent(self, e):
        i = self.tab_at(e.position().toPoint())
        if i is not None and i != self.active:
            self.set_active(i)
        self.forward_drag(e)

    def forward_drag(self, e):
        tab = self.current() if self.tabs else None
        if tab is not None and hasattr(tab, "handle_drag_enter"):
            tab.handle_drag_enter(e)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        tab = self.current() if self.tabs else None
        if tab is not None and hasattr(tab, "set_drop_hover"):
            tab.set_drop_hover(False)

    def dropEvent(self, e):
        tab = self.current() if self.tabs else None
        if tab is not None and hasattr(tab, "handle_drop"):
            tab.handle_drop(e)
