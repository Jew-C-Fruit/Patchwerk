#!/usr/bin/env python3
"""Capture manual figures from a running Patchwerk instance.

SCAFFOLD — proved end to end on 2026-07-26 with FIG-03-01, one figure. The
whole-page path below is real and works; the per-card path is sketched from
the same primitives but has NOT been run for all 30 card portraits yet.

Conventions come from docs/manual/DESIGN.md and docs/manual/img/README.md:
1440x900 viewport, device_scale_factor 2, blocks mode, browser chrome cropped
(headless has none), cursor hidden (headless has none).

Output goes to docs/manual/img/, which is GITIGNORED for rasters — these
files are regenerated, never committed.

    # bring the rig up first (needs SuperCollider on PATH):
    export PATH="/Applications/SuperCollider.app/Contents/Resources:$PATH"
    ./run.sh pad_space --no-browser

    .venv/bin/python docs/manual/capture.py page fig-03-01
    .venv/bin/python docs/manual/capture.py card fig-14-01 lowpass
    .venv/bin/python docs/manual/capture.py list        # what's on screen

Requires the venv's playwright (already present; the repo's headless GUI
checks use it).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765"
OUT = Path(__file__).resolve().parent / "img"

VIEWPORT = {"width": 1440, "height": 900}   # = --ar-page 16:10
SCALE = 2                                    # retina

# How long to let the page settle after the first card appears. The GUI routes
# wires and paints its canvases asynchronously; 2.5 s was enough in every trial
# but it is a HEURISTIC, not a signal. See img/README.md on determinism.
SETTLE_MS = 2500


def _boot(pw, quiet_motion: bool):
    """Open the page and wait until it has actually drawn."""
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL, wait_until="networkidle")
    page.wait_for_selector(".mod", timeout=15000)
    if quiet_motion:
        # Freeze CSS transitions so a card mid-shove does not smear. Canvas
        # animation (meters, scope, LFO viz) is NOT frozen by this — it is
        # driven by rAF and live data. See the caveat in img/README.md.
        page.add_style_tag(content="*,*::before,*::after{"
                                   "transition:none!important;animation:none!important}")
    page.wait_for_timeout(SETTLE_MS)
    return browser, page, errors


def _report(page, errors, path: Path, t0: float) -> None:
    cards = page.evaluate("document.querySelectorAll('.mod').length")
    wires = page.evaluate("document.querySelectorAll('#wires path').length")
    print(f"{path.name}: cards={cards} wires={wires} "
          f"errors={errors or 'none'} {time.time() - t0:.1f}s")


def capture_page(name: str, quiet_motion: bool = True) -> None:
    """Whole-window figure. PROVEN — this is how FIG-03-01 was made."""
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    with sync_playwright() as pw:
        browser, page, errors = _boot(pw, quiet_motion)
        page.screenshot(path=str(path))
        _report(page, errors, path, t0)
        browser.close()


def capture_card(name: str, gid: str, pad: int = 28) -> None:
    """Single-card portrait, cropped to the card plus even padding.

    UNPROVEN at scale — the primitives are the same as capture_page, but the
    30 card portraits have not been run. The likely snag is `gid`: cards are
    keyed by INSTANCE id (`lowpass`, `lowpass.2`), not type, so a patch with
    two of a module needs the suffix. Run `capture.py list` to see what is
    actually on screen before batching.
    """
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    with sync_playwright() as pw:
        browser, page, errors = _boot(pw, quiet_motion=True)
        el = page.query_selector(f'.mod[data-gid="{gid}"]')
        if el is None:
            ids = page.evaluate(
                "[...document.querySelectorAll('.mod')].map(m=>m.dataset.gid)")
            browser.close()
            raise SystemExit(f"no card with gid {gid!r}. On screen: {ids}")
        box = el.bounding_box()
        page.screenshot(path=str(path), clip={
            "x": box["x"] - pad, "y": box["y"] - pad,
            "width": box["width"] + pad * 2, "height": box["height"] + pad * 2,
        })
        _report(page, errors, path, t0)
        browser.close()


def list_cards() -> None:
    """What is on screen right now — instance ids, types and size classes."""
    with sync_playwright() as pw:
        browser, page, _ = _boot(pw, quiet_motion=True)
        rows = page.evaluate("""[...document.querySelectorAll('.mod')].map(m=>({
            gid: m.dataset.gid, size: m.dataset.size,
            title: (m.querySelector('.title')||{}).textContent }))""")
        for r in rows:
            print(f"  {r['gid']:22} {r['size'] or '?':3}  {r['title'] or ''}")
        print(f"  ({len(rows)} cards)")
        browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    mode = sys.argv[1]
    if mode == "list":
        list_cards()
    elif mode == "page":
        capture_page(sys.argv[2])
    elif mode == "card":
        capture_card(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(f"unknown mode {mode!r} — page | card | list")
