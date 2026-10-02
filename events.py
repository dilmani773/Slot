"""Turn classes and exams into calendar events.

Both the .ics download and the Google Calendar sync use these events, so they
always match. Every event gets a stable id. The id stays the same when a class
moves to a new time or hall, so a timetable update becomes a change, not a
delete plus a new event.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ics_builder import DAYS, normalize_time

TZ_NAME = "Asia/Colombo"
UTC = ZoneInfo("UTC")


def _hm(t):
    return tuple(map(int, normalize_time(t).split(":")))


def _id(*parts) -> str:
    # Google event ids allow 0-9 and a-v. md5 hex (0-9, a-f) fits.
    return "slot" + hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()


def _title(code, subject, kind):
    code, subject, kind = (code or "").strip(), (subject or "").strip(), (kind or "").strip()
    parts = [code, subject if subject and subject != code else "", f"({kind})" if kind and kind != "Lecture" else ""]
    return " ".join(p for p in parts if p) or "Class"


def class_events(lectures, sem_start: date, sem_end: date, breaks=None, reminder_min=10, tz_name=TZ_NAME):
    tz = ZoneInfo(tz_name)
    breaks = breaks or []
    events = []

    # Number repeat slots of the same course on the same day (e.g. a morning and afternoon lab)
    seen = {}
    for lec in sorted(lectures, key=lambda l: (l["day"], normalize_time(l["start"]))):
        key = (lec.get("code") or lec.get("subject"), lec.get("type"), lec["day"])
        idx = seen.get(key, 0)
        seen[key] = idx + 1

        weekday = DAYS.index(lec["day"])
        first = sem_start + timedelta(days=(weekday - sem_start.weekday()) % 7)
        if first > sem_end:
            continue
        sh, sm = _hm(lec["start"])
        eh, em = _hm(lec["end"])

        exdates, d = [], first
        while d <= sem_end:
            if any(b0 <= d <= b1 for b0, b1 in breaks):
                exdates.append(datetime.combine(d, time(sh, sm), tz))
            d += timedelta(days=7)

        desc = "\n".join(f"{k.title()}: {lec[k]}" for k in ("type", "lecturer") if lec.get(k))
        events.append({
            "id": _id("class", *key, idx, sem_start),
            "kind": "class",
            "summary": _title(lec.get("code"), lec.get("subject"), lec.get("type")),
            "location": lec.get("venue") or "",
            "description": desc,
            "start": datetime.combine(first, time(sh, sm), tz),
            "end": datetime.combine(first, time(eh, em), tz),
            "until": datetime.combine(sem_end, time(23, 59), tz).astimezone(UTC),
            "exdates": exdates,
            "reminders": [reminder_min] if reminder_min else [],
            "label": f"{lec['day'][:3]} {normalize_time(lec['start'])}",
            "tz": tz_name,
        })
    return events


def exam_events(exams, tz_name=TZ_NAME):
    tz = ZoneInfo(tz_name)
    events = []
    for ex in exams:
        d = ex["date"] if isinstance(ex["date"], date) else date.fromisoformat(str(ex["date"])[:10])
        sh, sm = _hm(ex["start"])
        eh, em = _hm(ex["end"])
        code = (ex.get("code") or "").strip()
        events.append({
            "id": _id("exam", code, ex.get("subject", "")),
            "kind": "exam",
            "summary": f"Exam: {_title(code, ex.get('subject'), '')}",
            "location": ex.get("venue") or "",
            "description": "Added by Slot",
            "start": datetime.combine(d, time(sh, sm), tz),
            "end": datetime.combine(d, time(eh, em), tz),
            "until": None,
            "exdates": [],
            "reminders": [24 * 60, 60],
            "label": d.strftime("%a %d %b"),
            "tz": tz_name,
        })
    return events


def fingerprint(ev) -> str:
    """Short hash of everything that matters. If it changes, the event needs updating."""
    data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in ev.items() if k not in ("label",)}
    data["exdates"] = [x.isoformat() for x in ev["exdates"]]
    return hashlib.md5(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def validate_exams(exams) -> list[str]:
    problems = []
    for i, ex in enumerate(exams, start=1):
        label = ex.get("code") or f"Exam {i}"
        if not ex.get("date"):
            problems.append(f"{label}: add a date.")
        s, e = normalize_time(ex.get("start")), normalize_time(ex.get("end"))
        if not s or not e:
            problems.append(f"{label}: times must look like 09:00.")
        elif e <= s:
            problems.append(f"{label}: end time must be after start time.")
    return problems


def to_ics(events, tz_name=None) -> bytes:
    tz_name = tz_name or (events[0]["tz"] if events else TZ_NAME)
    from icalendar import Alarm, Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//Slot//Timetable to Calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Lectures")
    cal.add("x-wr-timezone", tz_name)
    for ev in events:
        e = Event()
        e.add("uid", ev["id"] + "@slot")
        e.add("summary", ev["summary"])
        e.add("dtstart", ev["start"])
        e.add("dtend", ev["end"])
        e.add("dtstamp", datetime.now(UTC))
        if ev["until"]:
            e.add("rrule", {"freq": "weekly", "until": ev["until"]})
        for x in ev["exdates"]:
            e.add("exdate", x)
        if ev["location"]:
            e.add("location", ev["location"])
        if ev["description"]:
            e.add("description", ev["description"])
        for mins in ev["reminders"]:
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", ev["summary"])
            a.add("trigger", timedelta(minutes=-mins))
            e.add_component(a)
        cal.add_component(e)
    cal.add_missing_timezones()
    return cal.to_ical()
