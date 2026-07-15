"""Headless GUI check for flex.html v6 (no synth server needed).

    python tests/gui_check6.py

Part A — v5 regression checks (written to FAIL on the broken behavior):
  * scope: palette entry, card detection by TYPE (incl. "scope_tap.2" and
    entries missing the type field), instance-id polling, trace painting.
  * note monitor: on/off tap pairs close and recede; re-fired open notes
    don't leak duplicate opens; closed notes roll off and get pruned.
  * waveform monitor: riding history is a FIXED-LENGTH rolling window —
    length constant across pushes, so bars roll instead of packing.
  * deck viz: paired notes draw bounded bars, never full-width smears.

Part B — key shifter card: popover key palette, progression step strip,
per-lane ports wired independently. Screenshots to /tmp/flexv6.png.
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


def mod(key, name, kind, family, params=None, type=None, **extra):
    return {"key": key, "type": type or key.split(".")[0], "name": name,
            "kind": kind, "family": family, "enabled": True, "service": False,
            "params": params or {"amp": param()}, **extra}


# NOTE: the second scope instance deliberately OMITS the "type" field (the
# legacy/edge shape) — card detection must fall back to type_of(key), not
# the raw instance id.
scope2 = mod("scope_tap.2", "Scope Tap 2", "effect", "effect",
             {"gain": param(1.0, 0.0, 2.0)})
del scope2["type"]

STATE = {
    "patch": "mock", "patches": ["mock"], "volume": 0.8,
    "devices": {"inputs": [], "outputs": []}, "current_input": None,
    "current_output": None, "input_enabled": False, "boot_note": None,
    "chain": [
        mod("pluck", "Pluck", "source", "voice",
            {"amp": param(), "freq": param(220, 20, 2000), "gate": param(0)}),
        mod("echo", "Echo", "effect", "time"),
        scope2,
    ],
    "wires": [
        {"from": "pluck", "to": "echo"},
        {"from": "echo", "to": "scope_tap.2"},
        {"from": "scope_tap.2", "to": "master"},
    ],
    "ctl_wires": [
        {"from": "keys", "to": "arp"}, {"from": "arp", "to": "voice"},
        {"from": "arp", "to": "deck"}, {"from": "deck", "to": "voice"},
    ],
    "drums_target": None, "voice_target": "pluck",
    "voices": [{"id": "voice", "target": "pluck"}],
    "tonics": [],
    "keyshifts": [{"id": "keyshift", "key": 0, "length": 4,
                   "steps": [None, None, None, None], "active": 0}],
    "transpose": 0,
    "midi_inputs": [], "midi_port": None, "midi_enabled": False,
    "arp": {"enabled": False, "pattern": "up", "patterns": ["up", "down"],
            "division": "1/8", "divisions": ["1/8", "1/16"], "gate": 0.6,
            "octaves": 1},
    "transport": {"bpm": 100, "beats_per_bar": 4, "click": False, "running": True},
    "drone": {"enabled": False, "every": "1 bar", "everies": ["1 bar"],
              "octave": 2, "root": None},
    "drums": {"enabled": False, "target": None, "to_chain": False,
              "lanes": ["kick", "snare", "hat", "clap"], "steps": 16,
              "patterns": {ln: [0] * 16 for ln in ("kick", "snare", "hat", "clap")},
              "levels": {"kick": 0.8, "snare": 0.7, "hat": 0.6, "clap": 0.7}},
    "looper": {"state": "playing", "bars": 2, "level": 0.8, "overdub": False,
               "position": "post", "loop_beats": 8,
               "notes": [[0, 60, True], [1, 60, False],
                         [2, 64, True], [3, 64, False]]},
    "lfos": [], "presets": [],
    "available": [
        {"key": "pluck", "name": "Pluck", "kind": "source", "family": "voice"},
        {"key": "echo", "name": "Echo", "kind": "effect", "family": "time"},
        {"key": "scope_tap", "name": "Scope Tap", "kind": "effect",
         "family": "effect"},
    ],
    "module_errors": {},
}

LAYOUT = {"pos": {
    "m:pluck": [480, 72], "m:echo": [480, 384], "m:scope_tap.2": [480, 648],
    "master": [480, 984], "drums": [1416, 96],
    "keys": [216, 72], "arp": [216, 288], "deck": [216, 504],
    "voice": [216, 768], "keyshift": [960, 72],
}, "monitors": [], "monN": 1}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1700, "height": 1250})
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
            // the real server streams meters at ~20Hz so the page watchdog
            // never times out; the mock is silent for seconds at a time, so
            // ignore watchdog closes — reconnect churn isn't under test here
            close() {}
          };
          localStorage.setItem("supersynth.flex.mock", %s);
        """ % json.dumps(json.dumps(LAYOUT)))
        page.goto(FLEX.as_uri())
        page.wait_for_timeout(300)
        page.evaluate("""(s) => {
          window.__msg = (m) => window.__wss[0].onmessage({data: JSON.stringify(m)});
          __msg({type: "state", ...s});
        }""", STATE)
        page.wait_for_timeout(400)

        # =====================================================================
        # A1 — oscilloscope: palette entry + card detection by TYPE
        # =====================================================================
        pal = page.evaluate(
            "[...document.querySelectorAll('#palette button')].map(b => b.textContent)")
        check("palette lists Scope Tap under effects",
              any("Scope Tap" in t for t in pal), str(pal))
        sc = page.evaluate("""(() => {
          const n = nodes.get('m:scope_tap.2');
          if (!n) return {ok: false};
          return {ok: true, scopeKey: n.scopeKey,
                  canvas: !!n.el.querySelector('canvas.vz[data-viz=scope]')};
        })()""")
        check("scope_tap.2 (no type field) still detected as the oscilloscope",
              sc.get("ok") and sc.get("canvas"), str(sc))
        check("scope card polls by INSTANCE id", sc.get("scopeKey") == "scope_tap.2")
        page.evaluate("window.__sent.length = 0")
        page.wait_for_timeout(400)
        polls = page.evaluate("window.__sent.filter(m => m.type === 'scope')")
        check("scope poll message carries the instance id",
              any(m.get("key") == "scope_tap.2" for m in polls), str(polls[:3]))
        page.evaluate("""() => {
          const s = [];
          for (let i = 0; i < 2048; i++) s.push(Math.sin(i / 10) * 0.5);
          __msg({type: "scope_data", key: "scope_tap.2", sr: 44100, samples: s});
        }""")
        page.wait_for_timeout(150)
        lit = page.evaluate("""(() => {
          const cv = nodes.get('m:scope_tap.2').el.querySelector('canvas.vz');
          const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
          let lit = 0;
          for (let i = 3; i < d.length; i += 4) if (d[i] > 0) lit++;
          return lit;
        })()""")
        check("scope canvas draws the trace", lit > 300, f"lit={lit}")

        # =====================================================================
        # A2 — note monitor: pairing, recession, roll-off, no duplicate opens
        # =====================================================================
        page.evaluate("buildMonitor('notes'); afterMonitor()")
        page.wait_for_timeout(150)
        page.evaluate("""() => {
          __msg({type: "beat", bar: 0, beat: 0, loop: null});
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 60, on: true}});
          __msg({type: "midi", event: {kind: "tap", src: "arp", note: 64, on: true}});
        }""")
        page.wait_for_timeout(250)
        page.evaluate("""() => {
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 60, on: false}});
          __msg({type: "midi", event: {kind: "tap", src: "arp", note: 64, on: false}});
        }""")
        page.wait_for_timeout(100)
        closed = page.evaluate(
            "tapNotes.filter(e => e.t1 !== null).length + '/' + tapNotes.length")
        check("on+off tap pairs close (per src)", closed == "2/2", closed)

        # drawn note rects: 2 bars now, receding — the rightmost lit pixel
        # must move LEFT over time once the notes are closed
        def right_edge():
            return page.evaluate("""(() => {
              const n = [...nodes.values()].find(x => x.montype === 'notes');
              const cv = n.el.querySelector('canvas.vz');
              const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
              let right = -1;
              for (let x = cv.width - 1; x >= 0 && right < 0; x--)
                for (let y = 0; y < cv.height; y++) {
                  const i = (y * cv.width + x) * 4;
                  // note bars are colored fills (not the gray hairline grid)
                  if (d[i+3] > 100 && (d[i] > 60 || d[i+1] > 90) &&
                      Math.abs(d[i] - d[i+1]) + Math.abs(d[i+1] - d[i+2]) > 40) return x;
                }
              return right;
            })()""")
        r1 = right_edge()
        page.wait_for_timeout(700)
        r2 = right_edge()
        check("closed notes recede across the roll", r1 > 0 and r2 < r1,
              f"r1={r1} r2={r2}")

        # re-fired note while still open must not leak a duplicate open entry
        page.evaluate("""() => {
          tapNotes.length = 0;
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 72, on: true}});
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 72, on: true}});
        }""")
        opens = page.evaluate("tapNotes.filter(e => e.t1 === null).length")
        check("re-fired open note leaves exactly ONE open entry", opens == 1,
              f"opens={opens}")
        page.evaluate("""() => {
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 72, on: false}});
        }""")
        opens = page.evaluate("tapNotes.filter(e => e.t1 === null).length")
        check("its off closes the roll completely", opens == 0, f"opens={opens}")

        # =====================================================================
        # A3 — waveform monitor: FIXED-length rolling window
        # =====================================================================
        page.evaluate("buildMonitor('wave'); afterMonitor()")
        page.wait_for_timeout(150)
        page.evaluate("""() => {
          const mon = [...nodes.values()].find(n => n.montype === 'wave');
          const w = wires.find(v => v.sig === 'audio' && v.from.node.gid === 'm:pluck');
          spliceWaveIntoAudio(w, mon);
        }""")
        page.wait_for_timeout(100)
        lens = []
        for i in range(4):
            page.evaluate("""() => {
              const s = [];
              for (let i = 0; i < 512; i++) s.push(0.3 + 0.1 * Math.sin(i));
              __msg({type: "scope_data", key: "pluck", sr: 44100, samples: s});
            }""")
            page.wait_for_timeout(330)
            lens.append(page.evaluate("""(() => {
              const mon = [...nodes.values()].find(n => n.montype === 'wave');
              return (mon.waveHist || []).length;
            })()"""))
        check("wavemon history length is CONSTANT (rolling window, not append)",
              len(set(lens)) == 1 and lens[0] > 0, f"lens={lens}")
        blocks = page.evaluate("""(() => {
          const mon = [...nodes.values()].find(n => n.montype === 'wave');
          const cv = mon.el.querySelector('canvas.vz');
          const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
          // count lit columns: with a fixed window the envelope must NOT be a
          // handful of giant blocks
          let lit = 0;
          const y = (cv.height / 2) | 0;
          for (let x = 0; x < cv.width; x++)
            if (d[(y * cv.width + x) * 4 + 3] > 100) lit++;
          return {w: cv.width, lit};
        })()""")
        check("wavemon draws thin rolling bars (fills right, no fat packing)",
              blocks["lit"] < blocks["w"], str(blocks))

        # =====================================================================
        # A4 — deck viz: paired notes draw bounded bars
        # =====================================================================
        page.evaluate("""() => {
          const deck = nodes.get('deck');
          if (!deck.expanded) deck.el.querySelector('.expander')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.wait_for_timeout(250)
        deckpx = page.evaluate("""(() => {
          const cv = nodes.get('deck').el.querySelector('canvas.vz');
          if (!cv || !cv.width) return null;
          const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
          let cols = 0;
          for (let x = 0; x < cv.width; x++) {
            for (let y = 0; y < cv.height; y++) {
              const i = (y * cv.width + x) * 4;
              if (d[i] > 150 && d[i+1] < 120 && d[i+3] > 100) { cols++; break; }
            }
          }
          return {w: cv.width, cols, frac: cols / cv.width};
        })()""")
        check("deck bars stay bounded (paired notes, no full-width smear)",
              deckpx and 0.05 < deckpx["frac"] < 0.6, str(deckpx))

        # =====================================================================
        # B — key shifter card
        # =====================================================================
        pal = page.evaluate(
            "[...document.querySelectorAll('#palette button')].map(b => b.textContent)")
        check("palette has a Key Shifter entry",
              any("Key Shifter" in t for t in pal), str(pal))
        page.evaluate("window.__sent.length = 0")
        page.click("#palette button:has-text('Key Shifter')")
        sent = page.evaluate("window.__sent")
        check("palette Key Shifter → spawn_keyshift",
              {"type": "spawn_keyshift"} in sent, str(sent))

        ks = page.evaluate("""(() => {
          const n = nodes.get('keyshift');
          if (!n) return {ok: false};
          const ins = n.ports.filter(p => p.dir === 'in' && p.sig === 'ctl' && p.lane);
          const outs = n.ports.filter(p => p.dir === 'out' && p.sig === 'ctl' && p.lane);
          return {ok: true, ins: ins.map(p => p.lane), outs: outs.map(p => p.lane),
                  chips: [...n.el.querySelectorAll('.mini label')].map(l => l.textContent)};
        })()""")
        check("keyshift card renders 4 in + 4 out lane ports",
              ks.get("ok") and ks.get("ins") == [1, 2, 3, 4]
              and ks.get("outs") == [1, 2, 3, 4], str(ks))
        check("keyshift card has key + length chips",
              {"key", "length"} <= set(ks.get("chips", [])), str(ks))

        # key chip → popover with 12 pitch classes; pick G → set_keyshift key=7
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('keyshift');
          const rows = [...n.el.querySelectorAll('.mini')];
          const kr = rows.find(r => r.querySelector('label').textContent === 'key');
          kr.querySelector('.chip').dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.wait_for_timeout(100)
        pop = page.evaluate("""(() => {
          const el = document.getElementById('keypop');
          if (!el) return {ok: false};
          return {ok: true, keys: [...el.querySelectorAll('button')].map(b => b.textContent)};
        })()""")
        check("key chip opens the 12-key popover",
              pop.get("ok") and len(pop.get("keys", [])) == 12 and
              "A#/Bb" in pop.get("keys", []) and "C" in pop.get("keys", []), str(pop))
        page.evaluate("""() => {
          [...document.querySelectorAll('#keypop button')]
            .find(b => b.textContent === 'G')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("popover key click sends set_keyshift key=7 (G)",
              any(m.get("type") == "set_keyshift" and m.get("id") == "keyshift"
                  and m.get("key") == 7 for m in sent), str(sent[-2:]))
        page.mouse.click(1300, 1100)   # click-away
        page.wait_for_timeout(100)
        check("click-away closes the popover",
              page.evaluate("!document.getElementById('keypop')"))

        # step strip: expand, click step 2, assign D via popover, click to clear
        page.evaluate("""() => {
          const n = nodes.get('keyshift');
          if (!n.expanded) n.el.querySelector('.expander')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.wait_for_timeout(200)
        nsteps = page.evaluate(
            "nodes.get('keyshift').el.querySelectorAll('.ksstep').length")
        check("step strip renders one step per bar", nsteps == 4, f"nsteps={nsteps}")
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          nodes.get('keyshift').el.querySelectorAll('.ksstep')[2]
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.wait_for_timeout(80)
        check("step click opens the key popover",
              page.evaluate("!!document.getElementById('keypop')"))
        page.evaluate("""() => {
          [...document.querySelectorAll('#keypop button')]
            .find(b => b.textContent === 'D')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        ok = any(m.get("type") == "set_keyshift" and (m.get("steps") or [None]*4)[2] == 2
                 for m in sent)
        check("assigning a step sends set_keyshift steps[2]=D", ok, str(sent[-2:]))
        lbl = page.evaluate(
            "nodes.get('keyshift').el.querySelectorAll('.ksstep')[2].textContent")
        check("assigned step shows its key letter", lbl == "D", lbl)
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {   // click assigned step: select…
          nodes.get('keyshift').el.querySelectorAll('.ksstep')[2]
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.evaluate("""() => {   // …click again: clear
          nodes.get('keyshift').el.querySelectorAll('.ksstep')[2]
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        ok = any(m.get("type") == "set_keyshift" and (m.get("steps") or [0])[2] is None
                 for m in sent)
        check("re-clicking the assigned step clears it", ok, str(sent[-2:]))

        # playing-step highlight follows beat messages (bar % length)
        page.evaluate("__msg({type: 'beat', bar: 6, beat: 0, loop: null})")
        page.wait_for_timeout(100)
        ph = page.evaluate("""[...nodes.get('keyshift').el.querySelectorAll('.ksstep')]
            .findIndex(b => b.classList.contains('ph'))""")
        check("playing step highlighted from beat messages (bar 6 % 4 = 2)",
              ph == 2, f"ph={ph}")

        # two lanes independently wired: rebuild with lane wires in state
        page.evaluate("""(s) => {
          s.ctl_wires = [
            {from: "keys", to: "keyshift:1"},
            {from: "keyshift:1", to: "voice"},
            {from: "arp", to: "keyshift:2"},
            {from: "keyshift:2", to: "deck"},
          ];
          __msg({type: "state", ...s});
        }""", STATE)
        page.wait_for_timeout(400)
        lanes = page.evaluate("""(() => {
          const ws = wires.filter(w => w.sig === 'ctl' &&
            (w.from.node.gid === 'keyshift' || w.to.node.gid === 'keyshift'));
          return ws.map(w => [w.from.node.gid, w.from.port.lane || null,
                              w.to.node.gid, w.to.port.lane || null]);
        })()""")
        check("lane wires land on their OWN port handles",
              sorted(map(str, lanes)) == sorted(map(str, [
                  ["keys", None, "keyshift", 1], ["keyshift", 1, "voice", None],
                  ["arp", None, "keyshift", 2], ["keyshift", 2, "deck", None]])),
              str(lanes))
        sent = page.evaluate("""(() => {
          const w = wires.find(v => v.sig === 'ctl' && v.from.node.gid === 'keyshift'
                               && v.from.port.lane === 2);
          window.__sent.length = 0;
          w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          return window.__sent.filter(m => m.type === 'ctl_wire');
        })()""")
        check("cutting a lane wire sends the lane endpoint",
              {"type": "ctl_wire", "action": "remove", "from": "keyshift:2",
               "to": "deck"} in sent, str(sent))

        # port drag: keys ctl out → keyshift in.2 = ctl_wire add "keyshift:2"
        page.evaluate("window.__sent.length = 0")
        drag = page.evaluate("""() => {
          const a = nodes.get('keys'), b = nodes.get('keyshift');
          const po = a.ports.find(p => p.dir === 'out' && p.sig === 'ctl');
          const pi = b.ports.find(p => p.dir === 'in' && p.lane === 2);
          const r = world.getBoundingClientRect();
          const [x1, y1] = portXY(a, po), [x2, y2] = portXY(b, pi);
          return [x1 + r.left, y1 + r.top, x2 + r.left, y2 + r.top];
        }""")
        page.mouse.move(drag[0], drag[1])
        page.mouse.down()
        page.mouse.move(drag[2], drag[3], steps=8)
        page.mouse.up()
        sent = page.evaluate("window.__sent")
        check("port drag onto in.2 wires lane 2",
              {"type": "ctl_wire", "action": "add", "from": "keys",
               "to": "keyshift:2"} in sent, str(sent[-3:]))
        # …and a keyshift lane out must NOT land on a tonic/audio-style port
        bad = page.evaluate("""(() => {
          const out = {node: nodes.get('keyshift'),
                       port: nodes.get('keyshift').ports.find(p => p.dir === 'out' && p.lane === 1)};
          const inn = {node: nodes.get('m:pluck'),
                       port: nodes.get('m:pluck').ports.find(p => p.dir === 'out')};
          return !!connectAction(out, inn);
        })()""")
        check("lane out cannot land on another out", bad is False)

        check("no page errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/flexv6.png", full_page=False)
        print("screenshot → /tmp/flexv6.png")
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
