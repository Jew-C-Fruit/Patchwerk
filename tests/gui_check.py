"""Headless GUI check for flex.html (no synth server needed).

    python tests/gui_check.py

Stubs WebSocket before load, injects a v5 mock state (instance ids +
types, duplicate lowpass instances, two mono voices, a tonic deriver
wired into a drone instance), then drives the page: verifies no
pageerrors, two lowpass cards render independently (param edits address
their own ids), the voice.2 card exists and is removable, the tonic wire
draws in its own amber family with a legend entry, the deriver card
renders its knobs + root readout, the drone card carries the tonic-in
handle + follow toggle, the palette RETAINS placed modules (click = fresh
instance), and the classic wire cut / port-drag / splice behaviors still
work. Screenshots to /tmp/flexv5.png.
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


STATE = {
    "patch": "mock", "patches": ["mock"], "volume": 0.8,
    "devices": {"inputs": [], "outputs": []}, "current_input": None,
    "current_output": None, "input_enabled": False, "boot_note": None,
    # v5 chain: DUPLICATE lowpass instances + an ordinary drone instance
    "chain": [
        mod("pluck", "Pluck", "source", "voice",
            {"amp": param(), "freq": param(220, 20, 2000), "gate": param(0)}),
        mod("lowpass", "Lowpass", "effect", "filter",
            {"cutoff": param(0.3), "res": param(0.2)}),
        mod("lowpass.2", "Lowpass 2", "effect", "filter",
            {"cutoff": param(0.7), "res": param(0.5)}),
        mod("echo", "Echo", "effect", "time"),
        mod("drone", "Drone", "source", "service",
            {"freq": param(55, 16, 500), "amp": param(0.16)},
            tonic_follow=True),
        mod("scope_tap", "Scope Tap", "effect", "io",
            {"gain": param(1.0, 0.0, 2.0)}),
    ],
    "wires": [
        {"from": "pluck", "to": "lowpass"}, {"from": "lowpass", "to": "lowpass.2"},
        {"from": "lowpass.2", "to": "echo"}, {"from": "echo", "to": "master"},
        {"from": "drone", "to": "scope_tap"}, {"from": "scope_tap", "to": "master"},
    ],
    # v5 control plane: two voices + a tonic deriver driving the drone
    "ctl_wires": [
        {"from": "keys", "to": "arp"},
        {"from": "arp", "to": "voice"},
        {"from": "arp", "to": "deck"},
        {"from": "deck", "to": "voice"},
        {"from": "keys", "to": "voice.2"},
        {"from": "arp", "to": "tonic"},
        {"from": "tonic", "to": "drone"},
    ],
    "drums_target": "echo",
    "voice_target": "pluck",
    "voices": [{"id": "voice", "target": "pluck"},
               {"id": "voice.2", "target": "pluck"}],
    "tonics": [{"id": "tonic", "every": "1 bar",
                "everies": ["1 beat", "2 beats", "1 bar", "2 bars", "4 bars"],
                "octave": 2, "root": "C"}],
    "transpose": 0,
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
    # v5: `available` lists EVERY module — placed ones stay in the palette
    "available": [
        {"key": "pluck", "name": "Pluck", "kind": "source", "family": "voice"},
        {"key": "drone", "name": "Drone", "kind": "source", "family": "service"},
        {"key": "lowpass", "name": "Lowpass", "kind": "effect", "family": "filter"},
        {"key": "chorus", "name": "Chorus", "kind": "effect", "family": "time"},
        {"key": "fm_bell", "name": "FM Bell", "kind": "source", "family": "voice"},
    ],
    "module_errors": {},
}

LAYOUT = {"pos": {
    "m:pluck": [480, 72], "m:lowpass": [480, 360], "m:lowpass.2": [480, 648],
    "m:echo": [480, 936], "m:drone": [960, 648], "m:scope_tap": [1200, 648],
    "master": [720, 1200], "drums": [1416, 96],
    "keys": [216, 72], "arp": [216, 288], "deck": [216, 504],
    "voice": [216, 768], "voice.2": [216, 984], "tonic": [960, 240],
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
            close() { this.readyState = 3; this.onclose && this.onclose(); }
          };
          localStorage.setItem("supersynth.flex.mock", %s);
        """ % json.dumps(json.dumps(LAYOUT)))
        page.goto(FLEX.as_uri())
        page.wait_for_timeout(300)

        page.evaluate("""(s) => {
          const ws = window.__wss[0];
          ws.onmessage({data: JSON.stringify({type: "state", ...s})});
        }""", STATE)
        page.wait_for_timeout(500)

        # ---- v5: duplicate instances render as independent cards ----
        two = page.evaluate("""(() => {
          const a = nodes.get('m:lowpass'), b = nodes.get('m:lowpass.2');
          if (!a || !b) return {ok: false};
          return {ok: true, aTitle: a.el.querySelector('.title').textContent,
                  bTitle: b.el.querySelector('.title').textContent,
                  aSub: a.el.querySelector('.sub').textContent,
                  bSub: b.el.querySelector('.sub').textContent};
        })()""")
        check("two lowpass cards render", two.get("ok"), str(two))
        check("second instance shows the ' 2' display suffix",
              two.get("bTitle") == "Lowpass 2" and two.get("aTitle") == "Lowpass")
        check("both cards show the TYPE in the sub-label",
              "lowpass ·" in two.get("aSub", "") and "lowpass ·" in two.get("bSub", ""))

        # param edits address each instance's own id
        for gid, key in (("m:lowpass", "lowpass"), ("m:lowpass.2", "lowpass.2")):
            page.evaluate("window.__sent.length = 0")
            box = page.evaluate("""(gid) => {
              const n = nodes.get(gid);
              const tr = n.el.querySelector('.mini .track');
              const r = tr.getBoundingClientRect();
              return [r.left + r.width * 0.9, r.top + r.height / 2];
            }""", gid)
            page.mouse.move(box[0], box[1])
            page.mouse.down()
            page.mouse.up()
            page.wait_for_timeout(80)
            sent = page.evaluate("window.__sent")
            ok = any(m.get("type") == "set_param" and m.get("key") == key
                     and m.get("name") == "cutoff" for m in sent)
            check(f"slider on {gid} sends set_param key={key}", ok, str(sent[-3:]))

        # audio wires between the twins mirror server truth
        seq = page.evaluate("""
          !!wires.find(w => w.sig==='audio' && w.from.node.gid==='m:lowpass'
                       && w.to.node.gid==='m:lowpass.2')""")
        check("lowpass → lowpass.2 audio wire drawn", seq)

        # ---- v5: multiple mono voices ----
        v2 = page.evaluate("""(() => {
          const v = nodes.get('voice'), v2 = nodes.get('voice.2');
          if (!v || !v2) return {ok: false};
          return {ok: true, name: v2.el.querySelector('.title').textContent,
                  vKill: !!v.el.querySelector('.kill'),
                  v2Kill: !!v2.el.querySelector('.kill')};
        })()""")
        check("voice.2 card renders", v2.get("ok") and v2.get("name") == "Mono Voice 2",
              str(v2))
        check("only the spawned voice is removable",
              v2.get("v2Kill") and not v2.get("vKill"))
        page.evaluate("""() => {
          nodes.get('voice.2').el.querySelector('.kill')
            .dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("voice.2 kill sends remove_voice",
              {"type": "remove_voice", "id": "voice.2"} in sent)
        has = page.evaluate("""
          !!wires.find(w => w.sig==='ctl' && w.from.node.gid==='keys'
                       && w.to.node.gid==='voice.2')""")
        check("keys→voice.2 ctl wire drawn", has)
        # each voice has a derived target wire
        vt = page.evaluate("""
          wires.filter(w => w.sig==='ctl' &&
            (w.from.node.gid==='voice' || w.from.node.gid==='voice.2') &&
            w.to.node.gid==='m:pluck').length""")
        check("both voices draw their target wires", vt == 2, f"vt={vt}")

        # palette voices group spawns voices
        page.evaluate("window.__sent.length = 0")
        page.click("#palette button:has-text('Mono Voice')")
        sent = page.evaluate("window.__sent")
        check("palette Mono Voice → spawn_voice", {"type": "spawn_voice"} in sent)

        # ---- v5: tonic deriver + tonic wire family ----
        ton = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          if (!n) return {ok: false};
          const chips = [...n.el.querySelectorAll('.mini label')].map(l => l.textContent);
          const root = [...n.el.querySelectorAll('.mini .chip')].map(c => c.textContent);
          return {ok: true, chips, root,
                  ctlIn: !!n.ports.find(p => p.dir==='in' && p.sig==='ctl'),
                  ctlOut: !!n.ports.find(p => p.dir==='out' && p.sig==='ctl'),
                  tonicOut: !!n.ports.find(p => p.dir==='out' && p.sig==='tonic')};
        })()""")
        check("deriver card renders every/octave/root",
              ton.get("ok") and {"every", "octave", "root"} <=
              set(ton.get("chips", [])), str(ton))
        check("deriver root readout shows the root", "C" in ton.get("root", []))
        check("deriver has ctl in + thru out + TONIC out",
              ton.get("ctlIn") and ton.get("ctlOut") and ton.get("tonicOut"))

        tw = page.evaluate("""(() => {
          const w = wires.find(v => v.sig === 'tonic');
          if (!w) return {ok: false};
          return {ok: true, from: w.from.node.gid, to: w.to.node.gid,
                  color: w.color, amber: LINES.tonic.includes(w.color),
                  cuttable: w.hitEl.classList.contains('cuttable')};
        })()""")
        check("tonic→drone wire drawn in the tonic family",
              tw.get("ok") and tw.get("from") == "tonic" and
              tw.get("to") == "m:drone" and tw.get("amber"), str(tw))
        check("tonic wire is cuttable (ctl_wire remove)", tw.get("cuttable"))
        check("legend gained a tonic entry",
              page.evaluate("!!document.querySelector('[data-legend=tonic]')"))

        # deriver knob sends set_tonic
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('tonic');
          const rows = [...n.el.querySelectorAll('.mini')];
          const every = rows.find(r => r.querySelector('label').textContent === 'every');
          every.querySelector('.chip').dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("deriver every chip sends set_tonic",
              any(m.get("type") == "set_tonic" and m.get("id") == "tonic"
                  for m in sent), str(sent))

        # drone card: tonic-in handle + follow toggle
        dr = page.evaluate("""(() => {
          const n = nodes.get('m:drone');
          if (!n) return {ok: false};
          const chips = [...n.el.querySelectorAll('.mini label')].map(l => l.textContent);
          return {ok: true, chips,
                  tonicIn: !!n.ports.find(p => p.dir==='in' && p.sig==='tonic'),
                  audioOut: !!n.ports.find(p => p.dir==='out' && p.sig==='audio')};
        })()""")
        check("drone card has tonic-in + audio out + follow chip",
              dr.get("ok") and dr.get("tonicIn") and dr.get("audioOut")
              and "follow" in dr.get("chips", []), str(dr))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('m:drone');
          const rows = [...n.el.querySelectorAll('.mini')];
          const f = rows.find(r => r.querySelector('label').textContent === 'follow');
          f.querySelector('.chip').dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("follow toggle sends drone_follow",
              {"type": "drone_follow", "id": "drone", "on": False} in sent, str(sent))

        # tonic port drag: deriver TONIC out → drone tonic-in = ctl_wire add
        page.evaluate("window.__sent.length = 0")
        drag = page.evaluate("""() => {
          const a = nodes.get('tonic'), b = nodes.get('m:drone');
          const po = a.ports.find(p => p.dir === 'out' && p.sig === 'tonic');
          const pi = b.ports.find(p => p.dir === 'in' && p.sig === 'tonic');
          const r = world.getBoundingClientRect();
          const [x1, y1] = portXY(a, po), [x2, y2] = portXY(b, pi);
          return [x1 + r.left, y1 + r.top, x2 + r.left, y2 + r.top];
        }""")
        page.mouse.move(drag[0], drag[1])
        page.mouse.down()
        page.mouse.move(drag[2], drag[3], steps=8)
        page.mouse.up()
        sent = page.evaluate("window.__sent")
        check("tonic port drag → ctl_wire add tonic→drone",
              {"type": "ctl_wire", "action": "add", "from": "tonic", "to": "drone"}
              in sent, str(sent[-3:]))
        # ...and a ctl out must NOT land on a tonic in (type rule)
        landable = page.evaluate("""(() => {
          const out = {node: nodes.get('keys'),
                       port: nodes.get('keys').ports.find(p => p.dir==='out')};
          const inn = {node: nodes.get('m:drone'),
                       port: nodes.get('m:drone').ports.find(p => p.sig==='tonic')};
          return !!connectAction(out, inn);
        })()""")
        check("ctl out cannot land on a tonic in", landable is False)

        # ---- v5: the palette retains placed modules ----
        pal = page.evaluate("""
          [...document.querySelectorAll('#palette button')].map(b => b.textContent)""")
        check("palette retains placed Lowpass",
              any("Lowpass" in t for t in pal), str(pal))
        check("palette retains placed Pluck & Drone",
              any("Pluck" in t for t in pal) and any("Drone" in t for t in pal))
        check("palette has Tonic Deriver + Mono Voice entries",
              any("Tonic Deriver" in t for t in pal) and
              any("Mono Voice" in t for t in pal))
        page.evaluate("window.__sent.length = 0")
        page.click("#palette button:has-text('Lowpass')")
        sent = page.evaluate("window.__sent")
        check("clicking a placed palette entry spawns a fresh instance",
              {"type": "spawn_module", "key": "lowpass"} in sent, str(sent))
        check("client predicts the server's next id",
              page.evaluate("nextId('lowpass')") == "lowpass.3" and
              page.evaluate("nextId('chorus')") == "chorus")

        # ---- classic behaviors still hold (id-aware) ----
        page.evaluate("""() => {
          const w = wires.find(v => v.sig === 'ctl' && v.from.node.gid === 'keys'
                               && v.to.node.gid === 'arp');
          w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("cut ctl wire → ctl_wire remove",
              {"type": "ctl_wire", "action": "remove", "from": "keys", "to": "arp"}
              in sent, str(sent[-3:]))

        page.evaluate("""() => {
          const w = wires.find(v => v.sig === 'audio' && v.from.node.gid === 'm:lowpass');
          w.hitEl.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("cut audio wire → graph_wire remove (by id)",
              {"type": "graph_wire", "action": "remove", "from": "lowpass"} in sent)

        # palette DRAG onto a wire → splice with the PREDICTED id
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
        page.screenshot(path="/tmp/flexv5.png", full_page=False)
        print("screenshot → /tmp/flexv5.png")
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
