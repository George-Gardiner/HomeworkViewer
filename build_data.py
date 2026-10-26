"""Build the homework viewer dataset from the two source PDFs.

Run this once (or whenever the PDFs change) to regenerate:

    python build_data.py

It parses the assignment schedule, maps every homework problem to the
textbook page that contains it, renders those pages as PNGs, and writes
``static/data.json``.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

ROOT = Path(__file__).resolve().parent
ASSIGNMENTS_PDF = ROOT / "Assignments_for_2020-2021.pdf"
TEXTBOOK_PDF = ROOT / "AP-Calc-BC_textbook_.pdf"
STATIC_DIR = ROOT / "static"
PAGES_DIR = STATIC_DIR / "pages"
DATA_FILE = STATIC_DIR / "data.json"

PAGE_OFFSET = 23  # 0-based PDF page index = printed page + 23
RENDER_ZOOM = 2.0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def split_page_refs(assignment: str) -> list[tuple[int, str]]:
    """Return [(printed_page, problem_text), ...] for an assignment cell."""
    matches = list(re.finditer(r"p\.\s*(\d+)\s*:", assignment, re.I))
    if not matches:
        return []
    out: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(assignment)
        content = assignment[start:end].strip(" ;,")
        content = re.sub(r"\s+AFTER AP EXAM\s*$", "", content, flags=re.I).strip(" ;,")
        out.append((int(match.group(1)), content))
    return out


def expand_part_letters(part_string: str) -> list[str]:
    """'gh' -> ['g', 'h'] (multi-letter part names are not used here)."""
    return [ch for ch in part_string.lower() if ch.isalpha()]


def parse_question_token(
    raw: str,
    printed_page: int,
    assignment_text: str,
) -> list[dict[str, Any]]:
    token = raw.strip()
    if not token:
        return []

    note = None
    calculator = "*" in token
    token = token.replace("*", "").strip()

    note_match = re.search(r"\(([^)]*)\)\s*$", token)
    if note_match:
        note = norm(note_match.group(1))
        note = re.sub(r"^(w/|with)\s*", "", note, flags=re.I)
        token = token[: note_match.start()].strip()

    trailing = re.search(r"\s+(AFTER AP EXAM|optional[^,;]*)$", token, re.I)
    if trailing:
        note = " · ".join(x for x in (note, norm(trailing.group(1))) if x)
        token = token[: trailing.start()].strip()

    token = token.strip(" .")
    if not token:
        return []

    # A range like "29-51 odd" becomes individual questions.
    range_match = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)(?:\s*(odd|even))?", token, re.I)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        qualifier = (range_match.group(3) or "").lower()
        if qualifier == "odd" and start % 2 == 0:
            start += 1
        if qualifier == "even" and start % 2 == 1:
            start += 1
        step = 2 if qualifier else 1
        return [
            {
                "number": n,
                "parts": [],
                "calculator": calculator,
                "note": note,
                "page": printed_page,
                "range": f"{range_match.group(1)}–{range_match.group(2)}"
                + (f" {qualifier}" if qualifier else ""),
                "assignment": assignment_text,
            }
            for n in range(start, end + 1, step)
        ]

    single = re.fullmatch(r"(\d+)\s*([a-zA-Z]*)", token)
    if single:
        return [
            {
                "number": int(single.group(1)),
                "parts": expand_part_letters(single.group(2)),
                "calculator": calculator,
                "note": note,
                "page": printed_page,
                "range": None,
                "assignment": assignment_text,
            }
        ]

    return [
        {
            "number": None,
            "parts": [],
            "calculator": calculator,
            "note": note,
            "page": printed_page,
            "range": None,
            "label": token,
            "assignment": assignment_text,
        }
    ]


def parse_assignment_cell(text: str) -> dict[str, Any]:
    worksheet = bool(re.search(r"worksheet", text, re.I))
    if worksheet:
        return {
            "text": text,
            "worksheet": True,
            "pages": [],
            "questions": [],
            "groups": [],
        }

    page_refs = split_page_refs(text)
    groups: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for page, content in page_refs:
        page_questions: list[dict[str, Any]] = []
        for token in re.split(r"\s*[,;]\s*", content):
            page_questions.extend(parse_question_token(token, page, text))
        groups.append(
            {
                "page": page,
                "text": f"p. {page}: {content}",
                "questions": page_questions,
            }
        )
        questions.extend(page_questions)

    return {
        "text": text,
        "worksheet": False,
        "pages": [p for p, _ in page_refs],
        "questions": questions,
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Assignment schedule
# ---------------------------------------------------------------------------

def extract_lines(page: pymupdf.Page) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = norm("".join(span.get("text", "") for span in line.get("spans", [])))
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    lines.sort(key=lambda item: (item["y0"], item["x0"]))
    return lines


def clean_schedule_title(title: str) -> str:
    replacements = [
        (r"\bFnct\b", "Functions"),
        (r"\bFcts?\b", "Functions"),
        (r"\bDer\.\s*(?:of\s+)?", "Derivatives of "),
        (r"\bDer\s+of\s+", "Derivatives of "),
        (r"\bDiff\b", "Differentiation"),
        (r"\bApprox\b", "Approximation"),
        (r"\bIncr,Decr,Concavity\b", "Increasing, Decreasing, Concavity"),
        (r"\bFund Thm of Calculus\b", "Fundamental Theorem of Calculus"),
    ]
    out = norm(title)
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out, flags=re.I)
    return norm(out)


def parse_schedule() -> list[dict[str, Any]]:
    doc = pymupdf.open(ASSIGNMENTS_PDF)
    all_lines: list[dict[str, Any]] = []
    for page_no, page in enumerate(doc):
        for line in extract_lines(page):
            line["page"] = page_no
            all_lines.append(line)

    # The table has clear x-columns. Group lines into visual rows first so a
    # topic number and the first section on that row are never processed out
    # of order (the PDF sometimes gives them y-coordinates 0.1pt apart).
    all_lines = [line for line in all_lines if 88 <= line["y0"] <= 748]
    table_rows: list[dict[str, Any]] = []
    for line in sorted(all_lines, key=lambda item: (item["page"], item["y0"])):
        row = next(
            (
                r
                for r in table_rows
                if r["page"] == line["page"] and abs(r["y"] - line["y0"]) <= 3.5
            ),
            None,
        )
        if row is None:
            row = {"page": line["page"], "y": line["y0"], "lines": []}
            table_rows.append(row)
        row["lines"].append(line)
    table_rows.sort(key=lambda r: (r["page"], r["y"]))

    topics: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for row in table_rows:
        lines = sorted(row["lines"], key=lambda item: item["x0"])

        # Topic number column: always handle the topic start first.
        for line in lines:
            if line["x0"] < 100 and re.fullmatch(r"\d{1,2}", line["text"]):
                current = {
                    "number": int(line["text"]),
                    "section_lines": [],
                    "assignment_lines": [],
                    "tests": [],
                    "notes": [],
                }
                topics.append(current)
                break

        if current is None:
            continue

        for line in lines:
            x0 = line["x0"]
            text = norm(line["text"])

            if x0 < 100:
                continue

            # Assignment column first: a p.106 row can contain "AFTER AP EXAM"
            # without being a test.
            if 250 <= x0 < 500 and re.search(r"^p\.|worksheet", text, re.I):
                current["assignment_lines"].append(
                    {"text": text, "y": line["y0"], "x": x0}
                )
                continue

            if x0 >= 240 and re.search(
                r"^(Test on|Cumulative Test|AP Exam\b)", text, re.I
            ):
                current["tests"].append(
                    re.sub(r"\s+on\s*$", "", text, flags=re.I)
                )
                continue

            # Section title column.
            if 100 <= x0 < 250:
                current["section_lines"].append(
                    {"text": text, "y": line["y0"], "x": x0}
                )
                continue

            if re.search(r"after ap exam", text, re.I):
                current["notes"].append(text)

    doc.close()

    # Build sections from the section-title column. Lines that begin with a
    # section number start a section; other lines wrap the previous title.
    for topic in topics:
        sections: list[dict[str, Any]] = []
        for item in sorted(topic["section_lines"], key=lambda v: v["y"]):
            match = re.match(r"^(\d+\.\d+)\b\s*(.*)$", item["text"])
            if match:
                sections.append(
                    {
                        "code": match.group(1),
                        "schedule_title": norm(match.group(2)),
                        "raw_titles": [norm(match.group(2))],
                    }
                )
                continue
            if not sections:
                sections.append(
                    {
                        "code": None,
                        "schedule_title": item["text"],
                        "raw_titles": [item["text"]],
                    }
                )
                continue
            if re.match(r"^(Accumulation|Density)\b", item["text"], re.I):
                sections.append(
                    {
                        "code": None,
                        "schedule_title": item["text"],
                        "raw_titles": [item["text"]],
                    }
                )
                continue
            sections[-1]["raw_titles"].append(item["text"])
            sections[-1]["schedule_title"] = norm(
                sections[-1]["schedule_title"] + " " + item["text"]
            )
        topic["sections"] = sections

    return topics


# ---------------------------------------------------------------------------
# Book table of contents
# ---------------------------------------------------------------------------

def book_toc() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    doc = pymupdf.open(TEXTBOOK_PDF)
    toc = doc.get_toc(simple=True)
    doc.close()

    sections: dict[str, dict[str, Any]] = {}
    chapters: dict[str, str] = {}
    for level, title, page in toc:
        title = norm(title)
        chapter_match = re.fullmatch(r"(\d+):\s*(.*)", title)
        if chapter_match:
            chapters[chapter_match.group(1)] = chapter_match.group(2)
            continue
        section_match = re.fullmatch(r"(\d+\.\d+):\s*(.*)", title)
        if section_match:
            sections[section_match.group(1)] = {
                "title": section_match.group(2),
                "pdf_page": page,  # 1-based
            }
    return sections, chapters


# ---------------------------------------------------------------------------
# Textbook page anchors
# ---------------------------------------------------------------------------

EXERCISE_HEADER_RE = re.compile(r"^EXERCISE SET\s+(\d+\.\d+)")
QUESTION_START_RE = re.compile(r"^(\d{1,3})\.(?:\s|$)")


@dataclass
class Anchor:
    number: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    column_count: int = 0


@dataclass
class PageAnalysis:
    index: int  # 0-based
    rect: pymupdf.Rect
    lines: list[dict[str, Any]]
    anchors: list[Anchor]
    header: dict[str, Any] | None = None
    printed_page: int | None = None
    rendered: bool = False
    image: str | None = None
    width: int = 0
    height: int = 0


def analyze_page(doc: pymupdf.Document, index: int) -> PageAnalysis:
    page = doc[index]
    lines = extract_lines(page)
    header = next(
        (line for line in lines if EXERCISE_HEADER_RE.match(line["text"])), None
    )

    answers_header = next(
        (
            line
            for line in lines
            if re.match(r"^.?QUICK CHECK ANSWERS", line["text"], re.I)
        ),
        None,
    )

    anchors: list[Anchor] = []
    for line in lines:
        match = QUESTION_START_RE.match(line["text"])
        if not match:
            continue
        # Exercise numbers are set in the left margin of a text column.
        if not (30 <= line["x0"] <= 590):
            continue
        if header and line["y0"] < header["y1"] + 2:
            # Ignore the quick-check exercises above the exercise set.
            continue
        if answers_header and line["y0"] > answers_header["y0"] - 2:
            # Ignore the answer key at the bottom of the page.
            continue
        # Avoid running headers/footers.
        if line["y1"] < 45 or line["y0"] > page.rect.height - 25:
            continue
        anchors.append(
            Anchor(
                number=int(match.group(1)),
                x0=line["x0"],
                y0=line["y0"],
                x1=line["x1"],
                y1=line["y1"],
                text=line["text"],
            )
        )

    # Count anchors sharing the same column. This distinguishes real exercise
    # numbers from stray numbers in figures/tables.
    for anchor in anchors:
        anchor.column_count = sum(
            1 for other in anchors if abs(other.x0 - anchor.x0) < 28
        )

    printed_page = None
    footer_candidates = [
        line
        for line in lines
        if line["y0"] > page.rect.height - 55
        and re.fullmatch(r"\d{1,4}", line["text"])
    ]
    if footer_candidates:
        printed_page = int(footer_candidates[-1]["text"])

    return PageAnalysis(
        index=index,
        rect=page.rect,
        lines=lines,
        anchors=anchors,
        header=header,
        printed_page=printed_page,
    )


def page_section_code(analysis: PageAnalysis) -> str | None:
    """Return the textbook section printed on this page, if one is visible."""
    if analysis.header:
        match = EXERCISE_HEADER_RE.match(analysis.header["text"])
        if match:
            return match.group(1)

    # Continuation pages do not always repeat "EXERCISE SET n.m", but the
    # running header usually starts with the section number.
    for line in analysis.lines[:5]:
        if line["y0"] > 120:
            break
        match = re.match(r"^(\d{1,2}\.\d{1,2})\b", line["text"])
        if match:
            return match.group(1)
    return None


def choose_anchor(page: PageAnalysis, number: int) -> Anchor | None:
    candidates = [a for a in page.anchors if a.number == number]
    if not candidates:
        return None
    # Highest column count wins; ties go to the higher line (usually the first
    # occurrence in the exercise set).
    return max(candidates, key=lambda a: (a.column_count, -a.y0))


def find_anchor(
    analyses: dict[int, PageAnalysis], number: int, page_index: int
) -> tuple[Anchor, PageAnalysis] | None:
    for candidate_index in (
        page_index,
        page_index + 1,
        page_index - 1,
        page_index + 2,
        page_index - 2,
    ):
        analysis = analyses.get(candidate_index)
        if not analysis:
            continue
        anchor = choose_anchor(analysis, number)
        if anchor:
            return anchor, analysis
    return None


def snippet_for(anchor: Anchor, analysis: PageAnalysis) -> str:
    # The column ends at the next anchor in the same column, or after roughly
    # ten lines. Keep this deliberately short; it is a preview, not the text.
    next_same_column = [
        other
        for other in analysis.anchors
        if abs(other.x0 - anchor.x0) < 28 and other.y0 > anchor.y0 + 2
    ]
    bottom = (
        min(other.y0 for other in next_same_column)
        if next_same_column
        else anchor.y0 + 105
    )
    next_column = [a.x0 for a in analysis.anchors if a.x0 > anchor.x0 + 22]
    right = min(next_column) - 4 if next_column else analysis.rect.width - 12

    pieces: list[str] = []
    for line in analysis.lines:
        if line["y0"] < anchor.y0 - 2 or line["y0"] >= bottom:
            continue
        if line["x0"] < anchor.x0 - 3 or line["x0"] > right:
            continue
        pieces.append(line["text"])
        if len(" ".join(pieces)) > 190:
            break

    text = norm(" ".join(pieces))
    text = QUESTION_START_RE.sub("", text, count=1).strip(" .")
    if len(text) > 190:
        text = text[:187].rstrip() + "…"
    return text


def anchor_boxes(
    anchor: Anchor, analysis: PageAnalysis
) -> tuple[dict[str, float], dict[str, float]]:
    width, height = analysis.rect.width, analysis.rect.height

    next_same_column = [
        other
        for other in analysis.anchors
        if abs(other.x0 - anchor.x0) < 28 and other.y0 > anchor.y0 + 2
    ]
    next_column = sorted(a.x0 for a in analysis.anchors if a.x0 > anchor.x0 + 22)

    right = (next_column[0] - 5) if next_column else width - 12
    bottom = (
        min(other.y0 - 3 for other in next_same_column)
        if next_same_column
        else min(anchor.y0 + 125, height - 40)
    )
    top = anchor.y0 - 2

    highlight = {
        "x": max(0.0, (anchor.x0 - 5) / width),
        "y": max(0.0, top / height),
        "w": max(0.0, min(width, right) - (anchor.x0 - 5)) / width,
        "h": max(0.0, bottom - top) / height,
    }
    marker = {
        "x": max(0.0, (anchor.x0 - 4) / width),
        "y": max(0.0, (anchor.y0 - 2) / height),
        "w": max(0.0, min(width, anchor.x1 + 6) - (anchor.x0 - 4)) / width,
        "h": max(0.0, (anchor.y1 - anchor.y0 + 4)) / height,
    }
    return highlight, marker


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def code_parts(code: str | None) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", code or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def section_title_for_code(
    sections: list[dict[str, Any]], code: str | None
) -> str | None:
    for section in sections:
        if section["code"] == code:
            return clean_schedule_title(section["schedule_title"])
    return None


NO_CODE_HINTS: tuple[tuple[str, str, str | None], ...] = (
    (r"\bdensity\b", "6.6", None),
    (r"\baccumulation\b", "5.6", None),
    (r"\bvector\b", "12.2", "Covers textbook sections 12.1–12.2."),
    (r"\bmotion\b.*\bcurve\b", "12.6", None),
)


def no_code_hint(title: str) -> tuple[str, str | None] | None:
    for pattern, code, note in NO_CODE_HINTS:
        if re.search(pattern, title, re.I):
            return code, note
    return None


NO_CODE_COMPANIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\bvector\b", ("12.1",)),
)


def no_code_companions(title: str) -> tuple[str, ...]:
    for pattern, codes in NO_CODE_COMPANIONS:
        if re.search(pattern, title, re.I):
            return codes
    return ()


def fallback_section(
    sections: list[dict[str, Any]], index: int | None
) -> tuple[str | None, dict[str, Any] | None, bool]:
    """Return (code, no-code section, used_hint) for an assignment cell."""
    if index is None or not sections:
        return None, None, False
    index = min(max(index, 0), len(sections) - 1)
    section = sections[index]
    if section["code"]:
        return section["code"], None, False

    for other in sections[index + 1:]:
        if other["code"]:
            return other["code"], section, False
    for other in reversed(sections[:index]):
        if other["code"]:
            return other["code"], section, False

    hint = no_code_hint(section["schedule_title"])
    if hint:
        return hint[0], section, True
    return None, section, False


def test_unit_numbers(text: str) -> list[int]:
    """Extract the textbook unit(s) an assessment covers."""
    ranges = re.findall(
        r"Chapters?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})", text, re.I
    )
    if ranges:
        # A cumulative "Chapters 1–4" test belongs at the end of unit 4.
        return sorted({int(end) for _start, end in ranges})

    numbers = [
        int(value)
        for value in re.findall(r"\b(\d{1,2})\.\d{1,2}\b", text)
    ]
    if not numbers:
        numbers = [
            int(value)
            for value in re.findall(
                r"\bChapters?\s+(\d{1,2})\b", text, re.I
            )
        ]
    return sorted(set(numbers))


def main() -> int:
    if not ASSIGNMENTS_PDF.exists():
        print(f"Missing {ASSIGNMENTS_PDF}", file=sys.stderr)
        return 1
    if not TEXTBOOK_PDF.exists():
        print(f"Missing {TEXTBOOK_PDF}", file=sys.stderr)
        return 1

    sections_toc, _chapters_toc = book_toc()
    topics = parse_schedule()

    textbook = pymupdf.open(TEXTBOOK_PDF)
    analyses: dict[int, PageAnalysis] = {}

    def get_analysis(index: int) -> PageAnalysis | None:
        if index < 0 or index >= textbook.page_count:
            return None
        if index not in analyses:
            analyses[index] = analyze_page(textbook, index)
        return analyses[index]

    def printed_page_code(printed_page: int | None) -> str | None:
        if printed_page is None:
            return None
        analysis = get_analysis(printed_page + PAGE_OFFSET)
        return page_section_code(analysis) if analysis else None

    units_by_number: dict[int, dict[str, Any]] = {}
    chapters_by_code: dict[str, dict[str, Any]] = {}

    def unit_for(number: int) -> dict[str, Any]:
        unit = units_by_number.get(number)
        if unit is None:
            unit = {
                "number": number,
                "label": f"Unit {number}",
                "tests": [],
                "notes": [],
                "chapters": [],
                "questionCount": 0,
            }
            units_by_number[number] = unit
        return unit

    def chapter_for(
        code: str,
        schedule_title: str | None = None,
    ) -> dict[str, Any] | None:
        parts = code_parts(code)
        if parts is None:
            return None
        unit_number, chapter_number = parts
        chapter = chapters_by_code.get(code)
        if chapter is None:
            toc_entry = sections_toc.get(code)
            chapter = {
                "id": f"u{unit_number}-c{chapter_number}",
                "code": code,
                "number": chapter_number,
                "title": None,
                "book_title": toc_entry["title"] if toc_entry else None,
                "schedule_title": None,
                "toc_page": (
                    toc_entry["pdf_page"] - 1 - PAGE_OFFSET
                    if toc_entry
                    else None
                ),
                "assignments": [],
                "questions": [],
                "notes": [],
                "_unit_number": unit_number,
                "_schedule_titles": [],
                "_assignment_keys": set(),
                "_question_keys": set(),
            }
            chapters_by_code[code] = chapter
            unit_for(unit_number)["chapters"].append(chapter)
        if schedule_title and schedule_title not in chapter["_schedule_titles"]:
            chapter["_schedule_titles"].append(schedule_title)
        return chapter

    def add_assignment(
        chapter: dict[str, Any],
        text: str,
        worksheet: bool,
        pages: list[int],
    ) -> None:
        key = (text, worksheet, tuple(pages))
        if key in chapter["_assignment_keys"]:
            return
        chapter["_assignment_keys"].add(key)
        chapter["assignments"].append(
            {
                "text": text,
                "worksheet": worksheet,
                "pages": list(pages),
            }
        )

    def add_question(chapter: dict[str, Any], question: dict[str, Any]) -> None:
        key = (
            question.get("page"),
            question.get("number"),
            tuple(question.get("parts") or []),
            question.get("label"),
        )
        if key in chapter["_question_keys"]:
            existing = next(
                item
                for item in chapter["questions"]
                if (
                    item.get("page"),
                    item.get("number"),
                    tuple(item.get("parts") or []),
                    item.get("label"),
                )
                == key
            )
            existing["calculator"] = existing["calculator"] or question["calculator"]
            if question.get("note") and question["note"] not in (existing.get("note") or ""):
                existing["note"] = " · ".join(
                    value for value in (existing.get("note"), question["note"]) if value
                )
            return
        chapter["_question_keys"].add(key)
        chapter["questions"].append(question)

    def add_note(chapter: dict[str, Any], note: str | None) -> None:
        note = norm(note or "")
        if note and note not in chapter["notes"]:
            chapter["notes"].append(note)

    # Create every scheduled textbook section up front. Empty sections such as
    # 9.9 still belong in the unit view; their homework may be listed under a
    # neighboring section.
    for topic in topics:
        for section in topic["sections"]:
            if section["code"]:
                chapter_for(
                    section["code"],
                    clean_schedule_title(section["schedule_title"]),
                )

    for topic in topics:
        sections = topic["sections"]
        assignments = [
            parse_assignment_cell(line["text"])
            for line in topic["assignment_lines"]
        ]

        if any(re.search(r"after ap exam", cell["text"], re.I) for cell in assignments):
            topic["notes"].append("After AP exam")

        topic_codes: set[str] = set()
        topic_units: set[int] = set()

        for index, assignment in enumerate(assignments):
            fallback_code, no_code_section, used_hint = fallback_section(
                sections, index
            )
            fallback_note = None
            if no_code_section:
                fallback_note = clean_schedule_title(
                    no_code_section["schedule_title"]
                )
                hint = no_code_hint(no_code_section["schedule_title"])
                if hint and hint[1]:
                    fallback_note = f"{fallback_note} — {hint[1]}"

            targets: list[dict[str, Any]] = []

            if assignment["groups"]:
                for group in assignment["groups"]:
                    code = printed_page_code(group["page"]) or fallback_code
                    if not code:
                        continue
                    schedule_title = section_title_for_code(sections, code)
                    if not schedule_title and used_hint:
                        schedule_title = clean_schedule_title(
                            no_code_section["schedule_title"]
                        )
                    chapter = chapter_for(code, schedule_title)
                    if chapter is None:
                        continue
                    add_assignment(
                        chapter,
                        group["text"],
                        False,
                        [group["page"]],
                    )
                    for question in group["questions"]:
                        add_question(chapter, question)
                    targets.append(chapter)
                    topic_codes.add(code)
                    topic_units.add(chapter["_unit_number"])
            else:
                code = fallback_code
                if not code:
                    continue
                schedule_title = section_title_for_code(sections, code)
                if not schedule_title and used_hint:
                    schedule_title = clean_schedule_title(
                        no_code_section["schedule_title"]
                    )
                chapter = chapter_for(code, schedule_title)
                if chapter is None:
                    continue
                add_assignment(chapter, assignment["text"], True, [])
                targets.append(chapter)
                topic_codes.add(code)
                topic_units.add(chapter["_unit_number"])

            if fallback_note:
                for chapter in targets:
                    if fallback_note not in chapter["_schedule_titles"]:
                        add_note(chapter, fallback_note)
            for note in topic["notes"]:
                for chapter in targets:
                    add_note(chapter, note)

        # A worksheet can cover more than one textbook section even when the
        # schedule only names the topic. Keep the companion section visible.
        for section in sections:
            if section["code"]:
                continue
            hint = no_code_hint(section["schedule_title"])
            if not hint or hint[0] not in topic_codes:
                continue
            for companion_code in no_code_companions(section["schedule_title"]):
                companion = chapter_for(companion_code)
                if companion:
                    add_note(
                        companion,
                        f"Covered with {hint[0]}; the worksheet is listed under {hint[0]}.",
                    )

        # If a schedule row names a section but all of its homework was routed
        # to another section on the same row, say where the homework is.
        if topic_codes:
            elsewhere = " / ".join(sorted(topic_codes))
            for section in sections:
                code = section["code"]
                if not code or code in topic_codes:
                    continue
                chapter = chapter_for(
                    code, clean_schedule_title(section["schedule_title"])
                )
                if chapter:
                    add_note(
                        chapter,
                        f"Covered in class; homework is listed under {elsewhere}.",
                    )

        topic["_units"] = topic_units

    # Attach assessments to the unit(s) they cover.
    for topic in topics:
        for test in topic["tests"]:
            label = norm(test)
            unit_numbers = test_unit_numbers(label) or sorted(topic["_units"])
            for number in unit_numbers:
                unit = units_by_number.get(number)
                if unit and label not in unit["tests"]:
                    unit["tests"].append(label)

    units = sorted(units_by_number.values(), key=lambda unit: unit["number"])
    for unit in units:
        unit["chapters"].sort(
            key=lambda chapter: (
                chapter["number"] is None,
                chapter["number"] or 0,
                chapter["code"],
            )
        )

        for chapter in unit["chapters"]:
            chapter["schedule_title"] = (
                " / ".join(chapter["_schedule_titles"]) or None
            )
            chapter["title"] = (
                chapter["book_title"]
                or chapter["schedule_title"]
                or chapter["code"]
            )
            chapter["assignments"].sort(
                key=lambda assignment: (
                    assignment["pages"][0] if assignment["pages"] else 9999,
                    1 if assignment["worksheet"] else 0,
                )
            )
            chapter["questions"].sort(
                key=lambda question: (
                    question.get("page") if question.get("page") is not None else 9999,
                    question.get("number")
                    if question.get("number") is not None
                    else 9999,
                    question.get("label") or "",
                )
            )

        unit["questionCount"] = sum(
            len(chapter["questions"]) for chapter in unit["chapters"]
        )

    used_ids: set[str] = set()

    def unique_id(base: str) -> str:
        candidate = base
        suffix = 2
        while candidate in used_ids:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(candidate)
        return candidate

    for unit in units:
        for chapter in unit["chapters"]:
            for question_index, question in enumerate(chapter["questions"]):
                if question["number"] is not None:
                    question["label"] = (
                        f"{question['number']}{''.join(question['parts'])}"
                    )
                    base = (
                        f"u{unit['number']}-c{chapter['number']}-"
                        f"p{question['page']}-q{question['number']}-"
                        f"{''.join(question['parts']) or 'x'}"
                    )
                else:
                    base = (
                        f"u{unit['number']}-c{chapter['number']}-"
                        f"custom-{question_index}"
                    )
                question["id"] = unique_id(base)

            chapter.pop("_unit_number", None)
            chapter.pop("_schedule_titles", None)
            chapter.pop("_assignment_keys", None)
            chapter.pop("_question_keys", None)

    # Collect every page we need, then analyze/render each once.
    needed_pages: set[int] = set()
    for unit in units:
        for chapter in unit["chapters"]:
            for question in chapter["questions"]:
                if question["page"] is not None:
                    needed_pages.add(question["page"] + PAGE_OFFSET)

    # Resolve anchors (searching one neighbour page if needed).
    for unit in units:
        for chapter in unit["chapters"]:
            for question in chapter["questions"]:
                if question["page"] is None or question["number"] is None:
                    question["pageIndex"] = (
                        question["page"] + PAGE_OFFSET
                        if question["page"] is not None
                        else None
                    )
                    question["anchor"] = None
                    question["marker"] = None
                    question["snippet"] = None
                    continue

                requested_index = question["page"] + PAGE_OFFSET
                found = None
                for candidate in (
                    requested_index,
                    requested_index + 1,
                    requested_index - 1,
                    requested_index + 2,
                    requested_index - 2,
                ):
                    analysis = get_analysis(candidate)
                    if not analysis:
                        continue
                    anchor = choose_anchor(analysis, question["number"])
                    if anchor:
                        found = (anchor, analysis)
                        break

                if found:
                    anchor, analysis = found
                    question["pageIndex"] = analysis.index
                    question["snippet"] = snippet_for(anchor, analysis)
                    highlight, marker = anchor_boxes(anchor, analysis)
                    question["anchor"] = highlight
                    question["marker"] = marker
                    needed_pages.add(analysis.index)
                else:
                    question["pageIndex"] = requested_index
                    question["anchor"] = None
                    question["marker"] = None
                    question["snippet"] = None

    # Render pages.
    if PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)

    pages_data: dict[str, Any] = {}
    for index in sorted(needed_pages):
        analysis = get_analysis(index)
        if not analysis:
            continue
        page = textbook[index]
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM), alpha=False
        )
        filename = f"page-{index:04d}.png"
        pixmap.save(PAGES_DIR / filename)
        analysis.rendered = True
        analysis.image = f"pages/{filename}"
        analysis.width = pixmap.width
        analysis.height = pixmap.height
        printed = analysis.printed_page
        if printed is None:
            printed = index - PAGE_OFFSET
        pages_data[str(index)] = {
            "image": analysis.image,
            "width": pixmap.width,
            "height": pixmap.height,
            "printedPage": printed,
        }
        print(f"rendered page {index} -> {filename}")

    textbook.close()

    # Attach page image metadata and strip internal fields.
    for unit in units:
        for chapter in unit["chapters"]:
            for question in chapter["questions"]:
                index = question.get("pageIndex")
                if index is not None and str(index) in pages_data:
                    question["pageInfo"] = pages_data[str(index)]
                else:
                    question["pageInfo"] = None
                question.pop("assignment", None)

    total_questions = sum(unit["questionCount"] for unit in units)
    total_chapters = sum(len(unit["chapters"]) for unit in units)
    missing = [
        q["id"]
        for unit in units
        for chapter in unit["chapters"]
        for q in chapter["questions"]
        if q["number"] is not None and q["anchor"] is None
    ]

    data = {
        "meta": {
            "course": "BC Calculus",
            "year": "2020–2021",
            "textbook": "Calculus: Early Transcendentals",
            "edition": "10th edition",
            "authors": "Anton, Bivens & Davis",
            "assignmentSource": ASSIGNMENTS_PDF.name,
            "textbookSource": TEXTBOOK_PDF.name,
            "pageOffset": PAGE_OFFSET,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "totalUnits": len(units),
            "totalChapters": total_chapters,
            "totalQuestions": total_questions,
        },
        "units": units,
        "pages": pages_data,
    }

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    print()
    print(f"Units:              {len(units)}")
    print(f"Chapters:           {total_chapters}")
    print(f"Questions:          {total_questions}")
    print(f"Rendered pages:     {len(pages_data)}")
    print(f"Missing anchors:    {len(missing)}")
    if missing:
        print("  " + ", ".join(missing[:15]))
    print(f"Wrote {DATA_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
