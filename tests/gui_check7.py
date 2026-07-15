"""Headless GUI check for flex.html v7 (no synth server needed).

    python tests/gui_check7.py [path/to/flex.html]

v7 closure regressions (written to FAIL on the broken behavior):
  * DECK viz never draws a full-width bar: unpaired-on segments cap to the
    playhead (recording) or a short stub (playing); orphan offs are dropped;
    genuine wrap pairs still draw head + tail.
  * NOTE MONITOR keys taps always close: keyup, octave change, blur,
    caps-sustain release, keys-checkbox off, ⌘/ctrl modifier while holding
    (macOS swallows those keyups), and messages queued across a websocket
    reconnect gap are flushed, not dropped; a fresh socket closes any taps
    left open from before it.
"""

import glob
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
FLEX = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "gui" / "flex.html"
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


def mod(key, name, kind, family, params=None, **extra):
    return {"key": key, "type": key.split(".")[0], "name": name,
            "kind": kind, "family": family, "enabled": True, "service": False,
            "params": params or {"amp": param()}, **extra}


STATE = {
    "patch": "mock", "patches": ["mock"], "volume": 0.8,
    "devices": {"inputs": [], "outputs": []}, "current_input": None,
    "current_output": None, "input_enabled": False, "boot_note": None,
    "chain": [
        mod("pluck", "Pluck", "source", "voice",
            {"amp": param(), "freq": param(220, 20, 2000), "gate": param(0)}),
        mod("echo", "Echo", "effect", "time"),
    ],
    "wires": [{"from": "pluck", "to": "echo"}, {"from": "echo", "to": "master"}],
    "ctl_wires": [
        {"from": "keys", "to": "arp"}, {"from": "arp", "to": "voice"},
        {"from": "arp", "to": "deck"}, {"from": "deck", "to": "voice"},
    ],
    "drums_target": None, "voice_target": "pluck",
    "voices": [{"id": "voice", "target": "pluck"}],
    "tonics": [], "keyshifts": [], "transpose": 0,
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
               "position": "post", "loop_beats": 8, "notes": []},
    "lfos": [], "presets": [],
    "available": [
        {"key": "pluck", "name": "Pluck", "kind": "source", "family": "voice"},
        {"key": "echo", "name": "Echo", "kind": "effect", "family": "time"},
    ],
    "module_errors": {},
}


def deck_red_frac(page):
    """Fraction of deck-viz columns containing a red note bar."""
    return page.evaluate("""(() => {
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
      return cols / cv.width;
    })()""")


def set_deck_notes(page, notes, state="playing"):
    page.evaluate("""(args) => {
      state.looper.notes = args.notes;
      state.looper.state = args.st;
    }""", {"notes": notes, "st": state})
    page.wait_for_timeout(120)   # a couple of rAF frames


def main():
    print(f"checking {FLEX}")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1700, "height": 1250})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("""
          window.__sent = [];
          window.__wss = [];
          window.__nextConnecting = false;  // next socket stays CONNECTING
          window.WebSocket = class {
            constructor(url) { this.url = url;
              this.readyState = window.__nextConnecting ? 0 : 1;
              window.__wss.push(this);
              if (this.readyState === 1)
                setTimeout(() => this.onopen && this.onopen(), 0); }
            send(d) { window.__sent.push(JSON.parse(d)); }
            close() {}   // watchdog churn isn't under test (see gui_check6)
          };
        """)
        page.goto(FLEX.as_uri())
        page.wait_for_timeout(300)
        page.evaluate("""(s) => {
          window.__msg = (m) => window.__wss[0].onmessage({data: JSON.stringify(m)});
          __msg({type: "state", ...s});
        }""", STATE)
        page.wait_for_timeout(400)
        page.evaluate("""() => {
          const deck = nodes.get('deck');
          if (!deck.expanded) deck.el.querySelector('.expander')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        page.wait_for_timeout(250)

        # ---- A. deck viz: full-width bars are impossible -------------------
        set_deck_notes(page, [[0, 60, True], [1, 60, False],
                              [2, 64, True], [3, 64, False]])
        f = deck_red_frac(page)
        check("paired take draws bounded bars", f and 0.05 < f < 0.5, f"frac={f}")

        # unpaired ON while PLAYING → short stub, never the full track
        set_deck_notes(page, [[0, 60, True]])
        f = deck_red_frac(page)
        check("unpaired on (playing) draws a stub, NOT a full-width bar",
              f is not None and f < 0.2, f"frac={f}")

        # orphan OFF → dropped entirely (no bogus 0→beat bar)
        set_deck_notes(page, [[6.5, 64, False]])
        f = deck_red_frac(page)
        check("orphan off draws nothing", f == 0, f"frac={f}")

        # genuine wrap (on late, off early) still draws head + tail
        set_deck_notes(page, [[1.0, 60, False], [7.0, 60, True]])
        wrap = page.evaluate("""(() => {
          const cv = nodes.get('deck').el.querySelector('canvas.vz');
          const d = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height).data;
          const red = (x) => {
            for (let y = 0; y < cv.height; y++) {
              const i = (y * cv.width + x) * 4;
              if (d[i] > 150 && d[i+1] < 120 && d[i+3] > 100) return true;
            }
            return false;
          };
          const W = cv.width;
          return {head: red((0.05 * W) | 0), tail: red((0.95 * W) | 0),
                  mid: red((0.5 * W) | 0)};
        })()""")
        check("wrapped note draws tail + head, not the middle",
              wrap["head"] and wrap["tail"] and not wrap["mid"], str(wrap))

        # unpaired ON while RECORDING caps to the playhead
        page.evaluate("__msg({type: 'beat', bar: 0, beat: 1, loop: 2.0})")
        set_deck_notes(page, [[0.5, 60, True]], state="recording")
        f = deck_red_frac(page)
        check("open note under the record head caps to the playhead",
              f is not None and 0.05 < f < 0.45, f"frac={f}")
        set_deck_notes(page, [], state="playing")

        # ---- B. note monitor: every keys off path --------------------------
        page.evaluate("buildMonitor('notes'); afterMonitor()")
        page.wait_for_timeout(150)

        def sent(clear=False):
            msgs = page.evaluate("window.__sent")
            if clear:
                page.evaluate("window.__sent.length = 0")
            return msgs

        def clear_sent():
            page.evaluate("window.__sent.length = 0")

        # keyup sends note_off
        clear_sent()
        page.keyboard.down("a")
        page.keyboard.up("a")
        msgs = sent()
        check("keyup sends note_off",
              {"type": "note_on", "note": 60} in msgs and
              {"type": "note_off", "note": 60} in msgs, str(msgs[-3:]))

        # octave change while holding → all_notes_off
        clear_sent()
        page.keyboard.down("a")
        page.keyboard.down("z")
        page.keyboard.up("z")
        page.keyboard.up("a")
        check("octave change panics held notes",
              {"type": "all_notes_off"} in sent(True))

        # ⌘ (Meta) while holding → all_notes_off (macOS swallows the keyup)
        page.keyboard.down("a")
        clear_sent()
        page.keyboard.down("Meta")
        msgs = sent()
        page.keyboard.up("Meta")
        page.keyboard.up("a")
        check("modifier during held notes panics (swallowed-keyup defense)",
              {"type": "all_notes_off"} in msgs, str(msgs))

        # blur → all_notes_off
        page.keyboard.down("a")
        clear_sent()
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        check("window blur panics held notes",
              {"type": "all_notes_off"} in sent(True))
        page.keyboard.up("a")

        # keys checkbox off → all_notes_off
        clear_sent()
        page.evaluate("""() => {
          const cb = document.getElementById('keys-on');
          cb.checked = false;
          cb.dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        check("keys toggle off panics", {"type": "all_notes_off"} in sent(True))
        page.evaluate("document.getElementById('keys-on').checked = true")

        # caps sustain release → sustain off + all_notes_off
        clear_sent()
        page.evaluate("""() => {
          window.dispatchEvent(new KeyboardEvent('keydown', {key: 'CapsLock'}));
          window.dispatchEvent(new KeyboardEvent('keyup', {key: 'CapsLock'}));
        }""")
        msgs = sent(True)
        check("caps release sends sustain-off + panic",
              {"type": "sustain", "on": True} in msgs and
              {"type": "sustain", "on": False} in msgs and
              {"type": "all_notes_off"} in msgs, str(msgs))

        # ---- C. reconnect gap: sends queue and flush, opens close ----------
        clear_sent()
        page.evaluate("""() => {
          __msg({type: "midi", event: {kind: "tap", src: "keys", note: 72, on: true}});
          window.__wss[0].readyState = 2;   // socket dying: sends would drop
          window.__nextConnecting = true;   // the reconnect hangs mid-handshake
        }""")
        page.keyboard.down("a")
        page.keyboard.up("a")
        gap = sent()
        check("nothing leaks onto a dead socket", gap == [], str(gap))
        page.evaluate("""() => {
          const w = window.__wss[window.__wss.length - 1];  // reconnected
          w.readyState = 1;
          w.onopen();
        }""")
        msgs = sent(True)
        ons = [m for m in msgs if m.get("type") == "note_on"]
        offs = [m for m in msgs if m.get("type") == "note_off"]
        check("queued note_on/note_off flush on reconnect",
              ons and offs and ons[0]["note"] == offs[0]["note"], str(msgs))
        opens = page.evaluate("tapNotes.filter(e => e.t1 === null).length")
        check("a fresh socket closes taps left open from before it",
              opens == 0, f"opens={opens}")

        check("no page errors", not errors, "; ".join(errors[:3]))
        page.screenshot(path="/tmp/flexv7.png", full_page=False)
        print("screenshot → /tmp/flexv7.png")
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
