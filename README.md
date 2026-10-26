# Calculus Homework Viewer

A small local web app for working through the 2020–2021 BC Calculus homework
assignments. It organizes every assignment as **unit → chapter → question** and
shows the relevant textbook page beside the question list.

The application is Python + Flask with server-rendered HTML and CSS. A small
progressive-enhancement script adds pinch-to-zoom to the textbook page viewer;
the rest of the app works without JavaScript.

## Requirements

- Python 3.10 or newer (the project was built and tested with Python 3.14)
- The two source PDFs in this folder:
  - `Assignments_for_2020-2021.pdf`
  - `AP-Calc-BC_textbook_.pdf`

## Install

From this folder:

```powershell
py -m pip install -r requirements.txt
```

## Run

```powershell
py app.py
```

You can also double-click `run.bat`. The app opens
`http://127.0.0.1:5000/` in your browser. To run it without opening a browser,
or on a different port:

```powershell
py app.py --no-browser --port 5000
```

The first run automatically builds the page index and renders the textbook pages
that are referenced by homework. That takes a little while. Later runs reuse the
generated data.

## Rebuild the data

Run this after editing either PDF, then restart the app so it reloads the new
data:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
py build_data.py
```

`build_data.py` reads the assignment schedule, extracts exercise ranges (for
example `29–51 odd`), preserves subparts such as `6gh`, `34b`, and `67ab`, and
marks calculator problems. It also crops nothing from the textbook: referenced
pages are rendered as PNGs into `static/pages/`.

Generated files:

- `static/data.json` — units, chapters, questions, page metadata, anchors
- `static/pages/page-XXXX.png` — rendered textbook pages

## How the hierarchy works

Textbook sections are labeled `n.m`. The first number is the **unit**, and the
second number is the **chapter** within that unit.

- **Unit** — the first number of the section code (`1`–`10`, `12`)
- **Chapter** — the second number of the section code (`1`, `2`, …), displayed
  with its full section code such as `3.2` or `9.10`
- **Question** — an individual homework problem, including expanded ranges and
  multi-part labels

The assignment schedule's numbered rows (1–49) are kept as the source of the
homework, but they are not the unit numbers. When a schedule row covers several
textbook sections, each problem is filed under the section where the textbook
prints it. A row can therefore contribute homework to more than one chapter.

Some chapters are worksheets and therefore have no textbook question set.

## Viewing pages

Opening a question focuses the page on its highlighted problem. To see more of
the page, pinch out on a trackpad or use the on-screen zoom controls. Click the
percentage (or double-click the page) to fit the page width again. Pages are
still shown at fit-to-viewer size when JavaScript is unavailable.

## Progress

Marking a question complete writes to `data/progress.json`. Delete that file to
reset all progress.

## Project files

| File | Purpose |
| --- | --- |
| `app.py` | Flask routes, search, progress, and page rendering |
| `build_data.py` | PDF parsing, exercise extraction, anchor detection, page rendering |
| `templates/base.html` | Shared page shell, top bar, and unit sidebar |
| `templates/chapter.html` | Chapter question list and textbook page viewer |
| `templates/search.html` | Server-rendered search results |
| `static/style.css` | Light, responsive layout |
| `static/viewer.js` | Pinch-to-zoom enhancement for the page viewer |
| `static/data.json` | Generated homework data |
| `static/pages/` | Generated textbook page images |

## Notes

- The printed textbook page is mapped to a zero-based PDF page with
  `PDF page index = printed page + 23`.
- Highlights are stored as normalized page coordinates, so they stay aligned
  when the page image is resized.
- A few exercise numbers do not have a detected anchor in this edition of the
  textbook. Their pages still open normally; only the yellow highlight is absent.
