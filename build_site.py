"""Build a standalone, file-openable copy of the homework viewer.

The normal Flask app is convenient while editing, but it needs a running
server.  This script renders every chapter and question with the existing
templates and writes a static site to ``site/``.  Open ``site/index.html``
directly from disk: no server, no localhost, and no Python process required.

Progress is stored in the browser (localStorage, with a window.name fallback
for browsers that block storage on file:// URLs).  The current server-side
progress in ``data/progress.json`` is embedded as the initial value.

Run:

    py build_site.py

Rebuild after changing either PDF or the templates.  The generated ``site/``
folder is intentionally ignored by git; the source PDFs and page images are
the inputs.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import app as viewer

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"

# Only rewrite HTML URLs that point at the Flask app.  Links that are already
# relative, fragments, and external URLs pass through untouched.
URL_ATTR_RE = re.compile(r'\b(href|src|action)="(/[^"]*)"')


def map_path(path: str) -> str:
    if path == "/":
        return "index.html"
    if path == "/search":
        return "search.html"
    if path == "/source/assignments.pdf":
        return "../Assignments_for_2020-2021.pdf"
    if path == "/source/textbook.pdf":
        return "../AP-Calc-BC_textbook_.pdf"
    if path.startswith("/static/"):
        return "../static/" + path[len("/static/") :]

    question = re.fullmatch(r"/chapter/([^/]+)/question/([^/]+)", path)
    if question:
        return f"question-{question.group(2)}.html"

    chapter = re.fullmatch(r"/chapter/([^/]+)", path)
    if chapter:
        return f"chapter-{chapter.group(1)}.html"

    toggle = re.fullmatch(r"/toggle/([^/]+)", path)
    if toggle:
        # The static viewer handles this with JavaScript.  Keeping the forms
        # on the page gives keyboard users the same controls.
        return "#"

    bulk = re.fullmatch(r"/bulk/(?:chapter|unit)/[^/]+", path)
    if bulk:
        return "#"

    raise ValueError(f"Unmapped internal URL: {path}")


def rewrite_url(url: str) -> str:
    if not url.startswith("/") or url.startswith("//"):
        return url

    path, _, fragment = url.partition("#")
    path, has_query, query = path.partition("?")
    mapped = map_path(path)
    if mapped == "#":
        return mapped
    if has_query:
        mapped += "?" + query
    if fragment:
        mapped += "#" + fragment
    return mapped


def rewrite_html(html: str) -> str:
    return URL_ATTR_RE.sub(
        lambda match: f'{match.group(1)}="{rewrite_url(match.group(2))}"',
        html,
    )


def render(client, path: str) -> str:
    response = client.get(path)
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned HTTP {response.status_code}")
    return response.get_data(as_text=True)


def add_scripts(html: str, *, include_search_data: bool = False) -> str:
    scripts = ['<script src="progress.js"></script>']
    if include_search_data:
        scripts.append('<script src="data.js"></script>')
    scripts.append('<script src="../static/app.js"></script>')
    return html.replace("</body>", "\n".join(scripts) + "\n</body>")


def js_json(value: object) -> str:
    """JSON that is safe to drop inside a classic <script> block."""
    text = json.dumps(value, ensure_ascii=False)
    return (
        text.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def write_page(path: Path, html: str, *, include_search_data: bool = False) -> None:
    page = add_scripts(rewrite_html(html), include_search_data=include_search_data)
    path.write_text(page, encoding="utf-8")


def main() -> None:
    viewer.ensure_data()
    viewer.load_data()
    assert viewer.DATA is not None

    if not viewer.FLAT_CHAPTERS:
        raise SystemExit("No chapters found in static/data.json")

    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    SITE_DIR.mkdir(parents=True)

    # Progress seeds and question ids used by static/app.js.
    chapter_questions = {
        chapter["id"]: [
            question["id"] for question in chapter.get("questions", [])
        ]
        for _unit, chapter in viewer.FLAT_CHAPTERS
    }
    unit_questions = {
        str(unit["number"]): [
            question["id"]
            for chapter in unit.get("chapters", [])
            for question in chapter.get("questions", [])
        ]
        for unit in viewer.DATA.get("units", [])
    }
    progress_script = (
        "window.PROGRESS_STORAGE_KEY = "
        + js_json("bccalc-homework-progress-v1")
        + ";\nwindow.INITIAL_PROGRESS = "
        + js_json(sorted(viewer.PROGRESS))
        + ";\nwindow.QUESTION_IDS = "
        + js_json([question["id"] for _, _, question in viewer.FLAT_QUESTIONS])
        + ";\nwindow.SCOPE_QUESTIONS = "
        + js_json({"chapter": chapter_questions, "unit": unit_questions})
        + ";\n"
    )
    (SITE_DIR / "progress.js").write_text(progress_script, encoding="utf-8")

    search_data = {
        "meta": viewer.DATA.get("meta", {}),
        "units": viewer.DATA.get("units", []),
    }
    (SITE_DIR / "data.js").write_text(
        "window.HOMEWORK_DATA = " + js_json(search_data) + ";\n",
        encoding="utf-8",
    )

    client = viewer.app.test_client()

    chapter_count = 0
    question_count = 0
    first_chapter_html: str | None = None

    for _unit, chapter in viewer.FLAT_CHAPTERS:
        html = render(client, f"/chapter/{chapter['id']}")
        write_page(SITE_DIR / f"chapter-{chapter['id']}.html", html)
        if first_chapter_html is None:
            first_chapter_html = html
        chapter_count += 1

        for question in chapter.get("questions", []):
            question_html = render(
                client,
                f"/chapter/{chapter['id']}/question/{question['id']}",
            )
            write_page(
                SITE_DIR / f"question-{question['id']}.html",
                question_html,
            )
            question_count += 1

    # Opening index.html drops the reader straight into the first chapter.
    # The brand/home links on every page also point back here.
    assert first_chapter_html is not None
    write_page(SITE_DIR / "index.html", first_chapter_html)

    search_html = render(client, "/search")
    write_page(SITE_DIR / "search.html", search_html, include_search_data=True)

    print(
        f"Built {SITE_DIR / 'index.html'}\n"
        f"  {chapter_count} chapter pages, {question_count} question pages\n"
        f"  {len(viewer.PROGRESS)} seeded complete questions\n"
        f"  Open site/index.html directly in a browser."
    )


if __name__ == "__main__":
    main()
