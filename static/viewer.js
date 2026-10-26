/*
 * Pinch-to-zoom for the textbook page viewer.
 *
 * The application is fully usable without this file: the textbook page simply
 * appears at its normal fit-to-viewer size. This is a progressive enhancement
 * that starts focused on the current question and lets a trackpad pinch zoom
 * back out to the full page width.
 *
 * Trackpad pinches reach the browser in two different ways:
 *   - Chromium/Firefox: a wheel event with ctrlKey set.
 *   - Safari: gesturestart/gesturechange/gestureend events.
 */
(() => {
  "use strict";

  const scroller = document.getElementById("pageScroll");
  if (!scroller) return;

  const shell = scroller.querySelector(".page-shell");
  const image = shell ? shell.querySelector("img") : null;
  if (!shell || !image) return;

  const regionElements = Array.from(
    shell.querySelectorAll(".page-highlight, .page-marker")
  );
  const controls = document.getElementById("zoomControls");
  const hint = document.getElementById("zoomHint");
  const levelButton = document.getElementById("zoomLevel");
  const zoomInButton = document.getElementById("zoomIn");
  const zoomOutButton = document.getElementById("zoomOut");

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 3;
  const START_MAX_ZOOM = 2.6;
  const MOBILE_QUERY = window.matchMedia("(max-width: 900px)");

  let zoom = 1;
  let focusZoom = 1;
  let userZoomed = false;
  let gestureStartZoom = null;

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  /* The highlight and marker are two boxes over the same exercise. Their union
     also covers question numbers whose highlight ends at a nearby false anchor. */
  function regionRect() {
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const element of regionElements) {
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 && rect.height <= 0) continue;
      left = Math.min(left, rect.left);
      top = Math.min(top, rect.top);
      right = Math.max(right, rect.right);
      bottom = Math.max(bottom, rect.bottom);
    }
    if (!Number.isFinite(left)) return null;
    return {
      left,
      top,
      right,
      bottom,
      width: right - left,
      height: bottom - top,
    };
  }

  function applyZoom() {
    shell.style.transformOrigin = "0 0";
    shell.style.transform = zoom === 1 ? "" : `scale(${zoom})`;

    if (levelButton) {
      const percent = `${Math.round(zoom * 100)}%`;
      levelButton.textContent = percent;
      levelButton.title =
        zoom > MIN_ZOOM + 0.01 ? "Fit page width" : "Page width is fitted";
      levelButton.setAttribute(
        "aria-label",
        `Zoom ${percent}. ${zoom > MIN_ZOOM + 0.01 ? "Fit page width" : "Page width is fitted"}`
      );
    }
    if (zoomOutButton) zoomOutButton.disabled = zoom <= MIN_ZOOM + 0.001;
    if (zoomInButton) zoomInButton.disabled = zoom >= MAX_ZOOM - 0.001;
  }

  /* Change the zoom level while keeping the content under (x, y) in place.
     When x/y are omitted, the centre of the viewer stays fixed. */
  function setZoom(nextZoom, x, y) {
    nextZoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(nextZoom - zoom) < 0.001) {
      applyZoom();
      return;
    }

    const scrollRect = scroller.getBoundingClientRect();
    if (x === undefined || x === null) {
      x = scrollRect.left + scroller.clientLeft + scroller.clientWidth / 2;
    }
    if (y === undefined || y === null) {
      y = scrollRect.top + scroller.clientTop + scroller.clientHeight / 2;
    }

    const before = shell.getBoundingClientRect();
    const originX = (x - before.left) / zoom;
    const originY = (y - before.top) / zoom;

    zoom = nextZoom;
    applyZoom();

    const after = shell.getBoundingClientRect();
    scroller.scrollLeft += after.left + originX * zoom - x;
    scroller.scrollTop += after.top + originY * zoom - y;
  }

  /* Put the highlighted question in the middle of the viewer. */
  function centerRegion() {
    const rect = regionRect();
    if (!rect) return;
    const scrollRect = scroller.getBoundingClientRect();
    const centreX =
      scrollRect.left + scroller.clientLeft + scroller.clientWidth / 2;
    const centreY =
      scrollRect.top + scroller.clientTop + scroller.clientHeight / 2;
    scroller.scrollLeft += (rect.left + rect.right) / 2 - centreX;
    scroller.scrollTop += (rect.top + rect.bottom) / 2 - centreY;
  }

  /* Work out how much magnification makes the highlighted question readable
     while leaving its surrounding context visible. */
  function calculateFocusZoom() {
    const rect = regionRect();
    if (!rect || MOBILE_QUERY.matches) return 1;

    const regionWidth = rect.width / zoom;
    const regionHeight = rect.height / zoom;
    if (regionWidth < 1 || regionHeight < 1) return 1;

    const availableWidth = Math.max(scroller.clientWidth - 36, 120);
    const availableHeight = Math.max(scroller.clientHeight - 36, 120);
    const widthZoom = (availableWidth * 0.92) / regionWidth;
    const heightZoom = (availableHeight * 0.58) / regionHeight;
    const nextZoom = Math.min(widthZoom, heightZoom, START_MAX_ZOOM);

    return nextZoom >= 1.15 ? nextZoom : 1;
  }

  function fitPage() {
    userZoomed = true;
    setZoom(MIN_ZOOM);
    scroller.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }

  function focusQuestion(x, y) {
    userZoomed = true;
    focusZoom = calculateFocusZoom();
    setZoom(focusZoom, x, y);
  }

  function init() {
    if (controls) controls.hidden = MOBILE_QUERY.matches;
    if (hint) hint.hidden = MOBILE_QUERY.matches;

    applyZoom();
    focusZoom = calculateFocusZoom();
    if (focusZoom > 1.01) {
      setZoom(focusZoom);
      centerRegion();
    }

    /* The URL fragment (#page-highlight) can make the browser scroll the
       highlighted box into view after this script runs. Re-centre once the
       page has settled, unless the reader has already taken over the zoom. */
    const settle = () => {
      if (!userZoomed) centerRegion();
    };
    requestAnimationFrame(() => {
      settle();
      requestAnimationFrame(settle);
    });
    window.addEventListener("load", settle, { once: true });

    if (!image.complete) {
      image.addEventListener(
        "load",
        () => {
          if (userZoomed) return;
          focusZoom = calculateFocusZoom();
          setZoom(focusZoom);
          centerRegion();
        },
        { once: true }
      );
    }
  }

  /* Chromium and Firefox report a trackpad pinch as ctrl + wheel. */
  scroller.addEventListener(
    "wheel",
    (event) => {
      if (!event.ctrlKey || gestureStartZoom !== null) return;
      event.preventDefault();
      userZoomed = true;

      const unit =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? scroller.clientHeight
            : 1;
      const factor = clamp(
        Math.exp(-event.deltaY * unit * 0.01),
        0.6,
        1.6
      );
      setZoom(zoom * factor, event.clientX, event.clientY);
    },
    { passive: false }
  );

  /* Safari reports a trackpad pinch with its own gesture events. */
  scroller.addEventListener(
    "gesturestart",
    (event) => {
      event.preventDefault();
      userZoomed = true;
      gestureStartZoom = zoom;
    },
    { passive: false }
  );

  scroller.addEventListener(
    "gesturechange",
    (event) => {
      if (gestureStartZoom === null) return;
      event.preventDefault();
      setZoom(
        gestureStartZoom * event.scale,
        event.clientX,
        event.clientY
      );
    },
    { passive: false }
  );

  scroller.addEventListener(
    "gestureend",
    (event) => {
      event.preventDefault();
      gestureStartZoom = null;
    },
    { passive: false }
  );

  scroller.addEventListener("dblclick", (event) => {
    if (event.target.closest("a, button, label, input")) return;
    event.preventDefault();
    if (zoom > MIN_ZOOM + 0.02) {
      fitPage();
    } else {
      focusQuestion(event.clientX, event.clientY);
    }
  });

  if (zoomInButton) {
    zoomInButton.addEventListener("click", () => {
      userZoomed = true;
      setZoom(zoom * 1.25);
    });
  }

  if (zoomOutButton) {
    zoomOutButton.addEventListener("click", () => {
      userZoomed = true;
      setZoom(zoom / 1.25);
    });
  }

  if (levelButton) {
    levelButton.addEventListener("click", fitPage);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
