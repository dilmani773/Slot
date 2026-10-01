"""Read a timetable straight from a PDF's text. No AI, free and exact.

Works when the PDF was made on a computer (Word, Excel, Google Docs), so the
text inside it is real text. Scanned PDFs and photos still go to the AI.
"""
import re

import pymupdf

from ics_builder import DAYS
from parser import merge_slots

DAY_ALIASES = {d[:3].lower(): d for d in DAYS}
CODE_RE = re.compile(r"\b([A-Z]{2,4})\s?(\d{3,4}[A-Z]?)\b\s*(?:\[([^\]]*)\]|\(([^)]*)\))?")
TIME_RE = re.compile(r"(\d{1,2})[.:](\d{2})\s*(?:-|–|—|to)\s*(\d{1,2})[.:](\d{2})", re.I)


def _day(text):
    t = (text or "").strip().lower()
    return DAY_ALIASES.get(t[:3]) if t[:3] in DAY_ALIASES and len(t) <= 10 else None


def _hm(h, m):
    h = int(h)
    if h < 7:  # "1.00" on a lecture timetable means 1 PM
        h += 12
    return f"{h:02d}:{int(m):02d}"


def _times(text):
    m = TIME_RE.search(text or "")
    if not m:
        return None
    return _hm(m[1], m[2]), _hm(m[3], m[4])


def _classes_in(cell):
    out = []
    for m in CODE_RE.finditer(cell or ""):
        venue = (m[3] if m[3] is not None else m[4] or "").strip()
        out.append({"code": m[1] + m[2], "venue": venue})
    return out


def _read_grid(rows):
    """rows: list of lists of cell text. Finds days on one axis and times on the other."""
    items = []
    # Days across the top, times down the side (most common)
    for hi, header in enumerate(rows[:3]):
        day_cols = {j: _day(c) for j, c in enumerate(header) if _day(c)}
        if len(day_cols) >= 3:
            last = {}
            for row in rows[hi + 1:]:
                t = next((_times(c) for c in row[:2] if _times(c)), None)
                if not t:
                    continue
                if not any(_classes_in(c) for c in row if c):
                    last = {}  # TEA / LUNCH row: nothing carries over it
                    continue
                for j, day in day_cols.items():
                    cell = row[j] if j < len(row) else None
                    if cell is None:
                        cell = last.get(j, "")  # merged downwards from the row above
                    last[j] = cell
                    for c in _classes_in(cell):
                        items.append({**c, "day": day, "start": t[0], "end": t[1], "type": "Lecture", "subject": "", "lecturer": ""})
            return items

    # Days down the side, times across the top
    for hi, header in enumerate(rows[:3]):
        time_cols = {j: _times(c) for j, c in enumerate(header) if _times(c)}
        if len(time_cols) >= 3:
            for row in rows[hi + 1:]:
                day = next((_day(c) for c in row[:2] if _day(c)), None)
                if not day:
                    continue
                prev = None
                for j, t in sorted(time_cols.items()):
                    cell = row[j] if j < len(row) else None
                    if cell is None:
                        cell = prev or ""  # merged sideways
                    prev = cell
                    for c in _classes_in(cell):
                        items.append({**c, "day": day, "start": t[0], "end": t[1], "type": "Lecture", "subject": "", "lecturer": ""})
            return items
    return items


def read_pdf_table(data: bytes) -> list[dict]:
    """Return classes found in the PDF's tables, or [] if this PDF has no readable table."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return []
    items = []
    for page in list(doc)[:4]:
        if len(page.get_text().strip()) < 30:  # scanned page, no real text
            continue
        for table in page.find_tables().tables:
            rows = [[(c or "") if c is not None else None for c in r] for r in table.extract()]
            items += _read_grid(rows)
    classes = merge_slots(items)
    return classes if len(classes) >= 3 else []
