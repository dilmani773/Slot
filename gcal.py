"""Sign in with Google and keep a "Lectures" calendar in sync with the timetable.

Slot only asks for two permissions:
  - create and manage calendars that Slot itself made
  - see the list of your calendars (to find the one Slot made last time)
It can never read or change your other calendars.
"""
import secrets as pysecrets
from urllib.parse import urlencode

import requests

from events import TZ_NAME, fingerprint

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.app.created",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]
CAL_NAME = "Lectures"
CAL_TAG = "Made by Slot"  # stored in the calendar description so we can find it again
CLASS_COLOR, EXAM_COLOR = None, "11"  # 11 = Tomato (red)


class GoogleError(Exception):
    pass


# ---------- Sign in ----------
def new_state() -> str:
    return pysecrets.token_urlsafe(24)


def auth_link(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
        "state": state,
    })


def exchange_code(code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
    r = requests.post(TOKEN_URL, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=20)
    if r.status_code != 200:
        raise GoogleError(f"Google sign-in failed: {r.json().get('error_description', r.text)}")
    granted = r.json().get("scope", "")
    if SCOPES[0] not in granted:
        raise GoogleError("Calendar access was not allowed. Sign in again and tick the calendar boxes.")
    return r.json()["access_token"]


# ---------- API helpers ----------
def _req(token, method, path, **kw):
    r = requests.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)
    if r.status_code == 401:
        raise GoogleError("Your Google sign-in expired. Sign in again.")
    return r


def _ok(r):
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text
        raise GoogleError(f"Google Calendar error ({r.status_code}): {msg}")
    return r.json() if r.content else {}


def find_calendar(token) -> str | None:
    page = None
    while True:
        r = _ok(_req(token, "GET", "/users/me/calendarList", params={"pageToken": page} if page else {}))
        for c in r.get("items", []):
            if c.get("description") == CAL_TAG and c.get("accessRole") == "owner":
                return c["id"]
        page = r.get("nextPageToken")
        if not page:
            return None


def get_or_create_calendar(token) -> str:
    cid = find_calendar(token)
    if cid:
        return cid
    return _ok(_req(token, "POST", "/calendars", json={
        "summary": CAL_NAME, "description": CAL_TAG, "timeZone": TZ_NAME,
    }))["id"]


def existing_events(token, cal_id) -> dict:
    """id -> event, for events Slot made in this calendar."""
    out, page = {}, None
    while True:
        params = {"maxResults": 2500, "singleEvents": "false", "showDeleted": "false"}
        if page:
            params["pageToken"] = page
        r = _ok(_req(token, "GET", f"/calendars/{cal_id}/events", params=params))
        for ev in r.get("items", []):
            out[ev["id"]] = ev
        page = r.get("nextPageToken")
        if not page:
            return out


# ---------- Sync ----------
def _body(ev):
    tz = TZ_NAME
    body = {
        "id": ev["id"],
        "summary": ev["summary"],
        "location": ev["location"],
        "description": ev["description"],
        "start": {"dateTime": ev["start"].isoformat(), "timeZone": tz},
        "end": {"dateTime": ev["end"].isoformat(), "timeZone": tz},
        "reminders": {"useDefault": False,
                      "overrides": [{"method": "popup", "minutes": m} for m in ev["reminders"]]},
        "extendedProperties": {"private": {"slot": fingerprint(ev), "kind": ev["kind"]}},
        "status": "confirmed",
    }
    rec = []
    if ev["until"]:
        rec.append("RRULE:FREQ=WEEKLY;UNTIL=" + ev["until"].strftime("%Y%m%dT%H%M%SZ"))
    if ev["exdates"]:
        rec.append(f"EXDATE;TZID={tz}:" + ",".join(x.strftime("%Y%m%dT%H%M%S") for x in ev["exdates"]))
    if rec:
        body["recurrence"] = rec
    color = EXAM_COLOR if ev["kind"] == "exam" else CLASS_COLOR
    if color:
        body["colorId"] = color
    return body


def plan(desired: list[dict], existing: dict, keep_kinds: set = frozenset()) -> dict:
    """Work out what to add, change and remove. Nothing is sent to Google here."""
    want = {e["id"]: e for e in desired}
    add = [e for i, e in want.items() if i not in existing]
    change = [
        (existing[i], e) for i, e in want.items()
        if i in existing and existing[i].get("extendedProperties", {}).get("private", {}).get("slot") != fingerprint(e)
    ]
    remove = [
        ev for i, ev in existing.items()
        if i not in want and ev.get("extendedProperties", {}).get("private", {}).get("kind") not in keep_kinds
    ]
    same = len(want) - len(add) - len(change)
    return {"add": add, "change": change, "remove": remove, "same": same}


def apply(token, cal_id, p: dict, progress=None):
    jobs = [("add", e) for e in p["add"]] + [("change", e) for _, e in p["change"]] + [("remove", e) for e in p["remove"]]
    for n, (kind, ev) in enumerate(jobs, 1):
        if kind == "remove":
            r = _req(token, "DELETE", f"/calendars/{cal_id}/events/{ev['id']}")
            if r.status_code not in (200, 204, 404, 410):
                _ok(r)
        elif kind == "add":
            r = _req(token, "POST", f"/calendars/{cal_id}/events", json=_body(ev))
            if r.status_code == 409:  # same id was deleted before: bring it back
                r = _req(token, "PUT", f"/calendars/{cal_id}/events/{ev['id']}", json=_body(ev))
            _ok(r)
        else:
            _ok(_req(token, "PUT", f"/calendars/{cal_id}/events/{ev['id']}", json=_body(ev)))
        if progress:
            progress(n / max(len(jobs), 1))
    return len(jobs)


def calendar_link() -> str:
    return "https://calendar.google.com/calendar/r/week"
