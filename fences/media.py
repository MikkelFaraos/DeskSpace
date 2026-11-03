"""Media controls: what's playing (Spotify, YouTube in a browser, ...) with play/pause/skip, using
Windows' own media session API - the one behind the volume flyout and lock screen."""
import asyncio
import datetime
import threading

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .common import ACCENT
from .panel import Panel

PLAYING = 4  # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


def app_label(app_id):
    """'Spotify.exe' / 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> a short readable name."""
    name = app_id.split("!")[-1].split("\\")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    name = name.split(".")[-1] if "." in name else name
    return {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "ZuneMusic": "Media Player"}.get(name, name)


class MediaBackend(QObject):
    """Polls the current media session once a second on a background asyncio thread."""
    updated = Signal(object)  # dict, or None when nothing is playing

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = None
        self.thread = None
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=lambda: asyncio.run(self.main()), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    async def main(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        self.loop = asyncio.get_running_loop()
        self.manager = await Manager.request_async()
        last_key, art = None, b""
        while self.running:
            try:
                session = self.manager.get_current_session()
                if session is None:
                    state = None
                else:
                    props = await session.try_get_media_properties_async()
                    info = session.get_playback_info()
                    timeline = session.get_timeline_properties()
                    key = (session.source_app_user_model_id, props.title, props.artist)
                    if key != last_key:
                        art = await self.read_thumbnail(props)
                        last_key = key
                    state = {"title": props.title or "", "artist": props.artist or props.album_title or "",
                             "app": app_label(session.source_app_user_model_id),
                             "playing": int(info.playback_status) == PLAYING, "art": art,
                             "position": timeline.position.total_seconds(),
                             "duration": (timeline.end_time - timeline.start_time).total_seconds(),
                             "updated": timeline.last_updated_time}
                self.updated.emit(state)
            except Exception:  # sessions come and go mid-call; try again next second
                self.updated.emit(None)
            await asyncio.sleep(1)

    async def read_thumbnail(self, props):
        if props.thumbnail is None:
            return b""
        try:
            from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
            stream = await props.thumbnail.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
            reader = DataReader.from_buffer(buf)
            data = bytearray(buf.length)
            reader.read_bytes(data)
            return bytes(data)
        except Exception:
            return b""

    def command(self, name):
        if self.loop is None:
            return

        async def run():
            session = self.manager.get_current_session()
            if session is not None:
                await {"toggle": session.try_toggle_play_pause_async, "next": session.try_skip_next_async,
                       "prev": session.try_skip_previous_async}[name]()
        asyncio.run_coroutine_threadsafe(run(), self.loop)


class MediaView(QWidget):
    def __init__(self, fence):
        super().__init__()
        self.fence = fence
        self.state = None
        self.art = QPixmap()
        self.art_bytes = b""
        self.buttons = {}
        self.tick = QTimer(self, interval=1000, timeout=self.update)  # keeps the progress bar moving
        self.tick.start()

    def set_state(self, state):
        self.state = state
        if state and state["art"] != self.art_bytes:
            self.art_bytes = state["art"]
            self.art = QPixmap()
            self.art.loadFromData(self.art_bytes)
        self.update()

    def position(self):
        s = self.state
        pos = s["position"]
        if s["playing"] and s["updated"]:
            now = datetime.datetime.now(datetime.timezone.utc)
            pos += max(0.0, (now - s["updated"]).total_seconds())
        return min(pos, s["duration"]) if s["duration"] else pos

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        s = self.state
        if not s:
            p.setPen(QColor(255, 255, 255, 120))
            p.drawText(self.rect(), Qt.AlignCenter, "Nothing playing")
            self.buttons = {}
            return
        size = min(h - 8, 96)
        art_rect = QRectF(4, (h - size) / 2, size, size)
        clip = QPainterPath()
        clip.addRoundedRect(art_rect, 8, 8)
        if not self.art.isNull():
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(art_rect.toRect(), self.art.scaled(int(size * 2), int(size * 2), Qt.KeepAspectRatioByExpanding,
                                                            Qt.SmoothTransformation))
            p.restore()
        else:
            p.fillPath(clip, QColor(255, 255, 255, 25))
        x = art_rect.right() + 12
        tw = w - x - 6
        title = QFont()
        title.setPixelSize(15)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QRectF(x, art_rect.top(), tw, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(title).elidedText(s["title"] or s["app"], Qt.ElideRight, int(tw)))
        small = QFont()
        small.setPixelSize(12)
        p.setFont(small)
        p.setPen(QColor(255, 255, 255, 150))
        sub = " · ".join(v for v in (s["artist"], s["app"]) if v)
        p.drawText(QRectF(x, art_rect.top() + 21, tw, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(small).elidedText(sub, Qt.ElideRight, int(tw)))

        # Buttons: previous, play/pause, next.
        cy = art_rect.top() + size * 0.62
        self.buttons = {}
        for name, cx, radius in (("prev", x + 16, 14), ("toggle", x + 54, 18), ("next", x + 92, 14)):
            self.buttons[name] = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 235) if name == "toggle" else QColor(255, 255, 255, 30))
            p.drawEllipse(self.buttons[name])
            p.setBrush(QColor(20, 20, 28) if name == "toggle" else QColor(255, 255, 255, 230))
            self.paint_symbol(p, name, cx, cy, s["playing"])

        # Progress.
        if s["duration"] > 0:
            bar = QRectF(x, art_rect.bottom() - 6, tw, 4)
            p.setBrush(QColor(255, 255, 255, 35))
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(ACCENT)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * self.position() / s["duration"], 4), 2, 2)

    def paint_symbol(self, p, name, cx, cy, playing):
        if name == "toggle":
            if playing:
                p.drawRect(QRectF(cx - 6, cy - 7, 4, 14))
                p.drawRect(QRectF(cx + 2, cy - 7, 4, 14))
            else:
                p.drawPolygon(QPolygonF([QPointF(cx - 5, cy - 8), QPointF(cx - 5, cy + 8), QPointF(cx + 8, cy)]))
            return
        d = -1 if name == "prev" else 1
        p.drawPolygon(QPolygonF([QPointF(cx - 5 * d, cy - 6), QPointF(cx - 5 * d, cy + 6), QPointF(cx + 3 * d, cy)]))
        p.drawRect(QRectF(cx + 3 * d - (2 if d > 0 else 0), cy - 6, 2, 12))

    def mousePressEvent(self, e):
        for name, rect in self.buttons.items():
            if rect.contains(e.position()) and e.button() == Qt.LeftButton:
                self.fence.app.media.command(name)
                e.accept()
                return
        e.ignore()  # let the panel drag


class MediaFence(Panel):
    kind = "media"
    default_size = (340, 140)
    drag_anywhere = True

    def build_content(self, data):
        self.view = MediaView(self)
        self.app.media.updated.connect(self.view.set_state)
        self.app.media.updated.connect(lambda _: self.update() if self.rolled else None)  # song is in the title
        self.app.media.start()
        return self.view

    def rolled_title(self):
        s = self.view.state
        return f"{'▶' if s['playing'] else '❚❚'}  {s['title']}" if s else self.name
