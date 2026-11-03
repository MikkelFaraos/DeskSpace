"""Pop-up dialogs that always open in front.

Fences sit at the bottom of the window stack and are never the foreground window, so a plain
dialog can open behind whatever app was used last - and since it's modal, DeskSpace then
looks frozen. Qt's own dialogs here are made always-on-top, brought to the foreground and
centred on the mouse pointer. Windows' native folder picker can't take those flags, so it is
owned by an invisible always-on-top anchor window instead (owned windows stay above their owner).
"""
from contextlib import contextmanager

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QColorDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget

from .common import APP_NAME
from .win32 import user32

_anchor = None
open_count = 0  # dialogs currently open; windows shouldn't rebuild themselves under an open dialog


def bring_to_front(widget):
    widget.show()
    widget.raise_()
    widget.activateWindow()
    user32.SetForegroundWindow(int(widget.winId()))


def _place_at_cursor(dialog):
    """Centre the dialog on the mouse pointer, kept inside that screen."""
    pos = QCursor.pos()
    screen = (QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()).availableGeometry()
    size = dialog.frameGeometry().size()
    x = min(max(pos.x() - size.width() // 2, screen.left()), screen.right() - size.width())
    y = min(max(pos.y() - size.height() // 2, screen.top()), screen.bottom() - size.height())
    dialog.move(QPoint(x, y))


def _run(dialog):
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)

    def front():
        _place_at_cursor(dialog)
        bring_to_front(dialog)
    QTimer.singleShot(0, front)  # once exec() has shown it
    global open_count
    open_count += 1
    try:
        return dialog.exec()
    finally:
        open_count -= 1


def ask(text):
    box = QMessageBox(QMessageBox.Question, APP_NAME, text, QMessageBox.Yes | QMessageBox.No)
    return _run(box) == QMessageBox.Yes


def tell(text, warning=False):
    _run(QMessageBox(QMessageBox.Warning if warning else QMessageBox.Information, APP_NAME, text))


def get_text(label, text=""):
    """(text, ok)"""
    dialog = QInputDialog()
    dialog.setWindowTitle(APP_NAME)
    dialog.setLabelText(label)
    dialog.setTextEchoMode(QLineEdit.Normal)
    dialog.setTextValue(text)
    ok = bool(_run(dialog))
    return dialog.textValue(), ok


def get_color(initial, title="Color"):
    """The chosen colour, or None if cancelled."""
    dialog = QColorDialog(initial)
    dialog.setWindowTitle(title)
    dialog.setOption(QColorDialog.ShowAlphaChannel)
    return dialog.selectedColor() if _run(dialog) else None


@contextmanager
def _anchored():
    global _anchor
    if _anchor is None:
        _anchor = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        _anchor.setAttribute(Qt.WA_TranslucentBackground)
        _anchor.setWindowTitle(APP_NAME)
        _anchor.resize(1, 1)
    _anchor.move(QCursor.pos())
    bring_to_front(_anchor)
    try:
        yield _anchor
    finally:
        _anchor.hide()


def get_folder(title, start):
    """The chosen folder path, or "" if cancelled."""
    global open_count
    open_count += 1
    try:
        with _anchored() as parent:
            return QFileDialog.getExistingDirectory(parent, title, str(start))
    finally:
        open_count -= 1
