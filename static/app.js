/*
 * Client-side behaviour for the standalone (file://) homework viewer.
 *
 * The static build renders the pages with Python, then this file takes over
 * progress and search. It deliberately uses only classic scripts, relative
 * URLs, and no fetch/XHR so the generated `site/index.html` works when opened
 * directly from disk.
 */
(() => {
  "use strict";

  const STORAGE_KEY =
    window.PROGRESS_STORAGE_KEY || "bccalc-homework-progress-v1";
  const NAME_PREFIX = "bccalc-homework-progress:";
  const INITIAL_PROGRESS = Array.isArray(window.INITIAL_PROGRESS)
    ? window.INITIAL_PROGRESS
    : [];
  const QUESTION_IDS = Array.isArray(window.QUESTION_IDS)
    ? window.QUESTION_IDS
    : [];
  const SCOPE_QUESTIONS =
    window.SCOPE_QUESTIONS && typeof window.SCOPE_QUESTIONS === "object"
      ? window.SCOPE_QUESTIONS
      : {};
  const VALID_IDS = new Set(QUESTION_IDS);
  const TOTAL_QUESTIONS = QUESTION_IDS.length;

  function parseIdList(raw) {
    if (typeof raw !== "string" || !raw) return null;
    try {
      const value = JSON.parse(raw);
      if (!Array.isArray(value)) return null;
      return new Set(
        value.filter(
          (id) => typeof id === "string" && (!VALID_IDS.size || VALID_IDS.has(id))
        )
      );
    } catch (error) {
      return null;
    }
  }

  function readProgress() {
    /* window.name survives same-tab file:// navigation even when a browser
       refuses localStorage for file URLs. It is only a fallback, not a second
       source of truth. */
    if (window.name && window.name.startsWith(NAME_PREFIX)) {
      const fromName = parseIdList(window.name.slice(NAME_PREFIX.length));
      if (fromName) return fromName;
    }

    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw !== null) {
        const fromStorage = parseIdList(raw);
        if (fromStorage) return fromStorage;
      }
    } catch (error) {
      /* file:// storage can be disabled; window.name above is the fallback. */
    }

    return new Set(
      INITIAL_PROGRESS.filter(
        (id) => typeof id === "string" && (!VALID_IDS.size || VALID_IDS.has(id))
      )
    );
  }

  let done = readProgress();

  function persistProgress() {
    const raw = JSON.stringify([...done].sort());
    try {
      window.localStorage.setItem(STORAGE_KEY, raw);
    } catch (error) {
      /* Keep going with the window.name fallback. */
    }
    window.name = NAME_PREFIX + raw;
  }

  function doneCount() {
    if (!VALID_IDS.size) return done.size;
    let count = 0;
    for (const id of done) {
      if (VALID_IDS.has(id)) count += 1;
    }
    return count;
  }

  function scopeIds(scope, target) {
    const group = SCOPE_QUESTIONS[scope];
    if (!group || target == null) return [];
    const ids = group[String(target)];
    if (!Array.isArray(ids)) return [];
    return ids.filter(
      (id) => typeof id === "string" && (!VALID_IDS.size || VALID_IDS.has(id))
    );
  }

  function scopeComplete(ids) {
    return ids.length > 0 && ids.every((id) => done.has(id));
  }

  function updateBulkButtons() {
    for (const form of document.querySelectorAll("form[data-bulk-scope]")) {
      const ids = scopeIds(form.dataset.bulkScope, form.dataset.bulkTarget);
      const complete = scopeComplete(ids);
      const button = form.querySelector(".bulk-btn");
      if (!button) continue;

      const name = form.dataset.bulkName || "all questions";
      const action = complete ? "Clear" : "Complete";
      const label = button.querySelector("[data-bulk-label]");
      const title = complete
        ? `Mark all questions in ${name} as not complete`
        : `Mark all questions in ${name} complete`;

      if (label) label.textContent = `${action} ${name}`;
      button.classList.toggle("is-done", complete);
      button.title = title;
      button.setAttribute("aria-label", title);
    }
  }

  function toggleScope(scope, target) {
    const ids = scopeIds(scope, target);
    if (!ids.length) return;

    const complete = scopeComplete(ids);
    for (const id of ids) {
      if (complete) done.delete(id);
      else done.add(id);
    }
    persistProgress();
    updateProgress();
  }

  function updateProgress() {
    const total = TOTAL_QUESTIONS;
    const complete = doneCount();
    const percent = total ? (complete / total) * 100 : 0;

    const chip = document.getElementById("progressChip");
    if (chip) {
      chip.title = `${complete} of ${total} questions complete`;
      const label = chip.querySelector("[data-progress-label]");
      if (label) label.textContent = `${complete} / ${total}`;
      const bar = chip.querySelector(".progress-track > span");
      if (bar) bar.style.width = `${percent}%`;
    }

    const sidebarDone = document.querySelector("[data-sidebar-done]");
    if (sidebarDone) sidebarDone.textContent = String(complete);

    const cards = document.querySelectorAll(".q-card[data-question-id]");
    for (const card of cards) {
      const id = card.dataset.questionId;
      const isDone = done.has(id);
      card.classList.toggle("is-done", isDone);

      const toggle = card.querySelector(".q-done-toggle");
      if (toggle) {
        const label = isDone ? "Mark as not complete" : "Mark complete";
        toggle.setAttribute("aria-pressed", isDone ? "true" : "false");
        toggle.setAttribute("aria-label", label);
        toggle.title = label;
      }
    }

    for (const form of document.querySelectorAll("form[data-question-id]")) {
      const button = form.querySelector(".done-btn");
      if (!button) continue;

      const isDone = done.has(form.dataset.questionId);
      button.classList.toggle("is-done", isDone);
      button.setAttribute("aria-pressed", isDone ? "true" : "false");
      button.title = isDone ? "Mark as not complete" : "Mark complete";
      const label = button.querySelector(".done-btn-label");
      if (label) label.textContent = isDone ? "Completed" : "Mark complete";
    }

    const chapterText = document.querySelector("[data-chapter-progress-text]");
    if (chapterText) {
      let chapterDone = 0;
      for (const card of cards) {
        if (done.has(card.dataset.questionId)) chapterDone += 1;
      }
      chapterText.textContent = `${chapterDone}/${cards.length} done`;
    }

    updateBulkButtons();
  }

  function toggleQuestion(questionId) {
    if (!questionId) return;
    if (done.has(questionId)) done.delete(questionId);
    else done.add(questionId);
    persistProgress();
    updateProgress();
  }

  /* ------------------------------------------------------------------ */
  /* Search                                                              */
  /* ------------------------------------------------------------------ */

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => {
      switch (character) {
        case "&":
          return "&amp;";
        case "<":
          return "&lt;";
        case ">":
          return "&gt;";
        case '"':
          return "&quot;";
        case "'":
          return "&#39;";
        default:
          return character;
      }
    });
  }

  function normalize(value) {
    return String(value ?? "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[\u2013\u2014]/g, "-")
      .replace(/[\u2018\u2019]/g, "'")
      .replace(/[\u201c\u201d]/g, '"')
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function textScore(text, query) {
    if (!query) return 0;
    let score = 0;
    if (text === query) score += 160;
    if (text.startsWith(query)) score += 90;
    if (text.split(" ").includes(query)) score += 70;
    if (text.includes(query)) score += 35;
    return score;
  }

  function unitCodes(unit) {
    return (unit.chapters || [])
      .map((chapter) =>
        chapter.code && chapter.code !== "—"
          ? chapter.code
          : chapter.title || "Sheet"
      )
      .join(" · ");
  }

  function chapterFile(chapter) {
    return chapter && chapter.id ? `chapter-${chapter.id}.html` : "index.html";
  }

  function questionFile(question) {
    return `question-${question.id}.html`;
  }

  function byTitle(first, second) {
    if (first.title < second.title) return -1;
    if (first.title > second.title) return 1;
    return 0;
  }

  function searchResults(query) {
    const data = window.HOMEWORK_DATA;
    const groups = { units: [], chapters: [], questions: [] };
    if (!data || !Array.isArray(data.units)) return { groups, total: 0 };

    const needle = normalize(query);
    if (!needle) return { groups, total: 0 };

    const units = [];
    const chapters = [];
    const questions = [];

    for (const unit of data.units) {
      const shortCodes = unitCodes(unit);
      const chapterText = (unit.chapters || [])
        .map(
          (chapter) =>
            `${chapter.code || ""} ${chapter.title || ""} ${chapter.book_title || ""}`
        )
        .join(" ");
      const unitHaystack = normalize(
        `unit ${unit.number} u${unit.number} ${shortCodes} ${chapterText} ` +
          `${(unit.tests || []).join(" ")} ${unit.note || ""}`
      );
      const unitScore = textScore(unitHaystack, needle);
      if (unitScore) {
        const first = (unit.chapters || [])[0];
        units.push({
          score: unitScore,
          item: {
            kind: "unit",
            mark: `U${unit.number}`,
            title: `Unit ${unit.number}`,
            sub: shortCodes || "Assignment sheet",
            url: chapterFile(first),
          },
        });
      }

      for (const chapter of unit.chapters || []) {
        const chapterNumber = chapter.number || chapter.code || "—";
        const chapterHaystack = normalize(
          `${chapter.code || ""} ${chapter.title || ""} ` +
            `${chapter.book_title || ""} ${chapter.schedule_title || ""} ` +
            `${(chapter.notes || []).join(" ")} ` +
            `chapter ${chapterNumber} chapter ${chapter.code || ""} ` +
            `unit ${unit.number} u${unit.number}`
        );
        const chapterScore = textScore(chapterHaystack, needle);
        if (chapterScore) {
          const count = (chapter.questions || []).length;
          chapters.push({
            score: chapterScore,
            item: {
              kind: "chapter",
              mark: chapter.code || "—",
              title: `${chapter.code || "—"} ${chapter.title || ""}`.trim(),
              sub: `Unit ${unit.number} · Chapter ${chapterNumber} · ${count} question${
                count === 1 ? "" : "s"
              }`,
              url: chapterFile(chapter),
            },
          });
        }

        for (const question of chapter.questions || []) {
          const label = question.label || question.number || "Problem";
          const page =
            question.pageInfo && question.pageInfo.printedPage != null
              ? question.pageInfo.printedPage
              : question.page;
          const questionHaystack = normalize(
            `q${label} ${label} ${question.number || ""} question ` +
              `${question.range || ""} ${question.note || ""} ` +
              `${chapter.code || ""} ${chapter.title || ""} ` +
              `chapter ${chapterNumber} unit ${unit.number} ` +
              `${question.snippet || ""} p.${page || ""} page ${page || ""}`
          );
          const questionScore = textScore(questionHaystack, needle);
          if (questionScore) {
            questions.push({
              score: questionScore,
              item: {
                kind: "question",
                mark: String(label),
                title: `Question ${label}`,
                sub:
                  `Unit ${unit.number} · Chapter ${chapterNumber} · ` +
                  `${chapter.code || ""} ${chapter.title || ""}`.trim() +
                  (page ? ` · p. ${page}` : "") +
                  (question.range ? ` · from ${question.range}` : ""),
                url:
                  questionFile(question) +
                  (question.anchor ? "#page-highlight" : ""),
              },
            });
          }
        }
      }
    }

    units.sort((a, b) => b.score - a.score || byTitle(a.item, b.item));
    chapters.sort((a, b) => b.score - a.score || byTitle(a.item, b.item));
    questions.sort(
      (a, b) =>
        b.score - a.score ||
        (a.item.sub < b.item.sub ? -1 : a.item.sub > b.item.sub ? 1 : 0) ||
        byTitle(a.item, b.item)
    );

    return {
      groups: {
        units: units.slice(0, 16).map((entry) => entry.item),
        chapters: chapters.slice(0, 30).map((entry) => entry.item),
        questions: questions.slice(0, 60).map((entry) => entry.item),
      },
      total: units.length + chapters.length + questions.length,
    };
  }

  function searchPath() {
    const hash = window.location.hash ? window.location.hash.slice(1) : "";
    if (hash) {
      const params = new URLSearchParams(hash);
      if (params.has("q")) return params.get("q") || "";
    }
    return new URLSearchParams(window.location.search).get("q") || "";
  }

  function renderSearch(searchView) {
    const data = window.HOMEWORK_DATA || {};
    const course = (data.meta && data.meta.course) || "Calculus";
    const query = searchPath().trim();
    const input = document.getElementById("searchInput");
    if (input) input.value = query;

    document.title = query
      ? `Search · ${query} · ${course}`
      : `Search · ${course}`;

    /* `view` is the inner chapter-view; the surrounding section and sidebar
       are already static. */
    const view = searchView;
    if (!view) return;

    let html =
      `<div class="crumbs"><span>Search</span>` +
      `<svg><use href="#i-chevron"></use></svg>` +
      `<span>${escapeHtml(course)}</span></div>`;

    if (!query) {
      html +=
        `<div class="chapter-kicker"><span class="unit-kicker">Find homework</span></div>` +
        `<h1 class="chapter-title">Search the assignment plan</h1>` +
        `<p class="book-title">Try a unit number, chapter code, page number, question number, or a word from a problem.</p>` +
        `<div class="empty-state search-empty-state">` +
        `<span class="empty-mark">∫</span>` +
        `<h2>What are you looking for?</h2>` +
        `<p>Use the search field above or browse the units in the sidebar.</p>` +
        `</div>`;
      view.innerHTML = html;
      return;
    }

    const results = searchResults(query);
    const groups = results.groups;
    const shown =
      groups.units.length + groups.chapters.length + groups.questions.length;

    html +=
      `<div class="chapter-kicker"><span class="unit-kicker">Results for</span>` +
      `<span class="code-pill">“${escapeHtml(query)}”</span></div>` +
      `<h1 class="chapter-title">${results.total} match${
        results.total === 1 ? "" : "es"
      }</h1>` +
      `<p class="book-title">Search across unit descriptions, chapter titles, question numbers, and textbook snippets.` +
      `${shown < results.total ? ` Showing the first ${shown}.` : ""}</p>`;

    if (results.total === 0) {
      html +=
        `<div class="empty-state search-empty-state">` +
        `<span class="empty-mark">∅</span>` +
        `<h2>No matches</h2>` +
        `<p>Try a shorter query, or browse the units in the sidebar.</p>` +
        `</div>`;
      view.innerHTML = html;
      return;
    }

    const labels = [
      ["units", "Units"],
      ["chapters", "Chapters"],
      ["questions", "Questions"],
    ];
    for (const [group, label] of labels) {
      const items = groups[group];
      if (!items.length) continue;
      html +=
        `<div class="result-group"><h2 class="result-group-title">${label} ` +
        `<span>${items.length}</span></h2><div class="result-list">`;
      for (const item of items) {
        html +=
          `<a class="search-result" href="${escapeHtml(item.url)}">` +
          `<span class="result-mark">${escapeHtml(item.mark)}</span>` +
          `<span class="result-text"><strong>${escapeHtml(
            item.title
          )}</strong><small>${escapeHtml(item.sub)}</small></span>` +
          `<span class="result-kind">${escapeHtml(item.kind)}</span>` +
          `</a>`;
      }
      html += `</div></div>`;
    }

    view.innerHTML = html;
  }

  function initSearch(searchView) {
    renderSearch(searchView);
    window.addEventListener("hashchange", () => renderSearch(searchView));
  }

  function initSearchForm() {
    for (const form of document.querySelectorAll("form.search")) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const input = form.querySelector('input[name="q"]');
        const query = input ? input.value.trim() : "";
        if (document.querySelector(".search-view")) {
          if (query) window.location.hash = `q=${encodeURIComponent(query)}`;
          else if (window.location.hash) window.location.hash = "";
          else renderSearch(document.querySelector(".search-view"));
        } else {
          window.location.href = query
            ? `search.html#q=${encodeURIComponent(query)}`
            : "search.html";
        }
      });
    }
  }

  function start() {
    updateProgress();

    document.addEventListener("submit", (event) => {
      const bulkForm = event.target.closest("form[data-bulk-scope]");
      if (bulkForm) {
        event.preventDefault();
        toggleScope(bulkForm.dataset.bulkScope, bulkForm.dataset.bulkTarget);
        return;
      }

      const form = event.target.closest("form[data-question-id]");
      if (!form) return;
      event.preventDefault();
      toggleQuestion(form.dataset.questionId);
    });

    window.addEventListener("storage", (event) => {
      if (event.key !== STORAGE_KEY || !event.newValue) return;
      const next = parseIdList(event.newValue);
      if (!next) return;
      done = next;
      window.name = NAME_PREFIX + event.newValue;
      updateProgress();
    });

    initSearchForm();
    const searchView = document.querySelector(".search-view");
    if (searchView) initSearch(searchView);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
