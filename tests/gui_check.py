"""Headless GUI check for flex.html (no synth server needed).

    python tests/gui_check.py

Stubs WebSocket before load, injects a mock state with TWO audio cascades +
drums + v3 ctl_wires, then drives the page: verifies no pageerrors, wires
render from state.wires AND state.ctl_wires, clicking wires sends
graph_wire / ctl_wire remove, port drags send graph_wire / ctl_wire add,
the deck card has no position chip, the scope_tap module card carries the
scope canvas, palette spawn/splice, and label/circumscribe geometry.
Screenshots to /tmp/flexv3.png.
"""

import glob
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
FLEX = REPO / "gui" / "flex.html"
CHROME = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
          or ["/opt/pw-browsers/chromium"])[0]

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def param(v=0.5, lo=0.0, hi=1.0):
    return {"min": lo, "max": hi, "curve": "lin", "options": [],
            "default": v, "lfo": False, "value": v}


def mod(key, name, kind, family, params=None):
    return {"key": key, "name": name, "kind": kind, "family": family,
            "enabled": True, "service": False, "params": params or {"amp": param()}}


STATE = {
    "patch": "mock", "patches": ["mock"], "volume": 0.8,
    "devices": {"inputs": [], "outputs": []}, "current_input": None,
    "current_output": None, "input_enabled": False, "boot_note": None,
    "chain": [
        mod("pluck", "Pluck", "source", "voice",
            {"amp": param(), "freq": param(220, 20, 2000), "gate": param(0)}),
        mod("echo", "Echo", "effect", "time"),
        mod("wind", "Wind", "source", "input"),
        mod("reverb", "Reverb", "effect", "time"),
        mod("scope_tap", "Scope Tap", "effect", "io",
            {"gain": param(1.0, 0.0, 2.0)}),
    ],
    # TWO separate cascades feeding master + drums routed to echo; the scope
    # tap is spliced inline: reverb → scope_tap → master
    "wires": [
        {"from": "pluck", "to": "echo"}, {"from": "echo", "to": "master"},
        {"from": "wind", "to": "reverb"}, {"from": "reverb", "to": "scope_tap"},
        {"from": "scope_tap", "to": "master"},
    ],
    # v3 control plane: server truth, fully editable
    "ctl_wires": [
        {"from": "keys", "to": "arp"},
        {"from": "arp", "to": "voice"},
        {"from": "arp", "to": "deck"},
        {"from": "deck", "to": "voice"},
    ],
    "drums_target": "echo",
    "voice_target": "pluck", "transpose": 0,
    "midi_inputs": [], "midi_port": None, "midi_enabled": False,
    "arp": {"enabled": True, "pattern": "up", "patterns": ["up", "down"],
            "division": "1/8", "divisions": ["1/8", "1/16"], "gate": 0.6,
            "octaves": 1},
    "transport": {"bpm": 100, "beats_per_bar": 4, "click": False, "running": True},
    "drone": {"enabled": False, "every": "1 bar", "everies": ["1 bar"],
              "octave": 2, "root": None},
    "drums": {"enabled": True, "target": "echo", "to_chain": True,
              "lanes": ["kick", "snare", "hat", "clap"], "steps": 16,
              "patterns": {ln: [0] * 16 for ln in ("kick", "snare", "hat", "clap")},
              "levels": {"kick": 0.8, "snare": 0.7, "hat": 0.6, "clap": 0.7}},
    "looper": {"state": "empty", "bars": 2, "level": 0.8, "overdub": False,
               "position": "post", "loop_beats": 8, "notes": []},
    "lfos": [], "presets": [],
    "available": [
        {"key": "chorus", "name": "Chorus", "kind": "effect", "family": "time"},
        {"key": "fm_bell", "name": "FM Bell", "kind": "source", "family": "voice"},
    ],
    "module_errors": {},
}

# spread layout: long DOWNWARD vertical (pluck→echo), long UPWARD vertical
# (wind low → reverb high), and room to drop cards
LAYOUT = {"pos": {
    "m:pluck": [480, 72], "m:echo": [480, 576],
    "m:wind": [936, 576], "m:reverb": [936, 72], "m:scope_tap": [1200, 600],
    "master": [696, 936], "drums": [1416, 240],
    "keys": [216, 72], "arp": [216, 288], "deck": [216, 504], "voice": [216, 768],
}, "monitors": [], "monN": 1}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1700, "height": 1150})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("""
          window.__sent = [];
          window.__wss = [];
          window.WebSocket = class {
            constructor(url) { this.url = url; this.readyState = 1;
              window.__wss.push(this);
              setTimeout(() => this.onopen && this.onopen(), 0); }
            send(d) { window.__sent.push(JSON.parse(d)); }
            close() { this.readyState = 3; this.onclose && this.onclose(); }
          };
          localStorage.setItem("supersynth.flex.mock", %s);
        """ % json.dumps(json.dumps(LAYOUT)))
        page.goto(FLEX.as_uri())
        page.wait_for_timeout(300)

        # push the mock state through the stubbed socket
        page.evaluate("""(s) => {
          const ws = window.__wss[0];
          ws.onmessage({data: JSON.stringify({type: "state", ...s})});
        }""", STATE)
        page.wait_for_timeout(500)

        n_audio = page.evaluate("wires.filter(w => w.sig === 'audio').length")
        check("wires render from state.wires", n_audio == 6, f"audio={n_audio}")  # 5 chain + drums
        fanin = page.evaluate(
            "wires.filter(w => w.sig==='audio' && w.to.node.gid==='master').length")
        check("two cascades fan into master", fanin == 2, f"fanin={fanin}")

        # ---- v3: ctl wires are server truth ----
        n_ctl = page.evaluate("wires.filter(w => w.sig === 'ctl').length")
        # 4 from state.ctl_wires + the derived voice→target wire
        check("ctl wires render from state.ctl_wires", n_ctl == 5, f"ctl={n_ctl}")
        has = page.evaluate("""
          !!wires.find(w => w.sig==='ctl' && w.from.node.gid==='keys'
                       && w.to.node.gid==='arp')""")
        check("keys→arp ctl wire drawn", has)

        # cutting a ctl wire sends ctl_wire remove
        page.evaluate("""() => {
          const w = wires.find(v => v.sig === 'ctl' && v.from.node.gid === 'keys'
                               && v.to.node.gid === 'arp');
          w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("cut ctl wire → ctl_wire remove",
              {"type": "ctl_wire", "action": "remove", "from": "keys", "to": "arp"}
              in sent, str(sent[-3:]))

        # port drag keys.out → voice.in sends ctl_wire add
        drag = page.evaluate("""() => {
          const a = nodes.get('keys'), b = nodes.get('voice');
          const po = a.ports.find(p => p.dir === 'out' && p.sig === 'ctl');
          const pi = b.ports.find(p => p.dir === 'in' && p.sig === 'ctl');
          const r = world.getBoundingClientRect();
          const [x1, y1] = portXY(a, po), [x2, y2] = portXY(b, pi);
          return [x1 + r.left, y1 + r.top, x2 + r.left, y2 + r.top];
        }""")
        page.mouse.move(drag[0], drag[1])
        page.mouse.down()
        page.mouse.move(drag[2], drag[3], steps=8)
        page.mouse.up()
        sent = page.evaluate("window.__sent")
        check("port drag ctl-out→ctl-in → ctl_wire add",
              {"type": "ctl_wire", "action": "add", "from": "keys", "to": "voice"}
              in sent, str(sent[-3:]))

        # deck card must have NO position chip (wiring replaced it)
        deck_chips = page.evaluate("""
          [...nodes.get('deck').el.querySelectorAll('.mini label')]
            .map(l => l.textContent)""")
        check("deck card has no position chip", "position" not in deck_chips,
              str(deck_chips))

        # scope_tap renders as a real module card WITH the scope canvas
        scope = page.evaluate("""(() => {
          const n = nodes.get('m:scope_tap');
          if (!n) return {ok: false};
          return {ok: true, canvas: !!n.el.querySelector('canvas[data-viz=scope]'),
                  key: n.scopeKey,
                  audioIn: !!n.ports.find(p => p.dir==='in' && p.sig==='audio'),
                  audioOut: !!n.ports.find(p => p.dir==='out' && p.sig==='audio')};
        })()""")
        check("scope_tap card exists with scope canvas",
              scope.get("ok") and scope.get("canvas"), str(scope))
        check("scope_tap has real audio IO handles",
              scope.get("audioIn") and scope.get("audioOut"))
        check("scope_tap polls its own key", scope.get("key") == "scope_tap")
        # the 250 ms poll loop asks the server for the tap's bus
        page.wait_for_timeout(400)
        polled = page.evaluate(
            "window.__sent.some(m => m.type === 'scope' && m.key === 'scope_tap')")
        check("scope poll sends {type:scope, key:scope_tap}", polled)
        # no Oscilloscope monitor in the palette anymore
        osc_btn = page.evaluate("""
          [...document.querySelectorAll('#palette button')]
            .some(b => b.textContent.includes('Oscilloscope'))""")
        check("old target-chip scope card removed from palette", not osc_btn)

        # every wire with a cut handler is cuttable; voice→target is view-only
        vt_cut = page.evaluate("""(() => {
          const w = wires.find(v => v.sig==='ctl' && v.from.node.gid==='voice');
          return w ? w.hitEl.classList.contains('cuttable') : null;
        })()""")
        check("voice→target wire is not cuttable", vt_cut is False, str(vt_cut))

        # ---- audio behaviors preserved from v2 ----
        pt = page.evaluate("""() => {
          const w = wires.find(v => v.sig === 'audio' && v.from.node.gid === 'm:pluck');
          const pts = w.dpts || w.pts;
          let best = null, len = 0;
          for (let i = 0; i < pts.length - 1; i++) {
            const l = Math.abs(pts[i+1][0]-pts[i][0]) + Math.abs(pts[i+1][1]-pts[i][1]);
            if (l > len) { len = l; best = [(pts[i][0]+pts[i+1][0])/2, (pts[i][1]+pts[i+1][1])/2]; }
          }
          const r = world.getBoundingClientRect();
          return [best[0] + r.left, best[1] + r.top];
        }""")
        page.mouse.click(pt[0], pt[1])
        page.wait_for_timeout(100)
        sent = page.evaluate("window.__sent")
        cut_ok = {"type": "graph_wire", "action": "remove", "from": "pluck"} in sent
        if not cut_ok:  # geometry fallback: dispatch the click straight at the hit path
            page.evaluate("""() => {
              const w = wires.find(v => v.sig === 'audio' && v.from.node.gid === 'm:pluck');
              w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            }""")
            sent = page.evaluate("window.__sent")
            cut_ok = {"type": "graph_wire", "action": "remove", "from": "pluck"} in sent
        check("click audio wire → graph_wire remove", cut_ok, str(sent[-3:]))

        # drums wire cut → set_drums target null
        page.evaluate("""() => {
          const w = wires.find(v => v.from.node.gid === 'drums');
          w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("cut drums wire → set_drums target null",
              {"type": "set_drums", "target": None} in sent)

        # port drag: wind.out → echo.in should send graph_wire add wind→echo
        drag = page.evaluate("""() => {
          const w = nodes.get('m:wind'), e = nodes.get('m:echo');
          const po = w.ports.find(p => p.dir === 'out' && p.sig === 'audio');
          const pi = e.ports.find(p => p.dir === 'in' && p.sig === 'audio');
          const r = world.getBoundingClientRect();
          const [x1, y1] = portXY(w, po), [x2, y2] = portXY(e, pi);
          return [x1 + r.left, y1 + r.top, x2 + r.left, y2 + r.top];
        }""")
        page.mouse.move(drag[0], drag[1])
        page.mouse.down()
        page.mouse.move(drag[2], drag[3], steps=8)
        page.mouse.up()
        sent = page.evaluate("window.__sent")
        check("port drag → graph_wire add",
              {"type": "graph_wire", "action": "add", "from": "wind", "to": "echo"} in sent,
              str(sent[-3:]))

        # palette CLICK spawns UNCONNECTED
        page.evaluate("window.__sent.length = 0")
        page.click("#palette button:has-text('Chorus')")
        sent = page.evaluate("window.__sent")
        check("palette click → spawn_module (unconnected)",
              {"type": "spawn_module", "key": "chorus"} in sent, str(sent))
        no_wire_msg = not any(m.get("type") == "graph_wire" for m in sent)
        check("palette click adds no wires", no_wire_msg)

        # palette DRAG onto a wire → splice (spawn + two graph_wire adds)
        page.evaluate("window.__sent.length = 0")
        wire_pt = page.evaluate("""() => {
          const w = wires.find(v => v.sig === 'audio' && v.from.node.gid === 'm:echo');
          const pts = w.dpts || w.pts;
          let best = null, len = 0;
          for (let i = 0; i < pts.length - 1; i++) {
            const l = Math.abs(pts[i+1][0]-pts[i][0]) + Math.abs(pts[i+1][1]-pts[i][1]);
            if (l > len) { len = l; best = [(pts[i][0]+pts[i+1][0])/2, (pts[i][1]+pts[i+1][1])/2]; }
          }
          const r = world.getBoundingClientRect();
          return [best[0] + r.left, best[1] + r.top];
        }""")
        btn = page.locator("#palette button", has_text="Chorus")
        bb = btn.bounding_box()
        page.mouse.move(bb["x"] + 10, bb["y"] + 10)
        page.mouse.down()
        page.mouse.move(wire_pt[0], wire_pt[1], steps=12)
        page.wait_for_timeout(50)
        hi = page.evaluate("wires.some(w => w.el.classList.contains('splice-hi'))")
        page.mouse.up()
        page.wait_for_timeout(100)
        check("compatible wire highlights during palette drag", hi)
        sent = page.evaluate("window.__sent")
        splice_ok = ({"type": "spawn_module", "key": "chorus"} in sent and
                     {"type": "graph_wire", "action": "add", "from": "echo", "to": "chorus"} in sent and
                     {"type": "graph_wire", "action": "add", "from": "chorus", "to": "master"} in sent)
        check("palette drop on wire → splice messages", splice_ok, str(sent))

        # circumscribe: no drawn wire segment crosses a card body
        crossings = page.evaluate("""() => {
          let bad = 0;
          for (const w of wires) {
            const pts = w.dpts || w.pts;
            if (!pts) continue;
            for (let i = 0; i < pts.length - 1; i++) {
              const [a, b] = [pts[i], pts[i + 1]];
              for (const n of nodes.values()) {
                if (n === w.from.node || n === w.to.node) continue;
                const h = n.el.offsetHeight || 64, W2 = n.w || 170;
                const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
                const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
                if (x0 < n.x + W2 - 2 && n.x + 2 < x1 &&
                    y0 < n.y + h - 2 && n.y + 2 < y1) bad++;
              }
            }
          }
          return bad;
        }""")
        check("no wire under any card", crossings == 0, f"crossings={crossings}")

        check("no page errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/flexv3.png", full_page=False)
        print("screenshot → /tmp/flexv3.png")
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
