"""Slot: turn a lecture timetable into a calendar you can import."""
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from grid import INKS, week_grid
import time as _time

import ai_guard
import gcal
from events import class_events, exam_events, to_ics, validate_exams
from ics_builder import DAYS, normalize_time, validate
from parser import explain, extract_exams, extract_lectures
from pdf_table import read_pdf_table

st.set_page_config(page_title="Slot: timetable to calendar", page_icon="🗓️", layout="wide")
st.markdown(f"<style>{Path(__file__).with_name('style.css').read_text()}</style>", unsafe_allow_html=True)

COLUMNS = ["code", "subject", "type", "day", "start", "end", "venue", "lecturer"]
LIBRARY_DIR = Path(__file__).with_name("timetables")


@st.cache_data
def load_library() -> dict:
    """name -> classes, from timetables/*.json. Newest file names last."""
    lib = {}
    for f in sorted(LIBRARY_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
            lib[d["name"]] = d["classes"]
        except Exception:
            pass
    return lib

ss = st.session_state
ss.setdefault("step", 1)
ss.setdefault("lectures", None)
ss.setdefault("all_lectures", None)
ss.setdefault("picked", None)
ss.setdefault("gtoken", None)
ss.setdefault("ai_reads", 0)
EXAM_COLS = ["code", "subject", "date", "start", "end", "venue"]
ss.setdefault("breaks_base", pd.DataFrame({"From": pd.Series(dtype="object"), "To": pd.Series(dtype="object")}))
ss.setdefault("exams_base", pd.DataFrame({c: pd.Series(dtype="object") for c in EXAM_COLS}))


def secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# The app owner's AI key lives in secrets. Users never see it.
AI_PROVIDER = secret("PROVIDER", "gemini")
AI_KEY = secret("API_KEY")
AI_MODEL = secret("MODEL") or None
SESSION_LIMIT = int(secret("READS_PER_VISIT", 5))
DAILY_LIMIT = int(secret("READS_PER_DAY", 300))


def ai_read(kind, file, reader, status, fresh=False):
    """Read a file with AI, reusing earlier results for the same file. Returns (items, from_cache)."""
    data = file.getvalue()

    def call():
        ss.ai_reads += 1
        return reader(data, file.name, AI_PROVIDER, AI_KEY, AI_MODEL, on_status=lambda m: status.update(label=m))

    return ai_guard.cached_call(kind, data, call, fresh=fresh, session_used=ss.ai_reads,
                                session_limit=SESSION_LIMIT, daily_limit=DAILY_LIMIT)


GOOGLE_ID = secret("GOOGLE_CLIENT_ID")
GOOGLE_SECRET = secret("GOOGLE_CLIENT_SECRET")
REDIRECT = secret("GOOGLE_REDIRECT_URI", "http://localhost:8501")
SNAP_KEYS = ["step", "lectures", "all_lectures", "picked", "sem_start", "sem_end", "reminder",
             "breaks_base", "exams_base", "ai_reads"]


@st.cache_resource
def oauth_store() -> dict:
    """Holds each user's work while they visit Google to sign in. Entries expire after 15 minutes."""
    store = {}
    return store


def prune_store():
    store, now = oauth_store(), _time.time()
    for k in [k for k, v in store.items() if now - v["t"] > 900]:
        store.pop(k, None)


# Coming back from Google sign-in
qp = st.query_params
if "code" in qp and "state" in qp:
    prune_store()
    saved = oauth_store().pop(qp["state"], None)
    code = qp["code"]
    st.query_params.clear()
    if not saved:
        st.warning("Sign-in took too long or the page was reloaded. Please sign in again.")
    else:
        for k, v in saved["data"].items():
            ss[k] = v
        try:
            ss.gtoken = gcal.exchange_code(code, GOOGLE_ID, GOOGLE_SECRET, REDIRECT)
        except gcal.GoogleError as e:
            st.error(str(e))
elif "error" in qp:
    st.query_params.clear()
    st.warning("Google sign-in was cancelled.")


# ---------- Hero ----------
bars = "".join(
    f'<span style="--c:{bg};--h:{h}%;--d:{i*60}ms"></span>'
    for i, ((bg, _), h) in enumerate(zip(INKS * 2, [55, 90, 40, 75, 100, 60, 85, 45, 70, 95, 50, 80]))
)
if ss.step == 1:
    st.markdown(
    f"""<div class="hero">
  <div><h1>Your timetable,<br>in your calendar.</h1></div>
  <div><p>Pick your batch or upload its timetable. Choose your subjects, check the result, then add every class and exam to Google Calendar for the whole semester.</p>
  <div class="hero-strip" aria-hidden="true">{bars}</div></div>
</div>""",
    unsafe_allow_html=True,
    )
else:
    st.markdown('<div class="mini"><b>Slot</b><span>Timetable to calendar</span></div>', unsafe_allow_html=True)

labels = ["Upload", "Pick subjects", "Check", "Add to calendar"]
st.markdown(
    '<div class="steps">'
    + "".join(
        f'<div class="step {"on" if ss.step == n else "done" if ss.step > n else ""}"><b>{n}</b>{t}</div>'
        for n, t in enumerate(labels, 1)
    )
    + "</div>",
    unsafe_allow_html=True,
)


# ---------- Step 1: Upload ----------
def start_with(classes):
    ss.all_lectures = pd.DataFrame(classes).reindex(columns=COLUMNS).fillna("")
    ss.picked = None
    ss.step = 2
    st.rerun()


if ss.step == 1:
    library = load_library()
    left, right = st.columns([1, 1.15], gap="large")

    with left:
        st.subheader("Find your timetable")
        if library:
            st.markdown('<p class="note">If your batch is on the list, you don\'t need to upload anything.</p>', unsafe_allow_html=True)
            choice = st.selectbox("Batch", list(library), index=None, placeholder="Choose your batch", label_visibility="collapsed")
            if st.button("Use this timetable", type="primary", disabled=choice is None, width="stretch"):
                start_with(library[choice])
        else:
            st.markdown('<p class="note">No timetables have been added yet. Upload yours on the right.</p>', unsafe_allow_html=True)

    with right:
        st.subheader("Not on the list? Upload it")
        st.markdown('<p class="note">Upload the full timetable your faculty shared, with every subject. You pick your own subjects next. A PDF works best.</p>', unsafe_allow_html=True)
        file = st.file_uploader("Timetable photo or PDF", type=["png", "jpg", "jpeg", "pdf"], label_visibility="collapsed")
        if file and not file.name.lower().endswith(".pdf"):
            st.image(file, width="stretch")

        fresh = False
        if file and AI_KEY and ai_guard.load("timetable", file.getvalue()) is not None:
            fresh = st.checkbox("Read it again from scratch", help="Someone already uploaded this exact file, so it loads instantly. Tick this only if the result looked wrong.")

        if st.button("Read timetable", type="primary" if not library else "secondary", disabled=not file, width="stretch"):
            found = None
            with st.status("Reading every slot in your timetable...", expanded=False) as status:
                # 1. PDFs made on a computer: read the table directly, no AI
                if file.name.lower().endswith(".pdf") and not fresh:
                    found = read_pdf_table(file.getvalue()) or None
                    if found:
                        status.update(label="Read directly from the PDF. No AI needed.", state="complete")
                # 2. Photos and scans: use AI
                if found is None:
                    if not AI_KEY:
                        status.update(label="This file needs AI to read it", state="error")
                        st.info("This looks like a photo or scanned PDF. AI reading isn't switched on for this app yet. Try the original PDF from your faculty instead.")
                    else:
                        try:
                            found, cached = ai_read("timetable", file, extract_lectures, status, fresh=fresh)
                            status.update(label="Loaded instantly. This timetable was read before." if cached else "Done",
                                          state="complete")
                        except ai_guard.LimitReached as e:
                            status.update(label="Limit reached", state="error")
                            st.warning(str(e))
                        except Exception as e:
                            status.update(label="Could not read the timetable", state="error")
                            st.error(explain(e))
            if found is not None:
                if not found:
                    st.warning("No classes found. Make sure the whole table is in the photo.")
                else:
                    start_with(found)


# ---------- Step 2: Pick subjects ----------
elif ss.step == 2:
    all_rows = ss.all_lectures.fillna("").to_dict("records")
    keys = []
    for r in all_rows:
        k = str(r.get("code") or r.get("subject") or "")
        if k and k not in keys:
            keys.append(k)
    keys.sort()
    names = {str(r.get("code") or r.get("subject")): r.get("subject") for r in all_rows}
    hours = {k: 0 for k in keys}
    for r in all_rows:
        k = str(r.get("code") or r.get("subject") or "")
        if k in hours and normalize_time(r["start"]) and normalize_time(r["end"]):
            sh, sm = map(int, normalize_time(r["start"]).split(":"))
            eh, em = map(int, normalize_time(r["end"]).split(":"))
            hours[k] += (eh * 60 + em - sh * 60 - sm) / 60

    st.subheader("Which subjects are you taking?")
    st.markdown('<p class="note">We found every course on the timetable. Tap the ones you take. The week below updates as you pick.</p>', unsafe_allow_html=True)
    picked = st.pills(
        "Subjects",
        keys,
        selection_mode="multi",
        default=ss.picked if ss.picked is not None else [],
        format_func=lambda k: f"{k}  {names.get(k) or ''}".strip(),
        label_visibility="collapsed",
        key="pills",
    )
    picked = picked or []
    total = sum(hours[k] for k in picked)
    st.markdown(f'<p class="pick-count">{len(picked)} of {len(keys)} subjects, {round(total, 1):g} hours a week</p>', unsafe_allow_html=True)

    st.markdown(week_grid(all_rows, set(picked)), unsafe_allow_html=True)

    c1, _, c2 = st.columns([1, 2, 1])
    if c1.button("Upload a different file", width="stretch"):
        ss.step, ss.all_lectures, ss.picked = 1, None, None
        st.rerun()
    if c2.button("Continue", type="primary", disabled=not picked, width="stretch"):
        ss.picked = picked
        ss.lectures = ss.all_lectures[
            ss.all_lectures.apply(lambda r: str(r["code"] or r["subject"]) in picked, axis=1)
        ].reset_index(drop=True)
        ss.step = 3
        st.rerun()


# ---------- Step 3: Check ----------
elif ss.step == 3:
    st.subheader("Check your classes")
    st.markdown('<p class="note">Check times and venues against your timetable. Edit any cell and the week updates. Add a row for anything missing, or select a row and delete it.</p>', unsafe_allow_html=True)

    edited = st.data_editor(
        ss.lectures,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "code": st.column_config.TextColumn("Code", width="small"),
            "subject": st.column_config.TextColumn("Subject", width="medium"),
            "type": st.column_config.SelectboxColumn("Type", options=["Lecture", "Lab", "Tutorial", "Other"], width="small"),
            "day": st.column_config.SelectboxColumn("Day", options=DAYS, width="small"),
            "start": st.column_config.TextColumn("Start", help="Like 08:00", width="small"),
            "end": st.column_config.TextColumn("End", help="Like 10:00", width="small"),
            "venue": st.column_config.TextColumn("Venue", width="small"),
            "lecturer": st.column_config.TextColumn("Lecturer", width="small"),
        },
        key="editor",
    )
    rows = edited.fillna("").to_dict("records")

    st.markdown(week_grid(rows), unsafe_allow_html=True)

    problems = validate(rows)
    if problems:
        st.warning("Fix these before continuing:\n\n" + "\n".join(f"- {p}" for p in problems))

    c1, _, c2 = st.columns([1, 2, 1])
    if c1.button("Change subjects", width="stretch"):
        ss.step = 2
        st.rerun()
    if c2.button("Looks right", type="primary", disabled=bool(problems) or not rows, width="stretch"):
        ss.lectures = edited
        ss.step = 4
        st.rerun()


# ---------- Step 4: Add to calendar ----------
else:
    rows = ss.lectures.fillna("").to_dict("records")
    picked_codes = {str(r.get("code") or "").upper() for r in rows}

    # Semester
    st.subheader("Set your semester")
    today = date.today()
    ss.setdefault("sem_start", today)
    ss.setdefault("sem_end", today + timedelta(weeks=15))
    ss.setdefault("reminder", 10)
    c1, c2, c3 = st.columns(3)
    sem_start = c1.date_input("First day of lectures", key="sem_start")
    sem_end = c2.date_input("Last day of lectures", key="sem_end")
    reminder = c3.selectbox("Class reminder", [None, 5, 10, 15, 30], key="reminder",
                            format_func=lambda m: "No reminder" if m is None else f"{m} minutes before")

    st.markdown('<p class="note">Weeks with no lectures, like a mid-semester break or study leave. Leave empty if none.</p>', unsafe_allow_html=True)
    breaks_df = st.data_editor(
        ss.breaks_base, num_rows="dynamic", hide_index=True, key="breaks_ed",
        column_config={"From": st.column_config.DateColumn("From"), "To": st.column_config.DateColumn("To")},
    )
    breaks = [(r["From"], r["To"]) for _, r in breaks_df.dropna().iterrows()]

    # Exams
    st.subheader("Exams")
    st.markdown('<p class="note">Optional. Upload the exam timetable and we keep only your subjects, or type them in. Exams show in red with reminders a day and an hour before.</p>', unsafe_allow_html=True)
    ec1, ec2 = st.columns([1.3, 1], gap="large")
    with ec2:
        exam_file = st.file_uploader("Exam timetable", type=["png", "jpg", "jpeg", "pdf"], key="exam_file")
        if st.button("Read exams", disabled=not (exam_file and AI_KEY), width="stretch"):
            with st.status("Reading the exam timetable...") as status:
                try:
                    found, cached = ai_read("exams", exam_file, extract_exams, status)
                    mine = [e for e in found if str(e.get("code", "")).upper() in picked_codes]
                    status.update(label=f"Kept {len(mine)} of {len(found)} exams that match your subjects", state="complete")
                    if mine:
                        df = pd.DataFrame(mine).reindex(columns=EXAM_COLS).fillna("")
                        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
                        ss.exams_base = df
                        ss.pop("exams_ed", None)
                        st.rerun()
                except ai_guard.LimitReached as e:
                    status.update(label="Limit reached", state="error")
                    st.warning(str(e))
                except Exception as e:
                    status.update(label="Could not read the exam timetable", state="error")
                    st.error(explain(e))
    with ec1:
        exams_df = st.data_editor(
            ss.exams_base, num_rows="dynamic", hide_index=True, key="exams_ed", width="stretch",
            column_config={
                "code": st.column_config.SelectboxColumn("Code", options=sorted(picked_codes)),
                "subject": st.column_config.TextColumn("Subject"),
                "date": st.column_config.DateColumn("Date"),
                "start": st.column_config.TextColumn("Start", help="Like 09:00"),
                "end": st.column_config.TextColumn("End", help="Like 12:00"),
                "venue": st.column_config.TextColumn("Venue"),
            },
        )
    exams = [r for r in exams_df.fillna("").to_dict("records") if any(str(v).strip() for v in r.values())]
    exam_problems = validate_exams(exams)
    if exam_problems:
        st.warning("Fix these exams first:\n\n" + "\n".join(f"- {p}" for p in exam_problems))

    # Keep the latest edits so they survive the trip to Google sign-in
    ss.breaks_base_now, ss.exams_base_now = breaks_df, exams_df

    if sem_end <= sem_start:
        st.error("The last day must come after the first day.")
        st.stop()
    if exam_problems:
        st.stop()

    events = class_events(rows, sem_start, sem_end, breaks, reminder) + exam_events(exams)
    weeks = (sem_end - sem_start).days // 7 + 1
    st.markdown(
        f"**{len(rows)} classes a week** for about {weeks} weeks"
        + (f", plus **{len(exams)} exams**." if exams else "."),
    )

    tab_g, tab_f = st.tabs(["Add to Google Calendar", "Download a file"])

    with tab_g:
        if not (GOOGLE_ID and GOOGLE_SECRET):
            st.info("Google sign-in isn't set up for this copy of Slot yet. The README shows how. Until then, use Download a file.")
        elif not ss.gtoken:
            st.markdown('<p class="note">Slot makes its own calendar called Lectures. It cannot see or change your other calendars. When the timetable changes, come back, upload the new one, and Slot updates only what changed.</p>', unsafe_allow_html=True)
            state = ss.setdefault("oauth_state", gcal.new_state())
            snap = {k: ss[k] for k in SNAP_KEYS if k in ss}
            snap["breaks_base"], snap["exams_base"] = breaks_df, exams_df
            prune_store()
            oauth_store()[state] = {"t": _time.time(), "data": snap}
            url = gcal.auth_link(GOOGLE_ID, REDIRECT, state)
            st.markdown(f'<a class="g-btn" href="{url}" target="_top">Sign in with Google</a>', unsafe_allow_html=True)
        else:
            try:
                with st.spinner("Checking your Lectures calendar..."):
                    if not ss.get("cal_id"):
                        ss.cal_id = gcal.get_or_create_calendar(ss.gtoken)
                    existing = gcal.existing_events(ss.gtoken, ss.cal_id)
                keep = set() if exams else {"exam"}  # no exams entered: leave old exams alone
                p = gcal.plan(events, existing, keep_kinds=keep)
                n = len(p["add"]) + len(p["change"]) + len(p["remove"])

                st.markdown(
                    '<div class="diff">'
                    f'<div class="d add"><b>{len(p["add"])}</b>to add</div>'
                    f'<div class="d chg"><b>{len(p["change"])}</b>to change</div>'
                    f'<div class="d rem"><b>{len(p["remove"])}</b>to remove</div>'
                    f'<div class="d same"><b>{p["same"]}</b>already there</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
                if n:
                    with st.expander("See the changes"):
                        for e in p["add"]:
                            st.markdown(f"🟢 **{e['summary']}**, {e['label']}")
                        for old, e in p["change"]:
                            st.markdown(f"🟡 **{e['summary']}**, now {e['label']}" + (f" in {e['location']}" if e["location"] else ""))
                        for e in p["remove"]:
                            st.markdown(f"🔴 **{e.get('summary', 'Event')}**")
                    if st.button(f"Apply {n} change{'s' if n != 1 else ''} to Google Calendar", type="primary", width="stretch"):
                        bar = st.progress(0.0, text="Updating your calendar...")
                        gcal.apply(ss.gtoken, ss.cal_id, p, progress=lambda f: bar.progress(f, text="Updating your calendar..."))
                        bar.empty()
                        st.success("Done. Your Lectures calendar is up to date.")
                        st.link_button("Open Google Calendar", gcal.calendar_link())
                        st.balloons()
                else:
                    st.success("Your Lectures calendar already matches this timetable.")
                    st.link_button("Open Google Calendar", gcal.calendar_link())
            except gcal.GoogleError as e:
                st.error(str(e))
                if "expired" in str(e):
                    ss.gtoken = None
                    ss.pop("cal_id", None)

    with tab_f:
        st.download_button("Download calendar file", to_ics(events), "lectures.ics", "text/calendar", type="primary")
        st.markdown(
            """<div class="done-card"><b>Add it to Google Calendar</b><ol>
<li>Open calendar.google.com on a computer.</li>
<li>Click the gear icon, then Settings.</li>
<li>Choose Import and export, then Import.</li>
<li>Select lectures.ics and click Import.</li>
</ol><p class="note" style="margin-top:.6rem">With a file, updates are manual: delete the old Lectures calendar and import the new file. The Google Calendar tab does this for you.</p></div>""",
            unsafe_allow_html=True,
        )

    if st.button("Back to check"):
        ss.step = 3
        st.rerun()
