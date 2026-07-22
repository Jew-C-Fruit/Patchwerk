"""Captured-real-state replay for gui/blocks.html (headless, no server).

    python tests/check_real.py

REBUILT 2026-07-21 (the original lived only in a dead work container).
Replays tests/fixtures/real_state.json — a state broadcast captured from
Cole's LIVE rig (11-module chain incl. drone + scope, a real LFO
assignment, two derivers, a keyshift lane wire) — into blocks.html through
the mock websocket and asserts the whole board comes up: every entity gets
a card, every wire kind draws, nothing overflows, no page errors. The
fixture predates the item-6 estimator fields on purpose: builders must
tolerate older servers.

Re-capture (Mac, server running):  connect ws, save the first state
message over tests/fixtures/real_state.json.
"""

import glob
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
BLOCKS = REPO / "gui" / "blocks.html"
FIXTURE = REPO / "tests" / "fixtures" / "real_state.json"
_CHROME_GLOB = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
                or glob.glob("/opt/pw-browsers/chromium"))
CHROME = _CHROME_GLOB[0] if _CHROME_GLOB else None

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


STUB = """
  window.__sent = [];
  window.__wss = [];
  window.WebSocket = class {
    constructor(url) { this.url = url; this.readyState = 1;
      window.__wss.push(this);
      setTimeout(() => this.onopen && this.onopen(), 0); }
    send(d) { window.__sent.push(JSON.parse(d)); }
    close() {}
  };
"""


def main():
    st = json.loads(FIXTURE.read_text())
    with sync_playwright() as p:
        launch_kw = {"headless": True}
        if CHROME and os.path.exists(CHROME):
            launch_kw["executable_path"] = CHROME
        browser = p.chromium.launch(**launch_kw)
        page = browser.new_page(viewport={"width": 1700, "height": 1250})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STUB)
        page.goto(BLOCKS.as_uri())
        page.wait_for_timeout(300)
        page.evaluate(
            "(s) => window.__wss[0].onmessage({data: JSON.stringify(s)})", st)
        page.wait_for_timeout(800)

        got = set(page.evaluate("[...nodes.keys()]"))

        # every chain module, deriver, lfo assignment and core node = a card
        for c in st["chain"]:
            check(f"chain card: {c['key']}", ("m:" + c["key"]) in got)
        for t in st.get("tonics", []):
            check(f"deriver card: {t['id']}", t["id"] in got)
        for ln in st.get("lfos", []):
            check(f"lfo card: {ln['id']}", ("lfo:" + ln["id"]) in got)
        for core in ("keys", "arp", "deck", "master", "keyshift"):
            need = core != "keyshift" or st.get("keyshifts")
            if need:
                check(f"core card: {core}", core in got, str(sorted(got)))

        # wires: every audio wire + every ctl wire resolves and draws
        wire_info = page.evaluate("""(() => wires.map(w => ({
          sig: w.sig, from: w.from.node.gid, to: w.to.node.gid})))()""")
        audio_drawn = [w for w in wire_info if w["sig"] == "audio"]
        check("every audio wire draws",
              len(audio_drawn) >= len(st.get("wires", [])),
              f"{len(audio_drawn)} vs {len(st.get('wires', []))}")
        ctl_drawn = [w for w in wire_info if w["sig"] == "ctl"]
        check("every ctl wire draws (incl. the drone play wire)",
              len(ctl_drawn) >= len(st.get("ctl_wires", [])),
              f"{len(ctl_drawn)} vs {len(st.get('ctl_wires', []))}")
        check("the keys→drone wire lands on the drone card",
              any(w["to"] == "m:drone" and w["sig"] == "ctl"
                  for w in wire_info), str(ctl_drawn))
        mod_drawn = [w for w in wire_info if w["sig"] == "mod"]
        check("the LFO assignment draws a mod wire",
              len(mod_drawn) >= len(st.get("lfos", [])), str(mod_drawn))

        # nothing overflows: card content stays inside every card body
        overflow = page.evaluate("""(() => {
          const bad = [];
          for (const n of nodes.values()) {
            const el = n.el;
            if (el.scrollHeight > el.clientHeight + 2 ||
                el.scrollWidth > el.clientWidth + 2)
              bad.push([n.gid, el.scrollHeight, el.clientHeight]);
          }
          return bad;
        })()""")
        check("no card overflows its footprint", not overflow, str(overflow))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
