"""Headless GUI checks for v8 (no synth server needed).

    python tests/gui_check8.py

1. Palette entries must NEVER disappear (root cause of the "Signal Gen
   vanishes" bug: gui/index.html — the /legacy page — still filtered its
   add-list by `!inChain.has(key)`, which in the v5 instance-id world hides
   a type forever once any instance exists). Failing-first on the legacy
   page; flex retention + alloc-reuse cycle guarded too.
3. Wire LABELS are drag handles: dragging a label onto a compatible module
   highlights it (white outline) and dropping splices the module into that
   wire (same trio as card-drop; ctl wires splice arp/tonic/keyshift/deck).
4. The drone card's TONIC input handle sits at the card's upper-left corner,
   clear of every other input handle.
"""

import glob
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
FLEX = REPO / "gui" / "legacy" / "flex.html"
LEGACY = REPO / "gui" / "legacy" / "index.html"
# Prefer a chromium already unpacked in the dev sandbox's browser dir; if it
# isn't there (e.g. GitHub Actions, where `playwright install` lays the browser
# out under its own versioned path), fall back to None and let Playwright
# resolve the browser it installed itself. Keeps this test portable across the
# sandbox and CI without touching the workflow.
_CHROME_GLOB = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
CHROME = _CHROME_GLOB[0] if _CHROME_GLOB else None

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def param(v=0.5, lo=0.0, hi=1.0):
    return {"min": lo, "max": hi, "curve": "lin", "options": [],
            "default": v, "lfo": False, "value": v}


def mod(key, name, kind, family, params=None, **extra):
    return {"key": key, "type": key.split(".")[0], "name": name,
            "kind": kind, "family": family, "enabled": True, "service": False,
            "params": params or {"amp": param()}, **extra}


def base_state(chain, wires, ctl_wires=None, available=None, **over):
    s = {
        "patch": "mock", "patches": ["mock"], "volume": 0.8,
        "devices": {"inputs": [], "outputs": []}, "current_input": None,
        "current_output": None, "input_enabled": False, "boot_note": None,
        "chain": chain, "wires": wires,
        "ctl_wires": ctl_wires or [
            {"from": "keys", "to": "arp"}, {"from": "arp", "to": "voice"},
            {"from": "arp", "to": "deck"}, {"from": "deck", "to": "voice"}],
        "drums_target": None, "voice_target": chain[0]["key"],
        "voices": [{"id": "voice", "target": chain[0]["key"]}],
        "tonics": [], "keyshifts": [], "transpose": 0,
        "midi_inputs": [], "midi_port": None, "midi_enabled": False,
        "arp": {"enabled": False, "pattern": "up", "patterns": ["up", "down"],
                "division": "1/8", "divisions": ["1/8", "1/16"], "gate": 0.6,
                "octaves": 1},
        "transport": {"bpm": 100, "beats_per_bar": 4, "click": False,
                      "running": True},
        "drone": {"enabled": False, "every": "1 bar", "everies": ["1 bar"],
                  "octave": 2, "root": None},
        "drums": {"enabled": False, "target": None, "to_chain": False,
                  "lanes": ["kick", "snare", "hat", "clap"], "steps": 16,
                  "patterns": {ln: [0] * 16 for ln in
                               ("kick", "snare", "hat", "clap")},
                  "levels": {"kick": 0.8, "snare": 0.7, "hat": 0.6,
                             "clap": 0.7}},
        "looper": {"state": "empty", "bars": 2, "level": 0.8, "overdub": False,
                   "position": "post", "loop_beats": 8, "notes": []},
        "lfos": [], "presets": [],
        "available": available or [
            {"key": "signal_gen", "name": "Signal Gen", "kind": "source",
             "family": "voice"},
            {"key": "echo", "name": "Echo", "kind": "effect", "family": "time"},
            {"key": "chorus", "name": "Chorus", "kind": "effect",
             "family": "time"},
        ],
        "module_errors": {},
    }
    s.update(over)
    return s


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


# spread layout so wire labels have room to draw (labelPass hides labels on
# segments too short for the text)
LAYOUT = {"pos": {
    "keys": [216, 96], "arp": [216, 672], "deck": [216, 936],
    "voice": [216, 1200], "m:signal_gen": [552, 96], "m:echo": [552, 480],
    "m:chorus": [1224, 480], "m:drone": [864, 912], "master": [552, 912],
    "drums": [1416, 96], "keyshift": [936, 96], "tonic": [864, 672],
}, "monitors": [], "monN": 1}


def open_page(p, url, layout=None):
    launch_kw = {"headless": True}
    if CHROME and os.path.exists(CHROME):
        launch_kw["executable_path"] = CHROME   # sandbox: use the unpacked binary
    browser = p.chromium.launch(**launch_kw)    # else: Playwright resolves its own
    page = browser.new_page(viewport={"width": 1700, "height": 1250})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB + (
        "localStorage.setItem('supersynth.flex.mock', %s);"
        % json.dumps(json.dumps(layout)) if layout else ""))
    page.goto(url)
    page.wait_for_timeout(300)
    page.evaluate("""() => {
      window.__msg = (m) => window.__wss[0].onmessage({data: JSON.stringify(m)});
    }""")
    return browser, page, errors


def main():
    with sync_playwright() as p:
        # ================================================================
        # 1 — LEGACY page: the add list must retain placed types (v5 ids)
        # ================================================================
        browser, page, errors = open_page(p, LEGACY.as_uri())
        sg = mod("signal_gen", "Signal Gen", "source", "voice",
                 {"freq": param(220, 20, 2000), "amp": param()})
        sg2 = mod("signal_gen.2", "Signal Gen 2", "source", "voice",
                  {"freq": param(220, 20, 2000), "amp": param()})
        echo = mod("echo", "Echo", "effect", "time")
        st = base_state([sg, sg2, echo],
                        [{"from": "signal_gen", "to": "echo"},
                         {"from": "echo", "to": "master"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st)
        page.wait_for_timeout(300)
        opts = page.evaluate(
            "[...document.querySelectorAll('#add-sel option')].map(o => o.value)")
        check("legacy add list retains a placed type (never disappears)",
              "signal_gen" in opts, str(opts))
        # after "deleting signal_gen.2" (first instance remains) it must STILL
        # be addable — this is the exact reported lockout
        st2 = base_state([sg, echo],
                         [{"from": "signal_gen", "to": "echo"},
                          {"from": "echo", "to": "master"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st2)
        page.wait_for_timeout(200)
        opts = page.evaluate(
            "[...document.querySelectorAll('#add-sel option')].map(o => o.value)")
        check("legacy add list offers the type after deleting the .2 instance",
              "signal_gen" in opts, str(opts))
        check("legacy: no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

        # ================================================================
        # 1 — FLEX: palette retention + id-reuse across a full cycle
        # ================================================================
        browser, page, errors = open_page(p, FLEX.as_uri(), layout=LAYOUT)
        page.evaluate("(s) => __msg({type: 'state', ...s})", st)  # 2 instances
        page.wait_for_timeout(400)
        pal = page.evaluate(
            "[...document.querySelectorAll('#palette button')].map(b => b.textContent)")
        check("flex palette retains the type with 2 instances placed",
              any("Signal Gen" in t for t in pal), str(pal))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st2)  # deleted .2
        page.wait_for_timeout(400)
        page.evaluate("window.__sent.length = 0")
        page.click("#palette button:has-text('Signal Gen')")
        sent = page.evaluate("window.__sent")
        check("flex re-add after delete spawns again",
              {"type": "spawn_module", "key": "signal_gen"} in sent, str(sent))
        check("flex predicts the REUSED id after delete",
              page.evaluate("nextId('signal_gen')") == "signal_gen.2")

        # ================================================================
        # 3 — wire labels as splice handles
        # ================================================================
        # audio wire signal_gen→echo; a free Chorus card to splice in
        chorus = mod("chorus", "Chorus", "effect", "time")
        st3 = base_state(
            [sg, echo, chorus],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],  # chorus unwired
            keyshifts=[{"id": "keyshift", "key": 0, "length": 4,
                        "steps": [None] * 4, "active": 0}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st3)
        page.wait_for_timeout(500)

        def label_center(from_gid):
            return page.evaluate("""(fromGid) => {
              const w = wires.find(v => v.from.node.gid === fromGid &&
                                        v.labelG.style.display !== 'none');
              if (!w) return null;
              const r = w.labelR.getBoundingClientRect();
              return [r.x + r.width / 2, r.y + r.height / 2];
            }""", from_gid)

        def card_center(gid):
            return page.evaluate("""(gid) => {
              const n = nodes.get(gid);
              const r = n.el.getBoundingClientRect();
              return [r.x + r.width / 2, r.y + 10];
            }""", gid)

        lb = label_center("m:signal_gen")
        check("audio wire has a visible label", bool(lb), str(lb))
        if lb:
            tgt = card_center("m:chorus")
            page.evaluate("window.__sent.length = 0")
            page.mouse.move(lb[0], lb[1])
            page.mouse.down()
            page.mouse.move(tgt[0], tgt[1], steps=10)
            page.wait_for_timeout(80)
            hi = page.evaluate(
                "nodes.get('m:chorus').el.classList.contains('splice-target')")
            check("compatible module highlights under a dragged label", hi)
            page.mouse.up()
            sent = page.evaluate("window.__sent")
            ok = ({"type": "graph_wire", "action": "add", "from": "chorus",
                   "to": "echo"} in sent and
                  {"type": "graph_wire", "action": "add", "from": "signal_gen",
                   "to": "chorus"} in sent)
            check("label drop splices the module into the wire (audio trio)",
                  ok, str(sent))
            check("highlight cleared after drop", page.evaluate(
                "!nodes.get('m:chorus').el.classList.contains('splice-target')"))

        # ctl wire keys→arp label onto the keyshift card = lane-1 ctl splice
        lb = label_center("keys")
        check("ctl wire has a visible label", bool(lb), str(lb))
        if lb:
            tgt = card_center("keyshift")
            page.evaluate("window.__sent.length = 0")
            page.mouse.move(lb[0], lb[1])
            page.mouse.down()
            page.mouse.move(tgt[0], tgt[1], steps=10)
            page.wait_for_timeout(80)
            hi = page.evaluate(
                "nodes.get('keyshift').el.classList.contains('splice-target')")
            check("keyshift highlights under a dragged ctl label", hi)
            page.mouse.up()
            sent = page.evaluate("window.__sent")
            ok = ({"type": "ctl_wire", "action": "remove", "from": "keys",
                   "to": "arp"} in sent and
                  {"type": "ctl_wire", "action": "add", "from": "keys",
                   "to": "keyshift:1"} in sent and
                  {"type": "ctl_wire", "action": "add", "from": "keyshift:1",
                   "to": "arp"} in sent)
            check("ctl label drop splices through the shifter (lane 1)",
                  ok, str(sent))

        # an INCOMPATIBLE target (a source card) must never highlight
        lb = label_center("m:signal_gen")
        if lb:
            tgt = card_center("m:signal_gen")
            page.mouse.move(lb[0], lb[1])
            page.mouse.down()
            page.mouse.move(tgt[0], tgt[1], steps=6)
            page.wait_for_timeout(60)
            hi = page.evaluate(
                "nodes.get('m:signal_gen').el.classList.contains('splice-target')")
            page.mouse.up()
            check("wire endpoints / sources never highlight", hi is False)

        # ================================================================
        # 4 — drone tonic-in handle at the card's upper-left corner
        # ================================================================
        drone = mod("drone", "Drone", "source", "service",
                    {"freq": param(55, 16, 500), "amp": param(0.16)},
                    tonic_follow=True)
        st4 = base_state(
            [sg, drone, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"},
             {"from": "drone", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "arp"},
                       {"from": "arp", "to": "voice"},
                       {"from": "arp", "to": "tonic"},
                       {"from": "tonic", "to": "drone"}],
            tonics=[{"id": "tonic", "every": "1 bar",
                     "everies": ["1 bar"], "octave": 2, "root": "C"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st4)
        page.wait_for_timeout(500)
        pos = page.evaluate("""(() => {
          const n = nodes.get('m:drone');
          const t = n.ports.find(p => p.sig === 'tonic' && p.dir === 'in');
          if (!t) return null;
          const others = n.ports.filter(p => p !== t)
            .map(p => Math.hypot((p.ox ?? 999) - t.ox, (p.oy ?? 999) - t.oy));
          return {ox: t.ox, oy: t.oy, minDist: Math.min(...others),
                  h: n.el.offsetHeight};
        })()""")
        check("drone tonic-in sits at the upper-left corner",
              pos is not None and pos["ox"] == 0 and pos["oy"] <= 16,
              str(pos))
        check("tonic-in overlaps no other handle",
              pos is not None and pos["minDist"] >= 12, str(pos))
        wire_ok = page.evaluate("""(() => {
          const w = wires.find(v => v.sig === 'tonic');
          return w && w.to.port.oy <= 16 && w.to.port.ox === 0;
        })()""")
        check("tonic wire lands on the corner handle", bool(wire_ok))

        check("flex: no page errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/flexv8.png", full_page=False)
        print("screenshot → /tmp/flexv8.png")
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
