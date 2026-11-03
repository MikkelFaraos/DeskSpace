"""Upcoming events from an iCal (.ics) link, e.g. Google Calendar's or Outlook's published calendar."""
import re
from datetime import date, datetime, timedelta

import icalendar
import recurring_ical_events
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest


def parse_events(data, days=31, now=None):
    """[(start, all_day, title)] for events that haven't ended yet, within the next `days` days."""
    cal = icalendar.Calendar.from_ical(data)
    now = now or datetime.now().astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = []
    for ev in recurring_ical_events.of(cal).between(today, today + timedelta(days=days)):
        start = ev.get("DTSTART").dt
        end = ev.get("DTEND").dt if ev.get("DTEND") else None
        all_day = not isinstance(start, datetime)
        start, end = _local(start), _local(end) if end else None
        if (end or start + timedelta(days=1 if all_day else 0)) <= now:
            continue
        events.append((start, all_day, str(ev.get("SUMMARY") or "(no title)")))
    events.sort(key=lambda e: e[0])
    return events


def _local(value):
    if isinstance(value, datetime):
        return value.astimezone()  # naive times are treated as local
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).astimezone()
    return value


class CalendarFeed(QObject):
    updated = Signal()

    def __init__(self, net, parent=None):
        super().__init__(parent)
        self.net = net
        self.events = []
        self.error = ""
        self.url = ""

    def load(self, url):
        self.url = url.strip()
        if not self.url:
            self.events, self.error = [], ""
            self.updated.emit()
            return
        url = re.sub(r"^webcal://", "https://", self.url, flags=re.I)
        reply = self.net.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda: self._done(reply))

    def _done(self, reply):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.error = "Couldn't load the calendar"
        else:
            try:
                self.events = parse_events(bytes(reply.readAll()))
                self.error = ""
            except Exception:  # anything malformed in the feed
                self.error = "That link isn't a valid iCal calendar"
        self.updated.emit()
