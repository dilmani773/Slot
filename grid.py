"""Draw the week as a timetable grid with highlighter-colored class blocks."""
import hashlib
import html

from ics_builder import DAYS, normalize_time

# Highlighter pens: each course gets one, the same one every time
INKS = [
    ("#FFE36E", "#5C4A00"),
    ("#9FE8CB", "#0B4A35"),
    ("#FFB8CC", "#6A1630"),
    ("#A9D4FF", "#0E3A66"),
    ("#D4BEFF", "#3A1F73"),
    ("#FFCB94", "#663300"),
    ("#C8EE8A", "#2F4A00"),
]


def ink_for(key: str, order: list[str]):
    """Courses get pens in order of first appearance, so neighbours never share a color."""
    if key in order:
        return INKS[order.index(key) % len(INKS)]
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return INKS[h % len(INKS)]


def _mins(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def week_grid(lectures: list[dict], selected: set[str] | None = None) -> str:
    """selected: course keys to show in color. Others are drawn faded. None means all."""
    rows = []
    for lec in lectures:
        s, e = normalize_time(lec.get("start")), normalize_time(lec.get("end"))
        if lec.get("day") in DAYS and s and e and e > s:
            rows.append((lec, _mins(s), _mins(e)))

    order = []
    for lec, _, _ in rows:
        k = str(lec.get("code") or lec.get("subject") or "")
        if k not in order:
            order.append(k)

    days = DAYS[:5]
    if any(l["day"] == "Saturday" for l, _, _ in rows):
        days = DAYS[:6]

    lo = min([8 * 60] + [s for _, s, _ in rows]) // 60 * 60
    hi = max([17 * 60] + [e for _, _, e in rows])
    hi = -(-hi // 60) * 60
    px = 1.05  # pixels per minute
    height = int((hi - lo) * px)

    hour_lines = "".join(
        f'<div class="g-hour" style="top:{int((h*60-lo)*px)}px"><span>{h:02d}:00</span></div>'
        for h in range(lo // 60, hi // 60 + 1)
    )

    cols = []
    for d in days:
        blocks = []
        day_rows = sorted([r for r in rows if r[0]["day"] == d], key=lambda r: (r[1], r[2]))
        # Put clashing classes side by side: group overlaps, then give each a lane
        clusters, cur, cur_end = [], [], -1
        for r in day_rows:
            if cur and r[1] >= cur_end:
                clusters.append(cur); cur = []
            cur.append(r); cur_end = max(cur_end, r[2]) if len(cur) > 1 else r[2]
        if cur:
            clusters.append(cur)
        for cl in clusters:
            lane_ends, placed = [], []
            for r in cl:
                for i, end in enumerate(lane_ends):
                    if r[1] >= end:
                        lane_ends[i] = r[2]; placed.append((r, i)); break
                else:
                    lane_ends.append(r[2]); placed.append((r, len(lane_ends) - 1))
            n = len(lane_ends)
            for (lec, s, e), lane in placed:
                key = str(lec.get("code") or lec.get("subject") or "")
                code = html.escape(str(lec.get("code") or ""))
                subj = html.escape(str(lec.get("subject") or ""))
                venue = html.escape(str(lec.get("venue") or ""))
                kind = html.escape(str(lec.get("type") or ""))
                bg, fg = ink_for(key, order)
                off = selected is not None and key not in selected
                top, h = int((s - lo) * px), max(int((e - s) * px) - 3, 22)
                title = code or subj
                sub = subj if code and subj != code and e - s >= 90 and n == 1 else ""
                meta = f'{normalize_time(lec["start"])} to {normalize_time(lec["end"])}'
                if venue:
                    meta += f", {venue}"
                if kind and kind != "Lecture" and n == 1:
                    meta += f", {kind}"
                left = f"calc({lane} * 100% / {n} + 2px)"
                width = f"calc(100% / {n} - 4px)"
                blocks.append(
                    f'<div class="g-block{" off" if off else ""}" style="top:{top}px;height:{h}px;left:{left};width:{width};--hl:{bg};--hl-text:{fg}">'
                    f"<b>{title}</b>" + (f"<i>{sub}</i>" if sub else "") + f"<small>{meta}</small></div>"
                )
        cols.append(
            f'<div class="g-col"><div class="g-day">{d[:3]}</div>'
            f'<div class="g-body" style="height:{height}px;--hr:{int(60*px)}px">{"".join(blocks)}</div></div>'
        )

    return f"""
<div class="g-wrap"><div class="g-grid" style="--days:{len(days)}">
  <div class="g-axis"><div class="g-day">&nbsp;</div><div class="g-body" style="height:{height}px">{hour_lines}</div></div>
  {"".join(cols)}
</div></div>
"""
