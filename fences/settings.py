"""The Settings window: app-wide options on a General page, plus one page per fence/widget.
Changes apply immediately; there's nothing to save or cancel."""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from . import dialogs
from .common import APP_NAME, DATA_ROOT, make_app_icon
from .dialogs import bring_to_front
from .pet import PALETTES, SPECIES
from .system import IS_WINDOWS, open_path

MAX_PETS = 8
GENERAL = object()  # selects the General page (its entry in the list is None)
KIND_NAMES = {"folder": "Fence", "portal": "Folder portal", "note": "Sticky note", "todo": "To-do list",
              "clock": "Clock & calendar", "monitor": "System monitor", "media": "Media controls",
              "packages": "Package tracker", "network": "Network monitor", "tabs": "Tab group"}


def checkbox(text, checked, on_toggle):
    box = QCheckBox(text)
    box.setChecked(checked)
    box.toggled.connect(on_toggle)
    return box


def hint(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setEnabled(False)  # greyed: explanatory text, not a control
    return label


def group(title):
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    return box, form


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.Window)
        self.app = app
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setWindowIcon(make_app_icon())
        self.resize(760, 560)

        layout = QHBoxLayout(self)
        self.nav = QListWidget()
        self.nav.setFixedWidth(220)
        self.nav.currentRowChanged.connect(self.show_page)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        layout.addWidget(self.nav)
        layout.addWidget(self.area, 1)
        self.entries = []

    def open_for(self, panel=None):
        """Show the window on this panel's page, or on General when opened from the tray."""
        self.reload(select=panel or GENERAL)
        bring_to_front(self)

    def reload(self, select=None):
        """Rebuild the page list, keeping (or moving to) the selected entry."""
        current = select or (self.entries[self.nav.currentRow()] if 0 <= self.nav.currentRow() < len(self.entries)
                             else None)
        if current is GENERAL:
            current = None
        self.entries = [None]
        self.nav.blockSignals(True)
        self.nav.clear()
        self.nav.addItem(QListWidgetItem("General"))
        for panel in self.app.fences:
            self.entries.append(panel)
            self.nav.addItem(QListWidgetItem(f"{panel.name}\n{KIND_NAMES.get(panel.kind, '')}"
                                             + ("" if panel.shown else "  (hidden)")))
            for tab in getattr(panel, "tabs", []):  # tabs are listed under their group
                self.entries.append(tab)
                self.nav.addItem(QListWidgetItem(f"     ↳ {tab.name}\n       {KIND_NAMES.get(tab.kind, '')} tab"))
        self.nav.blockSignals(False)
        row = self.entries.index(current) if current in self.entries else 0
        self.nav.setCurrentRow(row)
        self.show_page(row)

    def reload_later(self):
        # Controls on the current page call this; rebuilding right away would delete the control
        # while its own signal is still running.
        QTimer.singleShot(0, self.reload)

    def changeEvent(self, e):
        # Things may have changed on the desktop meanwhile (title scrolled, fence rolled up...).
        # Not while one of our own dialogs is closing (e.g. the colour picker opened from this page):
        # the button that opened it is still running and must not be deleted underneath it.
        if e.type() == QEvent.ActivationChange and self.isActiveWindow() and self.entries and not dialogs.open_count:
            self.reload()
        super().changeEvent(e)

    def show_page(self, row):
        if not 0 <= row < len(self.entries):
            return
        panel = self.entries[row]
        page = QWidget()
        layout = QVBoxLayout(page)
        for box in (self.general_page() if panel is None else self.panel_page(panel)):
            layout.addWidget(box)
        layout.addStretch(1)
        self.area.setWidget(page)

    # -- pages

    def general_page(self):
        app = self.app
        boxes = []

        box, form = group("Startup && behaviour")
        form.addRow(checkbox(f"Start DeskSpace with {'Windows' if IS_WINDOWS else 'the desktop'}", app.startup_enabled(),
                             app.set_startup))
        form.addRow(checkbox("Lock fence positions and sizes", app.locked, lambda on: app.set_option("locked", on)))
        snap_row = QHBoxLayout()
        distance = QSpinBox()
        distance.setRange(4, 48)
        distance.setSuffix(" px")
        distance.setValue(app.snap_distance)
        distance.setEnabled(app.snap)
        distance.valueChanged.connect(lambda v: app.set_option("snap_distance", v))
        snap = checkbox("Snap to screen edges and other fences, within", app.snap,
                        lambda on: (app.set_option("snap", on), distance.setEnabled(on)))
        snap_row.addWidget(snap)
        snap_row.addWidget(distance)
        snap_row.addStretch(1)
        form.addRow(snap_row)
        boxes.append(box)

        box, form = group("Quick-hide" if IS_WINDOWS else "Hide all fences")
        icons = checkbox("Also hide the desktop icons", app.hide_icons, lambda on: app.set_option("hide_icons", on))
        icons.setEnabled(app.quick_hide)
        if IS_WINDOWS:
            form.addRow(checkbox("Double-click an empty spot on the desktop to hide/show all fences", app.quick_hide,
                                 lambda on: (app.set_option("quick_hide", on), icons.setEnabled(on))))
            form.addRow(icons)
        form.addRow(hint("You can also double-click the tray icon."))
        boxes.append(box)

        box, form = group("Appearance")
        form.addRow(checkbox("Match fence colours to the wallpaper", app.match_wallpaper,
                             lambda on: app.set_option("match_wallpaper", on)))
        form.addRow(hint("Fences you've given a colour yourself keep it; tick \"Use the wallpaper colour\" on a "
                         "fence's page to hand it back."))
        form.addRow(checkbox("Show a preview when hovering over a file in a fence", app.previews,
                             lambda on: app.set_option("previews", on)))
        boxes.append(box)

        box, form = group("Quick search")
        status = hint("")

        def apply_hotkey():
            ok = app.apply_hotkey()
            status.setText("" if ok else "That shortcut is already used by another program - try a different one.")
        keys = QKeySequenceEdit(QKeySequence(app.search_hotkey))
        keys.setMaximumSequenceLength(1)
        keys.editingFinished.connect(lambda: (app.set_option("search_hotkey", keys.keySequence().toString()),
                                              apply_hotkey()))
        enabled = checkbox("Open quick search with a keyboard shortcut:", app.search_enabled,
                           lambda on: (app.set_option("search_enabled", on), keys.setEnabled(on), apply_hotkey()))
        keys.setEnabled(app.search_enabled)
        if IS_WINDOWS:
            row = QHBoxLayout()
            row.addWidget(enabled)
            row.addWidget(keys)
            row.addStretch(1)
            form.addRow(row)
            form.addRow(status)
            form.addRow(hint("Finds apps, fence items, desktop items and files. Enter opens, Ctrl+Enter shows it in "
                             "Explorer, Esc closes. Needs a modifier like Ctrl, Alt or Shift."))
            if app.search_enabled and not app.hotkey.registered:
                status.setText("That shortcut is already used by another program - try a different one.")
        else:
            form.addRow(hint("Open it from the tray icon. It finds apps, fence items, desktop items and files. "
                             "Enter opens, Ctrl+Enter shows it in the file manager, Esc closes."))
        boxes.append(box)

        box, form = group("Desktop pets")
        form.addRow(checkbox("Show little animals that wander along your fences", app.pet_enabled,
                             lambda on: app.set_option("pet_enabled", on)))
        for info in app.pets:
            form.addRow(self.pet_row(info))
        add = QPushButton("Add another pet")
        add.setEnabled(len(app.pets) < MAX_PETS)
        add.clicked.connect(lambda: (app.add_pet(), self.reload_later()))
        add_row = QHBoxLayout()
        add_row.addWidget(add)
        add_row.addStretch(1)
        form.addRow(add_row)
        form.addRow(checkbox("Dress up for the season (Halloween, Christmas, New Year, summer)", app.pet_outfits,
                             lambda on: app.set_option("pet_outfits", on)))
        form.addRow(checkbox("Tell me a minute before calendar events start", app.pet_meetings,
                             lambda on: app.set_option("pet_meetings", on)))
        form.addRow(hint("Drag a pet to pick it up, click it to make it happy. They nap when you're away. "
                         "Right-click a pet to give it a treat or throw a ball. Event reminders use the calendar "
                         "link on a Clock && calendar widget."))
        boxes.append(box)

        box, form = group("Console mode")
        form.addRow(checkbox("Start in Console mode when I sign in (for a PC on a TV)", app.console_autostart,
                             lambda on: app.set_option("console_autostart", on)))
        console = QPushButton("Open Console mode")
        console.clicked.connect(app.enter_console)
        console_row = QHBoxLayout()
        console_row.addWidget(console)
        console_row.addStretch(1)
        form.addRow(console_row)
        form.addRow(hint("A full-screen home screen with your Steam and GOG games, your pets, volume and power, for "
                         "a controller or keyboard. Hold Back + Start on the controller to return to it from "
                         "anywhere. Leave it from System → Exit to desktop."))
        boxes.append(box)

        box, form = group("File tidy")
        days = QSpinBox()
        days.setRange(0, 60)
        days.setSuffix(" days")
        days.setValue(app.tidy_days)
        days.valueChanged.connect(lambda v: app.set_option("tidy_days", v))
        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("Leave downloads alone for the first"))
        days_row.addWidget(days)
        days_row.addStretch(1)
        form.addRow(checkbox("Tidy Downloads and Screenshots by myself once a day", app.tidy_auto,
                             lambda on: app.set_option("tidy_auto", on)))
        form.addRow(days_row)
        tidy = QPushButton("Tidy files now…")
        tidy.clicked.connect(app.open_tidy)
        tidy_row = QHBoxLayout()
        tidy_row.addWidget(tidy)
        tidy_row.addStretch(1)
        form.addRow(tidy_row)
        form.addRow(hint("Sorts loose downloads into Installers, Documents, Images, Videos && music and Archives "
                         "folders, and older screenshots into month folders. Files are only moved, and the last "
                         "tidy can be undone. Duplicate downloads are only removed when you choose to, in the "
                         "Tidy window."))
        boxes.append(box)

        box, form = group("Fences && widgets on the desktop")
        if not app.fences:
            form.addRow(hint("None yet. Use New to add one."))
        for panel in app.fences:
            form.addRow(checkbox(f"{panel.name}   ({KIND_NAMES.get(panel.kind, '')})".replace("&", "&&"), panel.shown,
                                 lambda on, p=panel: (p.set_shown(on), self.reload_later())))
        actions = QHBoxLayout()
        new = QToolButton()
        new.setText("New")
        new.setPopupMode(QToolButton.InstantPopup)
        new_menu = QMenu(new)
        app.fill_new_menu(new_menu, at_cursor=False)
        new.setMenu(new_menu)
        actions.addWidget(new)
        auto = QPushButton("Auto-sort desktop into fences…")
        auto.clicked.connect(app.auto_sort)
        actions.addWidget(auto)
        data = QPushButton("Open data folder")
        data.clicked.connect(lambda: open_path(DATA_ROOT))
        actions.addWidget(data)
        actions.addStretch(1)
        form.addRow(actions)
        boxes.append(box)
        return boxes

    def pet_row(self, info):
        """Animal, name and colour of one pet, plus a button to send it home."""
        app = self.app
        animals = QComboBox()
        animals.addItems(list(SPECIES))
        animals.setCurrentText(info["kind"])
        animals.currentTextChanged.connect(lambda a: app.update_pet(info, "kind", a))
        name = QLineEdit(info["name"])
        name.setPlaceholderText("Name")
        name.editingFinished.connect(lambda: app.update_pet(info, "name", name.text().strip() or "Mochi"))
        colors = QComboBox()
        colors.addItems(list(PALETTES))
        colors.setCurrentText(info["color"])
        colors.currentTextChanged.connect(lambda c: app.update_pet(info, "color", c))
        remove = QPushButton("Remove")
        remove.setEnabled(len(app.pets) > 1)  # turn pets off with the tick box above instead
        remove.clicked.connect(lambda: app.remove_pet(info))
        row = QHBoxLayout()
        row.addWidget(animals)
        row.addWidget(name, 1)
        row.addWidget(colors)
        row.addWidget(remove)
        return row

    def panel_page(self, panel):
        box, form = group(KIND_NAMES.get(panel.kind, "Panel").replace("&", "&&"))  # "&" marks a shortcut key
        name = QLineEdit(panel.name)
        name.editingFinished.connect(lambda: (panel.set_name(name.text()), self.reload_later()))
        form.addRow("Name", name)
        if panel.group is not None:
            # A tab shares its group's window: position, colour and title belong to the group.
            form.addRow("", hint("This is a tab in a tab group. Its window, colour and title bar are set on the "
                                 "group's page."))
        else:
            form.addRow("", checkbox("Show on desktop", panel.shown,
                                     lambda on: (panel.set_shown(on), self.reload_later())))
            form.addRow("", checkbox("Rolled up (double-click the title bar to toggle)", panel.rolled,
                                     panel.set_rolled))
            size = QSpinBox()
            size.setRange(7, 32)
            size.setSuffix(" pt")
            size.setValue(int(panel.title_size))
            size.valueChanged.connect(panel.set_title_size)
            form.addRow("Title size", size)
            color = QPushButton()
            color.setFixedWidth(80)

            def paint_swatch():
                r, g, b, a = panel.effective_color().getRgb()
                color.setStyleSheet(f"background: rgba({r},{g},{b},{a}); border: 1px solid #888; "
                                    f"border-radius: 4px; min-height: 20px;")
            paint_swatch()
            use_wallpaper = checkbox("Use the wallpaper colour", not panel.custom_color,
                                     lambda on: (setattr(panel, "custom_color", not on), panel.update(),
                                                 paint_swatch(), self.app.schedule_save()))
            use_wallpaper.setEnabled(self.app.match_wallpaper)
            color.clicked.connect(lambda: (panel.pick_color(), paint_swatch(),
                                           use_wallpaper.setChecked(not panel.custom_color)))
            color_row = QHBoxLayout()
            color_row.addWidget(color)
            color_row.addWidget(use_wallpaper)
            color_row.addStretch(1)
            form.addRow("Color", color_row)

        own, own_form = group("Options")
        panel.build_settings(own_form)
        boxes = [box] + ([own] if own_form.rowCount() else [])

        row = QHBoxLayout()
        row.addStretch(1)
        if panel.group is not None:
            detach = QPushButton("Detach into its own fence")
            detach.clicked.connect(lambda: (panel.group.detach(panel), self.reload_later()))
            row.addWidget(detach)
        remove = QPushButton(panel.remove_label())
        remove.clicked.connect(lambda: self.app.remove_panel(panel))
        row.addWidget(remove)
        holder = QWidget()
        holder.setLayout(row)
        boxes.append(holder)
        return boxes
