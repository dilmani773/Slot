"""Read a timetable image or PDF with an AI model and return a list of classes."""
import base64
import json
import re

import pymupdf as fitz

from ics_builder import normalize_time

PROMPT = """You are transcribing a university lecture timetable grid.
Return ONLY a JSON array. No markdown, no explanation.

Return one item for EVERY course in EVERY time row. Do not merge rows.
If a cell lists two courses, return two items for that row.
If a cell spans several rows (one big merged box), return it once with the full time span.

Item format:
{
  "code": "course code exactly as written, e.g. CO421",
  "subject": "course name if the timetable shows one, else empty string",
  "day": "Monday | Tuesday | Wednesday | Thursday | Friday | Saturday | Sunday",
  "start": "HH:MM, 24-hour",
  "end": "HH:MM, 24-hour",
  "venue": "text in brackets after the code, without the brackets, e.g. CO421[14] -> 14. Empty if none.",
  "type": "Lecture, unless the timetable clearly says lab, practical or tutorial"
}

Rules:
- Read times from the time column. 8.00-8.55 means start 08:00, end 08:55.
- Skip rows like TEA, LUNCH, BREAK, and cells that only say NOTE or similar.
- Ignore cell background colors.
- Never invent courses. Copy codes exactly.
"""


EXAM_PROMPT = """You are reading a university exam timetable.
Return ONLY a JSON array. No markdown, no explanation.

One item per exam paper:
{
  "code": "course code exactly as written, e.g. CO421",
  "subject": "course name if shown, else empty string",
  "date": "YYYY-MM-DD",
  "start": "HH:MM, 24-hour",
  "end": "HH:MM, 24-hour",
  "venue": "exam hall if shown, else empty string"
}

Rules:
- If the year is not shown, use the next date in the future.
- If only a session is shown (Morning / Afternoon), use the times printed in the timetable key.
- Never invent exams. Copy codes exactly.
"""


def file_to_images(data: bytes, filename: str) -> list[tuple[bytes, str]]:
    """Return a list of (image_bytes, mime_type). PDFs are rendered page by page."""
    if filename.lower().endswith(".pdf"):
        doc = fitz.open(stream=data, filetype="pdf")
        return [(p.get_pixmap(dpi=200).tobytes("png"), "image/png") for p in list(doc)[:4]]
    mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    return [(data, mime)]


def _parse_json(text: str) -> list[dict]:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("The AI did not return a list of classes.")
    return [i for i in json.loads(text[start : end + 1]) if isinstance(i, dict)]


def merge_slots(items: list[dict], max_gap_min: int = 0) -> list[dict]:
    """Join back-to-back slots of the same course into one class.
    8:00-8:55 + 8:55-9:50 becomes 8:00-9:50. Slots split by tea or lunch stay separate."""
    def mins(t):
        h, m = map(int, t.split(":"))
        return h * 60 + m

    clean = []
    for it in items:
        s, e = normalize_time(it.get("start")), normalize_time(it.get("end"))
        if not (s and e and it.get("day")):
            continue
        it = {k: ("" if v is None else str(v).strip()) for k, v in it.items()}
        it["start"], it["end"] = s, e
        it["code"] = re.sub(r"\s+", "", it.get("code", "")).upper()
        it["venue"] = it.get("venue", "").strip(" []")
        clean.append(it)

    clean.sort(key=lambda x: (x["day"], x["code"], x["venue"], x["start"]))
    merged = []
    for it in clean:
        last = merged[-1] if merged else None
        if (
            last
            and last["day"] == it["day"]
            and last["code"] == it["code"]
            and last["venue"] == it["venue"]
            and 0 <= mins(it["start"]) - mins(last["end"]) <= max_gap_min
        ):
            last["end"] = max(last["end"], it["end"])
        elif not (last and last["day"] == it["day"] and last["code"] == it["code"]
                  and last["venue"] == it["venue"] and last["start"] == it["start"]):
            merged.append(dict(it))
    return merged


# ---------- Gemini ----------
GEMINI_DEFAULT = "gemini-3.8-flash"
BUSY_CODES = {429, 500, 503, 504}


def _flash_models(client) -> list[str]:
    """Flash models this key can use, newest first. Full Flash before Flash Lite."""
    skip = ("image", "tts", "live", "audio", "embedding", "thinking", "exp", "preview")
    found = []
    for m in client.models.list():
        name = m.name.split("/")[-1]
        if "flash" in name and not any(x in name for x in skip):
            ver = tuple(int(x) for x in re.findall(r"\d+", name)[:2])
            found.append((("lite" not in name), ver, name))
    return [n for *_, n in sorted(found, reverse=True)]


def _ask_gemini(images, api_key, model, on_status=None, prompt=PROMPT):
    import time

    from google import genai
    from google.genai import errors, types

    client = genai.Client(api_key=api_key)
    parts = [types.Part.from_bytes(data=b, mime_type=m) for b, m in images]
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)

    def call(name):
        return client.models.generate_content(model=name, contents=[*parts, prompt], config=config).text

    def say(msg):
        if on_status:
            on_status(msg)

    # 1. Try the chosen model, waiting a little longer each time it is busy
    last_err = None
    for wait in (0, 3, 8):
        if wait:
            say(f"Google is busy. Trying again in {wait} seconds...")
            time.sleep(wait)
        try:
            return call(model)
        except errors.APIError as e:
            last_err = e
            if e.code == 404:
                break  # model retired, go straight to backups
            if e.code not in BUSY_CODES:
                raise

    # 2. Still failing: try other Flash models this key can use
    try:
        backups = [m for m in _flash_models(client) if m != model][:3]
    except Exception:
        backups = []
    for name in backups:
        say(f"Switching to {name}...")
        try:
            return call(name)
        except errors.APIError as e:
            last_err = e
            if e.code not in BUSY_CODES | {404}:
                raise
    raise last_err


# ---------- Claude ----------
CLAUDE_DEFAULT = "claude-sonnet-5-5"


def _ask_claude(images, api_key, model, prompt=PROMPT):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": m, "data": base64.b64encode(b).decode()}}
        for b, m in images
    ]
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(model=model, max_tokens=8000, messages=[{"role": "user", "content": content}])
    return "".join(b.text for b in resp.content if b.type == "text")


def extract_lectures(data: bytes, filename: str, provider: str, api_key: str, model: str | None = None, on_status=None) -> list[dict]:
    images = file_to_images(data, filename)
    if provider == "claude":
        raw = _ask_claude(images, api_key, model or CLAUDE_DEFAULT)
    else:
        raw = _ask_gemini(images, api_key, model or GEMINI_DEFAULT, on_status)
    return merge_slots(_parse_json(raw))


def extract_exams(data: bytes, filename: str, provider: str, api_key: str, model: str | None = None, on_status=None) -> list[dict]:
    images = file_to_images(data, filename)
    if provider == "claude":
        raw = _ask_claude(images, api_key, model or CLAUDE_DEFAULT, EXAM_PROMPT)
    else:
        raw = _ask_gemini(images, api_key, model or GEMINI_DEFAULT, on_status, EXAM_PROMPT)
    out = []
    for ex in _parse_json(raw):
        ex = {k: ("" if v is None else str(v).strip()) for k, v in ex.items()}
        ex["code"] = re.sub(r"\s+", "", ex.get("code", "")).upper()
        ex["start"] = normalize_time(ex.get("start")) or ex.get("start", "")
        ex["end"] = normalize_time(ex.get("end")) or ex.get("end", "")
        out.append(ex)
    return out




def explain(e: Exception) -> str:
    """Turn API errors into a message a student can act on."""
    msg = str(e)
    if "API key" in msg or "401" in msg or "403" in msg or "PERMISSION" in msg:
        return "The API key was rejected. Check that you copied the whole key."
    if "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower():
        return "Google's AI servers are busy right now. We tried a few times and a backup model. Wait a minute and click Read timetable again."
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "You hit the free tier limit. Wait a minute and try again."
    if "404" in msg:
        return "That AI model is not available for your key. Set MODEL in secrets to a current model name."
    if "JSON" in msg or "list of classes" in msg:
        return "The AI reply could not be read. Try again, or crop the image to just the table."
    return f"Something went wrong: {msg}"
