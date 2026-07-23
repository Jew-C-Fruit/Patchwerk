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
  11. Flex mode + zoom + lasso (backlog item 1): the header mode toggle
     swaps blocks ↔ flex; flex renders every card at fixed width with AUTO
     height (data-size F, all rows visible), seeds positions from the
     blocks layout, routes wires with the ported A* router by default and
     cubic beziers behind the ⌇/∿ toggle; scroll PANS (native) while a
     trackpad pinch (ctrl+wheel) or the +/- controls ZOOM — smoothly
     (rAF-eased), flex always and blocks only while UNLOCKED (locking
     snaps to the closest grid size); a drag from a dead zone draws a
     square lasso that
     mass-selects, a selected card's head drags the WHOLE group, clicking
     anything else deselects; blocks geometry and flex spots both survive
     the mode round-trip.
  12. Palette reorg + Instrument (item 2): top-line sections Allocation/
     Control(+Extractors)/Triggers/Voices(+Psines/Drone)/FX(Filters,
     Time & Space, Dirt, Dynamics, Pitch)/Monitors; the four voice-family
     sources fold into ONE "Instrument" palette entry whose placed card
     carries a voice dropdown sending swap_synth (in-place swap: same id,
     same wires); Estimator→Theory Wizard, Literal→Instant; psines carry
     their mechanism names (Waveshaper / Harmonic Bank / Crossfade).
  13. Generator viz (item 3): every generator card (sources minus
     audio_in/drone/scope_tap) carries the ∿static/●live canvas — static
     previews computed per type (psine law, pulse/saw/tri/sine, wobble
     tremolo, FM snapshot, mini Karplus-Strong, bandpassed noise), live
     mode polls the module's out bus via {"type":"scope"}; the mode
     choice survives state rebuilds; effects get no gen viz.
  14. Deck mini strip (item 4): the collapsed Loop Deck keeps a compact
     20px track view (miniviz) instead of hiding it; expand grows the
     full view; round-trips clean.
  15. Stepped integer sliders (item 5): numeric cycle-chips replaced by
     detented sliders (keys transpose ±12, tonic octave, literal
     fold/transpose value, keyshift length over KS_LENGTHS, deck bars
     1/2/4/8); relative drag through detents (one send per crossing,
     zoom-safe math), bare click applies nothing; the literal's value
     row type follows its place mode.
  9. Routable LFO (item 7): the LFO is a standalone node (palette spawns
     via spawn_lfo, kill sends remove_lfo); the card has rate/depth/shape
     and NO center row; one card fans out to MANY destinations (a mod wire
     per dest, each cut sending a targeted lfo_wire remove); LFO-out onto
     a param connects via lfo_wire add; a mapped param's slider steers the
     destination's center (locally synced between broadcasts) and its row
     wears the amplitude band; pre-item-7 per-assignment entries (the
     check_real fixture's shape) still render as one-dest legacy cards
     whose wires/kill fall back to lfo_unassign.
  17. GATE suite (backlog item 8): LOGIC palette section (Switch/Logic
     spawns); the Switch card's power-pad LED (click = set_switch, lit
     follows state + live {"kind":"gate"} events); the Logic card's op
     chip (set_logic) and op-shaped ports (SR latch: named set/reset ins
     vs ONE bare fan-in); the gate wire kind (signal-red family, legend
     swatch); head enable checkboxes converted to power-LED buttons with
     QUIET ":pwr" gate toggle-ins (modules/arp/drums) and the deck's four
     button-ins; the gate/ping toggle grammar (gate→pwr yes, gate→note/
     trigger no; ping→pwr/switch yes).
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

        # keycap click fires (item 6: the keycap replaced the ◉ pad as the
        # button's manual fire surface)
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('button').el.querySelector('.keycap').click()")
        sent = page.evaluate("window.__sent")
        check("keycap click sends fire_button",
              {"type": "fire_button", "id": "button"} in sent, str(sent))

        # pairing: arm via the bindline, then an ASSIGNED (note) key must
        # NOT bind…
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('button').el.querySelector('.bindline').click()")
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
            tonics=[{"id": "tonic", "every": "1 bar",
                     "everies": ["1 beat", "2 beats", "1 bar", "2 bars",
                                 "4 bars", "deck"],
                     "octave": 2, "root": "C", "memory": 6.0,
                     "bass": 0.06, "deck_feed": False, "scale": None,
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
        for knob in ("memory", "deck feed", "bass", "listening"):
            check(f"estimator knob row: {knob}", knob in est["labels"],
                  str(est))
        scale_txt = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const row = [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'scale');
          return row && row.parentElement.querySelector('.chip').textContent;
        })()""")
        check("estimator scale row exists, idle shows listening…",
              scale_txt == "listening…", str(scale_txt))

        # deck feed chip: off → click → sends deck_feed:true
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('tonic');
          [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'deck feed')
            .parentElement.querySelector('.chip').click();
        }""")
        sent = page.evaluate("window.__sent")
        check("deck feed chip sends set_tonic deck_feed:true",
              {"type": "set_tonic", "id": "tonic", "deck_feed": True}
              in sent, str(sent))

        # the trigger chip cycles through the server everies to "deck"
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('tonic');
          const chip = [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'trigger')
            .parentElement.querySelector('.chip');
          chip.click(); chip.click(); chip.click();   // 1 bar → … → deck
        }""")
        sent = page.evaluate("window.__sent")
        check("trigger chip cycles through to every:'deck'",
              {"type": "set_tonic", "id": "tonic", "every": "deck"}
              in sent, str(sent))

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
          leading: 7, root: 0, confidence: 0.42,
          scale: {tonic: 0, mode: 'ionian', conf: 0.62, label: 'C ionian'},
          deck: false})""")
        page.wait_for_timeout(60)
        viz = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const cells = [...n.el.querySelectorAll('.tonic > div')];
          const srow = [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'scale');
          return {h0: cells[0].querySelector('span').style.height,
                  root0: cells[0].classList.contains('root'),
                  lead7: cells[7].classList.contains('lead'),
                  conf: n.el.querySelector('.tconf').textContent,
                  scale: srow
                    && srow.parentElement.querySelector('.chip').textContent};
        })()""")
        check("histogram bars follow the weights", viz["h0"] == "100%",
              str(viz))
        check("committed root marked distinctly", viz["root0"], str(viz))
        check("leading candidate outlined", viz["lead7"], str(viz))
        check("confidence readout shown", "42" in viz["conf"], str(viz))
        check("scale readout follows the broadcast",
              viz["scale"] is not None and "C ionian" in viz["scale"]
              and "62" in viz["scale"], str(viz))

        # scale: null WITH evidence → chromatic fallback on the chip
        page.evaluate("""() => __msg({type: 'deriver', id: 'tonic',
          weights: [1, 0.4, 0.7, 0, 0.5, 0, 0, 0.6, 0, 0.3, 0, 0.2],
          scores:  [1, 0, 0, 0, 0, 0, 0, 0.9, 0, 0, 0, 0],
          leading: 0, root: 0, confidence: 0.1,
          scale: null, deck: false})""")
        page.wait_for_timeout(60)
        sc_null = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          return [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'scale')
            .parentElement.querySelector('.chip').textContent;
        })()""")
        check("scale:null with evidence reads chromatic",
              sc_null == "chromatic", str(sc_null))

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

        # ================================================================
        # 11 — flex mode + zoom + lasso group move (item 1 of the backlog)
        # ================================================================
        st_flex = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_flex)
        page.wait_for_timeout(500)
        # mode toggle replaces the "blocks" title; default mode = blocks
        check("mode toggle renders in the header", page.evaluate(
            "!!document.getElementById('mode-blocks') && "
            "!!document.getElementById('mode-flex')"))
        check("default mode is blocks", page.evaluate(
            "document.body.dataset.mode") == "blocks")

        # remember blocks-mode geometry of two cards, then switch to flex
        pre = page.evaluate("""(() => {
          const g = (gid) => { const n = nodes.get(gid);
            return {left: n.el.style.left, top: n.el.style.top,
                    size: n.el.dataset.size, x: n.x, y: n.y}; };
          return {sg: g('m:signal_gen'), echo: g('m:echo'),
                  zoom: world.style.zoom};
        })()""")
        page.click("#mode-flex")
        page.wait_for_timeout(80)   # BEFORE the settle pass and any transition
        early = page.evaluate("""(() => {
          const bad = [];
          for (const r of (window._routedDebug || [])) {
            if (r.noroute) continue;
            for (const [H, n] of [[r.sH, r.w.from.node], [r.dH, r.w.to.node]]) {
              if (!H || !n) continue;
              const b = flexRect(n);
              const inX = H.x > b.x - 2 && H.x < b.x + b.w + 2;
              const inY = H.y > b.y - 2 && H.y < b.y + b.h + 2;
              const onEdge = Math.abs(H.x - b.x) < 2 || Math.abs(H.x - (b.x + b.w)) < 2
                          || Math.abs(H.y - b.y) < 2 || Math.abs(H.y - (b.y + b.h)) < 2;
              if (!(inX && inY && onEdge))
                bad.push([n.gid, H.edge, H.x | 0, H.y | 0]);
            }
          }
          return bad;
        })()""")
        check("switch to flex: wires connect IMMEDIATELY (no transition skew)",
              early == [], str(early)[:200])
        page.wait_for_timeout(320)
        check("flex: body carries the mode", page.evaluate(
            "document.body.dataset.mode") == "flex")
        flex = page.evaluate("""(() => {
          const g = (gid) => { const n = nodes.get(gid);
            return {w: n.el.offsetWidth, h: n.el.offsetHeight,
                    hs: n.el.style.height, ds: n.el.dataset.size,
                    fx: n.fx, fy: n.fy, x: n.x}; };
          return {sg: g('m:signal_gen'), echo: g('m:echo'),
                  rows: nodes.get('m:signal_gen')
                    .el.querySelectorAll('.mini').length};
        })()""")
        check("flex cards render at fixed width / AUTO height",
              flex["sg"]["ds"] == "F" and flex["sg"]["w"] == 170
              and flex["sg"]["hs"] == "auto" and flex["sg"]["h"] > 0,
              str(flex["sg"]))
        check("flex seeds x from the blocks layout (continuous switch)",
              abs(flex["sg"]["fx"] - pre["sg"]["x"]) <= 8, str((pre, flex)))
        check("flex: every param row stays visible (auto height)",
              flex["rows"] >= 2, str(flex))

        # wires stay CONNECTED across the switch: every routed endpoint must
        # sit on its card's CURRENT perimeter (the old code measured cards
        # mid-CSS-transition, landing wires visibly disconnected)
        page.wait_for_timeout(400)   # past the settle pass
        discon = page.evaluate("""(() => {
          const bad = [];
          for (const r of (window._routedDebug || [])) {
            if (r.noroute) continue;
            for (const [H, n] of [[r.sH, r.w.from.node], [r.dH, r.w.to.node]]) {
              if (!H || !n) continue;
              const b = flexRect(n);
              const inX = H.x > b.x - 2 && H.x < b.x + b.w + 2;
              const inY = H.y > b.y - 2 && H.y < b.y + b.h + 2;
              const onEdge = Math.abs(H.x - b.x) < 2 || Math.abs(H.x - (b.x + b.w)) < 2
                          || Math.abs(H.y - b.y) < 2 || Math.abs(H.y - (b.y + b.h)) < 2;
              if (!(inX && inY && onEdge))
                bad.push([n.gid, H.edge, H.x, H.y, b]);
            }
          }
          return bad;
        })()""")
        check("flex wires stay connected to card perimeters after the switch",
              discon == [], str(discon)[:200])

        # wires: default flex style is the ROUTED (A*) orthogonal path;
        # the ⌇/∿ toggle swaps to bezier curves
        wire_d = page.evaluate(
            "wires.length ? wires[0].topEl.getAttribute('d') : ''")
        check("flex wires route by default (orthogonal, no cubic)",
              wire_d and " C " not in wire_d, wire_d[:80])
        page.click("#wirestyle")
        page.wait_for_timeout(250)
        bez_d = page.evaluate(
            "wires.length ? wires[0].topEl.getAttribute('d') : ''")
        check("wire-style toggle draws cubic beziers", " C " in bez_d,
              bez_d[:80])
        page.click("#wirestyle")   # back to routed
        page.wait_for_timeout(250)

        # zoom: scroll is PAN — only a trackpad PINCH (ctrl+wheel) zooms,
        # smoothly (rAF-eased toward the target)
        z0 = page.evaluate("parseFloat(world.style.zoom) || 1")
        page.evaluate("""() => {
          document.getElementById('board').dispatchEvent(new WheelEvent(
            'wheel', {deltaY: -240, ctrlKey: false, clientX: 700, clientY: 500,
                      bubbles: true, cancelable: true}));
        }""")
        page.wait_for_timeout(250)
        zp = page.evaluate("parseFloat(world.style.zoom) || 1")
        check("flex: plain scroll PANS, never zooms", abs(zp - z0) < 1e-9,
              f"{z0}->{zp}")
        page.evaluate("""() => {
          document.getElementById('board').dispatchEvent(new WheelEvent(
            'wheel', {deltaY: -240, ctrlKey: true, clientX: 700, clientY: 500,
                      bubbles: true, cancelable: true}));
        }""")
        page.wait_for_timeout(120)
        z_mid = page.evaluate("parseFloat(world.style.zoom) || 1")
        page.wait_for_timeout(400)
        z1 = page.evaluate("parseFloat(world.style.zoom) || 1")
        check("flex: pinch zooms IN (scale rises)", z1 > z0, f"{z0}->{z1}")
        check("zoom is SMOOTH (eases through intermediate scales)",
              z0 < z_mid < z1 or abs(z_mid - z1) < 1e-9 and z_mid > z0,
              f"{z0} -> {z_mid} -> {z1}")

        # lasso in flex: drag from empty space around both cards, then drag
        # the group by one card's head — both cards move by the same delta
        page.evaluate("""(() => {   // park the cards + pin the view (zoom 1)
          const a = nodes.get('m:signal_gen'), b = nodes.get('m:echo');
          a.fx = 200; a.fy = 120; b.fx = 200; b.fy = 416;
          place(a); place(b); rerouteAll();
          flexZoom = 1; applyView();
          const brd = document.getElementById('board');
          brd.scrollLeft = 0; brd.scrollTop = 0;
        })()""")
        page.wait_for_timeout(200)
        lasso_sel = page.evaluate("""(() => {
          const brd = document.getElementById('board');
          const r = brd.getBoundingClientRect();
          const zs = parseFloat(world.style.zoom) || 1;
          // client coords of world points via OUR zoom (convention-neutral)
          const cx = (wx) => r.left - brd.scrollLeft + wx * zs;
          const cy = (wy) => r.top - brd.scrollTop + wy * zs;
          const fire = (t, x, y) => brd.dispatchEvent(new PointerEvent(t,
            {clientX: x, clientY: y, bubbles: true, pointerId: 7}));
          fire('pointerdown', cx(120), cy(60));
          fire('pointermove', cx(500), cy(660));
          fire('pointerup', cx(500), cy(660));
          return [...sel];
        })()""")
        check("lasso from a dead zone selects the enclosed cards",
              "m:signal_gen" in lasso_sel and "m:echo" in lasso_sel,
              str(lasso_sel))
        before = page.evaluate(
            "(() => { const a = nodes.get('m:signal_gen'),"
            " b = nodes.get('m:echo'); return [a.fx, a.fy, b.fx, b.fy]; })()")
        gh = page.evaluate("""(() => {
          const r = nodes.get('m:signal_gen').el.querySelector('.head')
            .getBoundingClientRect();
          return {x: r.left + 6, y: r.top + 6,
                  zs: parseFloat(world.style.zoom) || 1};
        })()""")
        page.mouse.move(gh["x"], gh["y"])
        page.mouse.down()
        page.mouse.move(gh["x"] + 96 * gh["zs"], gh["y"] + 64 * gh["zs"],
                        steps=6)
        page.mouse.up()
        page.wait_for_timeout(150)
        after = page.evaluate(
            "(() => { const a = nodes.get('m:signal_gen'),"
            " b = nodes.get('m:echo'); return [a.fx, a.fy, b.fx, b.fy]; })()")
        deltas = [after[i] - before[i] for i in range(4)]
        check("group drag moves BOTH selected cards by the same delta",
              deltas == [96, 64, 96, 64], f"{before} -> {after}")

        # clicking anything that isn't a selected card's head deselects
        tr = page.evaluate("""(() => {
          const r = nodes.get('m:signal_gen').el.querySelector('.track')
            .getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        })()""")
        page.mouse.click(tr["x"], tr["y"])
        page.wait_for_timeout(80)
        check("clicking anything else deselects",
              page.evaluate("sel.size") == 0)

        # back to blocks: geometry restores exactly; flex spots persist
        page.click("#mode-blocks")
        page.wait_for_timeout(300)
        post = page.evaluate("""(() => {
          const g = (gid) => { const n = nodes.get(gid);
            return {left: n.el.style.left, top: n.el.style.top,
                    size: n.el.dataset.size}; };
          return {mode: document.body.dataset.mode,
                  sg: g('m:signal_gen'), echo: g('m:echo'),
                  fx: nodes.get('m:signal_gen').fx};
        })()""")
        check("back to blocks: mode + card sizes restore",
              post["mode"] == "blocks" and post["sg"]["size"] in "SML"
              and post["sg"]["left"] == pre["sg"]["left"]
              and post["sg"]["top"] == pre["sg"]["top"], str((pre, post)))
        check("flex position survives the round-trip", page.evaluate(
            "nodes.get('m:signal_gen').fx") == after[0], str(post))

        # BLOCKS group move: lasso both cards, drag one head → the whole
        # group moves by a WHOLE-BLOCK delta (no shove; must fit to land)
        page.evaluate("""(() => {   // park in the empty bottom-right, full view
          viewCols = BX; viewRows = BY; panLocked = true; blocksFree = null;
          applyView();
          const a = nodes.get('m:signal_gen'), b = nodes.get('m:echo');
          a.bx = 9; a.by = 5; a.half = null;
          b.bx = 9; b.by = 6; b.half = null;
          place(a); place(b); rerouteAll();
        })()""")
        page.wait_for_timeout(200)
        page.evaluate("""(() => {
          const brd = document.getElementById('board');
          const r = brd.getBoundingClientRect();
          const zs = parseFloat(world.style.zoom) || 1;
          const cx = (wx) => r.left - brd.scrollLeft + wx * zs;
          const cy = (wy) => r.top - brd.scrollTop + wy * zs;
          const A = nodeUnitRect(nodes.get('m:signal_gen'));
          const B = nodeUnitRect(nodes.get('m:echo'));
          const x0 = (A.x - 1) * U, y0 = (A.y - 1) * U;
          const x1 = (B.x + B.w + 1) * U, y1 = (B.y + B.h + 1) * U;
          const fire = (t, x, y) => brd.dispatchEvent(new PointerEvent(t,
            {clientX: x, clientY: y, bubbles: true, pointerId: 11}));
          fire('pointerdown', cx(x0), cy(y0));
          fire('pointermove', cx(x1), cy(y1));
          fire('pointerup', cx(x1), cy(y1));
        })()""")
        page.wait_for_timeout(100)
        check("blocks lasso selects the group", page.evaluate(
            "sel.has('m:signal_gen') && sel.has('m:echo')"))
        bh = page.evaluate("""(() => {
          const r = nodes.get('m:signal_gen').el.querySelector('.head')
            .getBoundingClientRect();
          return {x: r.left + 6, y: r.top + 6,
                  step: (PITCH * U) * (parseFloat(world.style.zoom) || 1)};
        })()""")
        page.mouse.move(bh["x"], bh["y"])
        page.mouse.down()
        page.mouse.move(bh["x"] + bh["step"], bh["y"], steps=6)
        page.mouse.up()
        page.wait_for_timeout(150)
        gpos = page.evaluate(
            "(() => { const a = nodes.get('m:signal_gen'),"
            " b = nodes.get('m:echo');"
            " return [a.bx, a.by, b.bx, b.by]; })()")
        check("blocks group drag moves BOTH cards one block right",
              gpos == [10, 5, 10, 6], str(gpos))
        page.mouse.click(60, 700)   # empty board space → deselect
        page.wait_for_timeout(80)
        check("blocks: clicking empty space deselects",
              page.evaluate("sel.size") == 0)

        # blocks zoom: LOCKED ignores the wheel; UNLOCKED free-zooms; locking
        # again snaps to the closest grid size (a scaleFor(cols,rows) value)
        page.evaluate("""() => {   // ensure locked state to start
          if (!panLocked) { panLocked = true; lockSnap(); applyView(); }
        }""")
        zb0 = page.evaluate("parseFloat(world.style.zoom) || 1")
        page.evaluate("""() => {
          document.getElementById('board').dispatchEvent(new WheelEvent(
            'wheel', {deltaY: -240, ctrlKey: true, clientX: 700, clientY: 500,
                      bubbles: true, cancelable: true}));
        }""")
        page.wait_for_timeout(300)
        zb1 = page.evaluate("parseFloat(world.style.zoom) || 1")
        check("blocks LOCKED: pinch does not zoom", abs(zb1 - zb0) < 1e-9,
              f"{zb0}->{zb1}")
        page.click("#panlock")     # unlock
        page.evaluate("""() => {
          document.getElementById('board').dispatchEvent(new WheelEvent(
            'wheel', {deltaY: -240, ctrlKey: true, clientX: 700, clientY: 500,
                      bubbles: true, cancelable: true}));
        }""")
        page.wait_for_timeout(500)
        zb2 = page.evaluate("parseFloat(world.style.zoom) || 1")
        check("blocks UNLOCKED: pinch free-zooms", zb2 > zb1, f"{zb1}->{zb2}")
        page.click("#panlock")     # lock again → snap to closest grid size
        page.wait_for_function("() => zAnim === null", timeout=4000)
        page.wait_for_timeout(50)
        snap_ok = page.evaluate("""(() => {
          const z = parseFloat(world.style.zoom) || 1;
          for (let c = 3; c <= BX; c++) for (let r = 2; r <= BY; r++)
            if (Math.abs(scaleFor(c, r) - z) < 1e-4) return true;
          return false;
        })()""")
        check("locking snaps the zoom to the closest grid size", snap_ok)

        # locking gutter-aligns the viewport on ALL sides (whole blocks only)
        align = page.evaluate("""(() => {
          const z = parseFloat(world.style.zoom) || 1;
          const brd = document.getElementById('board');
          return {c: (brd.scrollLeft / z) / (PITCH * U),
                  r: (brd.scrollTop / z) / (PITCH * U), locked: panLocked};
        })()""")
        check("locking gutter-aligns the view to whole blocks",
              align["locked"] and abs(align["c"] - round(align["c"])) < 0.02
              and abs(align["r"] - round(align["r"])) < 0.02, str(align))

        # +/- canvas buttons: never unlock; a locked view stays gutter-aligned
        page.click("#zoomout")
        page.wait_for_function("() => zAnim === null", timeout=4000)
        page.wait_for_timeout(50)
        zo = page.evaluate("""(() => {
          const z = parseFloat(world.style.zoom) || 1;
          const brd = document.getElementById('board');
          return {locked: panLocked,
                  grid: Math.abs(scaleFor(viewCols, viewRows) - z) < 1e-4,
                  c: (brd.scrollLeft / z) / (PITCH * U),
                  r: (brd.scrollTop / z) / (PITCH * U)};
        })()""")
        check("+/- while LOCKED stays locked, on-grid and gutter-aligned",
              zo["locked"] and zo["grid"]
              and abs(zo["c"] - round(zo["c"])) < 0.02
              and abs(zo["r"] - round(zo["r"])) < 0.02, str(zo))

        # +/- pull in/out relative to the view's 0,0 upper corner (unlocked)
        page.click("#panlock")     # unlock
        page.wait_for_timeout(80)
        c0 = page.evaluate("""(() => {
          const z = parseFloat(world.style.zoom) || 1;
          const brd = document.getElementById('board');
          return [brd.scrollLeft / z, brd.scrollTop / z];
        })()""")
        page.click("#zoomin")
        page.wait_for_function("() => zAnim === null", timeout=4000)
        page.wait_for_timeout(50)
        c1 = page.evaluate("""(() => {
          const z = parseFloat(world.style.zoom) || 1;
          const brd = document.getElementById('board');
          // scrollbars can appear on zoom-in (overflow auto), shifting
          // clientWidth → compare scales with a scrollbar-wide tolerance
          return [brd.scrollLeft / z, brd.scrollTop / z,
                  Math.abs(scaleFor(viewCols, viewRows) - z) / z < 0.03];
        })()""")
        check("+/- holds the view's upper corner (0,0-relative pull)",
              abs(c1[0] - c0[0]) < 1.0 and abs(c1[1] - c0[1]) < 1.0 and c1[2],
              f"{c0} -> {c1}")

        # ================================================================
        # 12 — palette reorg + Instrument card (item 2)
        # ================================================================
        AVAIL2 = [
            {"key": "fm_bell", "name": "FM Bell", "kind": "source",
             "family": "voice"},
            {"key": "pluck", "name": "Pluck", "kind": "source",
             "family": "voice"},
            {"key": "wind", "name": "Wind", "kind": "source",
             "family": "voice"},
            {"key": "wobble_saw", "name": "Wobble Saw", "kind": "source",
             "family": "voice"},
            {"key": "pulse_pad", "name": "PW Pulse Pad", "kind": "source",
             "family": "voice"},
            {"key": "power_sine_shaper", "name": "Psine Waveshaper",
             "kind": "source", "family": "psine"},
            {"key": "drone", "name": "Drone", "kind": "source",
             "family": "service"},
            {"key": "lowpass", "name": "Low-pass Filter", "kind": "effect",
             "family": "filter"},
            {"key": "echo", "name": "Echo", "kind": "effect",
             "family": "time"},
        ]
        inst_bell = mod("fm_bell", "FM Bell", "source", "voice",
                        params={"freq": param(), "amp": param()})
        st12 = base_state(
            [inst_bell, mod("echo", "Echo", "effect", "time")],
            [{"from": "fm_bell", "to": "echo"},
             {"from": "echo", "to": "master"}],
            available=AVAIL2)
        page.evaluate("(s) => __msg({type: 'state', ...s})", st12)
        page.wait_for_timeout(500)

        pal = page.evaluate("""(() => ({
          h3: [...document.querySelectorAll('#palette h3')].map(h => h.textContent),
          h4: [...document.querySelectorAll('#palette h4')].map(h => h.textContent),
          btns: [...document.querySelectorAll('#palette button')].map(b => b.textContent),
        }))()""")
        check("palette top-line sections in order",
              pal["h3"] == ["allocation", "control", "triggers", "logic",
                            "voices", "fx", "monitors"], str(pal["h3"]))
        for sub in ("extractors", "psines", "drone", "filters",
                    "time & space"):
            check(f"palette subsection: {sub}", sub in pal["h4"],
                  str(pal["h4"]))
        check("palette has ONE Instrument entry for the voice family",
              pal["btns"].count("Instrument") == 1, str(pal["btns"]))
        for hidden in ("FM Bell", "Pluck", "Wind", "Wobble Saw"):
            check(f"voice-family member not listed individually: {hidden}",
                  hidden not in pal["btns"], str(pal["btns"]))
        check("psine renamed in palette (Psine Waveshaper)",
              "Psine Waveshaper" in pal["btns"], str(pal["btns"]))
        check("PW Pulse Pad stays a standalone entry",
              "PW Pulse Pad" in pal["btns"], str(pal["btns"]))
        for nm, msgt in (("Theory Wizard", "spawn_tonic"),
                         ("Instant", "spawn_literal")):
            page.evaluate("window.__sent.length = 0")
            page.evaluate("""(nm) => {
              [...document.querySelectorAll('#palette button')]
                .find(b => b.textContent === nm).click();
            }""", nm)
            page.wait_for_timeout(60)
            sent = page.evaluate("window.__sent")
            check(f"palette {nm} sends {msgt}",
                  {"type": msgt} in sent, str(sent))

        # the Instrument card: renamed title, voice dropdown, in-place swap
        card = page.evaluate("""(() => {
          const n = nodes.get('m:fm_bell');
          const sel = n && n.el.querySelector('select.devsel');
          return n && {title: n.el.querySelector('.title').textContent,
                       sub: (n.el.querySelector('.sub')||{}).textContent,
                       opts: sel ? [...sel.options].map(o => o.value) : null,
                       cur: sel && sel.value};
        })()""")
        check("voice-family instance renders as the Instrument card",
              bool(card) and card["title"] == "Instrument", str(card))
        check("Instrument dropdown lists the four voices",
              bool(card) and card["opts"] == ["FM Bell", "Pluck", "Wind",
                                              "Wobble Saw"], str(card))
        check("Instrument dropdown shows the current voice",
              bool(card) and card["cur"] == "FM Bell", str(card))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""(() => {
          const n = nodes.get('m:fm_bell');
          const sel = n.el.querySelector('select.devsel');
          sel.value = 'Pluck';
          sel.dispatchEvent(new Event('change'));
        })()""")
        page.wait_for_timeout(60)
        sent12 = page.evaluate("window.__sent")
        check("voice change sends an in-place swap_synth",
              {"type": "swap_synth", "id": "fm_bell", "key": "pluck"}
              in sent12, str(sent12))

        # a SWAPPED instance (id fm_bell, type pluck) still renders as
        # Instrument with the new voice selected — state round-trip
        swapped = dict(inst_bell)
        swapped["type"] = "pluck"
        swapped["name"] = "Pluck"
        st12b = dict(st12)
        st12b["chain"] = [swapped, mod("echo", "Echo", "effect", "time")]
        page.evaluate("(s) => __msg({type: 'state', ...s})", st12b)
        page.wait_for_timeout(400)
        card2 = page.evaluate("""(() => {
          const n = nodes.get('m:fm_bell');
          const sel = n && n.el.querySelector('select.devsel');
          return n && {title: n.el.querySelector('.title').textContent,
                       cur: sel && sel.value};
        })()""")
        check("swapped instance keeps the Instrument card under the same id",
              bool(card2) and card2["title"] == "Instrument", str(card2))
        check("swapped instance's dropdown shows the new voice",
              bool(card2) and card2["cur"] == "Pluck", str(card2))

        # renamed deriver cards (Theory Wizard / Instant)
        st12c = base_state(
            [inst_bell], [{"from": "fm_bell", "to": "master"}],
            available=AVAIL2,
            tonics=[{"id": "tonic", "every": "1 bar",
                     "everies": ["1 beat", "2 beats", "1 bar", "2 bars",
                                 "4 bars", "deck"],
                     "octave": 2, "root": "C", "memory": 6.0,
                     "bass": 0.06, "deck_feed": False, "scale": None,
                     "listening": "triadic",
                     "listenings": ["triadic", "root+fifth", "chromatic"]}],
            literals=[{"id": "literal", "every": "immediate",
                       "everies": ["immediate"], "extract": "lowest-held",
                       "extracts": ["lowest-held"], "place": "absolute",
                       "places": ["absolute"], "fold_octave": 3,
                       "transpose": 0, "hold_on_empty": True,
                       "note": None}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st12c)
        page.wait_for_timeout(400)
        names12 = page.evaluate("""(() => ({
          tonic: nodes.get('tonic')
            && nodes.get('tonic').el.querySelector('.title').textContent,
          literal: nodes.get('literal')
            && nodes.get('literal').el.querySelector('.title').textContent,
        }))()""")
        check("Estimator card renamed to Theory Wizard",
              names12["tonic"] == "Theory Wizard", str(names12))
        check("Literal card renamed to Instant",
              names12["literal"] == "Instant", str(names12))

        # ================================================================
        # 13 — generator viz: ∿static/●live on every generator (item 3)
        # ================================================================
        st13 = base_state(
            [mod("fm_bell", "FM Bell", "source", "voice",
                 params={"freq": param(), "ratio": param(), "amp": param()}),
             mod("pulse_pad", "PW Pulse Pad", "source", "voice",
                 params={"freq": param(), "wave": param(), "pwm": param(),
                         "amp": param()}),
             mod("echo", "Echo", "effect", "time")],
            [{"from": "fm_bell", "to": "echo"},
             {"from": "pulse_pad", "to": "echo"},
             {"from": "echo", "to": "master"}],
            available=AVAIL2)
        page.evaluate("(s) => __msg({type: 'state', ...s})", st13)
        page.wait_for_timeout(600)
        gv = page.evaluate("""(() => {
          const pick = (k) => {
            const n = nodes.get('m:' + k);
            return n && {canvas: !!n.el.querySelector('canvas[data-viz=gen]'),
                         btn: (n.el.querySelector('.pslive')||{}).textContent,
                         drawn: n._psKey !== undefined};
          };
          const e = nodes.get('m:echo');
          return {bell: pick('fm_bell'), pad: pick('pulse_pad'),
                  echoHasViz: !!(e && e.el.querySelector('canvas[data-viz=gen]'))};
        })()""")
        check("generator cards carry the gen viz canvas",
              bool(gv["bell"]) and gv["bell"]["canvas"]
              and bool(gv["pad"]) and gv["pad"]["canvas"], str(gv))
        check("gen viz starts in static mode",
              gv["bell"]["btn"] == "∿ static", str(gv))
        check("static preview computed at least once (bell)",
              gv["bell"]["drawn"], str(gv))
        check("effects do NOT get the gen viz", not gv["echoHasViz"], str(gv))

        # toggle to live: scope polls flow, and the choice survives a rebuild
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""(() => {
          nodes.get('m:fm_bell').el.querySelector('.pslive').click();
        })()""")
        page.wait_for_timeout(400)
        sent13 = page.evaluate("window.__sent")
        check("live mode polls the module's out bus (scope message)",
              any(m == {"type": "scope", "key": "fm_bell"} for m in sent13),
              str(sent13[:6]))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st13)
        page.wait_for_timeout(500)
        btn13 = page.evaluate("""(() =>
          (nodes.get('m:fm_bell').el.querySelector('.pslive')||{}).textContent
        )()""")
        check("live choice survives the state rebuild",
              btn13 == "● live", str(btn13))

        # ================================================================
        # 14 — Loop Deck mini track display when collapsed (item 4)
        # ================================================================
        dk = page.evaluate("""(() => {
          const n = nodes.get('deck');
          const vs = n.el.querySelector('.viz-sec');
          const cv = n.el.querySelector('canvas[data-viz=deck]');
          return {mini: n.el.classList.contains('miniviz'),
                  shown: getComputedStyle(vs).display !== 'none',
                  h: cv.clientHeight, expanded: !!n.expanded};
        })()""")
        # 07-22: the strip is 3 grid squares tall now (48px; Cole: "room
        # for 3" — was 1 square / 20px)
        check("collapsed deck keeps its track view visible (mini strip)",
              not dk["expanded"] and dk["shown"] and dk["mini"]
              and 40 <= dk["h"] <= 52, str(dk))
        page.evaluate(
            "nodes.get('deck').el.querySelector('.expander').click()")
        page.wait_for_timeout(250)
        dk2 = page.evaluate("""(() => {
          const n = nodes.get('deck');
          const cv = n.el.querySelector('canvas[data-viz=deck]');
          return {mini: n.el.classList.contains('miniviz'),
                  h: cv.clientHeight, expanded: !!n.expanded};
        })()""")
        check("expanding the deck grows the full track view",
              dk2["expanded"] and not dk2["mini"] and dk2["h"] > dk["h"],
              str(dk2))
        page.evaluate(
            "nodes.get('deck').el.querySelector('.expander').click()")
        page.wait_for_timeout(250)
        dk3 = page.evaluate("""(() => {
          const n = nodes.get('deck');
          const cv = n.el.querySelector('canvas[data-viz=deck]');
          return {mini: n.el.classList.contains('miniviz'),
                  h: cv.clientHeight};
        })()""")
        check("collapsing returns to the mini strip (not hidden)",
              dk3["mini"] and 40 <= dk3["h"] <= 52, str(dk3))

        # ================================================================
        # 15 — stepped integer sliders replace numeric cycle-chips (item 5)
        # ================================================================
        st15 = base_state(
            [inst_bell], [{"from": "fm_bell", "to": "master"}],
            available=AVAIL2,
            keyshifts=[{"id": "keyshift", "key": "C", "length": 4,
                        "steps": [None, None, None, None]}],
            literals=[{"id": "literal", "every": "immediate",
                       "everies": ["immediate"], "extract": "lowest-held",
                       "extracts": ["lowest-held"], "place": "fold",
                       "places": ["absolute", "fold", "transpose"],
                       "fold_octave": 3, "transpose": 0,
                       "hold_on_empty": True, "note": None}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st15)
        page.wait_for_timeout(500)
        sl = page.evaluate("""(() => {
          const stepRow = (gid, label) => {
            const n = nodes.get(gid);
            const row = n && [...n.el.querySelectorAll('.mini.stepped')].find(
              r => (r.querySelector('label')||{}).title === label);
            return row ? {dets: row.querySelectorAll('.det').length,
                          v: row.querySelector('.v').textContent} : null;
          };
          const lit = nodes.get('literal');
          return {transpose: stepRow('keys', 'transpose'),
                  bars: stepRow('deck', 'bars'),
                  length: stepRow('keyshift', 'length'),
                  litval: stepRow('literal', 'value'),
                  litchips: lit
                    ? [...lit.el.querySelectorAll('.chip')].length : -1};
        })()""")
        check("keys transpose is a 25-detent stepped slider",
              bool(sl["transpose"]) and sl["transpose"]["dets"] == 25
              and sl["transpose"]["v"] == "0 st", str(sl))
        check("deck bars is a 4-detent stepped slider (1/2/4/8)",
              bool(sl["bars"]) and sl["bars"]["dets"] == 4, str(sl))
        check("keyshift length is a stepped slider over KS_LENGTHS",
              bool(sl["length"]) and sl["length"]["dets"] == 8
              and sl["length"]["v"] == "4 bars", str(sl))
        check("literal fold mode: value is a stepped slider (C3)",
              bool(sl["litval"]) and sl["litval"]["dets"] == 8
              and sl["litval"]["v"] == "C3", str(sl))

        # drag the transpose track via synthetic pointer events (viewport-
        # independent): +4 detents' worth of visual px → exactly +4 st
        page.evaluate("window.__sent.length = 0")
        sent15 = page.evaluate("""(() => {
          const n = nodes.get('keys');
          const row = [...n.el.querySelectorAll('.mini.stepped')].find(
            r => (r.querySelector('label')||{}).title === 'transpose');
          const track = row.querySelector('.track');
          const zs = parseFloat(world.style.zoom) || 1;
          const per = (track.offsetWidth || 1) / 24;   // 25 detents → 24 gaps
          const ev = (type, x) => track.dispatchEvent(new PointerEvent(type,
            {pointerId: 7, clientX: x, clientY: 0, bubbles: true}));
          ev('pointerdown', 500);
          for (let s = 1; s <= 8; s++)
            ev('pointermove', 500 + (4 * per * zs) * s / 8);
          ev('pointerup', 500 + 4 * per * zs);
          return window.__sent.filter(m => m.type === 'set_transpose');
        })()""")
        check("dragging steps through detents (set_transpose fired, ints)",
              bool(sent15) and all(float(m["semitones"]).is_integer()
                                   for m in sent15), str(sent15))
        check("drag of 4 detents lands on +4 st exactly",
              bool(sent15) and sent15[-1]["semitones"] == 4, str(sent15))
        # a bare click (down+up, no move) applies nothing
        clicked = page.evaluate("""(() => {
          window.__sent.length = 0;
          const n = nodes.get('keys');
          const row = [...n.el.querySelectorAll('.mini.stepped')].find(
            r => (r.querySelector('label')||{}).title === 'transpose');
          const track = row.querySelector('.track');
          const ev = (type, x) => track.dispatchEvent(new PointerEvent(type,
            {pointerId: 8, clientX: x, clientY: 0, bubbles: true}));
          ev('pointerdown', 500); ev('pointerup', 500);
          return window.__sent.filter(m => m.type === 'set_transpose');
        })()""")
        check("bare click on a stepped slider applies nothing",
              not clicked, str(clicked))

        # ================================================================
        # 16 — trigger card polish (item 6): Button keycap + Clock multi-bar
        # ================================================================
        clock_ladder = ["8/1", "4/1", "2/1", "1/1", "1/2", "1/4.", "1/4",
                        "1/4T", "1/8.", "1/8", "1/8T", "1/16", "1/16T",
                        "1/32"]
        st16 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            buttons=[{"id": "button", "binding": {"kind": "key",
                                                  "code": "KeyN"},
                      "armed": False},
                     {"id": "button.2", "binding": None, "armed": False},
                     {"id": "button.3", "binding": {"kind": "cc", "cc": 21},
                      "armed": False}],
            clocks=[{"id": "clock", "division": "1/32",
                     "divisions": clock_ladder}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st16)
        page.wait_for_timeout(500)

        # keycap renders the binding LARGE: key glyph / CC number / unbound
        caps = page.evaluate("""(() => {
          const g = (gid) => {
            const n = nodes.get(gid);
            const cap = n.el.querySelector('.keycap');
            return {txt: cap.textContent, unbound:
                      cap.classList.contains('unbound'),
                    cc: !!cap.querySelector('small'),
                    line: n.el.querySelector('.bindline').textContent,
                    size: n.size};
          };
          return {b: g('button'), b2: g('button.2'), b3: g('button.3')};
        })()""")
        check("bound key renders its glyph on the keycap",
              caps["b"]["txt"] == "N" and not caps["b"]["cc"], str(caps))
        check("binding label sits underneath",
              caps["b"]["line"] == "key N", str(caps))
        check("unbound keycap shows ＋ / pair…",
              caps["b2"]["txt"] == "＋" and caps["b2"]["unbound"]
              and caps["b2"]["line"] == "pair…", str(caps))
        check("CC binding renders CC prefix + number",
              caps["b3"]["cc"] and "21" in caps["b3"]["txt"], str(caps))
        check("button cards still measure into S",
              all(caps[k]["size"] == "S" for k in ("b", "b2", "b3")),
              str(caps))

        # the ◉ heartbeat icon rides next to the rendered ping-out handle
        fi = page.evaluate("""(() => {
          const n = nodes.get('button');
          const H = n.lay.handles.find(h => h.sig === 'ping'
                                            && h.side === 'out');
          const el = n.fireIcon;
          if (!H || !el) return null;
          const ix = n.el.offsetLeft + el.offsetLeft + 6;
          const iy = n.el.offsetTop + el.offsetTop + 6;
          return {d: Math.hypot(ix - H.x, iy - H.y),
                  inX: el.offsetLeft >= 0
                       && el.offsetLeft + 12 <= n.el.offsetWidth,
                  inY: el.offsetTop >= 0
                       && el.offsetTop + 12 <= n.el.offsetHeight};
        })()""")
        check("fire icon sits next to the out handle (<24px)",
              fi is not None and fi["d"] < 24, str(fi))
        check("fire icon stays inside the card", fi is not None
              and fi["inX"] and fi["inY"], str(fi))

        # firing pulses BOTH the keycap and the fire icon
        pulsed = page.evaluate("""(() => {
          const n = nodes.get('button');
          n.el.querySelector('.keycap').click();
          return {cap: n.el.querySelector('.keycap')
                        .classList.contains('pulse'),
                  icon: n.fireIcon.classList.contains('pulse')};
        })()""")
        check("fire pulses keycap + heartbeat icon",
              pulsed["cap"] and pulsed["icon"], str(pulsed))

        # arming flips the keycap to … and Escape restores it
        page.evaluate(
            "nodes.get('button.2').el.querySelector('.bindline').click()")
        armed = page.evaluate("""(() => {
          const n = nodes.get('button.2');
          return {cap: n.el.querySelector('.keycap').textContent,
                  line: n.el.querySelector('.bindline').textContent};
        })()""")
        check("arming shows … / press a key/CC…",
              armed == {"cap": "…", "line": "press a key/CC…"}, str(armed))
        page.keyboard.press("Escape")
        page.wait_for_timeout(60)
        armed = page.evaluate("""(() => {
          const n = nodes.get('button.2');
          return {cap: n.el.querySelector('.keycap').textContent,
                  line: n.el.querySelector('.bindline').textContent};
        })()""")
        check("Escape cancels pairing back to ＋ / pair…",
              armed == {"cap": "＋", "line": "pair…"}, str(armed))

        # clock multi-bar: the extended ladder cycles through the chip —
        # from the ladder's end (1/32) one click wraps to 8/1
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""(() => {
          const n = nodes.get('clock');
          const chip = [...n.el.querySelectorAll('label')]
            .find(l => l.title === 'division').parentElement
            .querySelector('.chip');
          chip.click();
        })()""")
        sent16 = page.evaluate(
            "window.__sent.filter(m => m.type === 'set_clock')")
        check("division chip cycles into the multi-bar entries (1/32→8/1)",
              sent16 and sent16[-1] == {"type": "set_clock", "id": "clock",
                                        "division": "8/1"}, str(sent16))
        sz16 = page.evaluate("nodes.get('clock').size")
        check("clock card still measures into S", sz16 == "S", str(sz16))

        # ================================================================
        # 17 — gate suite (item 8): switch/logic, toggle-ins, grammar
        # ================================================================
        st17 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "voice"},
                       {"from": "switch", "to": "echo:pwr"},
                       {"from": "logic", "to": "deck:play"},
                       {"from": "button", "to": "switch"}],
            tonics=[{"id": "tonic", "every": "1 bar", "everies": ["1 bar"],
                     "octave": 2, "root": None}],
            buttons=[{"id": "button", "binding": None, "armed": False}],
            switches=[{"id": "switch", "on": False}],
            logics=[{"id": "logic", "op": "AND",
                     "ops": ["AND", "OR", "NOT", "XOR", "SR latch"],
                     "out": False}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st17)
        page.wait_for_timeout(500)

        # palette: a LOGIC top-line section right after TRIGGERS
        pal17 = page.evaluate(
            "[...document.querySelectorAll('#palette h3')]"
            ".map(h => h.textContent)")
        check("palette LOGIC section sits right after TRIGGERS",
              "logic" in pal17 and
              pal17.index("logic") == pal17.index("triggers") + 1, str(pal17))
        for nm, msgt in (("Switch", "spawn_switch"), ("Logic", "spawn_logic")):
            page.evaluate("window.__sent.length = 0")
            page.evaluate("""(nm) => {
              [...document.querySelectorAll('#palette button')]
                .find(b => b.textContent === nm).click();
            }""", nm)
            sent = page.evaluate("window.__sent")
            check(f"palette {nm} sends {msgt}", {"type": msgt} in sent,
                  str(sent))

        # the Switch card: power pad LED, S size, flip-in + gate-out
        sw17 = page.evaluate("""(() => {
          const n = nodes.get('switch');
          if (!n) return null;
          const pad = n.el.querySelector('.powpad');
          return {size: n.size, pad: !!pad,
                  on: pad && pad.classList.contains('on'),
                  ports: n.ports.map(p => [p.dir, p.sig, p.label, !!p.quiet])};
        })()""")
        check("switch card renders with an (unlit) power pad LED",
              bool(sw17) and sw17["pad"] and sw17["on"] is False, str(sw17))
        check("switch card sizes to S", sw17 and sw17["size"] == "S",
              str(sw17))
        check("switch ports: quiet ping flip-in + gate level-out",
              bool(sw17) and ["in", "ping", "flip", True] in sw17["ports"]
              and ["out", "gate", "level", False] in sw17["ports"],
              str(sw17))

        # pad click flips locally + sends set_switch on:true
        page.evaluate("window.__sent.length = 0")
        page.evaluate("nodes.get('switch').el.querySelector('.powpad').click()")
        sent = page.evaluate("window.__sent")
        check("power pad click sends set_switch on:true",
              {"type": "set_switch", "id": "switch", "on": True} in sent,
              str(sent))
        check("power pad lights locally on click", page.evaluate(
            "nodes.get('switch').el.querySelector('.powpad')"
            ".classList.contains('on')"))

        # a live {"kind":"gate"} event drives the LED (off, then on)
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'switch', on: false}})""")
        check("gate event on:false unlights the switch LED", not page.evaluate(
            "nodes.get('switch').el.querySelector('.powpad')"
            ".classList.contains('on')"))
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'switch', on: true}})""")
        check("gate event on:true lights the switch LED", page.evaluate(
            "nodes.get('switch').el.querySelector('.powpad')"
            ".classList.contains('on')"))

        # the Logic card @AND: op chip, ONE bare fan-in, out LED
        lg17 = page.evaluate("""(() => {
          const n = nodes.get('logic');
          if (!n) return null;
          return {size: n.size,
                  sub: (n.el.querySelector('.sub')||{}).textContent,
                  led: !!n.el.querySelector('.gled'),
                  ledOn: n.el.querySelector('.gled')
                    && n.el.querySelector('.gled').classList.contains('on'),
                  ins: n.ports.filter(p => p.sig === 'gate' && p.dir === 'in')
                    .map(p => p.gate),
                  outs: n.ports.filter(p => p.sig === 'gate' && p.dir === 'out')
                    .length,
                  plus: portAllowsPlus(n.ports.find(p => p.gate === 'logic'))};
        })()""")
        check("logic card renders @AND with ONE bare gate fan-in",
              bool(lg17) and lg17["ins"] == ["logic"] and lg17["outs"] == 1,
              str(lg17))
        check("logic sub shows the current op", lg17
              and lg17["sub"] == "AND · gate logic", str(lg17))
        check("logic fan-in allows multiple wires (+)",
              lg17 and lg17["plus"] is True, str(lg17))
        check("logic card has an (unlit) output LED",
              lg17 and lg17["led"] and lg17["ledOn"] is False, str(lg17))
        check("logic card sizes to S", lg17 and lg17["size"] == "S",
              str(lg17))

        # gate wires drew from state: switch→echo:pwr + logic→deck:play in
        # the gate family; the ping wire button→switch stays ping-colored
        gw17 = page.evaluate("""(() => {
          const gw = wires.find(w => w.from.node.gid === 'switch');
          const lw = wires.find(w => w.from.node.gid === 'logic');
          const pw = wires.find(w => w.from.node.gid === 'button');
          const d = (w) => w && {sig: w.sig, to: w.to.node.gid,
            label: w.to.port.label, color: w.color,
            stroke: w.topEl.getAttribute('stroke'),
            fam: LINES.gate.includes(w.color)};
          return {gw: d(gw), lw: d(lw), pw: d(pw)};
        })()""")
        check("switch→module pwr wire draws in the gate family",
              bool(gw17["gw"]) and gw17["gw"]["sig"] == "gate"
              and gw17["gw"]["to"] == "m:echo"
              and gw17["gw"]["label"] == "pwr" and gw17["gw"]["fam"],
              str(gw17))
        check("gate wire stroke carries the gate color",
              bool(gw17["gw"])
              and gw17["gw"]["stroke"] == gw17["gw"]["color"], str(gw17))
        check("logic→deck:play wire lands on the deck's play toggle-in",
              bool(gw17["lw"]) and gw17["lw"]["sig"] == "gate"
              and gw17["lw"]["to"] == "deck"
              and gw17["lw"]["label"] == "play", str(gw17))
        check("button→switch flip wire stays in the ping family",
              bool(gw17["pw"]) and gw17["pw"]["sig"] == "ping"
              and gw17["pw"]["to"] == "switch", str(gw17))
        check("legend has a gate swatch", page.evaluate(
            "!!document.querySelector('[data-legend=gate]')"))

        # grammar: gate→pwr connects; gate→note-in / gate→trigger-in
        # refused; ping→pwr and ping→switch-flip connect
        acts17 = page.evaluate("""(() => {
          const sw = nodes.get('switch'), lg = nodes.get('logic');
          const echo = nodes.get('m:echo'), arp = nodes.get('arp');
          const ton = nodes.get('tonic'), btn = nodes.get('button');
          const gout = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'gate' && p.dir === 'out')});
          const pwr = (n) => ({node: n,
            port: n.ports.find(p => p.label === 'pwr')});
          const pout = {node: btn,
            port: btn.ports.find(p => p.sig === 'ping' && p.dir === 'out')};
          window.__sent.length = 0;
          const gatePwr = connectAction(gout(sw), pwr(echo));
          if (gatePwr) gatePwr();
          const gateNote = connectAction(gout(sw), {node: arp,
            port: arp.ports.find(p => p.sig === 'ctl' && p.dir === 'in')});
          const gateTrig = connectAction(gout(sw), {node: ton,
            port: ton.ports.find(p => p.sig === 'ping')});
          const pingPwr = connectAction(pout, pwr(echo));
          if (pingPwr) pingPwr();
          const pingSwitch = connectAction(pout, {node: sw,
            port: sw.ports.find(p => p.sig === 'ping' && p.dir === 'in')});
          if (pingSwitch) pingSwitch();
          const logicSelf = connectAction(gout(lg), {node: lg,
            port: lg.ports.find(p => p.gate === 'logic')});
          return {sent: window.__sent, gatePwr: !!gatePwr,
                  gateNote: !!gateNote, gateTrig: !!gateTrig,
                  pingPwr: !!pingPwr, pingSwitch: !!pingSwitch,
                  logicSelf: !!logicSelf};
        })()""")
        check("gate-out → module pwr connects (ctl_wire add)",
              acts17["gatePwr"] and
              {"type": "ctl_wire", "action": "add", "from": "switch",
               "to": "echo:pwr"} in acts17["sent"], str(acts17))
        check("gate-out → note-in refused", not acts17["gateNote"],
              str(acts17))
        check("gate-out → deriver trigger-in refused", not acts17["gateTrig"],
              str(acts17))
        check("ping-out → module pwr connects (alternator)",
              acts17["pingPwr"] and
              {"type": "ctl_wire", "action": "add", "from": "button",
               "to": "echo:pwr"} in acts17["sent"], str(acts17))
        check("ping-out → switch flip-in connects",
              acts17["pingSwitch"] and
              {"type": "ctl_wire", "action": "add", "from": "button",
               "to": "switch"} in acts17["sent"], str(acts17))
        check("logic self-wire refused", not acts17["logicSelf"],
              str(acts17))

        # op chip cycles AND→OR and sends set_logic
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const n = nodes.get('logic');
          [...n.el.querySelectorAll('label')].find(l => l.title === 'op')
            .parentElement.querySelector('.chip').click();
        }""")
        page.wait_for_timeout(120)
        sent = page.evaluate("window.__sent")
        check("op chip cycles + sends set_logic AND→OR",
              {"type": "set_logic", "id": "logic", "op": "OR"} in sent,
              str(sent))

        # SR latch payload: TWO named quiet ins (set/reset endpoints)
        st17b = json.loads(json.dumps(st17))
        st17b["logics"][0]["op"] = "SR latch"
        page.evaluate("(s) => __msg({type: 'state', ...s})", st17b)
        page.wait_for_timeout(400)
        sr17 = page.evaluate("""(() => {
          const n = nodes.get('logic');
          return {sub: (n.el.querySelector('.sub')||{}).textContent,
                  ins: n.ports.filter(p => p.sig === 'gate' && p.dir === 'in')
                    .map(p => p.gate)};
        })()""")
        check("SR latch renders TWO named gate-ins (set/reset)",
              sr17["ins"] == ["logic:set", "logic:reset"], str(sr17))
        # a gate event lights the logic card's output LED
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'logic', on: true}})""")
        check("gate event lights the logic output LED", page.evaluate(
            "nodes.get('logic').el.querySelector('.gled')"
            ".classList.contains('on')"))

        # head power LEDs: module/arp/drums carry the LED button + a quiet
        # ":pwr" gate toggle-in anchored to the head; deck carries FOUR
        # button-ins with the deck:… endpoints
        eps17 = page.evaluate("""(() => {
          const g = (gid) => {
            const n = nodes.get(gid);
            return n && n.ports.filter(p => p.sig === 'gate' && p.dir === 'in')
              .map(p => [p.gate, !!p.quiet, !!(p.rowEl && p.rowEl.isConnected)]);
          };
          return {echo: g('m:echo'), arp: g('arp'), drums: g('drums'),
                  deck: g('deck')};
        })()""")
        check("module card carries a quiet head-anchored :pwr toggle-in",
              eps17["echo"] == [["echo:pwr", True, True]], str(eps17))
        check("arp carries arp:pwr", eps17["arp"] == [["arp:pwr", True, True]],
              str(eps17))
        check("drums carries drums:pwr",
              eps17["drums"] == [["drums:pwr", True, True]], str(eps17))
        check("deck carries the four button toggle-ins",
              eps17["deck"] == [["deck:rec", True, True],
                                ["deck:stop", True, True],
                                ["deck:play", True, True],
                                ["deck:clear", True, True]], str(eps17))

        # the head LED is a real button: lit = enabled; click toggles the
        # bypass class AND still sends exactly what the checkbox sent
        led17 = page.evaluate("""(() => {
          const n = nodes.get('m:echo');
          const el = n.el.querySelector('.onoff');
          return {tag: el.tagName, role: el.getAttribute('role'),
                  on: el.classList.contains('on'),
                  byp: n.el.classList.contains('bypassed')};
        })()""")
        check("module head LED is a lit switch button when enabled",
              led17 == {"tag": "BUTTON", "role": "switch", "on": True,
                        "byp": False}, str(led17))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("nodes.get('m:echo').el.querySelector('.onoff').click()")
        sent = page.evaluate("window.__sent")
        check("head LED click sends set_enabled false",
              {"type": "set_enabled", "key": "echo", "enabled": False}
              in sent, str(sent))
        led17b = page.evaluate("""(() => {
          const n = nodes.get('m:echo');
          return {on: n.el.querySelector('.onoff').classList.contains('on'),
                  byp: n.el.classList.contains('bypassed')};
        })()""")
        check("head LED click unlights + toggles the bypassed class",
              led17b == {"on": False, "byp": True}, str(led17b))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("nodes.get('m:echo').el.querySelector('.onoff').click()")
        sent = page.evaluate("window.__sent")
        check("second click re-enables (set_enabled true, bypass off)",
              {"type": "set_enabled", "key": "echo", "enabled": True}
              in sent and page.evaluate(
                  "!nodes.get('m:echo').el.classList.contains('bypassed')"),
              str(sent))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
