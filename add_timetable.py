"""Add a batch timetable to the library so students can pick it from a list.

Usage:
    python add_timetable.py FILE "BATCH NAME" --university "University of Peradeniya" \
        [--faculty Engineering] [--timezone Asia/Colombo]

Example:
    python add_timetable.py "Timetable E21-CS2-V5.pdf" "E21 CS2 (V5)" \
        --university "University of Peradeniya" --faculty Engineering

PDFs with real text are read directly (no AI). Photos and scans use the AI key
from .streamlit/secrets.toml. Open the JSON file afterwards and check it.
"""
import json
import re
import sys
import tomllib
from pathlib import Path

from parser import extract_lectures
from pdf_table import read_pdf_table


def main():
    import argparse
    from zoneinfo import ZoneInfo

    ap = argparse.ArgumentParser(description="Add a timetable to the Slot library.")
    ap.add_argument("file")
    ap.add_argument("name", help='Batch name, e.g. "E21 CS2 (V5)"')
    ap.add_argument("--university", required=True)
    ap.add_argument("--faculty", default="")
    ap.add_argument("--timezone", default="Asia/Colombo")
    args = ap.parse_args()
    ZoneInfo(args.timezone)  # fails early on a typo
    path, name = Path(args.file), args.name
    data = path.read_bytes()

    classes = read_pdf_table(data) if path.suffix.lower() == ".pdf" else []
    how = "read directly from the PDF"
    if not classes:
        secrets = tomllib.loads(Path(".streamlit/secrets.toml").read_text())
        classes = extract_lectures(data, path.name, secrets.get("PROVIDER", "gemini"),
                                   secrets["API_KEY"], secrets.get("MODEL"))
        how = "read with AI, please check it carefully"

    slug = re.sub(r"[^a-z0-9]+", "-", f"{args.university} {args.faculty} {name}".lower()).strip("-")
    out = Path("timetables") / f"{slug}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "university": args.university, "faculty": args.faculty, "name": name,
        "timezone": args.timezone, "source": path.name, "classes": classes,
    }, indent=1))
    print(f"Saved {len(classes)} classes to {out} ({how}).")


if __name__ == "__main__":
    main()
