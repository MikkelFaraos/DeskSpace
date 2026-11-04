"""Does this PySide6 lose a reference to None on plain Qt calls? No DeskSpace code involved."""
import sys

from PySide6 import __version__
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

qt = QApplication([])
widget = QWidget()
pixmap = QPixmap(50, 50)
painter = QPainter(pixmap)
path = QPainterPath(QPointF(0, 0))
point = QPointF(1, 1)
CALLS = {"processEvents": qt.processEvents, "widget.update": widget.update, "widget.move": lambda: widget.move(1, 1),
         "path.lineTo": lambda: path.lineTo(point), "painter.save": lambda: (painter.save(), painter.restore())}
print("PySide6", __version__, "Python", sys.version.split()[0], flush=True)
for name, call in CALLS.items():
    before = sys.getrefcount(None)
    for _ in range(1000):
        call()
    print(f"  {name:16} lost {before - sys.getrefcount(None)} per 1000 calls", flush=True)
painter.end()
