"""Headless checks for gui/blocks.html (no synth server needed).

    python tests/check_blocks.py

REBUILT 2026-07-21: the original ~75-check suite lived only in an ephemeral
work container and was lost; this in-repo rebuild is now the canonical gate
for every blocks.html ship (grow it with each feature). A captured-real-state
replay lives in check_real.py (fixture: tests/fixtures/real_state.json).

Current coverage:
  1. Boot + base-state render: cards appear, no page errors.
  2. Sliders: a bare CLICK never changes the value or sends a set-param;
     a DRAG is relative (startU + dx/track-width), clamped 0..1, and derives
     scale from world.style.zoom (convention-neutral across the Chrome 128
     CSS-zoom change — NOTE the container Chromium is pre-128, so only the
     zoom-derivation *inputs* can be asserted here, not the post-128 branch).
  3. Wiring grammar: connectAction refuses cross-kind drops.
  4. Key Shifter: card renders its step grid; steps paint from state.
"""

import glob
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
BLOCKS = REPO / "gui" / "blocks.html"
_CHROME_GLOB = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
                or glob.glob("/opt/pw-browsers/chromium/chrome-linux/chrome")
                or glob.glob("/opt/pw-browsers/chromium"))
CHROME = _CHROME_GLOB[0] if _CHROME_GLOB else None

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
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
            {"from": "keys", "to": "arp"}, {"from": "arp", "to": "voice"}],
        "drums_target": None, "voice_target": chain[0]["key"] if chain else None,
        "voices": ([{"id": "voice", "target": chain[0]["key"]}]
                   if chain else []),
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
            {"key": "echo", "name": "Echo", "kind": "effect",
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


def open_page(p):
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
    page.evaluate("""() => {
      window.__msg = (m) => window.__wss[0].onmessage({data: JSON.stringify(m)});
    }""")
    return browser, page, errors


def slider_geom(page, gid, pname):
    """visual-px center + geometry of a slider track, robust to CSS zoom."""
    return page.evaluate("""([gid, pname]) => {
      const n = nodes.get(gid);
      const row = [...n.el.querySelectorAll('.mini')].find(
        r => r.querySelector('label')?.title === pname);
      const tr = row.querySelector('.track');
      const r = tr.getBoundingClientRect();
      const zs = parseFloat(world.style.zoom) || 1;
      // container Chromium is PRE-128: rects of zoomed content are visual px.
      return {x: r.x + r.width / 2, y: r.y + r.height / 2,
              w: tr.offsetWidth, zoom: zs,
              thumb: parseFloat(tr.firstElementChild.style.left) / 100};
    }""", [gid, pname])


def main():
    with sync_playwright() as p:
        browser, page, errors = open_page(p)
        sg = mod("signal_gen", "Signal Gen", "source", "voice",
                 {"freq": param(220, 20, 2000), "amp": param(0.5)})
        echo = mod("echo", "Echo", "effect", "time")
        st = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            keyshifts=[{"id": "keyshift", "key": 0, "length": 8,
                        "steps": [None, 2, None, None, 7, None, None, None],
                        "active": 0}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st)
        page.wait_for_timeout(500)

        # ================================================================
        # 1 — boot + render
        # ================================================================
        got = page.evaluate("[...nodes.keys()]")
        for gid in ("m:signal_gen", "m:echo", "master", "keys", "keyshift"):
            check(f"card renders: {gid}", gid in got, str(got))

        # ================================================================
        # 2 — sliders: click never applies; drag is relative + zoom-correct
        # ================================================================
        g0 = slider_geom(page, "m:signal_gen", "amp")
        check("slider present with a sane start value",
              g0 and 0.0 <= g0["thumb"] <= 1.0, str(g0))
        u0 = g0["thumb"]

        # bare click well away from the thumb (left edge of the track)
        page.evaluate("window.__sent.length = 0")
        page.mouse.click(g0["x"] - (g0["w"] * g0["zoom"]) * 0.45, g0["y"])
        page.wait_for_timeout(120)
        g1 = slider_geom(page, "m:signal_gen", "amp")
        sent = page.evaluate("window.__sent")
        check("bare click does NOT move the thumb",
              abs(g1["thumb"] - u0) < 1e-6, f"{u0} -> {g1['thumb']}")
        check("bare click sends NO message", not sent, str(sent))

        # drag by +25% of the track's VISUAL width -> value rises ~0.25
        page.evaluate("window.__sent.length = 0")
        dx_vis = g0["w"] * g0["zoom"] * 0.25
        page.mouse.move(g0["x"], g0["y"])
        page.mouse.down()
        page.mouse.move(g0["x"] + dx_vis, g0["y"], steps=8)
        page.mouse.up()
        page.wait_for_timeout(120)
        g2 = slider_geom(page, "m:signal_gen", "amp")
        check("drag is RELATIVE: +25% track width == +0.25 value",
              abs(g2["thumb"] - (u0 + 0.25)) < 0.03,
              f"{u0} -> {g2['thumb']} (zoom {g0['zoom']})")
        sent = page.evaluate(
            "window.__sent.filter(m => m.type && m.type.startsWith('set'))")
        check("drag sends param updates", len(sent) >= 1, str(sent[:3]))

        # huge drag clamps at 1.0, huge negative clamps at 0.0
        page.mouse.move(g0["x"], g0["y"])
        page.mouse.down()
        page.mouse.move(g0["x"] + g0["w"] * g0["zoom"] * 3, g0["y"], steps=4)
        page.mouse.up()
        page.wait_for_timeout(80)
        check("overshoot clamps at 1.0",
              abs(slider_geom(page, "m:signal_gen", "amp")["thumb"] - 1.0) < 1e-6)
        page.mouse.move(g0["x"], g0["y"])
        page.mouse.down()
        page.mouse.move(g0["x"] - g0["w"] * g0["zoom"] * 3, g0["y"], steps=4)
        page.mouse.up()
        page.wait_for_timeout(80)
        check("undershoot clamps at 0.0",
              abs(slider_geom(page, "m:signal_gen", "amp")["thumb"]) < 1e-6)

        # convention-neutrality: the drag math must consult world.style.zoom
        # and NEVER a rect-derived ratio (Chrome 128 landmine). Assert the
        # code path directly since the container browser is pre-128 only.
        src = page.evaluate("rowSlider.toString()")
        check("slider math derives scale from world.style.zoom",
              "world.style.zoom" in src, "")
        check("slider math uses no rect-derived scale",
              "getBoundingClientRect" not in src, "")

        # ================================================================
        # 3 — wiring grammar: cross-kind drops refused
        # ================================================================
        bad = page.evaluate("""(() => {
          const outA = {node: nodes.get('m:signal_gen'),
                        port: {dir: 'out', sig: 'audio', label: 'out'}};
          const innC = {node: nodes.get('arp'),
                        port: {dir: 'in', sig: 'ctl', label: 'play'}};
          return connectAction(outA, innC);
        })()""")
        check("audio-out onto ctl-in refused", not bad, str(bad))

        # ================================================================
        # 4 — Key Shifter renders its grid from state
        # ================================================================
        ks = page.evaluate("""(() => {
          const n = nodes.get('keyshift');
          const steps = [...n.el.querySelectorAll('.ksstep')];
          return {count: steps.length,
                  on: steps.filter(b => b.classList.contains('on')).length};
        })()""")
        check("keyshift grid has one cell per step", ks["count"] == 8, str(ks))
        check("keyshift assigned steps paint 'on'", ks["on"] == 2, str(ks))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
