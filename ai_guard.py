"""Protect the app owner's API key.

- Same file, one AI call: results are saved by file fingerprint and reused.
  A whole batch uploading the same PDF costs one read.
- Each visitor gets a few fresh AI reads.
- The whole app has a daily cap, so a leaked link cannot use up your quota.
"""
import hashlib
import json
import threading
from datetime import date
from pathlib import Path

CACHE_DIR = Path(__file__).with_name(".slot_cache")
_lock = threading.Lock()


class LimitReached(Exception):
    pass


def fingerprint(kind: str, data: bytes) -> str:
    return hashlib.sha256(kind.encode() + data).hexdigest()[:32]


def load(kind: str, data: bytes):
    f = CACHE_DIR / f"{fingerprint(kind, data)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return None
    return None


def save(kind: str, data: bytes, result):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{fingerprint(kind, data)}.json").write_text(json.dumps(result, default=str))


def _counter_file():
    return CACHE_DIR / "usage.json"


def used_today() -> int:
    try:
        u = json.loads(_counter_file().read_text())
        return u["count"] if u.get("day") == date.today().isoformat() else 0
    except Exception:
        return 0


def take_one(session_used: int, session_limit: int, daily_limit: int):
    """Call before a fresh AI read. Raises LimitReached if over a limit."""
    if session_used >= session_limit:
        raise LimitReached(
            f"You've used your {session_limit} fresh reads for this visit. "
            "Fix anything else by hand in the Check step, or come back later."
        )
    with _lock:
        n = used_today()
        if n >= daily_limit:
            raise LimitReached("Slot has reached today's reading limit. Please try again tomorrow, or use the sample to explore.")
        CACHE_DIR.mkdir(exist_ok=True)
        _counter_file().write_text(json.dumps({"day": date.today().isoformat(), "count": n + 1}))


def cached_call(kind, data, fn, *, fresh=False, session_used=0, session_limit=5, daily_limit=300):
    """Return (result, from_cache). fn() does the real AI call."""
    if not fresh:
        hit = load(kind, data)
        if hit is not None:
            return hit, True
    take_one(session_used, session_limit, daily_limit)
    result = fn()
    if result:
        save(kind, data, result)
    return result, False
