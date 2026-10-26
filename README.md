# Calculus Homework Viewer

A local viewer for the 2020–2021 BC Calculus homework. It organizes every
assignment as **unit → chapter → question** and shows the relevant textbook
page beside the question list.

The viewer is built as a static site: **open `index.html` (or
`site/index.html`) directly in your browser**. There is no server to start, no
localhost address, and no Python process left running. The Flask app is still
included for development, but it is optional.

The primary workflow is:

1. `py build_site.py` (first time, or after changing the PDFs/templates)
2. Open `index.html` — or just double-click `run.bat`

The root `index.html` is a tiny forwarding page. The actual generated viewer is
`site/index.html`.

`run.bat` opens the generated site, building it first if `site/index.html` does
not exist yet.

## Requirements

- Python 3.10 or newer (tested with Python 3.14)
- The two source PDFs in this folder:
  - `Assignments_for_2020-2021.pdf`
  - `AP-Calc-BC_textbook_.pdf`
- A browser. Chrome, Edge, and Firefox can store the static viewer's progress
  when the page is opened from disk. On browsers that block storage for
  `file://` pages, progress still survives navigation in the same tab.

Install the Python build dependencies:

```powershell
py -m pip install -r requirements.txt
```

## Build the static viewer

```powershell
py build_site.py
```

This command:

- loads the generated homework index, rebuilding it from the PDFs if needed;
- renders every chapter and question page with the existing templates;
- rewrites Flask URLs to relative file URLs;
- writes browser-side progress and search scripts; and
- produces `site/index.html`, `site/chapter-*.html`, `site/question-*.html`,
  and `site/search.html`.

`site/` is generated output and is ignored by git. Keep it inside the project
folder because its stylesheet, scripts, and textbook page images live in the
project's `static/` folder.

The first build can take a little while because it may render the textbook pages
referenced by the homework. Later builds reuse the generated page images unless
the PDFs are newer.

## Open it

Double-click `index.html` in the project folder, double-click `site/index.html`,
or run:

```powershell
start "" "site\index.html"
```

You can also double-click `run.bat`.

### Progress in the static viewer

The static viewer stores completion state in your browser. The state of
`data/progress.json` at build time is used as the starting value the first time
the static viewer is opened in a browser. After that, the browser's copy takes
over. Changes made in the static viewer **do not** update `data/progress.json`,
and changes made in the optional Flask server do not automatically appear in
the static viewer.

Progress is per browser. Clearing the browser's site data for the file resets
the static viewer; the next build's `progress.json` seed is used again.

### Search in the static viewer

Search runs entirely in the browser across unit descriptions, chapter titles,
question numbers, pages, and textbook snippets. Type in the top search field
and press Enter.

## Optional Flask server

The original server-rendered app is still available:

```powershell
py app.py
```

It opens `http://127.0.0.1:5000/`. In this mode progress is written to
`data/progress.json`. This is useful when editing templates or developing, but
it is not needed to read homework.

## Rebuild the data

Run this after editing either PDF:

```powershell
py build_data.py
py build_site.py
```

`build_site.py` also checks whether the PDFs are newer than the generated data,
so in most cases `py build_site.py` alone is enough.

`build_data.py` reads the assignment schedule, extracts exercise ranges (for
example `29–51 odd`), preserves subparts such as `6gh`, `34b`, and `67ab`, and
marks calculator problems. It renders referenced textbook pages as PNGs into
`static/pages/`.

Generated files:

- `static/data.json` — units, chapters, questions, page metadata, anchors
- `static/pages/page-XXXX.png` — rendered textbook pages
- `site/` — the standalone, file-openable viewer

## Viewing pages

Opening a question focuses the page on its highlighted problem. To see more of
the page, pinch out on a trackpad or use the on-screen zoom controls. Click the
percentage (or double-click the page) to fit the page width again. The viewer
falls back to fit-to-width when JavaScript is unavailable.

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

## Project files

| File | Purpose |
| --- | --- |
| `build_site.py` | Renders the standalone site into `site/` |
| `build_data.py` | PDF parsing, exercise extraction, anchor detection, page rendering |
| `static/app.js` | Static-site progress and search |
| `static/viewer.js` | Pinch-to-zoom enhancement for the page viewer |
| `static/style.css` | Light, responsive layout |
| `templates/base.html` | Shared page shell, top bar, and unit sidebar |
| `templates/chapter.html` | Chapter question list and textbook page viewer |
| `templates/search.html` | Search layout; results are rendered by `app.js` in the static site |
| `static/data.json` | Generated homework data |
| `static/pages/` | Generated textbook page images |
| `site/` | Generated static site; open `site/index.html` |
| `index.html` | Root forwarding page for the generated site |
| `app.py` | Optional Flask development server and static-build renderer |
| `run.bat` | Opens the static site (and builds it if needed) |

## Notes

- The printed textbook page is mapped to a zero-based PDF page with
  `PDF page index = printed page + 23`.
- Highlights are stored as normalized page coordinates, so they stay aligned
  when the page image is resized.
- A few exercise numbers do not have a detected anchor in this edition of the
  textbook. Their pages still open normally; only the yellow highlight is absent.
- The static viewer uses classic scripts and relative paths, not ES modules or
  `fetch`, because browsers restrict those when a page is opened from disk.
