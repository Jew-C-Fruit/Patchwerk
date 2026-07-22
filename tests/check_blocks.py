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
  5. Key Shifter sizing: S/M/L chips present; a LENGTH-32 track renders fully
     inside the card at every size (no overflow), cells stay tappable, the
     chip-set size survives a state rebuild (sizeLocked generalization).
  6. Drone rework: the "tonic" signal kind is fully retired (PRIMARY_SIGS,
     CSS vars, legend, header strip, connectAction); the drone card is an
     ordinary MONO ctl note-sink (play-in, no follow chip); the deriver has
     ONE ctl out; keys→drone and deriver→drone wire as plain ctl; ctl wires
     into drones draw in the ctl family.
  7. Ping (items 4+5): button/clock trigger cards render; the deriver grows
     a QUIET node-scoped trigger-in; ping wires draw in their own pastel
     family; the grammar is strict (ping↮mod/ctl/audio in BOTH directions);
     the pad click + bound computer key fire; pairing binds only UNASSIGNED
     keys (note keys can never bind) and the binding chip updates.
  8. Deriver split (item 6): the Estimator card carries knob rows + the
     12-bar histogram viz that breathes on "deriver" analysis messages
     (presence/scores toggle, committed vs leading marking, confidence);
     the Literal card's chips cycle and send set_literal; both derivers
     take notes/emit notes/accept ping triggers in the grammar.
  10. Threshold (item 8): the card renders from state.thresholds (small,
     level/hyst/edge rows); its cv-in is a QUIET single-input mod handle
     riding the level row; LFO-out → cv-in connects via threshold_wire
     (targeted remove on cut); its ping-out draws/wires like button/clock
     (trigger-ins only); ping events pulse the pad-less card.
  9. Routable LFO (item 7): the LFO is a standalone node (palette spawns
     via spawn_lfo, kill sends remove_lfo); the card has rate/depth/shape
     and NO center row; one card fans out to MANY destinations (a mod wire
     per dest, each cut sending a targeted lfo_wire remove); LFO-out onto
     a param connects via lfo_wire add; a mapped param's slider steers the
     destination's center (locally synced between broadcasts) and its row
     wears the amplitude band; pre-item-7 per-assignment entries (the
     check_real fixture's shape) still render as one-dest legacy cards
     whose wires/kill fall back to lfo_unassign.
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

        # ================================================================
        # 5 — Key Shifter sizing: 32 steps fit at S, M and L
        # ================================================================
        st32 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            keyshifts=[{"id": "keyshift", "key": 0, "length": 32,
                        "steps": [2 if i % 4 == 0 else None
                                  for i in range(32)], "active": 0}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st32)
        page.wait_for_timeout(400)

        chips = page.evaluate(
            "[...nodes.get('keyshift').el.querySelectorAll('.szchips button')]"
            ".map(b => b.dataset.sz)")
        check("keyshift has S/M/L size chips", chips == ["S", "M", "L"],
              str(chips))

        def ks_fit(size):
            return page.evaluate("""(size) => {
              const n = nodes.get('keyshift');
              n.el.querySelector(`.szchips button[data-sz="${size}"]`).click();
              const grid = n.el.querySelector('.ksgrid');
              const steps = [...grid.children];
              const cr = n.el.getBoundingClientRect();
              const last = steps[steps.length - 1].getBoundingClientRect();
              const cell = steps[0].getBoundingClientRect();
              const zs = parseFloat(world.style.zoom) || 1;
              return {size: n.size, count: steps.length,
                      cols: grid.style.gridTemplateColumns,
                      overflow: last.bottom - cr.bottom,
                      cellW: cell.width / zs, cellH: cell.height / zs,
                      visible: steps[0].offsetParent !== null};
            }""", size)

        for size in ("L", "M", "S"):
            r = ks_fit(size)
            page.wait_for_timeout(250)   # let the resize transition settle
            r = ks_fit(size)             # re-measure at rest
            check(f"keyshift@{size}: resize applied", r["size"] == size, str(r))
            check(f"keyshift@{size}: all 32 steps present", r["count"] == 32,
                  str(r))
            check(f"keyshift@{size}: grid inside the card (no overflow)",
                  r["overflow"] <= 1, str(r))
            check(f"keyshift@{size}: cells tappable (>=7px wide, >=6px tall)",
                  r["cellW"] >= 7 and r["cellH"] >= 6, str(r))
            check(f"keyshift@{size}: grid visible", r["visible"], str(r))

        # a step stays clickable at S: click opens the key pop-out
        page.evaluate("""() => {
          nodes.get('keyshift').el.querySelectorAll('.ksstep')[1].click();
        }""")
        page.wait_for_timeout(80)
        pop = page.evaluate(
            "!!document.querySelector('#keypop') && "
            "document.querySelector('#keypop').style.display !== 'none'")
        check("keyshift@S: step click opens the key pop-out", pop)
        page.keyboard.press("Escape")

        # chip-set size survives a rebuild (state resend) via posMem
        page.evaluate("(s) => __msg({type: 'state', ...s})", st32)
        page.wait_for_timeout(400)
        kept = page.evaluate("nodes.get('keyshift').size")
        check("keyshift chip-set size survives rebuild", kept == "S", kept)

        # ================================================================
        # 6 — drone rework: tonic retired; drone is a plain mono ctl sink
        # ================================================================
        drone = mod("drone", "Drone", "source", "service",
                    {"freq": param(55, 16, 500), "amp": param(0.16),
                     "glide": param(1.5, 0.05, 8.0)})
        st_d = base_state(
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
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_d)
        page.wait_for_timeout(500)

        check("PRIMARY_SIGS has no tonic kind",
              page.evaluate("!PRIMARY_SIGS.has('tonic')"))
        check("no --tonic CSS var remains", page.evaluate(
            "!getComputedStyle(document.documentElement)"
            ".getPropertyValue('--tonic').trim()"))
        check("no tonic legend entry", page.evaluate(
            "!document.querySelector('[data-legend=tonic]')"))
        check("no header tonic-root strip", page.evaluate(
            "!document.getElementById('tonic-root')"))

        dports = page.evaluate(
            "nodes.get('m:drone').ports.filter(p => !p.quiet)"
            ".map(p => [p.dir, p.sig, p.label])")
        check("drone in-port is ctl 'play' (no tonic port)",
              ["in", "ctl", "play"] in dports
              and not any(p[1] == "tonic" for p in dports), str(dports))
        check("drone card has no follow chip", page.evaluate(
            "![...nodes.get('m:drone').el.querySelectorAll('label')]"
            ".some(l => l.title === 'follow')"))
        tports = page.evaluate(
            "nodes.get('tonic').ports.map(p => [p.dir, p.sig, p.label])")
        check("deriver has ONE ctl out (root), no tonic/thru",
              tports.count(["out", "ctl", "root"]) == 1
              and not any(p[1] == "tonic" for p in tports), str(tports))

        # the deriver→drone wire from state draws in the ctl family
        wsig = page.evaluate(
            "(wires.find(w => w.to.node.gid === 'm:drone'"
            " && w.sig !== 'audio') || {}).sig")
        check("wire into the drone rides the ctl family", wsig == "ctl", wsig)

        # grammar: keys→drone and deriver→drone connect as ctl; nothing tonic
        acts = page.evaluate("""(() => {
          const drone = nodes.get('m:drone');
          const pi = drone.ports.find(p => p.sig === 'ctl' && p.dir === 'in');
          const mk = (gid) => ({node: nodes.get(gid),
            port: nodes.get(gid).ports.find(p => p.dir === 'out' && p.sig === 'ctl')});
          return {
            keys: !!connectAction(mk('keys'), {node: drone, port: pi}),
            tonic: !!connectAction(mk('tonic'), {node: drone, port: pi}),
          };
        })()""")
        check("keys→drone play-in connects (ctl)", acts["keys"], str(acts))
        check("deriver→drone play-in connects (ctl)", acts["tonic"], str(acts))
        check("drone play-in is single-input (no + handle)", page.evaluate(
            "!portAllowsPlus(nodes.get('m:drone').ports"
            ".find(p => p.sig === 'ctl' && p.dir === 'in'))"))

        # ================================================================
        # 7 — ping: trigger cards, quiet trigger-in, strict grammar
        # ================================================================
        st_p = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "arp"},
                       {"from": "arp", "to": "voice"},
                       {"from": "keys", "to": "tonic"},
                       {"from": "button", "to": "tonic"}],
            tonics=[{"id": "tonic", "every": "1 bar",
                     "everies": ["1 bar"], "octave": 2, "root": None}],
            buttons=[{"id": "button", "binding": None, "armed": False}],
            clocks=[{"id": "clock", "division": "1/4",
                     "divisions": ["1/4", "1/8"]}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_p)
        page.wait_for_timeout(500)

        check("PRIMARY_SIGS gained ping",
              page.evaluate("PRIMARY_SIGS.has('ping')"))
        check("--ping CSS var present", page.evaluate(
            "!!getComputedStyle(document.documentElement)"
            ".getPropertyValue('--ping').trim()"))
        check("ping legend entry present", page.evaluate(
            "!!document.querySelector('[data-legend=ping]')"))
        for gid in ("button", "clock"):
            check(f"trigger card renders: {gid}",
                  page.evaluate(f"nodes.has('{gid}')"))

        trig = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const p = n.ports.find(p => p.sig === 'ping');
          return p ? {dir: p.dir, quiet: !!p.quiet, label: p.label} : null;
        })()""")
        check("deriver has a QUIET node-scoped ping trigger-in",
              trig == {"dir": "in", "quiet": True, "label": "trigger"},
              str(trig))

        # trigger cards are SMALL (Cole, 2026-07-22): both measure into S
        for gid in ("button", "clock"):
            sz = page.evaluate(f"nodes.get('{gid}').size")
            check(f"trigger card {gid} sizes to S", sz == "S", str(sz))

        # the timing row is LABELED "trigger" now (Cole nomenclature; the
        # protocol field stays `every`), and the trigger-in handle rides
        # that row's line — asserted against the RENDERED (cached) layout
        # after the settle pass, not a fresh compute: the estimator handle
        # was rendering 10px stale before the settle reroute existed.
        page.wait_for_timeout(400)   # let the settle pass land
        aln = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const lay = n.lay;                       // RENDERED layout
          const row = [...n.el.querySelectorAll('.mini')].find(
            r => (r.querySelector('label')||{}).title === 'trigger');
          const rowY = n.y + row.offsetTop + row.offsetHeight / 2;
          const t = lay && lay.handles.find(H => H.sig === 'ping');
          return {rowY, trigY: t && t.y};
        })()""")
        check("deriver trigger-in aligns with the trigger row (rendered)",
              aln["trigY"] is not None
              and abs(aln["trigY"] - aln["rowY"]) < 1.0, str(aln))

        wsig = page.evaluate(
            "(wires.find(w => w.from.node.gid === 'button') || {}).sig")
        check("button→deriver wire draws in the ping family",
              wsig == "ping", str(wsig))

        # strict grammar: every cross-kind combination refused, both ways
        combos = page.evaluate("""(() => {
          const p = (node, dir, sig) =>
            ({node, port: {dir, sig, label: sig + '-' + dir}});
          const btn = nodes.get('button'), ton = nodes.get('tonic');
          const sgn = nodes.get('m:signal_gen'), arp = nodes.get('arp');
          return {
            ping_to_trig: !!connectAction(p(btn, 'out', 'ping'),
              {node: ton, port: ton.ports.find(q => q.sig === 'ping')}),
            ping_to_mod:  !!connectAction(p(btn, 'out', 'ping'), p(sgn, 'in', 'mod')),
            mod_to_ping:  !!connectAction(p(sgn, 'out', 'mod'),
              {node: ton, port: ton.ports.find(q => q.sig === 'ping')}),
            ping_to_ctl:  !!connectAction(p(btn, 'out', 'ping'), p(arp, 'in', 'ctl')),
            ctl_to_ping:  !!connectAction(p(nodes.get('keys'), 'out', 'ctl'),
              {node: ton, port: ton.ports.find(q => q.sig === 'ping')}),
            ping_to_audio: !!connectAction(p(btn, 'out', 'ping'),
              p(nodes.get('m:echo'), 'in', 'audio')),
          };
        })()""")
        check("ping-out → trigger-in connects", combos["ping_to_trig"],
              str(combos))
        for bad in ("ping_to_mod", "mod_to_ping", "ping_to_ctl",
                    "ctl_to_ping", "ping_to_audio"):
            check(f"grammar refuses {bad}", not combos[bad], str(combos))

        # pad click fires
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('button').el.querySelector('.pingpad').click()")
        sent = page.evaluate("window.__sent")
        check("pad click sends fire_button",
              {"type": "fire_button", "id": "button"} in sent, str(sent))

        # pairing: arm, then an ASSIGNED (note) key must NOT bind…
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const chip = [...nodes.get('button').el.querySelectorAll('label')]
            .find(l => l.title === 'bind').parentElement
            .querySelector('.chip');
          chip.click();
        }""")
        page.wait_for_timeout(60)
        sent = page.evaluate("window.__sent")
        check("arming sends set_button armed",
              {"type": "set_button", "id": "button", "armed": True} in sent,
              str(sent))
        page.keyboard.press("a")   # a note key — tonal, never binds
        page.wait_for_timeout(60)
        bound = page.evaluate(
            "window.__sent.filter(m => m.type === 'set_button' && m.binding)")
        check("a note key never binds while armed", not bound, str(bound))
        # …then an unassigned key binds and is consumed
        page.keyboard.press("n")
        page.wait_for_timeout(60)
        bound = page.evaluate(
            "window.__sent.filter(m => m.type === 'set_button' && m.binding)")
        check("an unassigned key binds (KeyN)",
              bound and bound[-1]["binding"] == {"kind": "key",
                                                 "code": "KeyN"}, str(bound))

        # the bound key now FIRES the ping (binding kept client-side)
        page.evaluate("window.__sent.length = 0")
        page.keyboard.press("n")
        page.wait_for_timeout(60)
        sent = page.evaluate("window.__sent")
        check("bound key fires the ping",
              {"type": "fire_button", "id": "button"} in sent, str(sent))
        check("bound key does not ALSO play a note",
              not [m for m in sent if m.get("type") == "note_on"], str(sent))

        # ================================================================
        # 8 — deriver split: Estimator viz + knobs, Literal chips, grammar
        # ================================================================
        st_l = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "arp"},
                       {"from": "arp", "to": "voice"},
                       {"from": "keys", "to": "literal"},
                       {"from": "literal", "to": "voice"}],
            tonics=[{"id": "tonic", "every": "1 bar", "everies": ["1 bar"],
                     "octave": 2, "root": "C", "memory": 6.0,
                     "stickiness": 1.25, "bass": 0.06,
                     "listening": "triadic",
                     "listenings": ["triadic", "root+fifth", "chromatic"]}],
            literals=[{"id": "literal", "every": "immediate",
                       "everies": ["immediate", "1 beat", "1 bar"],
                       "extract": "lowest-held",
                       "extracts": ["lowest-held", "highest-held",
                                    "last-played", "first-played"],
                       "place": "absolute",
                       "places": ["absolute", "fold", "transpose"],
                       "fold_octave": 3, "transpose": 0,
                       "hold_on_empty": True, "note": None}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_l)
        page.wait_for_timeout(500)

        est = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const labels = [...n.el.querySelectorAll('label')].map(l => l.title);
          return {name: n.el.querySelector('.title').textContent,
                  cells: n.el.querySelectorAll('.tonic > div').length,
                  labels};
        })()""")
        check("estimator card renders 12 histogram cells",
              est["cells"] == 12, str(est))
        for knob in ("memory", "stickiness", "bass", "listening"):
            check(f"estimator knob row: {knob}", knob in est["labels"],
                  str(est))

        # the Literal's trigger-in also rides its trigger row line
        # (rendered layout, post-settle)
        page.wait_for_timeout(400)
        laln = page.evaluate("""(() => {
          const n = nodes.get('literal');
          const lay = n.lay;
          const row = [...n.el.querySelectorAll('.mini')].find(
            r => (r.querySelector('label')||{}).title === 'trigger');
          const rowY = n.y + row.offsetTop + row.offsetHeight / 2;
          const t = lay && lay.handles.find(H => H.sig === 'ping');
          return {rowY, trigY: t && t.y};
        })()""")
        check("literal trigger-in aligns with the trigger row (rendered)",
              laln["trigY"] is not None
              and abs(laln["trigY"] - laln["rowY"]) < 1.0, str(laln))

        # a deriver analysis message animates the bars + marks root/leading
        page.evaluate("""() => __msg({type: 'deriver', id: 'tonic',
          weights: [1, 0, 0, 0, 0, 0, 0, 0.6, 0, 0, 0, 0],
          scores:  [1, 0, 0, 0, 0, 0, 0, 0.9, 0, 0, 0, 0],
          leading: 7, root: 0, confidence: 0.42})""")
        page.wait_for_timeout(60)
        viz = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const cells = [...n.el.querySelectorAll('.tonic > div')];
          return {h0: cells[0].querySelector('span').style.height,
                  root0: cells[0].classList.contains('root'),
                  lead7: cells[7].classList.contains('lead'),
                  conf: n.el.querySelector('.tconf').textContent};
        })()""")
        check("histogram bars follow the weights", viz["h0"] == "100%",
              str(viz))
        check("committed root marked distinctly", viz["root0"], str(viz))
        check("leading candidate outlined", viz["lead7"], str(viz))
        check("confidence readout shown", "42" in viz["conf"], str(viz))

        # presence/scores toggle swaps the vector
        page.evaluate("""() => {
          nodes.get('tonic').el.querySelector('.tmode').click();
          __msg({type: 'deriver', id: 'tonic',
            weights: [0.2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            scores:  [0.9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            leading: 0, root: 0, confidence: 0.9});
        }""")
        page.wait_for_timeout(60)
        h0 = page.evaluate(
            "nodes.get('tonic').el.querySelector('.tonic > div span')"
            ".style.height")
        check("scores mode renders the score vector", h0 == "90%", h0)

        # literal card chips cycle and send set_literal
        lit = page.evaluate(
            "[...nodes.get('literal').el.querySelectorAll('label')]"
            ".map(l => l.title)")
        for rowlabel in ("trigger", "extract", "place", "value", "on empty"):
            check(f"literal row: {rowlabel}", rowlabel in lit, str(lit))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('literal');
          const chipOf = (title) => [...n.el.querySelectorAll('label')]
            .find(l => l.title === title).parentElement.querySelector('.chip');
          chipOf('extract').click();
          chipOf('place').click();
          chipOf('on empty').click();
        }""")
        sent = page.evaluate("window.__sent")
        check("literal chips send set_literal",
              {"type": "set_literal", "id": "literal",
               "extract": "highest-held"} in sent
              and {"type": "set_literal", "id": "literal",
                   "place": "fold"} in sent
              and {"type": "set_literal", "id": "literal",
                   "hold_on_empty": False} in sent, str(sent))

        # grammar: keys→literal + literal→voice drew; ping→literal connects
        litwires = page.evaluate(
            "wires.filter(w => w.to.node.gid === 'literal' "
            "|| w.from.node.gid === 'literal').map(w => w.sig)")
        check("literal note wires drew (ctl in+out)",
              litwires.count("ctl") == 2, str(litwires))
        trig = page.evaluate("""(() => {
          const n = nodes.get('literal');
          const p = n.ports.find(p => p.sig === 'ping');
          return p ? {dir: p.dir, quiet: !!p.quiet} : null;
        })()""")
        check("literal has a quiet ping trigger-in",
              trig == {"dir": "in", "quiet": True}, str(trig))

        # ================================================================
        # 9 — routable LFO: standalone node, fan-out, center = dest slider
        # ================================================================
        sg_m = mod("signal_gen", "Signal Gen", "source", "voice",
                   {"freq": param(220, 20, 2000), "amp": param(0.5)})
        sg_m["params"]["amp"]["lfo"] = True          # mapped by the LFO below
        echo_m = mod("echo", "Echo", "effect", "time",
                     {"mix": param(0.4)})
        echo_m["params"]["mix"]["lfo"] = True
        st_lfo = base_state(
            [sg_m, echo_m],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            lfos=[{"id": "lfo", "rate": 1.0, "shape": 0, "depth": 0.5,
                   "shapes": ["sine", "tri", "ramp", "square", "s&h"],
                   "dests": [{"key": "signal_gen", "param": "amp",
                              "center": 0.5},
                             {"key": "echo", "param": "mix",
                              "center": 0.3}]},
                  {"id": "lfo.2", "rate": 4.0, "shape": 1, "depth": 0.2,
                   "shapes": ["sine", "tri", "ramp", "square", "s&h"],
                   "dests": []}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_lfo)
        page.wait_for_timeout(500)

        for gid in ("lfo:lfo", "lfo:lfo.2"):
            check(f"LFO card renders: {gid}", page.evaluate(f"nodes.has('{gid}')"))
        rows = page.evaluate(
            "[...nodes.get('lfo:lfo').el.querySelectorAll('label')]"
            ".map(l => l.title)")
        check("LFO card has rate/depth/shape rows",
              all(r in rows for r in ("rate", "depth", "shape")), str(rows))
        check("LFO card has NO center row", "center" not in rows, str(rows))

        modw = page.evaluate(
            "wires.filter(w => w.sig === 'mod' && w.lfoId === 'lfo')"
            ".map(w => w.to.node.gid)")
        check("one LFO fans out to BOTH destinations",
              sorted(modw) == ["m:echo", "m:signal_gen"], str(modw))
        check("the unwired LFO draws no wires", page.evaluate(
            "wires.filter(w => w.lfoId === 'lfo.2').length") == 0)

        # cutting one fan-out wire sends a TARGETED lfo_wire remove
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          wires.find(w => w.lfoId === 'lfo'
                     && w.to.node.gid === 'm:echo').cutAction();
        }""")
        sent = page.evaluate("window.__sent")
        check("wire cut sends lfo_wire remove for that dest only",
              {"type": "lfo_wire", "action": "remove", "id": "lfo",
               "key": "echo", "name": "mix"} in sent, str(sent))

        # LFO-out onto a param's quiet handle connects via lfo_wire add
        page.evaluate("window.__sent.length = 0")
        did = page.evaluate("""(() => {
          const src = nodes.get('lfo:lfo.2');
          const tgt = nodes.get('m:signal_gen');
          const act = connectAction(
            {node: src, port: src.ports.find(p => p.sig === 'mod')},
            {node: tgt, port: tgt.ports.find(p => p.quiet && p.param === 'freq')});
          if (act) act();
          return !!act;
        })()""")
        sent = page.evaluate("window.__sent")
        check("LFO-out → param connects (lfo_wire add)",
              did and {"type": "lfo_wire", "action": "add", "id": "lfo.2",
                       "key": "signal_gen", "name": "freq"} in sent, str(sent))

        # palette spawns via the server; card kill removes via the server
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          [...document.querySelectorAll('#palette button')]
            .find(b => b.textContent.includes('LFO')).click();
        }""")
        sent = page.evaluate("window.__sent")
        check("palette LFO button sends spawn_lfo",
              {"type": "spawn_lfo"} in sent, str(sent))

        # mapped row wears the band; its center marker sits at the DEST center
        page.wait_for_timeout(300)   # let the anim frame decorate
        band = page.evaluate("""(() => {
          const n = nodes.get('m:echo');
          const p = n.ports.find(q => q.quiet && q.param === 'mix');
          const row = p && p.rowEl;
          return row && row._lfoBand ? {
            mapped: row.classList.contains('mapped'),
            center: row._lfoCenter.style.left,
          } : null;
        })()""")
        check("mapped param row wears the LFO band",
              band and band["mapped"], str(band))
        check("band center = the destination's own center",
              band and band["center"] == "30%", str(band))

        # dragging the mapped slider steers the LOCAL dest center too
        g = slider_geom(page, "m:signal_gen", "amp")
        page.mouse.move(g["x"], g["y"])
        page.mouse.down()
        page.mouse.move(g["x"] + g["w"] * g["zoom"] * 0.3, g["y"], steps=6)
        page.mouse.up()
        page.wait_for_timeout(120)
        c = page.evaluate(
            "state.lfos[0].dests.find(d => d.key === 'signal_gen').center")
        check("mapped slider drag steers the dest center locally",
              abs(c - (0.5 + 0.3)) < 0.05, str(c))

        # legacy tolerance: a pre-item-7 per-assignment entry still renders
        st_old = base_state(
            [sg_m, echo_m],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            lfos=[{"id": "signal_gen.amp", "key": "signal_gen",
                   "param": "amp", "rate": 0.3, "shape": 0, "depth": 0.8,
                   "center": 0.5,
                   "shapes": ["sine", "tri", "ramp", "square", "s&h"]}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_old)
        page.wait_for_timeout(400)
        check("legacy (pre-item-7) LFO entry renders a card",
              page.evaluate("nodes.has('lfo:signal_gen.amp')"))
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "wires.find(w => w.lfoId === 'signal_gen.amp').cutAction()")
        sent = page.evaluate("window.__sent")
        check("legacy wire cut falls back to lfo_unassign",
              {"type": "lfo_unassign", "id": "signal_gen.amp"} in sent,
              str(sent))

        # ================================================================
        # 10 — threshold (item 8): CV edge → ping
        # ================================================================
        st_thr = base_state(
            [sg_m], [{"from": "signal_gen", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "voice"},
                       {"from": "threshold", "to": "tonic"}],
            tonics=[{"id": "tonic", "every": "1 bar", "everies": ["1 bar"],
                     "octave": 2, "root": None}],
            lfos=[{"id": "lfo", "rate": 1.0, "shape": 0, "depth": 0.5,
                   "shapes": ["sine", "tri", "ramp", "square", "s&h"],
                   "dests": []}],
            thresholds=[{"id": "threshold", "level": 0.0, "hysteresis": 0.02,
                         "mode": "rising",
                         "modes": ["rising", "falling", "both"],
                         "source": "lfo"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_thr)
        page.wait_for_timeout(500)

        check("threshold card renders", page.evaluate("nodes.has('threshold')"))
        check("threshold card sizes to S",
              page.evaluate("nodes.get('threshold').size") == "S")
        rows = page.evaluate(
            "[...nodes.get('threshold').el.querySelectorAll('label')]"
            ".map(l => l.title)")
        check("threshold rows: level/hyst/edge",
              all(r in rows for r in ("level", "hyst", "edge")), str(rows))

        cv = page.evaluate("""(() => {
          const n = nodes.get('threshold');
          const p = n.ports.find(q => q.sig === 'mod' && q.dir === 'in');
          const lay = computeLayout(n);
          const row = [...n.el.querySelectorAll('.mini')].find(
            r => (r.querySelector('label')||{}).title === 'level');
          const rowY = n.y + row.offsetTop + row.offsetHeight / 2;
          const h = lay.handles.find(H => H.sig === 'mod' && H.side === 'in');
          return {quiet: !!p.quiet, plus: portAllowsPlus(p),
                  aligned: h && Math.abs(h.y - rowY) < 1.0};
        })()""")
        check("cv-in is a quiet single-input handle on the level row",
              cv == {"quiet": True, "plus": False, "aligned": True}, str(cv))

        # the wired source draws LFO → cv-in in the mod family; a cut sends
        # a targeted threshold_wire remove
        check("LFO → threshold cv wire draws (mod family)", page.evaluate(
            "wires.some(w => w.sig === 'mod'"
            " && w.to.node.gid === 'threshold')"))
        check("threshold → deriver wire draws in the ping family",
              page.evaluate(
                  "(wires.find(w => w.from.node.gid === 'threshold') || {})"
                  ".sig") == "ping")
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "wires.find(w => w.sig === 'mod'"
            " && w.to.node.gid === 'threshold').cutAction()")
        sent = page.evaluate("window.__sent")
        check("cv wire cut sends threshold_wire remove",
              {"type": "threshold_wire", "action": "remove",
               "id": "threshold", "lfo": "lfo"} in sent, str(sent))

        # grammar: LFO-out → cv-in connects via threshold_wire add;
        # threshold ping-out lands ONLY on a trigger-in
        acts = page.evaluate("""(() => {
          const lfo = nodes.get('lfo:lfo'), thr = nodes.get('threshold');
          const ton = nodes.get('tonic'), arp = nodes.get('arp');
          const lout = {node: lfo, port: lfo.ports.find(p => p.sig === 'mod')};
          const cvin = {node: thr,
                        port: thr.ports.find(p => p.sig === 'mod' && p.dir === 'in')};
          const pout = {node: thr,
                        port: thr.ports.find(p => p.sig === 'ping' && p.dir === 'out')};
          window.__sent.length = 0;
          const add = connectAction(lout, cvin);
          if (add) add();
          const ping = connectAction(pout,
            {node: ton, port: ton.ports.find(p => p.sig === 'ping')});
          const bad = arp && connectAction(pout,
            {node: arp, port: arp.ports.find(p => p.sig === 'ctl' && p.dir === 'in')});
          return {addSent: window.__sent, ping: !!ping, bad: !!bad};
        })()""")
        check("LFO-out → cv-in connects (threshold_wire add)",
              {"type": "threshold_wire", "action": "add", "id": "threshold",
               "lfo": "lfo"} in acts["addSent"], str(acts))
        check("threshold ping-out → deriver trigger-in connects",
              acts["ping"], str(acts))
        check("threshold ping-out → ctl-in refused", not acts["bad"],
              str(acts))

        # controls talk to the server; a ping event pulses the card
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('threshold');
          [...n.el.querySelectorAll('.chip')].find(
            c => c.textContent === 'rising').click();
          __msg({type: 'midi', event: {kind: 'ping', src: 'threshold'}});
        }""")
        page.wait_for_timeout(80)
        sent = page.evaluate("window.__sent")
        check("edge chip cycles + sends set_threshold mode",
              {"type": "set_threshold", "id": "threshold",
               "mode": "falling"} in sent, str(sent))
        check("a ping event pulses the pad-less card", page.evaluate(
            "nodes.get('threshold').el.classList.contains('pulse')"))
        check("palette Threshold button sends spawn_threshold", page.evaluate(
            """(() => { window.__sent.length = 0;
              [...document.querySelectorAll('#palette button')]
                .find(b => b.textContent.includes('Threshold')).click();
              return window.__sent.some(m => m.type === 'spawn_threshold');
            })()"""))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
