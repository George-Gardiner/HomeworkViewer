"""Local BC Calculus homework viewer.

``build_site.py`` is the primary entry point. It renders a standalone copy of
the viewer into ``site/`` that can be opened directly from disk, with progress
and search handled in the browser. No server is needed for normal use.

This Flask app remains available as a development server and as the renderer
used by the static builder. Server progress is stored in ``data/progress.json``;
static-site progress is stored in the browser. If the PDFs change,
``build_data.py`` regenerates the page index and textbook page images.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
import webbrowser
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
DATA_FILE = STATIC_DIR / "data.json"
PROGRESS_FILE = DATA_DIR / "progress.json"
PAGES_DIR = STATIC_DIR / "pages"

app = Flask(
    __name__,
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.secret_key = os.environ.get("HOMEWORK_VIEWER_SECRET", "local-homework-viewer")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True

DATA: dict[str, Any] | None = None
CHAPTERS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
QUESTIONS: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
FLAT_QUESTIONS: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
QUESTION_POSITION: dict[str, int] = {}
FLAT_CHAPTERS: list[tuple[dict[str, Any], dict[str, Any]]] = []

PROGRESS_LOCK = threading.Lock()
PROGRESS: set[str] = set()


# ---------------------------------------------------------------------------
# Startup / data
# ---------------------------------------------------------------------------


def ensure_data() -> None:
    """Build the page index the first time the app runs."""
    source_pdfs = [
        ROOT / "Assignments_for_2020-2021.pdf",
        ROOT / "AP-Calc-BC_textbook_.pdf",
    ]
    rebuild = not DATA_FILE.exists()
    if DATA_FILE.exists():
        data_mtime = DATA_FILE.stat().st_mtime
        rebuild = any(pdf.stat().st_mtime > data_mtime for pdf in source_pdfs)
    if not PAGES_DIR.exists() or not any(PAGES_DIR.glob("*.png")):
        rebuild = True
    if not rebuild:
        return

    print("Building homework index and page images (first run)…")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    subprocess.run(
        [sys.executable, str(ROOT / "build_data.py")],
        check=True,
        cwd=ROOT,
        env=env,
    )


def load_data() -> None:
    global DATA
    DATA = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    CHAPTERS.clear()
    QUESTIONS.clear()
    FLAT_QUESTIONS.clear()
    QUESTION_POSITION.clear()
    FLAT_CHAPTERS.clear()

    for unit in DATA.get("units", []):
        for chapter in unit.get("chapters", []):
            chapter["_unit_number"] = unit["number"]
            CHAPTERS[chapter["id"]] = (unit, chapter)
            FLAT_CHAPTERS.append((unit, chapter))
            for question in chapter.get("questions", []):
                question["_chapter_id"] = chapter["id"]
                QUESTIONS[question["id"]] = (unit, chapter, question)
                QUESTION_POSITION[question["id"]] = len(FLAT_QUESTIONS)
                FLAT_QUESTIONS.append((unit, chapter, question))

    load_progress()


def load_progress() -> None:
    global PROGRESS
    try:
        saved = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
        PROGRESS = set(saved.get("done", []))
    except (OSError, ValueError, TypeError):
        PROGRESS = set()

    # Ignore old ids if the source PDFs were rebuilt and questions changed.
    PROGRESS &= set(QUESTIONS)


def save_progress() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"done": sorted(PROGRESS)}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(PROGRESS_FILE)


def is_done(question_id: str) -> bool:
    return question_id in PROGRESS


def chapter_done(chapter: dict[str, Any]) -> int:
    return sum(1 for question in chapter.get("questions", []) if is_done(question["id"]))


def progress_info() -> dict[str, Any]:
    done = sum(1 for question_id in QUESTIONS if question_id in PROGRESS)
    total = len(FLAT_QUESTIONS)
    return {
        "done": done,
        "total": total,
        "percent": round((done / total) * 100, 1) if total else 0,
    }


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def question_path(question: dict[str, Any], chapter: dict[str, Any]) -> str:
    path = url_for(
        "question_view",
        chapter_id=chapter["id"],
        question_id=question["id"],
    )
    if question.get("anchor"):
        path += "#page-highlight"
    return path


def chapter_path(chapter: dict[str, Any]) -> str:
    return url_for("chapter_view", chapter_id=chapter["id"])


def chapter_pages(chapter: dict[str, Any]) -> dict[str, Any]:
    pages: list[int] = []
    worksheet = False
    for assignment in chapter.get("assignments", []):
        if assignment.get("worksheet"):
            worksheet = True
        for page in assignment.get("pages", []):
            if page not in pages:
                pages.append(page)
    return {"pages": pages, "worksheet": worksheet}


def unit_codes(unit: dict[str, Any]) -> str:
    parts = []
    for chapter in unit.get("chapters", []):
        code = chapter.get("code") or "—"
        parts.append(code if code != "—" else chapter.get("title", "Sheet"))
    return " · ".join(parts)


def distinct_question_pages(chapter: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    for question in chapter.get("questions", []):
        page = question.get("pageInfo", {}).get("printedPage", question.get("page"))
        if page is not None and page not in pages:
            pages.append(page)
    return pages


def safe_next(value: str | None) -> str:
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or "\n" in value
        or "\r" in value
    ):
        return url_for("home")
    return value


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def text_score(text: str, query: str) -> int:
    if not query:
        return 0
    score = 0
    if text == query:
        score += 160
    if text.startswith(query):
        score += 90
    if query in text.split():
        score += 70
    if query in text:
        score += 35
    return score


def search_results(
    query: str,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    units: list[tuple[int, dict[str, Any]]] = []
    chapters: list[tuple[int, dict[str, Any]]] = []
    questions: list[tuple[int, dict[str, Any]]] = []
    needle = normalize(query)
    if not needle:
        return {"units": [], "chapters": [], "questions": []}, 0

    for unit in DATA.get("units", []):
        short_codes = unit_codes(unit)
        chapter_text = " ".join(
            f"{c.get('code', '')} {c.get('title', '')} {c.get('book_title') or ''}"
            for c in unit.get("chapters", [])
        )
        haystack = normalize(
            f"unit {unit['number']} u{unit['number']} {short_codes} {chapter_text} "
            f"{' '.join(unit.get('tests', []))} {unit.get('note') or ''}"
        )
        score = text_score(haystack, needle)
        if score:
            first = unit.get("chapters", [{}])[0]
            units.append(
                (
                    score,
                    {
                        "kind": "unit",
                        "mark": f"U{unit['number']}",
                        "title": f"Unit {unit['number']}",
                        "sub": short_codes or "Assignment sheet",
                        "url": chapter_path(first) if first else url_for("home"),
                    },
                )
            )

        for chapter in unit.get("chapters", []):
            chapter_number = chapter.get("number") or chapter.get("code") or "—"
            haystack = normalize(
                f"{chapter.get('code', '')} {chapter.get('title', '')} "
                f"{chapter.get('book_title') or ''} {chapter.get('schedule_title') or ''} "
                f"{' '.join(chapter.get('notes', []))} "
                f"chapter {chapter_number} chapter {chapter.get('code', '')} "
                f"unit {unit['number']} u{unit['number']}"
            )
            score = text_score(haystack, needle)
            if score:
                chapters.append(
                    (
                        score,
                        {
                            "kind": "chapter",
                            "mark": chapter.get("code") or "—",
                            "title": f"{chapter.get('code', '—')} {chapter.get('title', '')}".strip(),
                            "sub": (
                                f"Unit {unit['number']} · Chapter {chapter_number} · "
                                f"{len(chapter.get('questions', []))} question"
                                f"{'' if len(chapter.get('questions', [])) == 1 else 's'}"
                            ),
                            "url": chapter_path(chapter),
                        },
                    )
                )

            for question in chapter.get("questions", []):
                label = question.get("label") or question.get("number") or "Problem"
                page = question.get("pageInfo", {}).get("printedPage", question.get("page"))
                chapter_number = chapter.get("number") or chapter.get("code") or "—"
                haystack = normalize(
                    f"q{label} {label} {question.get('number') or ''} question "
                    f"{question.get('range') or ''} {question.get('note') or ''} "
                    f"{chapter.get('code', '')} {chapter.get('title', '')} "
                    f"chapter {chapter_number} unit {unit['number']} "
                    f"{question.get('snippet') or ''} p.{page or ''} page {page or ''}"
                )
                score = text_score(haystack, needle)
                if score:
                    questions.append(
                        (
                            score,
                            {
                                "kind": "question",
                                "mark": str(label),
                                "title": f"Question {label}",
                                "sub": (
                                    f"Unit {unit['number']} · Chapter {chapter_number} · "
                                    f"{chapter.get('code', '')} {chapter.get('title', '')}".strip()
                                    + (f" · p. {page}" if page else "")
                                    + (
                                        f" · from {question['range']}"
                                        if question.get("range")
                                        else ""
                                    )
                                ),
                                "url": question_path(question, chapter),
                            },
                        )
                    )

    units.sort(key=lambda item: (-item[0], item[1]["title"]))
    chapters.sort(key=lambda item: (-item[0], item[1]["title"]))
    questions.sort(key=lambda item: (-item[0], item[1]["sub"], item[1]["title"]))
    total = len(units) + len(chapters) + len(questions)
    return (
        {
            "units": [item[1] for item in units[:16]],
            "chapters": [item[1] for item in chapters[:30]],
            "questions": [item[1] for item in questions[:60]],
        },
        total,
    )


# ---------------------------------------------------------------------------
# Flask context / routes
# ---------------------------------------------------------------------------


@app.context_processor
def inject_globals() -> dict[str, Any]:
    return {
        "meta": DATA.get("meta", {}) if DATA else {},
        "units": DATA.get("units", []) if DATA else [],
        "progress": progress_info() if DATA else {"done": 0, "total": 0, "percent": 0},
        "is_done": is_done,
        "chapter_done": chapter_done,
        "chapter_path": chapter_path,
        "question_path": question_path,
        "chapter_pages": chapter_pages,
        "unit_codes": unit_codes,
        "distinct_question_pages": distinct_question_pages,
    }


@app.get("/")
def home():
    last = session.get("last_chapter")
    if last and last in CHAPTERS:
        return redirect(chapter_path(CHAPTERS[last][1]))
    if FLAT_CHAPTERS:
        return redirect(chapter_path(FLAT_CHAPTERS[0][1]))
    abort(404)


def render_homework(
    unit: dict[str, Any],
    chapter: dict[str, Any],
    question: dict[str, Any] | None,
):
    session["last_chapter"] = chapter["id"]

    if question:
        position = QUESTION_POSITION[question["id"]]
        previous = FLAT_QUESTIONS[position - 1] if position > 0 else None
        following = (
            FLAT_QUESTIONS[position + 1]
            if position + 1 < len(FLAT_QUESTIONS)
            else None
        )
        prev_url = question_path(previous[2], previous[1]) if previous else None
        next_url = question_path(following[2], following[1]) if following else None
        return_url = question_path(question, chapter)
        list_return_url = return_url.split("#", 1)[0]
    else:
        prev_url = next_url = None
        return_url = request.path
        list_return_url = request.path

    return render_template(
        "chapter.html",
        active_unit=unit["number"],
        active_chapter=chapter["id"],
        unit=unit,
        chapter=chapter,
        question=question,
        selected_question_id=question["id"] if question else None,
        previous_url=prev_url,
        next_url=next_url,
        return_url=return_url,
        list_return_url=list_return_url,
    )


@app.get("/chapter/<chapter_id>")
def chapter_view(chapter_id: str):
    entry = CHAPTERS.get(chapter_id)
    if not entry:
        abort(404)
    unit, chapter = entry
    question = chapter["questions"][0] if chapter.get("questions") else None
    return render_homework(unit, chapter, question)


@app.get("/chapter/<chapter_id>/question/<question_id>")
def question_view(chapter_id: str, question_id: str):
    entry = QUESTIONS.get(question_id)
    if not entry:
        abort(404)
    unit, chapter, question = entry
    if chapter["id"] != chapter_id:
        return redirect(question_path(question, chapter))
    return render_homework(unit, chapter, question)


@app.get("/search")
def search():
    query = request.args.get("q", "").strip()
    results, total = search_results(query)
    shown = sum(len(items) for items in results.values())
    return render_template(
        "search.html",
        active_unit=None,
        active_chapter=None,
        query=query,
        results=results,
        total_results=total,
        shown_results=shown,
    )


@app.post("/toggle/<question_id>")
def toggle_question(question_id: str):
    entry = QUESTIONS.get(question_id)
    if not entry:
        abort(404)
    unit, chapter, question = entry
    with PROGRESS_LOCK:
        if question_id in PROGRESS:
            PROGRESS.remove(question_id)
            flash("Question marked as not complete.", "info")
        else:
            PROGRESS.add(question_id)
            flash("Question marked complete.", "success")
        save_progress()
    return redirect(safe_next(request.form.get("next") or question_path(question, chapter)))


@app.route("/source/assignments.pdf")
def source_assignments():
    return send_from_directory(
        ROOT,
        "Assignments_for_2020-2021.pdf",
        mimetype="application/pdf",
    )


@app.route("/source/textbook.pdf")
def source_textbook():
    return send_from_directory(
        ROOT,
        "AP-Calc-BC_textbook_.pdf",
        mimetype="application/pdf",
    )


@app.errorhandler(404)
def not_found(error):
    return (
        render_template(
            "404.html",
            active_unit=None,
            active_chapter=None,
        ),
        404,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the homework viewer")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    ensure_data()
    load_data()

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"\n  BC Calculus homework viewer → {url}\n")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
