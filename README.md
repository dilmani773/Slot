# Slot: timetable to calendar

Upload a timetable photo or PDF. AI reads every class. You pick your subjects, check the result, add exams, and send it all to Google Calendar. When the timetable changes, Slot updates only what changed. Download a `.ics` file and import it into Google Calendar.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then add your key
streamlit run app.py
```

No key yet? Pick **E21 CS2 (V5)** from the list, or upload a normal PDF. Neither needs AI.

## How Slot avoids using AI

AI is the last option, not the first:

1. **Timetable library.** Students pick their batch from a list. No upload, no AI.
2. **PDFs read directly.** PDFs made in Word, Excel or Google Docs have real text inside. Slot reads the table with code: free, instant and exact.
3. **AI only for photos and scanned PDFs.** And even then, the same file is only read once (see below).

You can run Slot with no AI key at all. Library picks and normal PDFs still work.

### Add a batch to the library

```bash
python add_timetable.py "Timetable E21-CS2-V5.pdf" "E21 CS2 (V5)"
```

This saves `timetables/e21-cs2-v5.json`. Open it, check it, then commit and push. The new batch appears in the list.
When a new version comes out, add it with a new name, like "E21 CS2 (V6)", and delete the old file.

## Your users never need an API key

You add one key in secrets. Everyone uses the app with no setup.
To protect your free quota:
- **Same file, one read.** Results are saved by file fingerprint. If your whole batch uploads the same PDF, the AI reads it once and everyone else gets it instantly.
- **Per-visit limit:** each visitor gets 5 fresh reads (`READS_PER_VISIT`).
- **Daily cap:** the whole app stops at 300 fresh reads a day (`READS_PER_DAY`).
- If someone thinks the saved result is wrong, they can tick "Read it again from scratch".

Saved results live in `.slot_cache/`. On Streamlit Cloud this folder resets when the app restarts, which is fine. The first upload after a restart just reads again.

## Get a free API key (for you, the owner)

Gemini has a free tier: https://aistudio.google.com/apikey
Set `PROVIDER = "gemini"` and paste the key as `API_KEY`.
To use Claude instead, set `PROVIDER = "claude"` and use a key from console.anthropic.com.
Model names change often. For Gemini, if the model is retired the app asks Google for the newest Flash model and uses that. You can also set `MODEL` in secrets.

## Set up Google sign-in (for the "Add to Google Calendar" button)

Without this, the app still works. People download a `.ics` file instead.

1. Go to console.cloud.google.com and create a project called Slot.
2. Open **APIs & Services > Library**, search **Google Calendar API**, and click Enable.
3. Open **Google Auth Platform** (or **OAuth consent screen**). Choose **External**. Fill in the app name and your email.
4. Under **Data access** (or **Scopes**), add these two:
   - `https://www.googleapis.com/auth/calendar.app.created`
   - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
5. Under **Audience** (or **Test users**), add your own Gmail and your batchmates' Gmails. Testing mode allows up to 100 test users with no Google review.
6. Open **Clients** (or **Credentials**) > Create client > **Web application**. Under Authorized redirect URIs add:
   - `http://localhost:8501`
   - `https://your-app.streamlit.app` (after you deploy)
7. Copy the client ID and secret into `secrets.toml` with `GOOGLE_REDIRECT_URI`.

Notes:
- Slot only gets access to the calendar it creates. It cannot see your other calendars or events.
- Sign-in lasts one hour. Slot keeps no database, so to update later, sign in again.
- To open it to more than 100 people, submit the app for Google verification.

## How timetable updates work

Each class gets a fixed id based on its course, type and day. When a new timetable version comes out:
1. Upload it and pick your subjects again.
2. Sign in with Google. Slot finds your Lectures calendar and compares.
3. You see how many classes will be added, changed (new time or hall) and removed, then click Apply.

If you leave the exam table empty, your old exams stay in the calendar.

## Deploy free on Streamlit Cloud

1. Push this folder to a GitHub repo (secrets.toml is in .gitignore, keep it that way).
2. Go to share.streamlit.io, click Create app, pick the repo and `app.py`.
3. In Advanced settings > Secrets, paste the contents of your secrets.toml.
4. Deploy and share the link with your batch.

## Files

| File | What it does |
|---|---|
| `app.py` | The three-step UI |
| `parser.py` | Sends the image to Gemini or Claude, then joins back-to-back slots into one class |
| `pdf_table.py` | Reads tables straight from PDF text, no AI |
| `timetables/` | The batch library. One JSON file per timetable |
| `add_timetable.py` | Adds a timetable to the library |
| `events.py` | Builds class and exam events with stable ids, and the `.ics` file |
| `ai_guard.py` | Saves AI results per file and enforces read limits |
| `gcal.py` | Google sign-in, finding or creating the Lectures calendar, and syncing changes |
| `ics_builder.py` | Time helpers and checks |
| `grid.py` | Draws the weekly timetable preview |
| `style.css` | Custom look |

## Ideas for later

- Odd/even week classes (some labs run every other week)
- Save a batch's timetable so classmates skip the upload step
- Remember sign-in with refresh tokens and a small database, so updates happen automatically
