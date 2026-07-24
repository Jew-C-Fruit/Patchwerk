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
  7. Binary sources (items 4+5 + binary rework): button/clock trigger
     cards render; the deriver grows a QUIET node-scoped trigger-in; bin
     wires draw in the yellow→pink family; the grammar is strict
     (bin↮mod/ctl/audio in BOTH directions); the keycap press sends
     button_down/button_up (momentary) and a bound computer key does the
     same on keydown/keyup; pairing binds only UNASSIGNED keys (note keys
     can never bind) and the binding chip updates.
  8. Deriver split (item 6): the Estimator card carries knob rows + the
     12-bar histogram viz that breathes on "deriver" analysis messages
     (presence/scores toggle, committed vs leading marking, confidence);
     the Literal card's chips cycle and send set_literal; both derivers
     take notes/emit notes/accept ping triggers in the grammar.
  10. Threshold (item 8 + binary rework): the card renders from
     state.thresholds (small, level/hyst/edge rows); its cv-in is a QUIET
     single-input mod handle riding the level row; LFO-out → cv-in
     connects via threshold_wire (targeted remove on cut); its out is a
     BINARY level now — it draws/wires like button/clock; ping events
     (rising-edge taps) still pulse the pad-less card.
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
  17. The BINARY plane (binary rework, 07-23; card redesign, 07-24): ONE
     hi/lo signal kind ("bin", yellow→pink family, ONE "binary" legend
     swatch — ping and gate merged, the Switch node is GONE). Binary
     cards wear COLORED TITLE BANNERS instead of the family stripe:
     yellow (--bin) for sources (momentary button/clock/threshold),
     orange (--binlatch) for latch button/logic/relay. The Logic card
     has NO title — its banner carries a clickable CIRCUIT SELECTOR
     (cycles ops, set_logic) — and per-op NAMED single-input ins
     (":a"/":b", ":a" only for NOT, ":set"/":reset" for SR — bare-id
     dsts refused, no + handle) riding pin labels on the circuit-diagram
     canvas (per-op gate glyphs; traces light from live levels —
     pixel-diffed lo vs hi); its out LED sits at the card's RIGHT-HAND
     CENTER with the bin-out handle FIXED in line with it. The Button's
     banner carries a BTN tag + the MOM|LATCH segmented toggle
     (set_button latch; banner repaints with the mode) + momentary
     down/up messages + the same right-center LED/out-handle pairing;
     head enable power-LED buttons with QUIET ":pwr" level-ins
     (modules/arp/drums) and the deck's four button-ins; the bin
     grammar (bin→pwr/deck/logic-in/deriver-trigger yes;
     bin→note/mod/audio + self-wires no).
  18. GUI pass B (07-23): the XS card size — 4.5u x 4.5u, OPT-IN
     (cfg.allowXS: Button/Clock/Logic/Relay; every other card's floor
     stays S), quadrant-resolution slots (2 per half block at a 5.5u
     pitch = 4.5u + the 1u mid gutter, 4 per block), quadrant drops
     (pxToSlotMem), half-step S-vs-XS and quarter-step XS-vs-XS shoves,
     4-up tidy pour, and [bx,by,half,"XS",hh] layout memory. The Relay
     card (palette LOGIC section, spawn_relay/remove_relay): a
     type-agnostic switched junction whose circuits pair TOP in / BOTTOM
     out handles ("relay:k" both ways), each wearing its CLAIMED kind's
     sig (audio/ctl/bin from state.relays[].circuits) or the neutral
     "any" that accepts any kind's drag; circuit OUTs connect onward
     only per their claimed kind; the center power button sends
     set_relay + follows {"kind":"gate"}; "relay:ctl" is a quiet
     single-input bin level-in; AUTO-SIZE XS(4 circuits)↔S(9) from the
     circuits in use; audio hops (never in the RESOLVED state.wires)
     draw from the GUI-kept relayAW store, and every circuit wire cuts
     with its kind's remove (ctl_wire / graph_wire). With all 4 XS slots
     claimed, a little + right of the 4th slot latches the next wire
     onto circuit 5 ("relay:5"), whose claim expands the card to S
     (Cole, 07-24).
  19. Transport cards (item 9, 07-24): a top-line "transport" palette
     section (after LOGIC) spawning canvas VIEWS of the ONE global
     transport (spawn_transport_card play/tempo; cards build from
     state.transport_cards; kill = remove_transport_card). Play/Stop
     ("tplay", XS): ONE big bold button showing the CURRENT state — red
     ⏹ STOP stopped / green ⏵ PLAY running — whose click toggles via
     set_transport {playing}; a quiet bin LEVEL-in "run" →
     "transport:run". Tempo/Click ("ttempo", M): tempo slider (40–220,
     the top bar's mapping), TIME SIG detents mirroring the top bar's
     meter select, DOWNBEAT detents re-derived from the CURRENT meter
     (text "beat N", 1-based), click+accent power-LED toggles with quiet
     bin level-ins ("transport:click"/":accent"), a quiet bin TRIG-in
     "tap" ("transport:tap") riding the tempo row, and the LIVE
     metronome strip (one dot per beat, current lit via the "beat"
     broadcast, downbeat dot bigger/accented). Cards and top bar send
     the SAME set_transport messages and both follow the state
     broadcast; the endpoints are fan-in; wires cut via ctl_wire
     remove; old servers (no transport_cards/downbeat) render no cards
     and no errors.
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


def relay_clip(page):
    """A viewport-clamped clip rect around the relay card (screenshots)."""
    page.evaluate("nodes.get('relay').el.scrollIntoView("
                  "{block: 'center', inline: 'center'})")
    page.wait_for_timeout(120)
    return page.evaluate("""(() => {
      const b = nodes.get('relay').el.getBoundingClientRect();
      const x = Math.max(0, b.x - 16), y = Math.max(0, b.y - 16);
      return {x, y,
              width: Math.min(innerWidth - x, b.width + 32),
              height: Math.min(innerHeight - y, b.height + 32)};
    })()""")


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
        # 7 — binary sources: trigger cards, quiet trigger-in, grammar
        # (the buttons payload here PREDATES latch/on on purpose — an old
        # server's buttons must still build → momentary, LED dark)
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

        check("PRIMARY_SIGS gained bin (ONE binary kind)",
              page.evaluate("PRIMARY_SIGS.has('bin')"))
        check("PRIMARY_SIGS has no ping/gate kinds", page.evaluate(
            "!PRIMARY_SIGS.has('ping') && !PRIMARY_SIGS.has('gate')"))
        check("--bin CSS var present (yellow base)", page.evaluate(
            "!!getComputedStyle(document.documentElement)"
            ".getPropertyValue('--bin').trim()"))
        check("no --ping/--gate CSS vars remain", page.evaluate(
            "!getComputedStyle(document.documentElement)"
            ".getPropertyValue('--ping').trim() && "
            "!getComputedStyle(document.documentElement)"
            ".getPropertyValue('--gate').trim()"))
        check("ONE binary legend entry replaces ping+gate", page.evaluate(
            "!!document.querySelector('[data-legend=binary]') && "
            "!document.querySelector('[data-legend=ping]') && "
            "!document.querySelector('[data-legend=gate]')"))
        for gid in ("button", "clock"):
            check(f"trigger card renders: {gid}",
                  page.evaluate(f"nodes.has('{gid}')"))

        trig = page.evaluate("""(() => {
          const n = nodes.get('tonic');
          const p = n.ports.find(p => p.sig === 'bin');
          return p ? {dir: p.dir, quiet: !!p.quiet, label: p.label} : null;
        })()""")
        check("deriver has a QUIET node-scoped bin trigger-in",
              trig == {"dir": "in", "quiet": True, "label": "trigger"},
              str(trig))

        # trigger cards are SMALL (Cole, 2026-07-22): both opt into the
        # XS class since GUI pass B and measure into 4.5x4.5
        for gid in ("button", "clock"):
            sz = page.evaluate(f"nodes.get('{gid}').size")
            check(f"trigger card {gid} sizes to XS", sz == "XS", str(sz))

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
          const t = lay && lay.handles.find(H => H.sig === 'bin');
          return {rowY, trigY: t && t.y};
        })()""")
        check("deriver trigger-in aligns with the trigger row (rendered)",
              aln["trigY"] is not None
              and abs(aln["trigY"] - aln["rowY"]) < 1.0, str(aln))

        wsig = page.evaluate("""(() => {
          const w = wires.find(w => w.from.node.gid === 'button');
          return w && {sig: w.sig, fam: LINES.bin.includes(w.color)};
        })()""")
        check("button→deriver wire draws in the bin family",
              bool(wsig) and wsig["sig"] == "bin" and wsig["fam"], str(wsig))

        # strict grammar: every cross-kind combination refused, both ways
        combos = page.evaluate("""(() => {
          const p = (node, dir, sig) =>
            ({node, port: {dir, sig, label: sig + '-' + dir}});
          const btn = nodes.get('button'), ton = nodes.get('tonic');
          const sgn = nodes.get('m:signal_gen'), arp = nodes.get('arp');
          return {
            bin_to_trig: !!connectAction(p(btn, 'out', 'bin'),
              {node: ton, port: ton.ports.find(q => q.sig === 'bin')}),
            bin_to_mod:  !!connectAction(p(btn, 'out', 'bin'), p(sgn, 'in', 'mod')),
            mod_to_bin:  !!connectAction(p(sgn, 'out', 'mod'),
              {node: ton, port: ton.ports.find(q => q.sig === 'bin')}),
            bin_to_ctl:  !!connectAction(p(btn, 'out', 'bin'), p(arp, 'in', 'ctl')),
            ctl_to_bin:  !!connectAction(p(nodes.get('keys'), 'out', 'ctl'),
              {node: ton, port: ton.ports.find(q => q.sig === 'bin')}),
            bin_to_audio: !!connectAction(p(btn, 'out', 'bin'),
              p(nodes.get('m:echo'), 'in', 'audio')),
          };
        })()""")
        check("bin-out → trigger-in connects", combos["bin_to_trig"],
              str(combos))
        for bad in ("bin_to_mod", "mod_to_bin", "bin_to_ctl",
                    "ctl_to_bin", "bin_to_audio"):
            check(f"grammar refuses {bad}", not combos[bad], str(combos))

        # the keycap is a MOMENTARY switch: pointerdown → button_down,
        # pointerup → button_up (fire_button stays server-side click compat)
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const cap = nodes.get('button').el.querySelector('.keycap');
          cap.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("keycap pointerdown sends button_down (momentary)",
              {"type": "button_down", "id": "button"} in sent, str(sent))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const cap = nodes.get('button').el.querySelector('.keycap');
          cap.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
          cap.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("keycap release sends button_up (and no fire_button)",
              {"type": "button_up", "id": "button"} in sent
              and not [m for m in sent if m.get("type") == "fire_button"],
              str(sent))

        # pairing: arm via the ↻ rebind icon (07-24: the bindline is gone),
        # then an ASSIGNED (note) key must NOT bind…
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('button').el.querySelector('.rebind').click()")
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

        # the bound key IS the keycap now: keydown holds the momentary
        # button down, keyup releases (binding kept client-side)
        page.evaluate("window.__sent.length = 0")
        page.keyboard.down("n")
        page.wait_for_timeout(60)
        sent = page.evaluate("window.__sent")
        check("bound keydown sends button_down (momentary)",
              {"type": "button_down", "id": "button"} in sent, str(sent))
        check("bound key does not ALSO play a note",
              not [m for m in sent if m.get("type") == "note_on"], str(sent))
        page.evaluate("window.__sent.length = 0")
        page.keyboard.up("n")
        page.wait_for_timeout(60)
        sent = page.evaluate("window.__sent")
        check("bound keyup sends button_up",
              {"type": "button_up", "id": "button"} in sent, str(sent))

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
          const t = lay && lay.handles.find(H => H.sig === 'bin');
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
          const p = n.ports.find(p => p.sig === 'bin');
          return p ? {dir: p.dir, quiet: !!p.quiet} : null;
        })()""")
        check("literal has a quiet bin trigger-in",
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
        # 10 — threshold (item 8 + binary rework): CV edge → binary level
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
        check("threshold → deriver wire draws in the bin family",
              page.evaluate(
                  "(wires.find(w => w.from.node.gid === 'threshold') || {})"
                  ".sig") == "bin")
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "wires.find(w => w.sig === 'mod'"
            " && w.to.node.gid === 'threshold').cutAction()")
        sent = page.evaluate("window.__sent")
        check("cv wire cut sends threshold_wire remove",
              {"type": "threshold_wire", "action": "remove",
               "id": "threshold", "lfo": "lfo"} in sent, str(sent))

        # grammar: LFO-out → cv-in connects via threshold_wire add;
        # the threshold's binary out lands on a trigger-in, never a ctl-in
        acts = page.evaluate("""(() => {
          const lfo = nodes.get('lfo:lfo'), thr = nodes.get('threshold');
          const ton = nodes.get('tonic'), arp = nodes.get('arp');
          const lout = {node: lfo, port: lfo.ports.find(p => p.sig === 'mod')};
          const cvin = {node: thr,
                        port: thr.ports.find(p => p.sig === 'mod' && p.dir === 'in')};
          const bout = {node: thr,
                        port: thr.ports.find(p => p.sig === 'bin' && p.dir === 'out')};
          window.__sent.length = 0;
          const add = connectAction(lout, cvin);
          if (add) add();
          const trig = connectAction(bout,
            {node: ton, port: ton.ports.find(p => p.sig === 'bin')});
          const bad = arp && connectAction(bout,
            {node: arp, port: arp.ports.find(p => p.sig === 'ctl' && p.dir === 'in')});
          return {addSent: window.__sent, trig: !!trig, bad: !!bad};
        })()""")
        check("LFO-out → cv-in connects (threshold_wire add)",
              {"type": "threshold_wire", "action": "add", "id": "threshold",
               "lfo": "lfo"} in acts["addSent"], str(acts))
        check("threshold bin-out → deriver trigger-in connects",
              acts["trig"], str(acts))
        check("threshold bin-out → ctl-in refused", not acts["bad"],
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
                            "transport", "voices", "fx", "monitors"],
              str(pal["h3"]))
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
                      "armed": False, "latch": False, "on": False},
                     {"id": "button.2", "binding": None, "armed": False,
                      "latch": False, "on": False},
                     {"id": "button.3", "binding": {"kind": "cc", "cc": 21},
                      "armed": False, "latch": False, "on": False}],
            clocks=[{"id": "clock", "division": "1/32",
                     "divisions": clock_ladder}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st16)
        page.wait_for_timeout(500)

        # keycap renders the binding LARGE: key glyph / CC number / unbound
        # (07-24: SQUARE keycap; the bindline is gone — the keycap title
        # carries the readable label and the ↻ icon re-pairs)
        caps = page.evaluate("""(() => {
          const g = (gid) => {
            const n = nodes.get(gid);
            const cap = n.el.querySelector('.keycap');
            return {txt: cap.textContent, unbound:
                      cap.classList.contains('unbound'),
                    cc: !!cap.querySelector('small'),
                    title: cap.title,
                    word: cap.classList.contains('word'),
                    sq: [cap.offsetWidth, cap.offsetHeight],
                    rebind: !!n.el.querySelector('.rebind'),
                    line: !n.el.querySelector('.bindline'),
                    size: n.size};
          };
          return {b: g('button'), b2: g('button.2'), b3: g('button.3')};
        })()""")
        check("bound key renders its glyph on the keycap",
              caps["b"]["txt"] == "N" and not caps["b"]["cc"], str(caps))
        check("keycap is SQUARE and the readable label rides its title",
              caps["b"]["sq"][0] == caps["b"]["sq"][1]
              and caps["b"]["title"] == "key N", str(caps))
        check("bindline is gone; the ↻ rebind icon replaces it",
              all(caps[k]["rebind"] and caps[k]["line"]
                  for k in ("b", "b2", "b3")), str(caps))
        check("unbound keycap shows ＋",
              caps["b2"]["txt"] == "＋" and caps["b2"]["unbound"], str(caps))
        check("CC binding renders CC prefix + number",
              caps["b3"]["cc"] and "21" in caps["b3"]["txt"], str(caps))
        check("button cards still measure into XS",
              all(caps[k]["size"] == "XS" for k in ("b", "b2", "b3")),
              str(caps))

        # whole-word bindings ("L SHIFT") render whole on the square cap
        stw = json.loads(json.dumps(st16))
        stw["buttons"][1]["binding"] = {"kind": "key", "code": "ShiftLeft"}
        page.evaluate("(s) => __msg({type: 'state', ...s})", stw)
        page.wait_for_timeout(350)
        word = page.evaluate("""(() => {
          const cap = nodes.get('button.2').el.querySelector('.keycap');
          const g = cap.querySelector('.kglyph');
          return {txt: g.textContent, word: cap.classList.contains('word'),
                  fits: g.scrollWidth <= cap.clientWidth
                        && g.scrollHeight <= cap.clientHeight};
        })()""")
        check("ShiftLeft renders as the whole word L SHIFT",
              word["txt"] == "L SHIFT" and word["word"], str(word))
        check("the word fits inside the square keycap", word["fits"],
              str(word))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st16)
        page.wait_for_timeout(350)

        # the level LED sits at the card's RIGHT-HAND CENTER and the
        # bin-out handle is fixed exactly in line with it (07-24: the ◉
        # heartbeat icon is gone — the LED is the out-side indicator)
        led16 = page.evaluate("""(() => {
          rerouteAll();   // measure a settled layout, not a mid-shove one
          const n = nodes.get('button');
          const led = n.el.querySelector('.gled.side');
          const H = n.lay.handles.find(h => h.sig === 'bin'
                                            && h.side === 'out');
          if (!led || !H) return null;
          const r = n.el.getBoundingClientRect(),
                lr = led.getBoundingClientRect();
          const ly = lr.top + lr.height / 2 - r.top;
          return {noIcon: !n.el.querySelector('.fireicon'),
                  right: lr.left - r.left > r.width * 0.7,
                  centered: Math.abs(ly - r.height / 2) < 3,
                  edge: H.edge,
                  // offset coords on BOTH sides (rects carry the zoom)
                  hy: Math.abs((H.y - n.el.offsetTop)
                               - n.el.offsetHeight / 2)};
        })()""")
        check("button LED sits at the right-hand center of the card",
              led16 is not None and led16["noIcon"] and led16["right"]
              and led16["centered"], str(led16))
        check("bin-out handle is fixed at the right edge, in line with "
              "the LED", led16 is not None and led16["edge"] == "R"
              and led16["hy"] < 3, str(led16))

        # pressing pulses BOTH the keycap and the right-center LED
        pulsed = page.evaluate("""(() => {
          const n = nodes.get('button');
          const cap = n.el.querySelector('.keycap');
          cap.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
          const out = {cap: cap.classList.contains('pulse'),
                       led: n.sideLed.classList.contains('pulse')};
          cap.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
          return out;
        })()""")
        check("press pulses keycap + right-center LED",
              pulsed["cap"] and pulsed["led"], str(pulsed))

        # arming flips the keycap to … and Escape restores it
        page.evaluate(
            "nodes.get('button.2').el.querySelector('.rebind').click()")
        armed = page.evaluate("""(() => {
          const n = nodes.get('button.2');
          return {cap: n.el.querySelector('.keycap').textContent,
                  arming: n.el.querySelector('.rebind')
                    .classList.contains('arming')};
        })()""")
        check("arming shows … on the keycap + lights the ↻",
              armed == {"cap": "…", "arming": True}, str(armed))
        page.keyboard.press("Escape")
        page.wait_for_timeout(60)
        armed = page.evaluate("""(() => {
          const n = nodes.get('button.2');
          return {cap: n.el.querySelector('.keycap').textContent,
                  arming: n.el.querySelector('.rebind')
                    .classList.contains('arming')};
        })()""")
        check("Escape cancels pairing back to ＋",
              armed == {"cap": "＋", "arming": False}, str(armed))

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
        check("clock card still measures into XS", sz16 == "XS", str(sz16))

        # ================================================================
        # 17 — the BINARY plane: logic named ins + circuit viz, button
        # modes + down/up, ONE bin wire kind, Switch gone, Relay tolerated
        # ================================================================
        st17 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "voice"},
                       {"from": "logic", "to": "echo:pwr"},
                       {"from": "logic", "to": "deck:play"},
                       {"from": "button", "to": "logic:a"},
                       {"from": "threshold", "to": "logic:b"}],
            tonics=[{"id": "tonic", "every": "1 bar", "everies": ["1 bar"],
                     "octave": 2, "root": None}],
            buttons=[{"id": "button", "binding": None, "armed": False,
                      "latch": False, "on": False},
                     {"id": "button.2", "binding": None, "armed": False,
                      "latch": True, "on": True}],
            thresholds=[{"id": "threshold", "level": 0.0,
                         "hysteresis": 0.02, "mode": "rising",
                         "modes": ["rising", "falling", "both"],
                         "source": None, "on": False}],
            logics=[{"id": "logic", "op": "AND",
                     "ops": ["AND", "OR", "NOT", "XOR", "SR latch"],
                     "out": False}],
            # binary rework: relays ride the snapshot (pass B renders the
            # card — section 18); there is NO switches key any more
            relays=[{"id": "relay", "closed": False, "circuits": {}}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st17)
        page.wait_for_timeout(500)

        # palette: the LOGIC section holds Logic + Relay (Switch is
        # gone since the binary rework) and still follows TRIGGERS
        pal17 = page.evaluate("""(() => {
          const hs = [...document.querySelectorAll('#palette h3')];
          const logicH = hs.find(h => h.textContent === 'logic');
          const btns = [];
          for (let el = logicH && logicH.nextElementSibling;
               el && el.tagName === 'BUTTON'; el = el.nextElementSibling)
            btns.push(el.textContent);
          return {h3: hs.map(h => h.textContent), btns,
                  all: [...document.querySelectorAll('#palette button')]
                    .map(b => b.textContent)};
        })()""")
        check("palette LOGIC section sits right after TRIGGERS",
              "logic" in pal17["h3"] and pal17["h3"].index("logic")
              == pal17["h3"].index("triggers") + 1, str(pal17["h3"]))
        check("LOGIC section holds Logic + Relay (Switch gone)",
              pal17["btns"] == ["Logic", "Relay"], str(pal17))
        check("no Switch palette button anywhere",
              "Switch" not in pal17["all"], str(pal17["all"]))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          [...document.querySelectorAll('#palette button')]
            .find(b => b.textContent === 'Logic').click();
        }""")
        sent = page.evaluate("window.__sent")
        check("palette Logic sends spawn_logic",
              {"type": "spawn_logic"} in sent, str(sent))

        check("no switch card builds (the node type is gone)", page.evaluate(
            "![...nodes.keys()].some(k => k.startsWith('switch'))"))
        check("state.relays builds a Relay card now (GUI pass B)",
              page.evaluate("nodes.has('relay')"))

        # the Logic card @AND (07-24 rework): NO title/sub — the ORANGE
        # banner carries the clickable circuit selector; TWO named SINGLE-
        # INPUT ins riding A/B pin labels, one bin out FIXED at the right
        # edge's center, the right-center LED, the circuit canvas
        lg17 = page.evaluate("""(() => {
          const n = nodes.get('logic');
          if (!n) return null;
          const pins = [...n.el.querySelectorAll('.lpin')];
          const ins = n.ports.filter(p => p.sig === 'bin' && p.dir === 'in');
          const head = n.el.querySelector('.head');
          return {size: n.size,
                  banner: head.classList.contains('banner'),
                  bg: head.style.background,
                  noTitle: !n.el.querySelector('.title'),
                  opsel: (n.el.querySelector('.opsel')||{}).textContent,
                  led: !!n.el.querySelector('.gled.side'),
                  ledOn: n.el.querySelector('.gled')
                    && n.el.querySelector('.gled').classList.contains('on'),
                  canvas: !!n.el.querySelector('canvas.lvz'),
                  pins: pins.map(p => p.textContent),
                  ins: ins.map(p => [p.ep, p.label, !!p.quiet, !!p.single,
                                     portAllowsPlus(p),
                                     !!(p.rowEl && p.rowEl.isConnected)]),
                  outs: n.ports.filter(p => p.sig === 'bin'
                                            && p.dir === 'out').length};
        })()""")
        check("logic@AND: named single-input ins :a/:b labeled A/B (no +)",
              bool(lg17) and lg17["ins"] == [
                  ["logic:a", "A", True, True, False, True],
                  ["logic:b", "B", True, True, False, True]], str(lg17))
        check("logic@AND: A/B pin labels ride the circuit viz",
              lg17 and lg17["pins"] == ["A", "B"], str(lg17))
        check("logic has ONE bin out + right-center LED (unlit) + circuit "
              "canvas", lg17 and lg17["outs"] == 1 and lg17["led"]
              and lg17["ledOn"] is False and lg17["canvas"], str(lg17))
        check("logic head is an ORANGE banner with NO title",
              lg17 and lg17["banner"] and lg17["noTitle"]
              and "binlatch" in lg17["bg"], str(lg17))
        check("banner circuit selector shows the current op", lg17
              and lg17["opsel"] == "AND", str(lg17))
        check("logic card sizes to XS (opted in, pass B)",
              lg17 and lg17["size"] == "XS", str(lg17))

        # the logic out handle sits at the right edge's CENTER, in line
        # with the right-center LED (Cole, 07-24)
        lgout = page.evaluate("""(() => {
          rerouteAll();   // measure a settled layout, not a mid-shove one
          const n = nodes.get('logic');
          const H = n.lay.handles.find(h => h.sig === 'bin'
                                            && h.side === 'out');
          return H && {edge: H.edge,
                       dy: Math.abs((H.y - n.el.offsetTop)
                                    - n.el.offsetHeight / 2)};
        })()""")
        check("logic out handle fixed at right-center (LED-aligned)",
              bool(lgout) and lgout["edge"] == "R" and lgout["dy"] < 3,
              str(lgout))

        # the A and B in-handles land on SEPARATE lines (per-pin rows)
        aby = page.evaluate("""(() => {
          const lay = nodes.get('logic').lay;
          const y = (ep) => {
            const H = lay.handles.find(h => h.port && h.port.ep === ep);
            return H ? H.y : null;
          };
          return {a: y('logic:a'), b: y('logic:b')};
        })()""")
        check("A and B handles sit on separate rows",
              aby["a"] is not None and aby["b"] is not None
              and abs(aby["a"] - aby["b"]) > 4, str(aby))

        # wires from state: ONE bin kind — logic→echo:pwr, logic→deck:play,
        # button→logic:a, threshold→logic:b all draw bin-family colors
        gw17 = page.evaluate("""(() => {
          const d = (pred) => {
            const w = wires.find(pred);
            return w && {sig: w.sig, to: w.to.node.gid,
              ep: w.to.port.ep || null, color: w.color,
              stroke: w.topEl.getAttribute('stroke'),
              fam: LINES.bin.includes(w.color)};
          };
          return {pwr: d(w => w.from.node.gid === 'logic'
                              && w.to.node.gid === 'm:echo'),
                  play: d(w => w.from.node.gid === 'logic'
                               && w.to.node.gid === 'deck'),
                  ba: d(w => w.from.node.gid === 'button'),
                  tb: d(w => w.from.node.gid === 'threshold')};
        })()""")
        check("logic→module pwr wire draws in the bin family",
              bool(gw17["pwr"]) and gw17["pwr"]["sig"] == "bin"
              and gw17["pwr"]["ep"] == "echo:pwr" and gw17["pwr"]["fam"],
              str(gw17))
        check("bin wire stroke carries its line color",
              bool(gw17["pwr"])
              and gw17["pwr"]["stroke"] == gw17["pwr"]["color"], str(gw17))
        check("logic→deck:play lands on the deck's play button-in",
              bool(gw17["play"]) and gw17["play"]["ep"] == "deck:play",
              str(gw17))
        check("button→logic:a lands on the A pin (bin family)",
              bool(gw17["ba"]) and gw17["ba"]["sig"] == "bin"
              and gw17["ba"]["ep"] == "logic:a" and gw17["ba"]["fam"],
              str(gw17))
        check("threshold→logic:b lands on the B pin",
              bool(gw17["tb"]) and gw17["tb"]["ep"] == "logic:b",
              str(gw17))

        # circuit viz: deterministic, event-driven — a lo render and a hi
        # render pixel-diff (traces light from the derived source levels)
        lo_png = page.evaluate(
            "nodes.get('logic').el.querySelector('canvas.lvz').toDataURL()")
        page.evaluate("""() => {
          __msg({type: 'midi', event: {kind: 'gate', id: 'button', on: true}});
          __msg({type: 'midi', event: {kind: 'gate', id: 'threshold', on: true}});
          __msg({type: 'midi', event: {kind: 'gate', id: 'logic', on: true}});
        }""")
        page.wait_for_timeout(60)
        hi_png = page.evaluate(
            "nodes.get('logic').el.querySelector('canvas.lvz').toDataURL()")
        check("circuit viz canvas draws (non-empty lo render)",
              bool(lo_png) and len(lo_png) > 100, str(len(lo_png or "")))
        check("circuit traces light up (lo vs hi renders differ)",
              lo_png != hi_png)
        check("gate event lights the logic output LED", page.evaluate(
            "nodes.get('logic').el.querySelector('.gled')"
            ".classList.contains('on')"))
        page.evaluate("""() => {
          __msg({type: 'midi', event: {kind: 'gate', id: 'button', on: false}});
          __msg({type: 'midi', event: {kind: 'gate', id: 'threshold', on: false}});
          __msg({type: 'midi', event: {kind: 'gate', id: 'logic', on: false}});
        }""")

        # button cards (07-24): the banner IS the mode readout — yellow
        # momentary / orange latch, MOM|LATCH segmented toggle, tiny BTN
        # tag, NO title/sub; the level LED (right-center) seeds from on
        bt17 = page.evaluate("""(() => {
          const g = (gid) => {
            const n = nodes.get(gid);
            const head = n.el.querySelector('.head');
            const seg = n.el.querySelector('.modeseg');
            return {banner: head.classList.contains('banner'),
                    bg: head.style.background,
                    noTitle: !n.el.querySelector('.title'),
                    noSub: !n.el.querySelector('.sub'),
                    tag: (n.el.querySelector('.btntag')||{}).textContent,
                    mom: seg.querySelector('.mom').classList.contains('on'),
                    lat: seg.querySelector('.lat').classList.contains('on'),
                    led: n.el.querySelector('.gled.side')
                      .classList.contains('on')};
          };
          return {b: g('button'), b2: g('button.2')};
        })()""")
        check("momentary button wears the YELLOW banner, MOM lit",
              bt17["b"]["banner"] and "--bin)" in bt17["b"]["bg"]
              and bt17["b"]["mom"] and not bt17["b"]["lat"], str(bt17))
        check("latch button wears the ORANGE banner, LATCH lit",
              "binlatch" in bt17["b2"]["bg"] and bt17["b2"]["lat"]
              and not bt17["b2"]["mom"], str(bt17))
        check("button banner: BTN tag, no title/sub",
              bt17["b"]["tag"] == "BTN" and bt17["b"]["noTitle"]
              and bt17["b"]["noSub"], str(bt17))
        check("button LED seeds from settings.on",
              bt17["b"]["led"] is False and bt17["b2"]["led"] is True,
              str(bt17))

        # MOM|LATCH toggle click sends set_button latch:true AND repaints
        # the banner orange (then flip it back)
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('button').el.querySelector('.modeseg').click()")
        sent = page.evaluate("window.__sent")
        check("mode toggle momentary→latch (set_button latch)",
              {"type": "set_button", "id": "button", "latch": True} in sent,
              str(sent))
        check("mode toggle repaints the banner orange + lights LATCH",
              page.evaluate("""(() => {
                const n = nodes.get('button');
                return n.el.querySelector('.head').style.background
                         .includes('binlatch')
                       && n.el.querySelector('.modeseg .lat')
                         .classList.contains('on');
              })()"""))
        page.evaluate(
            "nodes.get('button').el.querySelector('.modeseg').click()")

        # a latch (persistent) button's keycap CLICK sends fire_button —
        # no down/up pair (the server toggles the level)
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const cap = nodes.get('button.2').el.querySelector('.keycap');
          cap.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
          cap.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
          cap.dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }""")
        sent = page.evaluate("window.__sent")
        check("latch keycap click sends fire_button (no down/up)",
              {"type": "fire_button", "id": "button.2"} in sent
              and not [m for m in sent
                       if m.get("type") in ("button_down", "button_up")],
              str(sent))

        # a live gate event drives the button LED (off, then on)
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'button.2', on: false}})""")
        check("gate event on:false unlights the button LED",
              not page.evaluate(
                  "nodes.get('button.2').el.querySelector('.gled')"
                  ".classList.contains('on')"))
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'button.2', on: true}})""")
        check("gate event on:true lights the button LED", page.evaluate(
            "nodes.get('button.2').el.querySelector('.gled')"
            ".classList.contains('on')"))
        check("button card (banner + keycap) still sizes to XS",
              page.evaluate("nodes.get('button').size") == "XS")

        # grammar: ONE bin branch — accepts pwr/deck/logic-named-ins/
        # deriver triggers; refuses note sinks, bare logic ids, self-wires
        acts17 = page.evaluate("""(() => {
          const lg = nodes.get('logic'), echo = nodes.get('m:echo');
          const arp = nodes.get('arp'), ton = nodes.get('tonic');
          const btn = nodes.get('button'), thr = nodes.get('threshold');
          const bout = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'bin' && p.dir === 'out')});
          const pwr = (n) => ({node: n,
            port: n.ports.find(p => p.label === 'pwr')});
          window.__sent.length = 0;
          const logicPwr = connectAction(bout(lg), pwr(echo));
          if (logicPwr) logicPwr();
          const btnPin = connectAction(bout(btn), {node: lg,
            port: lg.ports.find(p => p.ep === 'logic:a')});
          if (btnPin) btnPin();
          const logicTrig = connectAction(bout(lg), {node: ton,
            port: ton.ports.find(p => p.sig === 'bin')});
          if (logicTrig) logicTrig();
          const binNote = connectAction(bout(thr), {node: arp,
            port: arp.ports.find(p => p.sig === 'ctl' && p.dir === 'in')});
          const bareLogic = connectAction(bout(btn),
            {node: lg, port: {dir: 'in', sig: 'bin', label: 'in'}});
          const logicSelf = connectAction(bout(lg), {node: lg,
            port: lg.ports.find(p => p.ep === 'logic:a')});
          return {sent: window.__sent, logicPwr: !!logicPwr,
                  btnPin: !!btnPin, logicTrig: !!logicTrig,
                  binNote: !!binNote, bareLogic: !!bareLogic,
                  logicSelf: !!logicSelf};
        })()""")
        check("bin-out → module pwr connects (ctl_wire add)",
              acts17["logicPwr"] and
              {"type": "ctl_wire", "action": "add", "from": "logic",
               "to": "echo:pwr"} in acts17["sent"], str(acts17))
        check("bin-out → logic named in connects (button → logic:a)",
              acts17["btnPin"] and
              {"type": "ctl_wire", "action": "add", "from": "button",
               "to": "logic:a"} in acts17["sent"], str(acts17))
        check("bin-out → deriver trigger-in connects (logic → tonic)",
              acts17["logicTrig"] and
              {"type": "ctl_wire", "action": "add", "from": "logic",
               "to": "tonic"} in acts17["sent"], str(acts17))
        check("bin-out → note-in refused", not acts17["binNote"],
              str(acts17))
        check("bare-id logic dst refused (named ins only)",
              not acts17["bareLogic"], str(acts17))
        check("logic self-wire refused", not acts17["logicSelf"],
              str(acts17))

        # the banner circuit selector cycles AND→OR and sends set_logic
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('logic').el.querySelector('.opsel').click()")
        page.wait_for_timeout(120)
        sent = page.evaluate("window.__sent")
        check("circuit selector cycles + sends set_logic AND→OR",
              {"type": "set_logic", "id": "logic", "op": "OR"} in sent,
              str(sent))

        # per-op port SHAPES (server truth resent) + per-op glyphs: the
        # circuit renders distinctly for every op (same all-lo levels)
        op_pngs = {}

        def logic_ins(op):
            stx = json.loads(json.dumps(st17))
            stx["logics"][0]["op"] = op
            page.evaluate("(s) => __msg({type: 'state', ...s})", stx)
            page.wait_for_timeout(350)
            op_pngs[op] = page.evaluate(
                "nodes.get('logic').el.querySelector('canvas.lvz')"
                ".toDataURL()")
            return page.evaluate(
                "nodes.get('logic').ports"
                ".filter(p => p.sig === 'bin' && p.dir === 'in')"
                ".map(p => [p.ep, p.label])")

        check("NOT exposes ONE named in (:a)",
              logic_ins("NOT") == [["logic:a", "A"]], "")
        check("XOR exposes :a/:b", logic_ins("XOR")
              == [["logic:a", "A"], ["logic:b", "B"]], "")
        check("OR exposes :a/:b", logic_ins("OR")
              == [["logic:a", "A"], ["logic:b", "B"]], "")
        check("AND exposes :a/:b", logic_ins("AND")
              == [["logic:a", "A"], ["logic:b", "B"]], "")
        check("SR latch exposes :set/:reset",
              logic_ins("SR latch")
              == [["logic:set", "set"], ["logic:reset", "reset"]], "")
        check("every op draws a distinct gate glyph",
              len(set(op_pngs.values())) == len(op_pngs),
              str({k: len(v) for k, v in op_pngs.items()}))

        # head power LEDs: module/arp/drums carry the LED button + a quiet
        # ":pwr" binary level-in anchored to the head; deck carries FOUR
        # button-ins with the deck:… endpoints
        eps17 = page.evaluate("""(() => {
          const g = (gid) => {
            const n = nodes.get(gid);
            return n && n.ports.filter(p => p.sig === 'bin' && p.dir === 'in')
              .map(p => [p.ep, !!p.quiet, !!(p.rowEl && p.rowEl.isConnected)]);
          };
          return {echo: g('m:echo'), arp: g('arp'), drums: g('drums'),
                  deck: g('deck')};
        })()""")
        check("module card carries a quiet head-anchored :pwr level-in",
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

        # ================================================================
        # 18 — GUI pass B: the XS card size (4.5x4.5, quadrant slots) +
        # the Relay card (type-agnostic switched junction, auto-sized)
        # ================================================================
        st18 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "keys", "to": "relay:2"},
                       {"from": "relay:2", "to": "voice"},
                       {"from": "button", "to": "relay:3"},
                       {"from": "relay:3", "to": "logic:a"},
                       {"from": "button", "to": "relay:ctl"},
                       {"from": "clock", "to": "logic:b"}],
            tonics=[{"id": "tonic", "every": "1 bar", "everies": ["1 bar"],
                     "octave": 2, "root": None}],
            buttons=[{"id": "button", "binding": None, "armed": False,
                      "latch": False, "on": False}],
            clocks=[{"id": "clock", "division": "1/4",
                     "divisions": ["1/4", "1/8"]}],
            logics=[{"id": "logic", "op": "AND",
                     "ops": ["AND", "OR", "NOT", "XOR", "SR latch"],
                     "out": False}],
            relays=[{"id": "relay", "closed": False,
                     "circuits": {"2": {"kind": "notes"},
                                  "3": {"kind": "binary"}}}])
        page.evaluate("posMem = {}; relayAW = [];")   # deterministic geometry
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18)
        page.wait_for_timeout(500)

        # ---- XS: the size class itself --------------------------------
        check("SIZE_PX carries XS at 4.5u x 4.5u (72x72 px)",
              page.evaluate("SIZE_PX.XS") == [72, 72],
              str(page.evaluate("SIZE_PX.XS")))
        szs18 = page.evaluate("""(() => {
          const o = {};
          for (const g of ['button', 'clock', 'logic', 'relay'])
            o[g] = [nodes.get(g).size, nodes.get(g).el.offsetWidth,
                    nodes.get(g).el.offsetHeight];
          return o;
        })()""")
        for g in ("button", "clock", "logic", "relay"):
            check(f"opted-in {g} card measures XS (72x72)",
                  szs18[g] == ["XS", 72, 72], str(szs18))
        nonopt = page.evaluate("""(() => {
          const e = nodes.get('m:echo'), t = nodes.get('tonic');
          const keep = e.size;
          e.size = null; const floor = sizeFor(e);
          e.allowXS = true; e.size = null; const opted = sizeFor(e);
          e.allowXS = false; e.size = keep;
          return {floor, opted, tonicSize: t.size, tonicXS: !!t.allowXS};
        })()""")
        check("non-opted card floor stays S even when its rows would fit",
              nonopt["floor"] == "S" and nonopt["opted"] == "XS",
              str(nonopt))
        check("estimator (deriver) never measures XS",
              nonopt["tonicSize"] in ("M", "L") and not nonopt["tonicXS"],
              str(nonopt))

        # ---- XS packing: tidy pours 4 to a block, 5.5u quadrant pitch --
        page.evaluate("compactLayout()")
        page.wait_for_timeout(400)
        pack = page.evaluate("""(() => {
          const g = (id) => { const n = nodes.get(id);
            return {bx: n.bx, by: n.by, half: n.half, hh: n.hh,
                    r: nodeUnitRect(n)}; };
          return {button: g('button'), clock: g('clock'),
                  logic: g('logic'), relay: g('relay')};
        })()""")
        xs4 = [pack[g] for g in ("button", "clock", "logic", "relay")]
        check("tidy packs the four XS cards into ONE block",
              len({(q["bx"], q["by"]) for q in xs4}) == 1, str(pack))
        check("…across all four distinct quadrants",
              {(q["half"], q["hh"]) for q in xs4}
              == {("top", "left"), ("top", "right"),
                  ("bottom", "left"), ("bottom", "right")}, str(pack))
        tl = next(q for q in xs4 if (q["half"], q["hh"]) == ("top", "left"))
        tr = next(q for q in xs4 if (q["half"], q["hh"]) == ("top", "right"))
        bl = next(q for q in xs4 if (q["half"], q["hh"]) == ("bottom", "left"))
        check("side-by-side XS pitch is exactly 5.5u (4.5u + 1u gutter)",
              tr["r"]["x"] - tl["r"]["x"] == 5.5
              and tr["r"]["y"] == tl["r"]["y"]
              and tl["r"]["w"] == 4.5 and tl["r"]["h"] == 4.5, str(pack))
        check("stacked XS pitch is exactly 5.5u vertically too",
              bl["r"]["y"] - tl["r"]["y"] == 5.5
              and bl["r"]["x"] == tl["r"]["x"], str(pack))
        page.evaluate("nodes.get('button').el.scrollIntoView("
                      "{block: 'center', inline: 'center'})")
        page.wait_for_timeout(150)
        page.screenshot(path="/tmp/binB_board_xs.png")

        # ---- XS occupancy + shove: quadrant collisions honored ---------
        occ18 = page.evaluate("""(() => {
          const lg = placeOf(nodes.get('logic'));
          const sPl = {size: 'S', bx: lg.bx, by: lg.by, half: lg.half};
          const over = halfCells(sPl).filter(
            c => halfCells(lg).includes(c));
          const occ = occOf(currentPos(null));
          const fits = !halfCells(sPl).some(c => occ.get(c) !== undefined);
          return {cells: halfCells(lg).length, over: over.length, fits};
        })()""")
        check("an XS occupies exactly ONE quadrant cell",
              occ18["cells"] == 1, str(occ18))
        check("an S over an occupied quadrant collides (occupancy honored)",
              occ18["over"] == 1 and occ18["fits"] is False, str(occ18))
        shove = page.evaluate("""(() => {
          const lg = nodes.get('logic'), pl = placeOf(lg);
          // an S dropped onto logic's half: the XS shoves a HALF step down
          const sPlan = planShove('keys',
            {size: 'S', bx: pl.bx, by: pl.by, half: pl.half}, 'down');
          const sMove = sPlan.moves.get('logic');
          // an XS dropped onto logic's quadrant from the left: QUARTER step
          const xPlan = planShove('button',
            {size: 'XS', bx: pl.bx, by: pl.by, half: pl.half, hh: pl.hh},
            'right');
          const xMove = xPlan.moves.get('logic');
          return {pl, sOk: sPlan.ok, sMove, xOk: xPlan.ok, xMove};
        })()""")
        pl0 = shove["pl"]
        want_s = {"top": "bottom", "bottom": "top"}[pl0["half"]]
        check("S-onto-XS shove displaces the XS a half step",
              shove["sOk"] and shove["sMove"]
              and shove["sMove"]["half"] == want_s
              and shove["sMove"]["by"] == pl0["by"]
              + (1 if pl0["half"] == "bottom" else 0), str(shove))
        want_hh = {"left": "right", "right": "left"}[pl0["hh"]]
        check("XS-onto-XS shove displaces a quarter step sideways",
              shove["xOk"] and shove["xMove"]
              and shove["xMove"]["hh"] == want_hh
              and shove["xMove"]["bx"] == pl0["bx"]
              + (1 if pl0["hh"] == "right" else 0), str(shove))

        # ---- XS persistence: a quadrant position round-trips ----------
        mem18 = page.evaluate("memOf(nodes.get('logic'))")
        check("memOf records the quadrant (5-tuple ending in hh)",
              len(mem18) == 5 and mem18[3] == "XS"
              and mem18[4] in ("left", "right"), str(mem18))
        # move logic to the bottom-right quadrant of an EMPTY block (found
        # from the far corner — layout variants never reach it), rebuild
        spot = page.evaluate("""(() => {
          const occ = occOf(currentPos(null));
          for (let bx = BX - 1; bx >= 0; bx--)
            for (let by = BY - 1; by >= 0; by--) {
              const cells = [];
              for (const v of ['top', 'bottom'])
                for (const h of ['left', 'right'])
                  cells.push(`${bx},${by},${v},${h}`);
              if (!cells.some(c => occ.get(c) !== undefined))
                return [bx, by];
            }
        })()""")
        page.evaluate("""([bx, by]) => {
          const n = nodes.get('logic');   // a real move, then a rebuild:
          n.bx = bx; n.by = by; n.half = 'bottom'; n.hh = 'right';
          place(n); saveLayout();
        }""", spot)
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18)
        page.wait_for_timeout(400)
        back = page.evaluate("""(() => {
          const n = nodes.get('logic');
          return [n.bx, n.by, n.half, n.size, n.hh,
                  posMem['logic'] || null];
        })()""")
        want = [spot[0], spot[1], "bottom", "XS", "right"]
        check("a moved quadrant position survives the rebuild verbatim",
              back[:5] == want and back[5] == want, str([back, want]))
        # a point 2u into that block = its top-left quadrant; 8u in = its
        # bottom-right (occupied by logic → the OTHER half's quadrant)
        px2 = page.evaluate(
            "([bx,by]) => pxToSlotMem((bx*12+2+2)*16, (by*12+2+2)*16, 'XS')",
            spot)
        px8 = page.evaluate(
            "([bx,by]) => pxToSlotMem((bx*12+2+8)*16, (by*12+2+8)*16, 'XS')",
            spot)
        check("pxToSlotMem resolves XS drops at quadrant resolution",
              px2 == [spot[0], spot[1], "top", "XS", "left"]
              and px8 == [spot[0], spot[1], "top", "XS", "right"],
              str([px2, px8, spot]))

        # ---- Relay: palette spawn + the card itself -------------------
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          [...document.querySelectorAll('#palette button')]
            .find(b => b.textContent === 'Relay').click();
        }""")
        check("palette Relay sends spawn_relay",
              {"type": "spawn_relay"} in page.evaluate("window.__sent"),
              str(page.evaluate("window.__sent")))
        rl18 = page.evaluate("""(() => {
          const n = nodes.get('relay');
          const circ = n.ports.filter(p => p.relayCirc);
          const pair = (k) => {
            const i = n.lay.handles.find(h => h.port.relayCirc === k
                                              && h.side === 'in');
            const o = n.lay.handles.find(h => h.port.relayCirc === k
                                              && h.side === 'out');
            return i && o && {ix: i.x, iy: i.y, ox: o.x, oy: o.y,
                              ie: i.edge, oe: o.edge};
          };
          const r = nodeUnitRect(n);
          return {size: n.size, relayN: n.relayN,
                  name: n.el.querySelector('.title').textContent,
                  sub: n.el.querySelector('.sub').textContent,
                  nCirc: circ.length,
                  sigs: circ.filter(p => p.dir === 'in')
                            .map(p => [p.relayCirc, p.sig]),
                  ctl: n.ports.filter(p => p.ep === 'relay:ctl')
                    .map(p => [p.dir, p.sig, !!p.quiet, !!p.single]),
                  btn: !!n.el.querySelector('.relaybtn'),
                  lit: n.el.querySelector('.relaybtn')
                    .classList.contains('on'),
                  p1: pair(1), p3: pair(3), top: r.y * 16, bot: (r.y + r.h) * 16};
        })()""")
        check("relay card renders XS with 4 circuits while ≤4 in use",
              rl18["size"] == "XS" and rl18["relayN"] == 4
              and rl18["nCirc"] == 8, str(rl18))
        check("relay title/sub read Relay · signal relay · 4 circuits",
              rl18["name"] == "Rly"
              and rl18["sub"] == "signal relay · 4 circuits", str(rl18))
        # banner colors across the binary plane (Cole, 07-24): sources
        # (clock/threshold) yellow, relay orange (logic checked in §17)
        ban18 = page.evaluate("""(() => {
          const bg = (gid) => {
            const h = nodes.get(gid).el.querySelector('.head');
            return h.classList.contains('banner') ? h.style.background : null;
          };
          return {clock: bg('clock'), relay: bg('relay')};
        })()""")
        check("clock banner yellow, relay banner orange",
              ban18["clock"] and "--bin)" in ban18["clock"]
              and ban18["relay"] and "binlatch" in ban18["relay"],
              str(ban18))
        check("circuit handles pair vertically (in k above out k)",
              rl18["p1"] and rl18["p1"]["ix"] == rl18["p1"]["ox"]
              and rl18["p3"] and rl18["p3"]["ix"] == rl18["p3"]["ox"]
              and rl18["p1"]["ie"] == "T" and rl18["p1"]["oe"] == "B"
              and rl18["p1"]["iy"] == rl18["top"]
              and rl18["p1"]["oy"] == rl18["bot"], str(rl18))
        check("circuit sigs follow their claims (any/ctl/bin, agnostic card)",
              rl18["sigs"] == [[1, "any"], [2, "ctl"], [3, "bin"],
                               [4, "any"]], str(rl18))
        check("relay:ctl is ONE quiet single-input bin level-in",
              rl18["ctl"] == [["in", "bin", True, True]], str(rl18))
        check("relay button present, unlit while open",
              rl18["btn"] and rl18["lit"] is False, str(rl18))
        page.screenshot(path="/tmp/binB_relay_xs.png",
                        clip=relay_clip(page))

        # relay circuit wires draw in their KIND's family (transparent node)
        rw18 = page.evaluate("""(() => {
          const f = (pred) => { const w = wires.find(pred);
            return w && {sig: w.sig,
                         ctlFam: LINES.ctl.includes(w.color),
                         binFam: LINES.bin.includes(w.color)}; };
          return {kin: f(w => w.from.node.gid === 'keys'
                              && w.to.node.gid === 'relay'),
                  kout: f(w => w.from.node.gid === 'relay'
                               && w.to.node.gid === 'voice'),
                  bin: f(w => w.from.node.gid === 'button'
                              && w.to.port.ep === 'relay:3'),
                  bout: f(w => w.from.node.gid === 'relay'
                               && w.to.port.ep === 'logic:a'),
                  ctl: f(w => w.to.port && w.to.port.ep === 'relay:ctl')};
        })()""")
        check("notes wires through a circuit draw in the ctl family",
              bool(rw18["kin"]) and rw18["kin"]["ctlFam"]
              and bool(rw18["kout"]) and rw18["kout"]["ctlFam"], str(rw18))
        check("binary wires through a circuit draw in the bin family",
              bool(rw18["bin"]) and rw18["bin"]["binFam"]
              and bool(rw18["bout"]) and rw18["bout"]["binFam"], str(rw18))
        check("the ctl level-in wire lands on relay:ctl (bin family)",
              bool(rw18["ctl"]) and rw18["ctl"]["binFam"], str(rw18))

        # ---- relay switch: click sends set_relay; LED follows gates ----
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('relay').el.querySelector('.relaybtn').click()")
        sent = page.evaluate("window.__sent")
        check("relay button click sends set_relay closed:true + lights",
              {"type": "set_relay", "id": "relay", "closed": True} in sent
              and page.evaluate("nodes.get('relay').el"
                                ".querySelector('.relaybtn')"
                                ".classList.contains('on')"), str(sent))
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'relay', on: false}})""")
        check("gate event on:false unlights the relay button",
              not page.evaluate("nodes.get('relay').el"
                                ".querySelector('.relaybtn')"
                                ".classList.contains('on')"))
        page.evaluate("""() => __msg({type: 'midi',
          event: {kind: 'gate', id: 'relay', on: true}})""")
        check("gate event on:true lights the relay button", page.evaluate(
            "nodes.get('relay').el.querySelector('.relaybtn')"
            ".classList.contains('on')"))

        # ---- grammar: type-agnostic ins, kind-locked outs -------------
        acts18 = page.evaluate("""(() => {
          const rel = nodes.get('relay'), sgN = nodes.get('m:signal_gen');
          const echoN = nodes.get('m:echo'), keysN = nodes.get('keys');
          const btn = nodes.get('button'), lg = nodes.get('logic');
          const voiceN = nodes.get('voice'), ton = nodes.get('tonic');
          const cin = (k) => ({node: rel,
            port: rel.ports.find(p => p.relayCirc === k && p.dir === 'in')});
          const cout = (k) => ({node: rel,
            port: rel.ports.find(p => p.relayCirc === k && p.dir === 'out')});
          const aout = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'audio' && p.dir === 'out')});
          const ain = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'audio' && p.dir === 'in')});
          const bout = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'bin' && p.dir === 'out')});
          const nout = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'ctl' && p.dir === 'out')});
          const nin = (n) => ({node: n,
            port: n.ports.find(p => p.sig === 'ctl' && p.dir === 'in')});
          const ctlH = {node: rel,
            port: rel.ports.find(p => p.ep === 'relay:ctl')};
          window.__sent.length = 0;
          const r = {};
          r.audio_to_unclaimed = !!connectAction(aout(sgN), cin(1));
          r.audio_to_notes_circ = !!connectAction(aout(sgN), cin(2));
          r.note_to_unclaimed = !!connectAction(nout(keysN), cin(4));
          r.note_to_bin_circ = !!connectAction(nout(keysN), cin(3));
          r.bin_to_unclaimed = !!connectAction(bout(btn), cin(4));
          r.bin_to_ctl = !!connectAction(bout(btn), ctlH);
          r.note_to_ctl = !!connectAction(nout(keysN), ctlH);
          r.audio_to_ctl = !!connectAction(aout(sgN), ctlH);
          r.unclaimed_out = !!connectAction(cout(4), ain(echoN));
          r.note_out_to_voice = !!connectAction(cout(2), nin(voiceN));
          r.note_out_to_logic = !!connectAction(cout(2),
            {node: lg, port: lg.ports.find(p => p.ep === 'logic:a')});
          const binLg = connectAction(cout(3),
            {node: lg, port: lg.ports.find(p => p.ep === 'logic:a')});
          r.bin_out_to_logic = !!binLg;
          if (binLg) binLg();
          r.bin_out_to_deriver = !!connectAction(cout(3),
            {node: ton, port: ton.ports.find(p => p.sig === 'bin')});
          const audioAdd = connectAction(aout(sgN), cin(1));
          if (audioAdd) audioAdd();
          return {r, sent: window.__sent};
        })()""")
        page.wait_for_timeout(400)
        r18 = acts18["r"]
        check("audio-out → unclaimed circuit IN connects (graph_wire add)",
              r18["audio_to_unclaimed"] and
              {"type": "graph_wire", "action": "add", "from": "signal_gen",
               "to": "relay:1"} in acts18["sent"], str(acts18))
        check("note-out → unclaimed IN yes; onto bin/audio-claimed no",
              r18["note_to_unclaimed"] and not r18["note_to_bin_circ"]
              and not r18["audio_to_notes_circ"], str(r18))
        check("bin-out → unclaimed IN + relay:ctl connect",
              r18["bin_to_unclaimed"] and r18["bin_to_ctl"], str(r18))
        check("relay:ctl accepts bin ONLY (note/audio refused)",
              not r18["note_to_ctl"] and not r18["audio_to_ctl"], str(r18))
        check("an UNCLAIMED circuit OUT refuses (kind unknown)",
              not r18["unclaimed_out"], str(r18))
        check("notes circuit OUT → note dsts only (voice yes, logic:a no)",
              r18["note_out_to_voice"] and not r18["note_out_to_logic"],
              str(r18))
        check("bin circuit OUT → logic:a connects (ctl_wire add)",
              r18["bin_out_to_logic"] and
              {"type": "ctl_wire", "action": "add", "from": "relay:3",
               "to": "logic:a"} in acts18["sent"], str(acts18))
        check("bin circuit OUT → deriver trigger connects",
              r18["bin_out_to_deriver"], str(r18))

        # the audio add CLAIMED circuit 1 (local echo): a note drag onto it
        # now refuses; the stored hop draws src → circuit IN in the audio
        # family while the resolved duplicate (sg → echo) is suppressed
        claimed = page.evaluate("""(() => {
          const rel = nodes.get('relay'), keysN = nodes.get('keys');
          const p1 = rel.ports.find(p => p.relayCirc === 1 && p.dir === 'in');
          const refuse = !connectAction(
            {node: keysN,
             port: keysN.ports.find(p => p.sig === 'ctl' && p.dir === 'out')},
            {node: rel, port: p1});
          const w = wires.find(x => x.from.node.gid === 'm:signal_gen'
                                    && x.to.node.gid === 'relay');
          const dup = wires.find(x => x.from.node.gid === 'm:signal_gen'
                                      && x.to.node.gid === 'm:echo');
          return {sig1: p1.sig, refuse,
                  drawn: w && {sig: w.sig,
                               fam: LINES.audio.includes(w.color),
                               cut: !!w.cutAction},
                  dup: !!dup};
        })()""")
        check("first wire claims the circuit (audio; note drag now refused)",
              claimed["sig1"] == "audio" and claimed["refuse"],
              str(claimed))
        check("relay audio hop draws src → circuit IN in the audio family",
              bool(claimed["drawn"]) and claimed["drawn"]["sig"] == "audio"
              and claimed["drawn"]["fam"] and claimed["drawn"]["cut"],
              str(claimed))
        check("…and the RESOLVED duplicate (src → old dst) is suppressed",
              claimed["dup"] is False, str(claimed))

        # ---- endpoint wire cuts route per kind ------------------------
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          wires.find(x => x.from.node.gid === 'button'
                          && x.to.port.ep === 'relay:3').cutAction();
          wires.find(x => x.from.node.gid === 'm:signal_gen'
                          && x.to.node.gid === 'relay').cutAction();
        }""")
        page.wait_for_timeout(400)
        sent = page.evaluate("window.__sent")
        check("cutting a bin circuit wire sends the targeted ctl_wire remove",
              {"type": "ctl_wire", "action": "remove", "from": "button",
               "to": "relay:3"} in sent, str(sent))
        check("cutting the audio hop sends graph_wire remove (+ forgets it)",
              {"type": "graph_wire", "action": "remove",
               "from": "signal_gen"} in sent
              and page.evaluate("relayAW.length") == 0, str(sent))

        # ---- auto-size: >4 circuits in use flips XS → S (and back) ----
        st18b = json.loads(json.dumps(st18))
        st18b["relays"][0]["circuits"] = {
            str(k): {"kind": "binary"} for k in range(1, 6)}
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18b)
        page.wait_for_timeout(400)
        big = page.evaluate("""(() => {
          const n = nodes.get('relay');
          return {size: n.size, relayN: n.relayN,
                  sub: n.el.querySelector('.sub').textContent,
                  circ: n.ports.filter(p => p.relayCirc).length,
                  w: n.el.offsetWidth, h: n.el.offsetHeight};
        })()""")
        check("5 circuits in use → the relay re-measures S with 9 circuits",
              big == {"size": "S", "relayN": 9,
                      "sub": "signal relay · 9 circuits", "circ": 18,
                      "w": 160, "h": 72}, str(big))
        page.screenshot(path="/tmp/binB_relay_s.png",
                        clip=relay_clip(page))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18)
        page.wait_for_timeout(400)
        check("back to ≤4 in use → the relay re-measures XS",
              page.evaluate("nodes.get('relay').size") == "XS")

        # ---- the expansion + (Cole, 07-24): 4 slots claimed on XS ------
        check("no expansion + while XS has free circuits (2 in use)",
              page.evaluate(
                  "!nodes.get('relay').ports.some(p => p.plusSlot)"))
        st18c = json.loads(json.dumps(st18))
        st18c["relays"][0]["circuits"] = {
            str(k): {"kind": "binary"} for k in range(1, 5)}
        st18c["ctl_wires"] = [{"from": "button", "to": f"relay:{k}"}
                              for k in range(1, 5)]
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18c)
        page.wait_for_timeout(400)
        plus18 = page.evaluate("""(() => {
          const n = nodes.get('relay');
          const H = n.lay.handles.find(h => h.port.plusSlot);
          const h4 = n.lay.handles.find(h => h.port.relayCirc === 4
                                             && h.side === 'in'
                                             && h.role === 'wire');
          const clk = nodes.get('clock');
          window.__sent.length = 0;
          const act = connectAction(
            {node: clk,
             port: clk.ports.find(p => p.sig === 'bin' && p.dir === 'out')},
            {node: n, port: n.ports.find(p => p.plusSlot)});
          if (act) act();
          return {size: n.size, role: H && H.role, edge: H && H.edge,
                  rightOf4: !!(H && h4 && H.x > h4.x),
                  cls: H && H.el.className, sent: window.__sent};
        })()""")
        check("XS + 4 circuits in use → a + appears right of the 4th slot",
              plus18["size"] == "XS" and plus18["role"] == "plus"
              and plus18["edge"] == "T" and plus18["rightOf4"]
              and plus18["cls"] == "bhandle plus", str(plus18))
        check("splicing to the + latches the wire onto circuit 5",
              {"type": "ctl_wire", "action": "add", "from": "clock",
               "to": "relay:5"} in plus18["sent"], str(plus18))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18b)
        page.wait_for_timeout(400)
        check("5 circuits in use → S face, the + is gone",
              page.evaluate("nodes.get('relay').size") == "S"
              and page.evaluate(
                  "!nodes.get('relay').ports.some(p => p.plusSlot)"))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st18)
        page.wait_for_timeout(400)

        # ---- kill ------------------------------------------------------
        page.evaluate("window.__sent.length = 0")
        page.evaluate(
            "nodes.get('relay').el.querySelector('.kill').click()")
        check("relay kill sends remove_relay",
              {"type": "remove_relay", "id": "relay"}
              in page.evaluate("window.__sent"),
              str(page.evaluate("window.__sent")))

        # ================================================================
        # 19 — transport cards (item 9): canvas views of the ONE global
        # transport — Play/Stop + Tempo/Click, lockstep with the top bar
        # ================================================================
        st19 = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}],
            ctl_wires=[{"from": "button", "to": "transport:run"},
                       {"from": "clock", "to": "transport:tap"}],
            buttons=[{"id": "button", "binding": None, "armed": False,
                      "latch": False, "on": False}],
            clocks=[{"id": "clock", "division": "1/4",
                     "divisions": ["1/4", "1/8"]}],
            transport_cards=["play", "tempo"],
            transport={"bpm": 120, "beats_per_bar": 3, "click": True,
                       "accent": True, "downbeat": 1, "running": False,
                       "divisions": ["1/4", "1/8"]})
        page.evaluate("posMem = {}; relayAW = [];")
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19)
        page.wait_for_timeout(500)

        # ---- palette: a top-line transport section after LOGIC ---------
        hs19 = page.evaluate(
            "[...document.querySelectorAll('#palette h3')]"
            ".map(h => h.textContent)")
        check("palette grows a 'transport' section right after logic",
              "transport" in hs19 and
              hs19.index("transport") == hs19.index("logic") + 1, str(hs19))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          const bs = [...document.querySelectorAll('#palette button')];
          bs.find(b => b.textContent === 'Play/Stop').click();
          bs.find(b => b.textContent === 'Tempo/Click').click();
        }""")
        sent = page.evaluate("window.__sent")
        check("palette Play/Stop sends spawn_transport_card play",
              {"type": "spawn_transport_card", "which": "play"} in sent,
              str(sent))
        check("palette Tempo/Click sends spawn_transport_card tempo",
              {"type": "spawn_transport_card", "which": "tempo"} in sent,
              str(sent))

        # ---- cards build from state.transport_cards --------------------
        got19 = page.evaluate("""(() => {
          const tp = nodes.get('tplay'), tt = nodes.get('ttempo');
          return tp && tt && {
            tp: [tp.size, tp.el.offsetWidth, tp.el.offsetHeight],
            tt: [tt.size, tt.el.offsetWidth, tt.el.offsetHeight]};
        })()""")
        check("both transport cards render from the payload",
              bool(got19), str(got19))
        check("Play/Stop measures XS (allowXS, 72x72)",
              got19 and got19["tp"] == ["XS", 72, 72], str(got19))
        check("Tempo/Click measures M (5 rows + metronome strip)",
              got19 and got19["tt"] == ["M", 160, 160], str(got19))

        # ---- Play/Stop: the button shows the CURRENT state -------------
        pb = page.evaluate("""(() => {
          const b = nodes.get('tplay').el.querySelector('.tpbtn');
          return {txt: b.textContent, stopped: b.classList.contains('stopped'),
                  playing: b.classList.contains('playing'),
                  color: getComputedStyle(b).color,
                  bold: getComputedStyle(b).fontWeight};
        })()""")
        check("stopped payload → bold red ⏹ STOP",
              pb["stopped"] and not pb["playing"] and "⏹" in pb["txt"]
              and "STOP" in pb["txt"] and pb["color"] == "rgb(227, 73, 72)"
              and int(pb["bold"]) >= 700, str(pb))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("nodes.get('tplay').el.querySelector('.tpbtn').click()")
        check("button click toggles: set_transport playing:true",
              {"type": "set_transport", "playing": True}
              in page.evaluate("window.__sent"),
              str(page.evaluate("window.__sent")))
        st19b = json.loads(json.dumps(st19))
        st19b["transport"]["running"] = True
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19b)
        page.wait_for_timeout(400)
        pb = page.evaluate("""(() => {
          const b = nodes.get('tplay').el.querySelector('.tpbtn');
          return {txt: b.textContent, playing: b.classList.contains('playing'),
                  color: getComputedStyle(b).color};
        })()""")
        check("playing payload → green ⏵ PLAY (follows state.transport)",
              pb["playing"] and "⏵" in pb["txt"] and "PLAY" in pb["txt"]
              and pb["color"] == "rgb(27, 175, 122)", str(pb))

        # ---- tempo slider: the top bar's 40–220 mapping ----------------
        page.evaluate("nodes.get('ttempo').el.scrollIntoView("
                      "{block: 'center', inline: 'center'})")
        page.wait_for_timeout(150)
        g19 = slider_geom(page, "ttempo", "tempo")
        check("tempo slider seeds from the payload (120 bpm → u 0.444)",
              abs(g19["thumb"] - (120 - 40) / 180) < 0.01, str(g19))
        page.evaluate("window.__sent.length = 0")
        page.mouse.move(g19["x"], g19["y"])
        page.mouse.down()
        page.mouse.move(g19["x"] + g19["w"] * g19["zoom"] * 0.25, g19["y"],
                        steps=8)
        page.mouse.up()
        page.wait_for_timeout(150)
        sent = page.evaluate("window.__sent.filter("
                             "m => m.type === 'set_transport' && 'bpm' in m)")
        check("tempo drag +25% sends set_transport bpm ≈ 165 (throttled)",
              sent and abs(sent[-1]["bpm"] - 165) <= 3
              and all(float(m["bpm"]).is_integer() for m in sent), str(sent))

        # ---- time sig + downbeat step sliders --------------------------
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19b)
        page.wait_for_timeout(400)
        rows19 = page.evaluate("""(() => {
          const n = nodes.get('ttempo');
          const row = (label) => {
            const r = [...n.el.querySelectorAll('.mini.stepped')].find(
              x => (x.querySelector('label')||{}).title === label);
            return r && {dets: r.querySelectorAll('.det').length,
                         v: r.querySelector('.v').textContent};
          };
          const bar = [...document.querySelectorAll('#meter option')];
          return {ts: row('time sig'), db: row('downbeat'),
                  barVals: bar.map(o => +o.value),
                  barLabels: bar.map(o => o.textContent)};
        })()""")
        check("time sig detents mirror the top bar's meter select",
              rows19["ts"] and rows19["ts"]["dets"] == len(rows19["barVals"])
              and rows19["ts"]["v"] == "3/4", str(rows19))
        check("downbeat detents follow the meter (3/4 → 3), 1-based text",
              rows19["db"] and rows19["db"]["dets"] == 3
              and rows19["db"]["v"] == "beat 2", str(rows19))

        def step_drag(label, detents, gaps):
            return page.evaluate("""(([label, k, gaps]) => {
              window.__sent.length = 0;
              const n = nodes.get('ttempo');
              const row = [...n.el.querySelectorAll('.mini.stepped')].find(
                x => (x.querySelector('label')||{}).title === label);
              const track = row.querySelector('.track');
              const zs = parseFloat(world.style.zoom) || 1;
              const per = (track.offsetWidth || 1) / gaps;
              const ev = (type, x) => track.dispatchEvent(new PointerEvent(
                type, {pointerId: 9, clientX: x, clientY: 0, bubbles: true}));
              ev('pointerdown', 500);
              for (let s = 1; s <= 4; s++)
                ev('pointermove', 500 + (k * per * zs) * s / 4);
              ev('pointerup', 500 + k * per * zs);
              return window.__sent.filter(m => m.type === 'set_transport');
            })""", [label, detents, gaps])

        sent = step_drag("time sig", 1, 3)   # 4 detents → 3 gaps; 3/4 → 4/4
        check("time sig +1 detent sends set_transport beats_per_bar 4",
              sent and sent[-1] == {"type": "set_transport",
                                    "beats_per_bar": 4}, str(sent))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19b)
        page.wait_for_timeout(400)
        sent = step_drag("downbeat", 1, 2)   # 3 detents → 2 gaps; beat 2 → 3
        check("downbeat +1 detent sends set_transport downbeat 2",
              sent and sent[-1] == {"type": "set_transport", "downbeat": 2},
              str(sent))
        st19c = json.loads(json.dumps(st19b))
        st19c["transport"]["beats_per_bar"] = 4
        st19c["transport"]["downbeat"] = 0
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19c)
        page.wait_for_timeout(400)
        redet = page.evaluate("""(() => {
          const n = nodes.get('ttempo');
          const row = [...n.el.querySelectorAll('.mini.stepped')].find(
            x => (x.querySelector('label')||{}).title === 'downbeat');
          return {dets: row.querySelectorAll('.det').length,
                  v: row.querySelector('.v').textContent,
                  dots: n.el.querySelectorAll('.metrostrip i').length};
        })()""")
        check("meter change re-derives the downbeat detents (4/4 → 4)",
              redet["dets"] == 4 and redet["v"] == "beat 1"
              and redet["dots"] == 4, str(redet))
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19b)
        page.wait_for_timeout(400)

        # ---- click + accent power-LED toggles --------------------------
        page.evaluate("window.__sent.length = 0")
        leds19 = page.evaluate("""(() => {
          const n = nodes.get('ttempo');
          const led = (label) => [...n.el.querySelectorAll('.mini')].find(
            x => (x.querySelector('label')||{}).title === label)
            .querySelector('.onoff');
          const c = led('click'), a = led('accent');
          const lit = [c.classList.contains('on'), a.classList.contains('on')];
          c.click(); a.click();
          return {lit, sent: window.__sent};
        })()""")
        check("click + accent LEDs seed lit from the payload",
              leds19["lit"] == [True, True], str(leds19))
        check("LED clicks send set_transport click:false / accent:false",
              {"type": "set_transport", "click": False} in leds19["sent"]
              and {"type": "set_transport", "accent": False}
              in leds19["sent"], str(leds19))

        # ---- quiet endpoint handles + payload wires --------------------
        eps19 = page.evaluate("""(() => {
          const tp = nodes.get('tplay'), tt = nodes.get('ttempo');
          const q = (n, ep) => { const p = n.ports.find(x => x.ep === ep);
            return p && [p.dir, p.sig, !!p.quiet, !!p.single,
                         portAllowsPlus(p)]; };
          const w = (pred) => { const x = wires.find(pred);
            return x && {sig: x.sig, binFam: LINES.bin.includes(x.color),
                         cut: !!x.cutAction}; };
          return {run: q(tp, 'transport:run'), tap: q(tt, 'transport:tap'),
                  click: q(tt, 'transport:click'),
                  accent: q(tt, 'transport:accent'),
                  wRun: w(x => x.from.node.gid === 'button'
                              && x.to.port.ep === 'transport:run'),
                  wTap: w(x => x.from.node.gid === 'clock'
                              && x.to.port.ep === 'transport:tap')};
        })()""")
        for ep in ("run", "tap", "click", "accent"):
            check(f"quiet fan-in bin handle carries transport:{ep}",
                  eps19[ep] == ["in", "bin", True, False, True], str(eps19))
        check("payload wires land on the transport handles (bin family)",
              eps19["wRun"] and eps19["wRun"]["binFam"]
              and eps19["wRun"]["cut"]
              and eps19["wTap"] and eps19["wTap"]["binFam"], str(eps19))
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          wires.find(x => x.from.node.gid === 'button'
                          && x.to.port.ep === 'transport:run').cutAction();
        }""")
        check("cutting the run wire sends the targeted ctl_wire remove",
              {"type": "ctl_wire", "action": "remove", "from": "button",
               "to": "transport:run"} in page.evaluate("window.__sent"),
              str(page.evaluate("window.__sent")))

        # ---- grammar: bin lands on the transport ins, audio refused ----
        gram19 = page.evaluate("""(() => {
          const tp = nodes.get('tplay'), clk = nodes.get('clock');
          const sgN = nodes.get('m:signal_gen');
          const run = {node: tp,
                       port: tp.ports.find(p => p.ep === 'transport:run')};
          window.__sent.length = 0;
          const act = connectAction({node: clk,
            port: clk.ports.find(p => p.sig === 'bin' && p.dir === 'out')},
            run);
          if (act) act();
          const bad = connectAction({node: sgN,
            port: sgN.ports.find(p => p.sig === 'audio' && p.dir === 'out')},
            run);
          return {ok: !!act, bad: !!bad, sent: window.__sent};
        })()""")
        check("clock bin-out → transport:run connects (ctl_wire add)",
              gram19["ok"] and
              {"type": "ctl_wire", "action": "add", "from": "clock",
               "to": "transport:run"} in gram19["sent"], str(gram19))
        check("audio-out → transport:run refused", not gram19["bad"],
              str(gram19))

        # ---- live metronome: the beat broadcast moves the strip --------
        met19 = page.evaluate("""(() => {
          const n = nodes.get('ttempo');
          const dots = () => [...n.el.querySelectorAll('.metrostrip i')].map(
            d => [d.classList.contains('lit'), d.classList.contains('db'),
                  d.offsetWidth]);
          __msg({type: 'beat', bar: 0, beat: 0, downbeat: false, loop: null});
          const b0 = dots();
          __msg({type: 'beat', bar: 0, beat: 1, downbeat: true, loop: null});
          const b1 = dots();
          return {b0, b1};
        })()""")
        check("beat 0 lights dot 0 only",
              [d[0] for d in met19["b0"]] == [True, False, False],
              str(met19))
        check("beat 1 moves the light to the DOWNBEAT dot (accented, bigger)",
              [d[0] for d in met19["b1"]] == [False, True, False]
              and met19["b1"][1][1] and met19["b1"][1][2] > met19["b1"][0][2],
              str(met19))

        # ---- screenshots for Cole --------------------------------------
        # fresh broadcast first: the LED test-clicks left local echoes
        page.evaluate("(s) => __msg({type: 'state', ...s})", st19b)
        page.wait_for_timeout(400)
        page.evaluate("""() => {
          __msg({type: 'beat', bar: 0, beat: 1, downbeat: true, loop: null});
          // frame BOTH cards (+ the top bar): sit them side by side in the
          // first free block pair, then scroll to their midpoint
          const tp = nodes.get('tplay'), tt = nodes.get('ttempo');
          const occ = occOf(currentPos(null));
          const free = (bx, by) => ['top', 'bottom'].every(v =>
            ['left', 'right'].every(h =>
              occ.get(`${bx},${by},${v},${h}`) === undefined));
          outer:
          for (let by = 0; by < BY; by++)
            for (let bx = 0; bx + 1 < BX; bx++)
              if (free(bx, by) && free(bx + 1, by)) {
                tp.bx = bx; tp.by = by; tp.half = 'top'; tp.hh = 'left';
                tt.bx = bx + 1; tt.by = by; tt.half = null; tt.hh = null;
                place(tp); place(tt); rerouteAll();
                break outer;
              }
          const bd = document.getElementById('board');
          const zs = parseFloat(world.style.zoom) || 1;   // scroll is VISUAL px
          const mid = (n) => ({x: n.x + n.el.offsetWidth / 2,
                               y: n.y + n.el.offsetHeight / 2});
          const A = mid(tp), B = mid(tt);
          bd.scrollLeft = ((A.x + B.x) / 2) * zs - bd.clientWidth / 2;
          bd.scrollTop = ((A.y + B.y) / 2) * zs - bd.clientHeight / 2;
        }""")
        page.wait_for_timeout(200)
        page.screenshot(path="/tmp/t9_board.png")
        page.evaluate("nodes.get('ttempo').el.scrollIntoView("
                      "{block: 'center', inline: 'center'})")
        page.wait_for_timeout(150)
        clip19 = page.evaluate("""(() => {
          const b = nodes.get('ttempo').el.getBoundingClientRect();
          const x = Math.max(0, b.x - 16), y = Math.max(0, b.y - 16);
          return {x, y, width: Math.min(innerWidth - x, b.width + 32),
                  height: Math.min(innerHeight - y, b.height + 32)};
        })()""")
        page.screenshot(path="/tmp/t9_tempo.png", clip=clip19)

        # ---- kill ------------------------------------------------------
        page.evaluate("window.__sent.length = 0")
        page.evaluate("""() => {
          nodes.get('tplay').el.querySelector('.kill').click();
          nodes.get('ttempo').el.querySelector('.kill').click();
        }""")
        sent = page.evaluate("window.__sent")
        check("kills send remove_transport_card play / tempo",
              {"type": "remove_transport_card", "which": "play"} in sent
              and {"type": "remove_transport_card", "which": "tempo"}
              in sent, str(sent))

        # ---- old servers: no transport_cards/downbeat → no cards -------
        err0 = len(errors)
        st_old = base_state(
            [sg, echo],
            [{"from": "signal_gen", "to": "echo"},
             {"from": "echo", "to": "master"}])
        page.evaluate("(s) => __msg({type: 'state', ...s})", st_old)
        page.wait_for_timeout(400)
        old19 = page.evaluate(
            "[nodes.has('tplay'), nodes.has('ttempo')]")
        check("old-server state renders NO transport cards, no errors",
              old19 == [False, False] and len(errors) == err0,
              str([old19, errors[err0:]]))

        check("no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
