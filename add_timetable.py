"""Add a batch timetable to the library so students can pick it from a list.

Usage:
    python add_timetable.py "Timetable E21-CS2-V5.pdf" "E21 CS2 (V5)"

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
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    path, name = Path(sys.argv[1]), sys.argv[2]
    data = path.read_bytes()

    classes = read_pdf_table(data) if path.suffix.lower() == ".pdf" else []
    how = "read directly from the PDF"
    if not classes:
        secrets = tomllib.loads(Path(".streamlit/secrets.toml").read_text())
        classes = extract_lectures(data, path.name, secrets.get("PROVIDER", "gemini"),
                                   secrets["API_KEY"], secrets.get("MODEL"))
        how = "read with AI, please check it carefully"

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out = Path("timetables") / f"{slug}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"name": name, "source": path.name, "classes": classes}, indent=1))
    print(f"Saved {len(classes)} classes to {out} ({how}).")


if __name__ == "__main__":
    main()
