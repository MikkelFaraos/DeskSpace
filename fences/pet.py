"""Desktop pets: small animals (cat, dog, bear, hamster or rabbit) that walk along the tops of
fences and the bottom of the screen,
jump between them, nap when you're away, and can be picked up and dropped.

When there are several they get along: they say hello when they meet, play chase, follow each other
up onto fences, nap side by side, and a hamster will ride around on a bear.

Extras: give a pet a treat, throw them a ball to chase, they dress up for the season, and one of
them tells you a minute before a calendar event (from a clock widget's calendar) starts.

Each pet is its own window at desktop level like the fences, so they never cover your apps."""
import ctypes
import math
import random
import time
from ctypes import wintypes
from datetime import date

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .system import IS_WINDOWS, pin_to_desktop, user32

W, H = 90, 104
FOOT = QPointF(W / 2, H - 4)  # where the feet touch the ground, in window coordinates
GRAVITY = 0.55
FREE = ("idle", "walk", "sit")  # states in which a pet is up for meeting, playing or giving a ride
CHASE_SPEED, FLEE_SPEED = 3.2, 2.2
PALETTES = {  # body, belly/muzzle, outline/ears
    "Orange": ("#F2A04F", "#FCE5C8", "#B9692C"),
    "Brown": ("#9C6B47", "#E8D3BF", "#5F3F27"),
    "Golden": ("#E2B85C", "#FAEFD2", "#A57F2C"),
    "Cream": ("#EBDDC3", "#FFF8EC", "#B7A27E"),
    "Grey": ("#9AA3AE", "#E6E9ED", "#5E6772"),
    "Black": ("#3A3A42", "#6B6B76", "#1C1C22"),
    "White": ("#F4F4F6", "#FFFFFF", "#B9BCC6"),
    "Pink": ("#F4A6C1", "#FDE3EC", "#C8708F"),
    "Green": ("#7DC57A", "#E2F4DE", "#4A8A47"),
    "Blue": ("#7FB2E8", "#E3EFFB", "#4C7DB3"),
    "Purple": ("#B596E0", "#EFE6FA", "#7B5BAA"),
}
# Each animal: body and head size, leg length, ears, tail and face style, walking speed, and how
# high above the head the "happy" heart floats (rabbit ears are tall).
SPECIES = {
    "Cat": {"body": (40, 24), "head": (30, 26), "legs": 10, "ears": "pointy", "tail": "long", "face": "small",
            "whiskers": True, "speed": 1.3, "heart": 30},
    "Dog": {"body": (44, 24), "head": (30, 26), "legs": 11, "ears": "floppy", "tail": "wag", "face": "snout",
            "speed": 1.6, "heart": 28},
    "Bear": {"body": (48, 30), "head": (34, 30), "legs": 10, "ears": "round", "tail": "stub", "face": "round",
             "speed": 0.9, "heart": 32},
    "Hamster": {"body": (38, 26), "head": (30, 26), "legs": 5, "ears": "tiny", "tail": None, "face": "cheeks",
                "whiskers": True, "speed": 1.1, "heart": 26},
    "Rabbit": {"body": (36, 24), "head": (26, 25), "legs": 8, "ears": "long", "tail": "puff", "face": "teeth",
               "whiskers": True, "speed": 1.5, "heart": 44, "hop": True},
}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def round_pen(color, width):
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    return pen


pad_activity = 0.0  # when a game controller was last used: Windows doesn't count that as input


mouse_seen = (None, 0.0)  # Linux has no "last input" call: where the mouse last was, and when it moved


def idle_seconds():
    if not IS_WINDOWS:
        global mouse_seen
        pos = QCursor.pos()
        if pos != mouse_seen[0]:
            mouse_seen = (pos, time.monotonic())
        return time.monotonic() - max(mouse_seen[1], pad_activity)
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return min((ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000, time.monotonic() - pad_activity)


def outfit_today(today=None):
    """What the pets wear today: a witch hat for Halloween, a Santa hat at Christmas, a party hat at
    New Year and sunglasses in high summer."""
    d = today or date.today()
    if d.month == 10 and d.day >= 15:
        return "witch"
    if d.month == 12 and d.day <= 26:
        return "santa"
    if (d.month, d.day) in ((12, 31), (1, 1)):
        return "party"
    if (6, 21) <= (d.month, d.day) <= (8, 20):
        return "sunglasses"
    return None


# -- things the pets share the desktop with

def screen_area(x, y):
    screen = QApplication.screenAt(QPointF(x, y - 10).toPoint()) or QApplication.primaryScreen()
    return screen.availableGeometry()


def find_platforms(app, x, y):
    """Walkable surfaces: (left, right, y, what). Fence tops, and the bottom of the screen."""
    area = screen_area(x, y)
    if app.stage_floor is not None:  # Console mode: the top of its bottom bar is the only ground
        return [(area.left(), area.right(), app.stage_floor, "floor")]
    found = [(area.left(), area.right(), area.bottom() + 1, "floor")]
    for panel in app.fences:
        if panel.isVisible():
            g = panel.geometry()
            found.append((g.left() + 4, g.right() - 4, g.top(), panel))
    return found


def stay_above_fences(app, widget):
    # Fences and pets all sit at desktop level; keep this one on top of the fences there.
    if not IS_WINDOWS:
        widget.raise_()
        return
    me = int(widget.winId())
    for panel in app.fences:
        if panel.isVisible():
            user32.SetWindowPos(int(panel.winId()), me, 0, 0, 0, 0, 0x13)  # NOSIZE|NOMOVE|NOACTIVATE


class DesktopThing(QWidget):
    """A small window that lives at desktop level with the pets: a speech bubble, a treat, the ball."""

    def __init__(self, w, h, on_top=False):
        flags = Qt.FramelessWindowHint | Qt.Tool
        super().__init__(None, flags | (Qt.WindowStaysOnTopHint if on_top else Qt.WindowStaysOnBottomHint))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(w, h)
        self.on_top = on_top

    def appear(self, app):
        self.show()
        if not self.on_top:
            pin_to_desktop(int(self.winId()))
            stay_above_fences(app, self)


class Bubble(DesktopThing):
    """A speech bubble above a pet. Meeting reminders float over your apps; the rest stay on the desktop."""

    def __init__(self, text, on_top):
        f = QFont()
        f.setPixelSize(13)
        f.setBold(on_top)
        rect = QFontMetrics(f).boundingRect(QRect(0, 0, 220, 200), Qt.TextWordWrap, text)
        super().__init__(max(rect.width(), 18) + 24, rect.height() + 26, on_top)
        self.text, self.font_ = text, f
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        body = QRectF(1, 1, self.width() - 2, self.height() - 11)
        path = QPainterPath()
        path.addRoundedRect(body, 9, 9)
        tail = QPainterPath(QPointF(self.width() / 2 - 7, body.bottom() - 1))
        tail.lineTo(QPointF(self.width() / 2, self.height() - 1))
        tail.lineTo(QPointF(self.width() / 2 + 7, body.bottom() - 1))
        path = path.united(tail)
        p.setPen(QPen(QColor(60, 60, 70), 1.2))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPath(path)
        p.setFont(self.font_)
        p.setPen(QColor(30, 30, 36))
        p.drawText(body.adjusted(10, 6, -10, -6), Qt.AlignCenter | Qt.TextWordWrap, self.text)


TREATS = {"Dog": "bone", "Cat": "fish", "Bear": "honey", "Hamster": "seed", "Rabbit": "carrot"}


class Treat(DesktopThing):
    """Drops down next to a pet, which walks over and eats it."""
    SIZE = 30

    def __init__(self, pet, x, ground_y):
        super().__init__(self.SIZE, self.SIZE)
        self.pet, self.kind = pet, TREATS.get(pet.info["kind"], "bone")
        self.fx, self.fy, self.ground_y, self.vy = x, ground_y - 90.0, ground_y, 0.0
        self.landed = False
        self.eaten = 0.0  # 0..1, shrinks as it's eaten
        self.timer = QTimer(self, interval=33, timeout=self.tick)
        self.expire = QTimer(self, singleShot=True, interval=120000, timeout=self.finish)
        self.place()
        self.appear(pet.app)
        self.timer.start()
        self.expire.start()

    def place(self):
        self.move(int(self.fx - self.SIZE / 2), int(self.fy - self.SIZE + 2))

    def tick(self):
        if not self.landed:
            self.vy += GRAVITY
            self.fy = min(self.fy + self.vy, self.ground_y)
            if self.fy >= self.ground_y:
                self.landed = True
                self.timer.stop()
                self.pet.treat_landed(self)
        self.place()

    def finish(self):
        if self.pet.treat is self:
            self.pet.treat = None
        self.timer.stop()
        self.hide()
        self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = 1 - self.eaten * 0.75
        p.translate(self.SIZE / 2, self.SIZE - 4)
        p.scale(s, s)
        dark = QPen(QColor(70, 60, 55), 1.2)
        if self.kind == "bone":
            p.setPen(dark)
            p.setBrush(QColor(245, 238, 222))
            path = QPainterPath()
            path.addRoundedRect(QRectF(-8, -7, 16, 5), 2, 2)
            for x in (-9, 9):
                for y in (-8.5, -3.5):
                    path.addEllipse(QPointF(x, y), 3.2, 3.2)
            p.drawPath(path.simplified())
        elif self.kind == "fish":
            p.setPen(dark)
            p.setBrush(QColor(120, 170, 210))
            p.drawEllipse(QRectF(-9, -10, 14, 8))
            tail = QPainterPath(QPointF(4, -6))
            tail.lineTo(QPointF(10, -11))
            tail.lineTo(QPointF(10, -1))
            tail.closeSubpath()
            p.drawPath(tail)
            p.setBrush(QColor(30, 30, 36))
            p.drawEllipse(QPointF(-5.5, -7), 1, 1)
        elif self.kind == "honey":
            p.setPen(dark)
            p.setBrush(QColor(214, 150, 60))
            p.drawRoundedRect(QRectF(-8, -14, 16, 14), 5, 5)
            p.setBrush(QColor(250, 200, 70))
            p.drawEllipse(QRectF(-9, -17, 18, 6))
            p.setFont(QFont("Segoe UI", 5, QFont.Bold))
            p.setPen(QColor(110, 60, 20))
            p.drawText(QRectF(-8, -11, 16, 8), Qt.AlignCenter, "HONEY")
        elif self.kind == "carrot":
            p.setPen(dark)
            p.setBrush(QColor(240, 140, 50))
            body = QPainterPath(QPointF(-10, -6))
            body.quadTo(QPointF(-4, -11), QPointF(6, -8))
            body.lineTo(QPointF(6, -3))
            body.quadTo(QPointF(-4, -2), QPointF(-10, -6))
            p.drawPath(body)
            p.setPen(round_pen(QColor(90, 170, 80), 2))
            for dy in (-3, 0, 3):
                p.drawLine(QPointF(6, -5.5), QPointF(11, -5.5 + dy))
        else:  # sunflower seed
            p.setPen(dark)
            p.setBrush(QColor(60, 55, 55))
            p.drawEllipse(QRectF(-6, -9, 12, 7))
            p.setPen(QPen(QColor(230, 230, 230), 1))
            p.drawLine(QPointF(-4, -5.5), QPointF(4, -5.5))


class Ball(DesktopThing):
    """A beach ball that bounces along fences. Throw it with the mouse; the pets chase and kick it."""
    R = 11

    def __init__(self, app):
        super().__init__(2 * self.R + 4, 2 * self.R + 4)
        self.app = app
        self.fx = self.fy = 0.0
        self.vx = self.vy = 0.0
        self.spin = 0.0
        self.held = None
        self.platforms = []
        self.ticks = 0
        self.timer = QTimer(self, interval=33, timeout=self.tick)

    def throw_in(self, x=None, y=None):
        area = QApplication.primaryScreen().availableGeometry()
        self.fx = float(x if x is not None else area.center().x())
        self.fy = float(y if y is not None else area.top() + 80)
        self.vx, self.vy = random.uniform(-5, 5), -3.0
        self.platforms = find_platforms(self.app, self.fx, self.fy)
        self.place()
        self.appear(self.app)
        self.timer.start()
        for pet in self.app.pet_windows:  # everyone wants it
            if pet.state in FREE and random.random() < 0.7:
                pet.set_state("fetch", random.randint(200, 320))

    def place(self):
        self.move(int(self.fx - self.R - 2), int(self.fy - 2 * self.R - 2))

    def resting_on(self):
        return next((p for p in self.platforms if p[0] <= self.fx <= p[1] and abs(p[2] - self.fy) < 1.5), None)

    def kick(self, direction):
        self.vx = direction * random.uniform(4, 7.5)
        self.vy = -random.uniform(3, 6.5)
        self.fy -= 1

    def tick(self):
        self.ticks += 1
        if self.ticks % 30 == 0:
            self.platforms = find_platforms(self.app, self.fx, self.fy)
        if self.ticks % 90 == 0:
            stay_above_fences(self.app, self)
        if self.held:
            pos = QCursor.pos()
            self.vx = (pos.x() - self.fx) * 0.6 + self.vx * 0.4  # smoothed throwing speed
            self.vy = (pos.y() + self.R - self.fy) * 0.6 + self.vy * 0.4
            self.fx, self.fy = float(pos.x()), float(pos.y() + self.R)
        elif self.resting_on() and abs(self.vy) < 0.01:
            self.fx += self.vx
            self.vx *= 0.97  # rolling to a stop
            if abs(self.vx) < 0.05:
                self.vx = 0.0
            self.walls()
            if not self.resting_on():  # rolled off the edge
                self.vy = 0.1
        else:
            self.vy += GRAVITY * 0.9
            new_y = self.fy + self.vy
            self.fx += self.vx
            self.walls()
            landing = None
            if self.vy > 0:
                landing = min((p for p in self.platforms if p[0] <= self.fx <= p[1] and self.fy <= p[2] <= new_y + 1),
                              key=lambda p: p[2], default=None)
            if landing:
                self.fy = float(landing[2])
                self.vy = -self.vy * 0.55 if self.vy > 2.5 else 0.0  # bounce, until it settles
                self.vx *= 0.9
            else:
                self.fy = new_y
        self.spin += self.vx / self.R
        self.place()
        self.update()

    def walls(self):
        area = screen_area(self.fx, self.fy)
        if self.fx < area.left() + self.R:
            self.fx, self.vx = float(area.left() + self.R), abs(self.vx) * 0.7
        elif self.fx > area.right() - self.R:
            self.fx, self.vx = float(area.right() - self.R), -abs(self.vx) * 0.7
        if self.fy > area.bottom() + 1:
            self.fy, self.vy = float(area.bottom() + 1), 0.0

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.held = True

    def mouseReleaseEvent(self, e):
        if self.held:
            self.held = None
            self.vx, self.vy = max(-18.0, min(18.0, self.vx)), max(-18.0, min(18.0, self.vy))
            for pet in self.app.pet_windows:
                if pet.state in FREE and random.random() < 0.6:
                    pet.set_state("fetch", random.randint(200, 320))

    def contextMenuEvent(self, e):
        menu = QMenu()
        menu.addAction("Put the ball away", self.app.put_ball_away)
        menu.exec(e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.R + 2, self.R + 2)
        ball = QPainterPath()
        ball.addEllipse(c, self.R, self.R)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(ball)
        p.setClipPath(ball)
        for i, color in enumerate(("#E8504A", "#F5C342", "#4A90E2")):  # three rotating panels
            a = self.spin + i * 2 * math.pi / 3
            wedge = QPainterPath(c)
            wedge.arcTo(QRectF(c.x() - self.R, c.y() - self.R, 2 * self.R, 2 * self.R), -math.degrees(a), -60)
            wedge.closeSubpath()
            p.setBrush(QColor(color))
            p.drawPath(wedge)
        p.setClipping(False)
        p.setBrush(QColor(255, 255, 255, 90))
        p.drawEllipse(QPointF(c.x() - 4, c.y() - 4), 3, 2)  # shine
        p.setPen(QPen(QColor(60, 60, 70), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(ball)


class Pet(QWidget):
    def __init__(self, app, info):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnBottomHint)
        self.app = app
        self.info = info  # {"kind", "color", "name"}: the pet's entry in the app's pet list, edited in place
        self.scale = 1.0
        self.stage = None  # the Console mode window we're on, instead of the desktop
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(W, H)
        screen = QApplication.primaryScreen().availableGeometry()
        # Spread out along the bottom, so several pets don't start in one heap.
        self.fx = float(random.randint(screen.left() + W, screen.right() - W))
        self.fy = float(screen.bottom() + 1)
        self.vx, self.vy = 0.0, 0.0
        self.dir = 1
        self.state, self.state_left = "idle", 60
        self.ticks = 0
        self.heart = 0
        self.platforms = []
        self.drag = None
        self.partner = None  # the pet we're chasing / fleeing from / riding
        self.rider = None  # the hamster on our back
        self.goal = None  # what we're walking over to goal_x for: "sleep", or a bear to climb on
        self.goal_x = 0.0
        self.social_cd = random.randint(100, 400)  # ticks until we're up for meeting someone again
        self.treat = None
        self.bubble = None
        self.bubble_left = 0
        self.timer = QTimer(self, interval=33, timeout=self.tick)
        self.order_timer = QTimer(self, interval=3000, timeout=self.stay_above_fences)

    def start(self):
        self.platforms = self.find_platforms()
        self.place()
        self.show()
        if self.stage:  # an owned window stays above its owner, and hides when that's minimized
            if IS_WINDOWS:
                user32.SetWindowLongPtrW(int(self.winId()), -8, self.stage)
            else:
                self.windowHandle().setTransientParent(self.app.console.windowHandle())
            self.lift()  # owning doesn't move it: Qt opened us at the bottom of the stack
        else:
            pin_to_desktop(int(self.winId()))
        self.timer.start()
        self.order_timer.start()
        self.stay_above_fences()

    def set_stage(self, hwnd, scale, floor):
        """Move onto the Console mode window at this size (hwnd None: back to the desktop)."""
        self.stage, self.scale = hwnd, scale
        # Stays-on-bottom forces the window down on every move, and Windows then drags its owner
        # down with it: left on, it sinks the whole console below every other window.
        self.setWindowFlag(Qt.WindowStaysOnBottomHint, not hwnd)
        self.resize(int(W * scale), int(H * scale))
        self.fy = float(floor)
        self.vx, self.vy = 0.0, 0.0

    def stop(self):
        self.timer.stop()
        self.order_timer.stop()
        self.timer.setInterval(33)  # we may have been asleep
        self.set_state("idle", 30)
        self.partner = self.rider = self.goal = None
        self.drop_bubble()
        if self.treat is not None:
            self.treat.finish()
        self.hide()

    def colors(self):
        return [QColor(c) for c in PALETTES.get(self.info["color"], PALETTES["Orange"])]

    # -- world

    def screen_rect(self):
        return screen_area(self.fx, self.fy)

    def find_platforms(self):
        return find_platforms(self.app, self.fx, self.fy)

    def platform_under(self):
        return next((p for p in self.platforms if p[0] <= self.fx <= p[1] and abs(p[2] - self.fy) < 2), None)

    def stay_above_fences(self):
        if not self.stage:
            stay_above_fences(self.app, self)
        elif self.app.console.isActiveWindow():  # not over other apps you've switched to
            self.lift()

    def lift(self):
        if IS_WINDOWS:
            user32.SetWindowPos(int(self.winId()), 0, 0, 0, 0, 0, 0x13)  # HWND_TOP, NOSIZE|NOMOVE|NOACTIVATE
        else:
            self.raise_()

    def place(self):
        self.move(int(self.fx - FOOT.x() * self.scale), int(self.fy - FOOT.y() * self.scale))
        if self.bubble is not None:
            sp = self.spec()
            top = self.fy - (sp["legs"] + sp["body"][1] + sp["heart"] + 8) * self.scale
            self.bubble.move(int(self.fx - self.bubble.width() / 2), int(top - self.bubble.height()))

    # -- talking, treats

    def announce(self, text, on_top=True, ticks=200):
        """Say something in a speech bubble; important things (meetings) float over your apps."""
        self.drop_bubble()
        self.bubble = Bubble(f"{self.info['name']}: {text}" if on_top and text != "!" else text, on_top)
        self.bubble_left = ticks
        self.place()
        self.bubble.appear(self.app)
        if self.state == "sleep":
            self.timer.setInterval(33)
            self.set_state("sit", 40)
        if on_top and self.state in FREE + ("greet",):  # a little hop to get your attention
            self.vy, self.vx = -6.0, 0.0
            self.set_state("jump")

    def drop_bubble(self):
        if self.bubble is not None:
            self.bubble.hide()
            self.bubble.deleteLater()
            self.bubble = None

    def give_treat(self):
        ground = self.platform_under()
        if ground is None or self.state in ("held", "ride"):
            ground = self.platforms[0]  # in the air or on the bear: it lands on the floor
        if self.treat is not None:
            self.treat.finish()
        if self.state == "sleep":
            self.timer.setInterval(33)
            self.set_state("sit", 20)
        x = min(max(self.fx + self.dir * 70, ground[0] + 16), ground[1] - 16)
        if abs(x - self.fx) < 40:  # at the edge: put it on the other side
            x = min(max(self.fx - self.dir * 70, ground[0] + 16), ground[1] - 16)
        self.treat = Treat(self, x, ground[2])

    def treat_landed(self, treat):
        if self.state in FREE + ("greet", "fetch", "chase", "flee"):
            self.walk_over(treat)

    def eat(self):
        treat = self.treat
        self.state_left -= 1
        if treat is None:
            self.set_state("sit", 30)
            return
        treat.eaten = 1 - self.state_left / 80
        treat.update()
        if self.state_left <= 0:
            treat.finish()
            self.heart = 45
            self.announce("Yum!", on_top=False, ticks=60)
            self.set_state("sit", random.randint(60, 120))

    # -- behaviour

    def set_state(self, state, ticks=0):
        if self.state == "ride" and state != "ride":  # got off (or was picked up): free the bear
            if self.partner is not None and self.partner.rider is self:
                self.partner.rider = None
            self.partner = None
        self.state, self.state_left = state, ticks

    def tick(self):
        self.ticks += 1
        if self.ticks % 30 == 0:
            self.platforms = self.find_platforms()
        self.heart = max(0, self.heart - 1)
        self.social_cd = max(0, self.social_cd - 1)
        if self.bubble is not None:
            self.bubble_left -= 1
            if self.bubble_left <= 0:
                self.drop_bubble()
        s = self.state
        if s == "held":
            pos = QCursor.pos()
            self.vx, self.vy = pos.x() - self.fx, 0
            self.fx, self.fy = float(pos.x()), float(pos.y() + 34 * self.scale)
        elif s in ("fall", "jump"):
            self.vy += GRAVITY
            new_y = self.fy + self.vy
            self.fx += self.vx
            area = self.screen_rect()
            self.fx = min(max(self.fx, area.left() + 10), area.right() - 10)
            landing = None
            if self.vy > 0:
                landing = min((p for p in self.platforms if p[0] <= self.fx <= p[1] and self.fy <= p[2] <= new_y + 1),
                              key=lambda p: p[2], default=None)
            if landing:
                self.fy, self.vx, self.vy = float(landing[2]), 0.0, 0.0
                self.set_state("idle", random.randint(20, 50))
            else:
                self.fy = min(new_y, area.bottom() + 1.0)
                if self.fy >= area.bottom() + 1:
                    self.vy, self.vx = 0.0, 0.0
                    self.set_state("idle", 30)
        elif s == "ride":
            self.ride()
        else:
            ground = self.platform_under()
            if ground is None:  # the fence under us moved or vanished
                self.set_state("fall")
                self.vy = 0.0
            elif idle_seconds() > 120 and s not in ("sleep", "approach"):
                self.settle_down(ground)
            elif s == "sleep":
                if idle_seconds() < 2:  # you're back: wake up and stretch
                    self.timer.setInterval(33)
                    self.set_state("sit", 40)
            elif s == "walk":
                self.walk(ground)
            elif s in ("chase", "flee", "approach", "fetch"):
                self.run(ground)
            elif s == "eat":
                self.eat()
            else:
                self.state_left -= 1
                if self.state_left <= 0:
                    self.next_action(ground)
        self.place()
        self.update()

    def walk(self, ground):
        other = self.pet_ahead(ground)
        if other is not None:
            if not self.social_cd and not other.social_cd and other.state in FREE and random.random() < 0.6:
                self.greet(other)
                return
            self.vx, self.dir = -self.vx, -self.dir  # bump: turn around rather than walk through them
        self.fx += self.vx
        left, right = ground[0] + 8, ground[1] - 8
        if self.fx < left or self.fx > right:
            if ground[3] != "floor" and random.random() < 0.4:
                self.set_state("fall")  # step off the edge
                self.vy = -2.0
                return
            self.fx = min(max(self.fx, left), right)
            self.vx, self.dir = -self.vx, -self.dir
        self.state_left -= 1
        if self.state_left <= 0:
            self.next_action(ground)

    def next_action(self, ground):
        if self.treat is not None and self.treat.landed and self.treat_on(ground):
            self.walk_over(self.treat)  # we were interrupted on the way to it
            return
        if self.ball_on(ground) and random.random() < 0.5:
            self.set_state("fetch", random.randint(200, 320))
            return
        if not self.social_cd and self.try_social(ground):
            return
        roll = random.random()
        if roll < 0.18 and self.try_jump(ground):
            return
        if roll < 0.55:
            self.dir = random.choice((-1, 1))
            self.vx = self.speed() * self.dir
            self.set_state("walk", random.randint(60, 220))
        elif roll < 0.8:
            self.set_state("idle", random.randint(40, 120))
        else:
            self.set_state("sit", random.randint(90, 240))

    def try_jump(self, ground, only=None):
        """Jump up onto a fence that's within reach above (or onto that one, if given)."""
        def reach(p):  # horizontal distance to the nearest point of that platform
            return 0 if p[0] <= self.fx <= p[1] else min(abs(self.fx - p[0]), abs(self.fx - p[1]))
        options = [p for p in self.platforms
                   if p != ground and 20 < self.fy - p[2] < 260 and p[1] - p[0] > 40 and reach(p) < 260
                   and (only is None or p == only)]
        if not options:
            return False
        target = random.choice(options)
        tx = min(max(self.fx, target[0] + 24), target[1] - 24)
        rise = self.fy - target[2]
        vy0 = -math.sqrt(2 * GRAVITY * (rise + 30))
        t = (-vy0 + math.sqrt(vy0 * vy0 - 2 * GRAVITY * rise)) / GRAVITY  # time until we come down onto it
        self.vy, self.vx = vy0, (tx - self.fx) / t
        self.dir = 1 if self.vx >= 0 else -1
        self.set_state("jump")
        return True

    # -- getting along with the other pets

    def others(self):
        return [o for o in self.app.pet_windows if o is not self and o.isVisible()]

    def alive(self, other):
        return other is not None and other in self.app.pet_windows and other.isVisible()

    def pet_ahead(self, ground):
        """Another pet right in front of us on the same fence (or floor)."""
        return next((o for o in self.others() if o.state != "ride" and abs(o.fy - self.fy) < 2
                     and 0 < (o.fx - self.fx) * self.dir < self.reach_of(o) and o.platform_under() == ground), None)

    def reach_of(self, other):
        """How close (centre to centre) we can stand to another pet without overlapping it."""
        return ((self.spec()["body"][0] + other.spec()["body"][0]) / 2 + 4) * self.scale

    def greet(self, other):
        """Stop, face each other and share a heart."""
        for pet, toward in ((self, other.fx), (other, self.fx)):
            pet.dir = 1 if toward > pet.fx else -1
            pet.vx = 0.0
            pet.set_state("greet", random.randint(50, 80))
            pet.heart = 45
            pet.social_cd = random.randint(300, 600)

    def try_social(self, ground):
        """Maybe start something with another pet: a ride, a game of chase, or following a friend."""
        mates = [o for o in self.others() if o.state in FREE and o.platform_under() == ground]
        kind = self.info["kind"]
        if kind == "Hamster" and random.random() < 0.3:
            bears = [o for o in mates if o.info["kind"] == "Bear" and o.rider is None and abs(o.fx - self.fx) < 300]
            if bears:
                self.walk_over(random.choice(bears))
                return True
        if kind != "Bear":  # bears are too dignified for chase
            prey = [o for o in mates if o.info["kind"] != "Bear" and not o.social_cd and abs(o.fx - self.fx) < 350]
            cats = [o for o in prey if o.info["kind"] == "Cat"]
            if kind == "Dog" and cats and random.random() < 0.35:
                self.start_chase(random.choice(cats))
                return True
            if prey and random.random() < 0.12:
                self.start_chase(random.choice(prey))
                return True
        if random.random() < 0.12:  # hop up to a friend sitting on a fence above
            spots = [o.platform_under() for o in self.others() if o.state in FREE]
            spots = [p for p in spots if p is not None and p != ground and p in self.platforms]
            if spots and self.try_jump(ground, only=random.choice(spots)):
                return True
        return False

    def start_chase(self, prey):
        self.partner, prey.partner = prey, self
        self.set_state("chase", 260)
        prey.set_state("flee", 260)
        prey.dir = 1 if prey.fx >= self.fx else -1

    def walk_over(self, goal):
        """Head somewhere on purpose: to goal_x to nap by a friend ("sleep"), or up to a bear to ride."""
        self.goal = goal
        if isinstance(goal, Pet):
            self.goal_x = goal.fx
        elif isinstance(goal, Treat):
            self.goal_x = goal.fx
        # Long enough to cross the screen at walking pace, then give up.
        self.set_state("approach", int(abs(self.goal_x - self.fx) / (self.speed() * 1.2)) + 90)

    def run(self, ground):
        """Chasing, fleeing or walking over somewhere: like walking, with a purpose and a time limit."""
        left, right = ground[0] + 8, ground[1] - 8
        self.state_left -= 1
        s = self.state
        if s == "chase":
            prey = self.partner
            if not self.alive(prey) or prey.state != "flee" or self.state_left <= 0:
                self.set_state("sit", random.randint(60, 120))  # gave up: sit and catch our breath
                if self.alive(prey) and prey.state == "flee":
                    prey.set_state("sit", random.randint(60, 120))
                self.social_cd = random.randint(400, 800)
                return
            self.dir = 1 if prey.fx > self.fx else -1
            self.fx = min(max(self.fx + self.dir * CHASE_SPEED * self.scale, left), right)
            if abs(prey.fx - self.fx) < 16 * self.scale:  # tag! both bounce with joy
                for pet in (self, prey):
                    pet.heart = 45
                    pet.vy, pet.vx = -6.0, 0.0
                    pet.partner = None
                    pet.set_state("jump")
                    pet.social_cd = random.randint(500, 900)
        elif s == "flee":
            chaser = self.partner
            if not self.alive(chaser) or chaser.state != "chase":
                self.set_state("idle", 40)
                return
            self.fx += self.dir * FLEE_SPEED * self.scale
            if self.fx < left or self.fx > right:  # cornered: dodge back past them
                self.fx = min(max(self.fx, left), right)
                self.dir = -self.dir
        elif s == "fetch":
            ball = self.app.ball
            if not self.ball_on(ground) or self.state_left <= 0:
                self.set_state("sit", random.randint(40, 90))
                return
            self.dir = 1 if ball.fx > self.fx else -1
            reach = self.spec()["body"][0] / 2 + ball.R
            if abs(ball.fx - self.fx) > reach - 4:
                self.fx = min(max(self.fx + self.dir * CHASE_SPEED * 0.9, left), right)
            if abs(ball.fx - self.fx) < reach and ball.fy > self.fy - 34:  # got it: kick!
                ball.kick(self.dir)
                self.heart = 30
                if random.random() < 0.35:
                    self.set_state("sit", random.randint(40, 90))
        else:  # approach
            goal = self.goal
            if isinstance(goal, Treat):
                if goal is not self.treat or not self.treat_on(ground):
                    self.set_state("idle", 40)
                    return
                x = goal.fx
                self.dir = 1 if x > self.fx else -1
                self.fx += self.dir * min(self.speed() * 1.6, abs(x - self.fx))  # hurry
                if abs(x - self.fx) < self.spec()["body"][0] / 2 + 4:
                    self.set_state("eat", 80)
                elif self.state_left <= 0:
                    self.set_state("idle", 40)
                return
            if isinstance(goal, Pet):
                if not self.alive(goal) or goal.state not in FREE or goal.rider is not None:
                    self.set_state("idle", 40)
                    return
                self.goal_x = goal.fx
            elif idle_seconds() < 2:  # you're back before we got there
                self.set_state("sit", 40)
                return
            x = min(max(self.goal_x, left), right)
            self.dir = 1 if x > self.fx else -1
            self.fx += self.dir * min(self.speed() * 1.2, abs(x - self.fx))
            if isinstance(goal, Pet):
                if abs(goal.fx - self.fx) < 10 and abs(goal.fy - self.fy) < 2:
                    self.start_ride(goal)
                elif self.state_left <= 0:
                    self.set_state("idle", 40)
            elif abs(x - self.fx) < 1 or self.state_left <= 0:
                friend = min(self.others(), key=lambda o: abs(o.fx - self.fx), default=None)
                if friend is not None and abs(friend.fx - self.fx) > 1:
                    self.dir = 1 if friend.fx > self.fx else -1  # snuggle up facing them
                self.fall_asleep()

    def treat_on(self, ground):
        t = self.treat
        return t is not None and ground[0] <= t.fx <= ground[1] and abs(t.ground_y - ground[2]) < 2

    def ball_on(self, ground):
        """The ball is on our fence (or flying over it), so we can go after it."""
        b = self.app.ball
        return (b is not None and not b.held and ground[0] <= b.fx <= ground[1] and b.fy <= ground[2] + 2
                and ground[2] - b.fy < 260)

    def settle_down(self, ground):
        """You've gone away: nap - next to a friend who's already asleep on this fence, if there is one."""
        # Everyone asleep on this fence, and everyone on their way to a spot beside them.
        pile = [(o.fx, o) for o in self.others() if o.state == "sleep" and o.platform_under() == ground]
        pile += [(o.goal_x, o) for o in self.others()
                 if o.state == "approach" and o.goal == "sleep" and o.platform_under() == ground]
        left, right = ground[0] + 20, ground[1] - 20
        # Free spots at either end of the pile, so it grows outwards.
        spots = [x + side * self.reach_of(o) for x, o in pile for side in (-1, 1)]
        spots = [x for x in spots if left <= x <= right and all(abs(x - t) > 30 * self.scale for t, _ in pile)]
        spot = min(spots, key=lambda x: abs(x - self.fx), default=None)
        if spot is not None and abs(spot - self.fx) > 6:
            self.goal_x = spot
            self.walk_over("sleep")
        else:
            self.fall_asleep()

    def fall_asleep(self):
        self.vx = 0.0
        self.goal = None
        self.set_state("sleep")
        self.timer.setInterval(120)

    def start_ride(self, bear):
        self.partner, bear.rider = bear, self
        self.goal = None
        self.set_state("ride", random.randint(250, 500))
        self.heart = 45
        user32.SetWindowPos(int(bear.winId()), int(self.winId()), 0, 0, 0, 0, 0x13)  # we're drawn on top
        self.ride()

    def ride(self):
        bear = self.partner
        self.state_left -= 1
        if (not self.alive(bear) or bear.rider is not self or bear.state not in FREE + ("greet",)
                or self.state_left <= 0):
            self.set_state("jump")  # hop off
            self.vx, self.vy = random.choice((-1.5, 1.5)), -4.0
            return
        sp = bear.spec()
        self.dir = bear.dir
        self.fx = bear.fx - bear.dir * 4 * self.scale
        self.fy = bear.fy - (sp["legs"] + sp["body"][1] - 5) * self.scale

    # -- mouse

    def mousePressEvent(self, e):
        if self.stage:  # on the TV pets are just there to watch: don't take the focus from the console
            self.app.console.activateWindow()
            return
        if e.button() == Qt.LeftButton:
            self.drag = (QCursor.pos(), False)
            self.timer.setInterval(33)

    def mouseMoveEvent(self, e):
        if self.drag and (QCursor.pos() - self.drag[0]).manhattanLength() > 6:
            self.drag = (self.drag[0], True)
            self.set_state("held")

    def mouseReleaseEvent(self, e):
        if not self.drag:
            return
        moved = self.drag[1]
        self.drag = None
        if moved:
            self.vx = max(-12.0, min(12.0, self.vx))
            self.set_state("fall")
        else:  # a click: a happy hop
            self.heart = 45
            self.vy, self.vx = -7.0, 0.0
            self.set_state("jump")

    def contextMenuEvent(self, e):
        if self.stage:
            return
        menu = QMenu()
        menu.addAction(f"Pet {self.info['name']}", lambda: setattr(self, "heart", 45))
        menu.addAction(f"Give {self.info['name']} a treat", self.give_treat)
        if self.app.ball is None:
            menu.addAction("Throw a ball", lambda: self.app.throw_ball())
        else:
            menu.addAction("Put the ball away", self.app.put_ball_away)
        if self.state == "sleep":
            menu.addAction("Wake up", lambda: (self.timer.setInterval(33), self.set_state("sit", 40)))
        menu.addAction("Settings…", lambda: self.app.open_settings())
        if len(self.app.pets) > 1:
            menu.addAction(f"Send {self.info['name']} home", lambda: self.app.remove_pet(self.info))
        menu.addAction("Hide pets" if len(self.app.pets) > 1 else "Hide pet",
                       lambda: self.app.set_option("pet_enabled", False))
        menu.exec(e.globalPos())

    def enterEvent(self, _):
        self.setToolTip(self.info["name"])

    # -- drawing

    def spec(self):
        return SPECIES.get(self.info["kind"], SPECIES["Cat"])

    def speed(self):
        return self.spec()["speed"] * self.scale

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        colors = self.colors()
        sp = self.spec()
        t = self.ticks
        s = self.state
        p.scale(self.scale, self.scale)
        p.translate(FOOT.x(), FOOT.y())
        if self.dir < 0:
            p.scale(-1, 1)

        if s == "sleep":
            self.paint_sleeping(p, sp, colors, t)
            return
        body, light, dark = colors
        outline = QPen(dark, 1.6)
        sitting = s in ("sit", "ride")
        running = s in ("chase", "flee", "fetch")
        walking = s in ("walk", "approach") or running
        airborne = s in ("fall", "jump", "held")
        bw, bh = sp["body"]
        legs = sp["legs"]
        if walking and sp.get("hop"):
            bob = -abs(math.sin(t * 0.32)) * 8  # rabbits bound along
        else:
            bob = math.sin(t * 0.5) * 1.2 if walking else 0

        if sitting:
            body_rect = QRectF(-bw * 0.4, -bh * 1.3, bw * 0.8, bh * 1.3)
            hx, hy = 3, body_rect.top() + 2 - sp["head"][1] * 0.25
        else:
            body_rect = QRectF(-bw / 2, -legs - bh + 3 + bob, bw, bh)
            hx = bw / 2 - 7
            hy = body_rect.top() + (8 if sp["face"] == "cheeks" else 1)

        self.paint_tail(p, sp, colors, body_rect, t, walking or self.heart > 0)

        # legs
        p.setPen(outline)
        p.setBrush(body)
        if sitting:
            p.drawEllipse(QRectF(-bw * 0.32, -8, 12, 8))
            p.drawEllipse(QRectF(bw * 0.32 - 12, -8, 12, 8))
        elif sp.get("hop") and walking:
            p.drawEllipse(QRectF(-bw / 2 + 2, -9 + bob, 16, 9))  # big back feet, tucked mid-hop
            p.drawEllipse(QRectF(bw / 2 - 12, -8 + bob, 9, 8))
        else:
            speed = (0.9 if sp["face"] == "cheeks" else 0.5) * (1.8 if running else 1)  # hamsters scurry
            step = math.sin(t * speed) * 4 if walking else 0
            dangle = 4 if airborne else 0
            span = bw / 2 - 5
            for x, phase in ((-span, 1), (-span + 8, -1), (span - 8, -1), (span - 1, 1)):
                p.drawRoundedRect(QRectF(x - 3 + step * phase * 0.5, -legs - 2 + bob, 6, legs + 2 + dangle), 3, 3)

        # body
        p.drawEllipse(body_rect)
        p.setPen(Qt.NoPen)
        p.setBrush(light)
        p.drawEllipse(QRectF(body_rect.center().x() - 2, body_rect.center().y() - 2, bw * 0.4, body_rect.height() * 0.5))

        self.paint_head(p, sp, colors, hx, hy, t, blink=(t % 110) < 4)
        if self.heart:
            self.paint_heart(p, hx, hy - sp["heart"] - (45 - self.heart) * 0.4, self.heart / 45)

    def paint_tail(self, p, sp, colors, body_rect, t, lively):
        body, light, dark = colors
        rear, mid = body_rect.left() + 4, body_rect.center().y()
        kind = sp["tail"]
        if kind == "long":  # cat: a long curling tail
            swish = math.sin(t * (0.25 if lively else 0.08)) * 6
            path = QPainterPath(QPointF(rear, mid))
            path.cubicTo(QPointF(rear - 14, mid - 4), QPointF(rear - 14 + swish, mid - 22), QPointF(rear - 6 + swish, mid - 28))
            self.stroke(p, path, dark, body, 6.5)
        elif kind == "wag":  # dog: short and wagging, faster when happy
            wag = math.sin(t * (0.9 if lively else 0.15)) * 7
            path = QPainterPath(QPointF(rear + 2, mid - 4))
            path.quadTo(QPointF(rear - 10, mid - 8), QPointF(rear - 8 + wag * 0.4, mid - 20 - abs(wag) * 0.3))
            self.stroke(p, path, dark, body, 6)
        elif kind == "stub":  # bear
            p.setPen(QPen(dark, 1.6))
            p.setBrush(body)
            p.drawEllipse(QRectF(rear - 6, mid - 8, 9, 9))
        elif kind == "puff":  # rabbit: a cotton tail
            p.setPen(QPen(dark, 1.2))
            p.setBrush(QColor("#FFFFFF") if body.lightness() < 220 else light)
            p.drawEllipse(QRectF(rear - 8, mid - 9, 12, 12))

    def stroke(self, p, path, dark, body, width):
        p.setBrush(Qt.NoBrush)
        p.setPen(round_pen(dark, width))
        p.drawPath(path)
        p.setPen(round_pen(body, width - 2))
        p.drawPath(path)

    def paint_ears(self, p, sp, colors, hx, hy, hw, hh, t, lying=False):
        body, light, dark = colors
        outline = QPen(dark, 1.6)
        pink = QColor(245, 150, 170)
        kind = sp["ears"]
        p.setPen(outline)
        p.setBrush(body)
        if kind == "pointy":
            for side in (-1, 1):
                ear = QPainterPath(QPointF(hx + side * 4, hy - hh * 0.35))
                ear.lineTo(QPointF(hx + side * 13, hy - hh * 0.35 - 10))
                ear.lineTo(QPointF(hx + side * 13, hy - hh * 0.15))
                ear.closeSubpath()
                p.drawPath(ear)
        elif kind in ("round", "tiny"):
            r = 6.5 if kind == "round" else 4.5
            for side in (-1, 1):
                cx, cy = hx + side * hw * 0.36, hy - hh * 0.38
                p.setPen(outline)
                p.setBrush(body)
                p.drawEllipse(QPointF(cx, cy), r, r)
                p.setPen(Qt.NoPen)
                p.setBrush(light if kind == "round" else pink)
                p.drawEllipse(QPointF(cx, cy), r * 0.5, r * 0.5)
        elif kind == "long":
            sway = math.sin(t * 0.07) * 4
            for side in (-1, 1):
                p.save()
                p.translate(hx + side * 5, hy - hh * 0.35)
                p.rotate(-75 if lying else side * 8 + sway * (1 if side > 0 else 0.5))
                p.setPen(outline)
                p.setBrush(body)
                p.drawEllipse(QRectF(-4.5, -26, 9, 28))
                p.setPen(Qt.NoPen)
                p.setBrush(pink)
                p.drawEllipse(QRectF(-2, -22, 4, 19))
                p.restore()

    def paint_floppy_ears(self, p, colors, hx, hy, hw, t):
        # Dog ears hang over the sides of the head, so they're drawn after it.
        dark = colors[2]
        flop = math.sin(t * 0.2) * 3
        for side in (-1, 1):
            p.save()
            p.translate(hx + side * hw * 0.5, hy - 9)  # hung from the edge of the head, clear of the eyes
            p.rotate(side * (10 + flop))
            p.setPen(QPen(dark.darker(130), 1.4))
            p.setBrush(dark)
            p.drawEllipse(QRectF(-4, -2, 8, 16))
            p.restore()

    def paint_head(self, p, sp, colors, hx, hy, t, blink=False, closed=False, lying=False):
        body, light, dark = colors
        hw, hh = sp["head"]
        outline = QPen(dark, 1.6)
        self.paint_ears(p, sp, colors, hx, hy, hw, hh, t, lying)
        p.setPen(outline)
        p.setBrush(body)
        p.drawEllipse(QRectF(hx - hw / 2, hy - hh / 2, hw, hh))
        if sp["ears"] == "floppy":
            self.paint_floppy_ears(p, colors, hx, hy, hw, t)

        face = sp["face"]
        p.setPen(Qt.NoPen)
        if face == "cheeks":  # hamster: big puffy cheeks
            p.setBrush(light)
            p.drawEllipse(QRectF(hx - hw / 2 - 2, hy + 1, 13, 10))
            p.drawEllipse(QRectF(hx + hw / 2 - 11, hy + 1, 13, 10))
        if face != "snout":
            p.setBrush(QColor(245, 140, 160, 140))  # blush
            p.drawEllipse(QRectF(hx - hw * 0.42, hy + 2, 5, 3))
            p.drawEllipse(QRectF(hx + hw * 0.42 - 5, hy + 2, 5, 3))

        # eyes
        size = 4.0 if face == "round" else 4.5
        p.setPen(round_pen(QColor(30, 30, 36), 1.8))
        if blink or closed:
            p.drawLine(QPointF(hx - 8, hy - 2), QPointF(hx - 4, hy - 2))
            p.drawLine(QPointF(hx + 4, hy - 2), QPointF(hx + 8, hy - 2))
        else:
            p.setBrush(QColor(30, 30, 36))
            p.drawEllipse(QRectF(hx - 8, hy - 5, size, size + 1))
            p.drawEllipse(QRectF(hx + 3.5, hy - 5, size, size + 1))

        p.setPen(Qt.NoPen)
        nose_dark = QColor(40, 34, 36)
        if face == "snout":  # dog: a muzzle sticking out front, big dark nose
            p.setBrush(light)
            p.drawEllipse(QRectF(hx, hy, 17, 11))
            p.setBrush(nose_dark)
            p.drawEllipse(QRectF(hx + 12, hy + 1, 6, 4.5))
            if self.heart and not closed:
                p.setBrush(QColor(235, 110, 130))
                p.drawRoundedRect(QRectF(hx + 7, hy + 9, 5, 6), 2.5, 2.5)  # happy tongue
        elif face == "round":  # bear
            p.setBrush(light)
            p.drawEllipse(QRectF(hx - 8, hy + 1, 16, 11))
            p.setBrush(nose_dark)
            p.drawEllipse(QRectF(hx - 3, hy + 2, 6, 4))
        else:  # cat, hamster, rabbit: small muzzle, pink nose
            p.setBrush(light)
            p.drawEllipse(QRectF(hx - 6, hy + 1, 12, 8))
            p.setBrush(QColor(230, 110, 130))
            p.drawEllipse(QRectF(hx - 1.5, hy + 2, 3, 2.2))
            if face == "teeth":
                p.setBrush(QColor(255, 255, 255))
                p.setPen(QPen(QColor(200, 200, 205), 0.6))
                p.drawRect(QRectF(hx - 2, hy + 6, 2, 2.6))
                p.drawRect(QRectF(hx, hy + 6, 2, 2.6))
        if sp.get("whiskers"):
            p.setPen(QPen(QColor(dark.red(), dark.green(), dark.blue(), 150), 0.8))
            for side in (-1, 1):
                for dy in (-1, 2):
                    p.drawLine(QPointF(hx + side * 6, hy + 4), QPointF(hx + side * 15, hy + 4 + dy))
        if self.app.pet_outfits:
            self.paint_outfit(p, outfit_today(), hx, hy, hw, hh, closed)

    def paint_outfit(self, p, outfit, hx, hy, hw, hh, closed):
        top = hy - hh / 2 + 3
        line = QPen(QColor(40, 30, 40), 1.2)
        if outfit == "sunglasses" and not closed:
            p.setPen(QPen(QColor(20, 20, 24), 1.4))
            p.setBrush(QColor(25, 25, 32, 235))
            p.drawRoundedRect(QRectF(hx - 10, hy - 6.5, 8.5, 6.5), 2.5, 2.5)
            p.drawRoundedRect(QRectF(hx + 1.5, hy - 6.5, 8.5, 6.5), 2.5, 2.5)
            p.drawLine(QPointF(hx - 1.5, hy - 4.5), QPointF(hx + 1.5, hy - 4.5))
            p.setPen(QPen(QColor(255, 255, 255, 120), 1))
            p.drawLine(QPointF(hx - 8, hy - 5), QPointF(hx - 6, hy - 3))
        elif outfit == "witch":
            p.setPen(line)
            p.setBrush(QColor(48, 36, 64))
            cone = QPainterPath(QPointF(hx - 9, top))
            cone.quadTo(QPointF(hx - 2, top - 12), QPointF(hx - 8, top - 24))  # floppy tip, bent back
            cone.quadTo(QPointF(hx + 4, top - 12), QPointF(hx + 9, top))
            cone.closeSubpath()
            p.drawPath(cone)
            p.drawEllipse(QRectF(hx - 16, top - 3, 32, 6))  # brim
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(150, 100, 210))
            p.drawRect(QRectF(hx - 8, top - 5, 16, 3))  # band
            p.setBrush(QColor(240, 200, 80))
            p.drawRect(QRectF(hx - 1.5, top - 5.5, 3, 4))  # buckle
        elif outfit == "santa":
            p.setPen(line)
            p.setBrush(QColor(215, 45, 50))
            cone = QPainterPath(QPointF(hx - 11, top))
            cone.quadTo(QPointF(hx - 4, top - 18), QPointF(hx - 16, top - 14))  # flopping over backwards
            cone.quadTo(QPointF(hx + 4, top - 22), QPointF(hx + 11, top))
            cone.closeSubpath()
            p.drawPath(cone)
            p.setPen(QPen(QColor(200, 200, 205), 0.8))
            p.setBrush(QColor(255, 255, 255))
            p.drawRoundedRect(QRectF(hx - 13, top - 3, 26, 6), 3, 3)  # fur trim
            p.drawEllipse(QPointF(hx - 16, top - 14), 3.5, 3.5)  # bobble
        elif outfit == "party":
            p.setPen(line)
            cone = QPainterPath(QPointF(hx - 7, top + 1))
            cone.lineTo(QPointF(hx + 1, top - 20))
            cone.lineTo(QPointF(hx + 8, top + 1))
            cone.closeSubpath()
            p.setBrush(QColor(90, 160, 240))
            p.drawPath(cone)
            p.save()
            p.setClipPath(cone)
            p.setPen(QPen(QColor(250, 210, 70), 3))
            for y in (top - 4, top - 11):
                p.drawLine(QPointF(hx - 10, y + 3), QPointF(hx + 10, y - 3))
            p.restore()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(240, 90, 120))
            p.drawEllipse(QPointF(hx + 1, top - 21), 3, 3)

    def paint_sleeping(self, p, sp, colors, t):
        body, light, dark = colors
        bw, bh = sp["body"]
        breathe = math.sin(t * 0.15) * 1.2
        p.setPen(QPen(dark, 1.6))
        p.setBrush(body)
        p.drawEllipse(QRectF(-bw / 2 - 4, -bh * 0.85 - breathe, bw + 6, bh * 0.85 + breathe))  # curled up
        if sp["tail"] in ("long", "wag"):  # tail wrapped around the front
            path = QPainterPath(QPointF(-bw / 2, -6))
            path.cubicTo(QPointF(-10, 4), QPointF(12, 4), QPointF(bw / 2, -4))
            self.stroke(p, path, dark, body, 6)
        hx, hy = bw / 2 - 8, -bh * 0.75 - breathe
        self.paint_head(p, sp, colors, hx, hy, 0, closed=True, lying=True)
        p.resetTransform()
        p.scale(self.scale, self.scale)
        f = QFont()
        f.setBold(True)
        for i in range(3):  # drifting z's
            phase = ((t // 3) + i * 12) % 36
            f.setPixelSize(9 + i * 2)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, int(220 * (1 - phase / 36))))
            p.drawText(QPointF(W / 2 + 14 + phase * 0.4, FOOT.y() - 30 - phase * 0.6 - i * 5), "z")

    def paint_heart(self, p, x, y, alpha):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(240, 80, 110, int(255 * alpha)))
        heart = QPainterPath(QPointF(x, y + 6))
        heart.cubicTo(QPointF(x - 10, y - 2), QPointF(x - 4, y - 10), QPointF(x, y - 4))
        heart.cubicTo(QPointF(x + 4, y - 10), QPointF(x + 10, y - 2), QPointF(x, y + 6))
        p.drawPath(heart)