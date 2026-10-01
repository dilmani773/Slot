"""Turn a list of lectures into a .ics calendar file with weekly repeating events."""
import hashlib
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def normalize_time(value) -> str | None:
    """Accept 8.00, 8:00, 08:00, 1:30 PM and return HH:MM, or None if unreadable."""
    if value is None:
        return None
    s = str(value).strip().upper().replace(".", ":")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$", s)
    if not m:
        return None
    h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "PM" and h < 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mins < 60):
        return None
    return f"{h:02d}:{mins:02d}"


def validate(lectures: list[dict]) -> list[str]:
    """Return a list of readable problems. Empty list means everything is fine."""
    problems = []
    for i, lec in enumerate(lectures, start=1):
        label = lec.get("code") or lec.get("subject") or f"Row {i}"
        if lec.get("day") not in DAYS:
            problems.append(f"{label}: pick a day.")
        start, end = normalize_time(lec.get("start")), normalize_time(lec.get("end"))
        if not start or not end:
            problems.append(f"{label}: times must look like 08:00.")
        elif end <= start:
            problems.append(f"{label}: end time must be after start time.")
    return problems


def _in_break(d: date, breaks: list[tuple[date, date]]) -> bool:
    return any(b0 <= d <= b1 for b0, b1 in breaks)


def build_ics(
    lectures: list[dict],
    sem_start: date,
    sem_end: date,
    breaks: list[tuple[date, date]] | None = None,
    reminder_min: int | None = 10,
    tz_name: str = "Asia/Colombo",
) -> bytes:
    tz = ZoneInfo(tz_name)
    breaks = breaks or []

    cal = Calendar()
    cal.add("prodid", "-//Slot//Timetable to Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Lectures")
    cal.add("x-wr-timezone", tz_name)

    for lec in lectures:
        weekday = DAYS.index(lec["day"])
        sh, sm = map(int, normalize_time(lec["start"]).split(":"))
        eh, em = map(int, normalize_time(lec["end"]).split(":"))

        first = sem_start + timedelta(days=(weekday - sem_start.weekday()) % 7)
        if first > sem_end:
            continue

        ev = Event()
        code, subject = (lec.get("code") or "").strip(), (lec.get("subject") or "").strip()
        kind = (lec.get("type") or "").strip()
        title = " ".join(p for p in [code, subject if subject != code else "", f"({kind})" if kind and kind != "Lecture" else ""] if p)
        ev.add("summary", title or "Class")
        ev.add("dtstart", datetime.combine(first, time(sh, sm), tz))
        ev.add("dtend", datetime.combine(first, time(eh, em), tz))
        until = datetime.combine(sem_end, time(23, 59), tz).astimezone(ZoneInfo("UTC"))
        ev.add("rrule", {"freq": "weekly", "until": until})

        # Skip classes that fall inside break weeks
        d = first
        while d <= sem_end:
            if _in_break(d, breaks):
                ev.add("exdate", datetime.combine(d, time(sh, sm), tz))
            d += timedelta(days=7)

        if lec.get("venue"):
            ev.add("location", lec["venue"])
        notes = [f"{k.title()}: {lec[k]}" for k in ("type", "lecturer") if lec.get(k)]
        if notes:
            ev.add("description", "\n".join(notes))

        uid_src = f"{code}{subject}{lec['day']}{lec['start']}{sem_start}"
        ev.add("uid", hashlib.md5(uid_src.encode()).hexdigest() + "@slot")
        ev.add("dtstamp", datetime.now(ZoneInfo("UTC")))

        if reminder_min:
            alarm = Alarm()
            alarm.add("action", "DISPLAY")
            alarm.add("description", title)
            alarm.add("trigger", timedelta(minutes=-reminder_min))
            ev.add_component(alarm)

        cal.add_component(ev)

    cal.add_missing_timezones()
    return cal.to_ical()
