"""LIVE GUI probe — drives the RUNNING rig and checks what the browser
actually renders.

Mac-only manual check, like the other probe_*_ws.py scripts: it needs
`python -m synthbase gui` actually running (default http://127.0.0.1:8765).
CI cannot run this.

What makes it different from tests/check_blocks.py: nothing here is mocked.
Every reaction is caused by REAL backend logic travelling over the REAL
websocket — a gate wire is spawned and pulsed server-side, and the probe then
reads the rendered DOM/computed styles. That is the only way to verify the
REACTIVE-INDICATOR DOCTRINE (Cole, 2026-07-24): "all buttons and state
indicators must graphically react when triggered by LOGIC input, not just
user clicks." A headless mock can only prove the GUI reacts to a message we
invented; this proves it reacts to the engine.

Usage:
    ./run.sh                                  # or python -m synthbase gui
    .venv/bin/python tests/probe_live_gui.py  # add --url / --keep-shots

Screenshots land in /tmp/patchwerk_live/ for eyeballing after a run.
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    print("playwright missing — pip install -r requirements-dev.txt "
          "&& playwright install chromium")
    sys.exit(2)

SHOTS = "/tmp/patchwerk_live"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(("ok    " if ok else "FAIL  ") + name
          + (f"  [{detail}]" if detail and not ok else ""))


def alpha_of(page, css_color: str) -> float:
    """color-mix() computes to `color(srgb r g b / a)` in Chromium, so read
    the alpha instead of pattern-matching for "rgba"."""
    return page.evaluate("""(bg) => {
      const m = String(bg).match(/\\/\\s*([0-9.]+)\\s*\\)/)
             || String(bg).match(/rgba\\([^)]*,\\s*([0-9.]+)\\s*\\)/);
      return m ? parseFloat(m[1]) : 1;
    }""", css_color)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--keep-shots", action="store_true")
    args = ap.parse_args()
    os.makedirs(SHOTS, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(args.url, wait_until="networkidle")
        except Exception as exc:                       # noqa: BLE001
            print(f"cannot reach {args.url} — is the rig running?  ({exc})")
            browser.close()
            return 2
        page.wait_for_timeout(2500)

        live = page.evaluate("!!state && ws && ws.readyState === 1")
        check("GUI connects to the live rig over the websocket", live)
        if not live:
            browser.close()
            return 1

        def send(msg: dict, settle: int = 420) -> None:
            page.evaluate("(m) => send(m)", msg)
            page.wait_for_timeout(settle)

        print(f"      patch={page.evaluate('state.patch')} "
              f"chain={page.evaluate('state.chain.map(c => c.key)')}")

        # clean slate: drop any trigger cards a previous run left behind
        for bid in page.evaluate("(state.buttons || []).map(x => x.id)"):
            send({"type": "remove_button", "id": bid})

        # ============================================================
        # 1. REACTIVE DOCTRINE: a module's power stripe follows :pwr
        # ============================================================
        target = page.evaluate(
            "(state.chain.find(c => c.kind === 'effect' && !c.service)"
            " || state.chain[0]).key")
        send({"type": "spawn_button"})
        bid = page.evaluate("state.buttons[state.buttons.length - 1].id")
        send({"type": "set_button", "id": bid, "latch": True})
        send({"type": "ctl_wire", "action": "add",
              "from": bid, "to": f"{target}:pwr"})

        def stripe() -> dict:
            return page.evaluate("""(k) => {
              const n = nodes.get('m:' + k);
              if (!n) return null;
              const el = n.el.querySelector('.stripe');
              return {on: el.classList.contains('on'),
                      bg: getComputedStyle(el).backgroundColor,
                      bypassed: n.el.classList.contains('bypassed'),
                      enabled: (state.chain.find(c => c.key === k) || {}).enabled};
            }""", target)

        # NB: a level-in applies its source's level on FIRST SIGHT, so merely
        # attaching a lo button already drives the module off. That IS the
        # doctrine working — so assert the TRANSITIONS, never a starting state.
        wired = stripe()
        page.screenshot(path=f"{SHOTS}/1a_pwr_lo.png")
        send({"type": "fire_button", "id": bid})
        hi = stripe()
        page.screenshot(path=f"{SHOTS}/1b_pwr_hi.png")
        send({"type": "fire_button", "id": bid})
        lo = stripe()

        check("attaching a LO :pwr wire unfills the stripe + disables live",
              wired and not wired["on"] and wired["bypassed"]
              and wired["enabled"] is False, str(wired))
        check("a LOGIC pulse HI fills the power stripe (no click involved)",
              hi and hi["on"] and not hi["bypassed"]
              and hi["bg"] != "rgba(0, 0, 0, 0)", str(hi))
        check("...and the backend really enabled the module",
              hi and hi["enabled"] is True, str(hi))
        check("a LOGIC pulse LO unfills it again (both directions)",
              lo and not lo["on"] and lo["enabled"] is False, str(lo))

        # ============================================================
        # 2. REACTIVE DOCTRINE headline: transport play/stop by logic
        # ============================================================
        send({"type": "spawn_transport_card", "which": "play"})
        send({"type": "spawn_transport_card", "which": "tempo"})
        send({"type": "ctl_wire", "action": "add",
              "from": bid, "to": "transport:run"})

        def tplay() -> dict:
            return page.evaluate("""() => {
              const n = nodes.get('tplay');
              if (!n) return null;
              const btn = n.el.querySelector('.tpbtn');
              const lab = n.el.querySelector('.tplabel');
              return {glyph: btn.textContent.trim(),
                      playing: n.el.classList.contains('playing'),
                      stopped: n.el.classList.contains('stopped'),
                      label: lab ? lab.textContent : null,
                      bar: document.getElementById('play-btn').textContent,
                      running: state.transport.running};
            }""")

        t0 = tplay()
        send({"type": "fire_button", "id": bid})
        t1 = tplay()
        page.screenshot(path=f"{SHOTS}/2_transport.png")
        send({"type": "fire_button", "id": bid})
        t2 = tplay()

        check("Play/Stop card renders the redesign (glyph + state label)",
              t0 and t0["label"] in ("playing", "stopped")
              and t0["glyph"] in ("⏵", "⏹"), str(t0))
        check("LOGIC input flips the Play/Stop card visually",
              t1 and t0 and t1["playing"] != t0["playing"]
              and t1["glyph"] != t0["glyph"], f"{t0} -> {t1}")
        check("the card GLOW tracks the state (exactly one class)",
              t1 and (t1["playing"] ^ t1["stopped"]) == 1, str(t1))
        check("the TOP BAR follows the same logic input (one state, two views)",
              t1 and t0 and t1["bar"] != t0["bar"], f"{t0} -> {t1}")
        check("the backend transport really changed",
              t1 and t0 and t1["running"] != t0["running"], f"{t0} -> {t1}")
        check("logic flips it back (both directions)",
              t2 and t0 and t2["playing"] == t0["playing"], f"{t1} -> {t2}")

        # click LED follows its own level-in
        send({"type": "ctl_wire", "action": "remove",
              "from": bid, "to": "transport:run"})
        send({"type": "ctl_wire", "action": "add",
              "from": bid, "to": "transport:click"})

        def click_led() -> dict:
            return page.evaluate("""() => {
              const n = nodes.get('ttempo');
              const row = [...n.el.querySelectorAll('.mini')].find(
                x => (x.querySelector('label') || {}).title === 'click');
              const el = row && row.querySelector('.onoff');
              return el ? {on: el.classList.contains('on'),
                           bg: getComputedStyle(el).backgroundColor} : null;
            }""")

        c0 = click_led()
        send({"type": "fire_button", "id": bid})
        c1 = click_led()
        check("the click LED follows its LEVEL-in live",
              c0 and c1 and c0["on"] != c1["on"] and c0["bg"] != c1["bg"],
              f"{c0} -> {c1}")

        # ============================================================
        # 3. Master Out spreads a heavy fan-in over BOTH edges
        # ============================================================
        for _ in range(5):
            send({"type": "spawn_module", "key": "fm_bell"}, 320)
        for k in page.evaluate(
                "state.chain.filter(c => c.type === 'fm_bell').map(c => c.key)"):
            send({"type": "graph_wire", "action": "add",
                  "from": k, "to": "master"}, 320)
        page.wait_for_timeout(900)
        m = page.evaluate("""() => {
          const n = nodes.get('master');
          const hs = n.lay.handles.filter(h => h.side === 'in');
          const edges = {};
          for (const h of hs) edges[h.edge] = (edges[h.edge] || 0) + 1;
          return {edges, total: hs.length, dual: !!n.ports[0].dualEdge};
        }""")
        page.screenshot(path=f"{SHOTS}/3_master.png", full_page=True)
        check("Master Out is dual-edge and spreads its fan-in over two edges",
              m and m["dual"] and len(m["edges"]) >= 2, str(m))

        # ============================================================
        # 4. wires: none may hide completely inside another
        # ============================================================
        ov = page.evaluate("""() => {
          const segs = [];
          for (const w of wires) {
            const Q = w.dpts || w.pts;
            if (!Q) continue;
            for (let k = 0; k < Q.length - 1; k++) {
              const a = Q[k], b = Q[k + 1];
              const vert = Math.abs(a[0] - b[0]) < 0.01;
              const horiz = Math.abs(a[1] - b[1]) < 0.01;
              if (vert === horiz) continue;
              const p0 = vert ? a[1] : a[0], p1 = vert ? b[1] : b[0];
              const lo = Math.min(p0, p1), hi = Math.max(p0, p1);
              if (hi - lo < 8) continue;
              segs.push({w, vert, coord: vert ? a[0] : a[1], lo, hi});
            }
          }
          let worst = 0;
          for (let i = 0; i < segs.length; i++)
            for (let j = i + 1; j < segs.length; j++) {
              const A = segs[i], B = segs[j];
              if (A.w === B.w || A.vert !== B.vert) continue;
              if (Math.abs(A.coord - B.coord) > 1.0) continue;
              worst = Math.max(worst,
                Math.min(A.hi, B.hi) - Math.max(A.lo, B.lo));
            }
          return {wires: wires.length, segs: segs.length, worst};
        }""")
        check("no wire runs hidden inside another on a busy patch",
              ov["worst"] < 8, str(ov))

        # ============================================================
        # 5. layout: top margin, header clearance, scrollbars
        # ============================================================
        g = page.evaluate("""() => ({
          topUnits: unitRect({size: 'M', bx: 0, by: 0, half: null}).y,
          gut: GUT, topm: TOPM,
          hdrH: Math.ceil(document.querySelector('header')
                  .getBoundingClientRect().height),
          boardTop: parseFloat(document.getElementById('board').style.top || '0'),
          sb: getComputedStyle(document.getElementById('board')).scrollbarWidth,
        })""")
        check("the top row sits GUT + 1 unit down (wire clearance)",
              g["topUnits"] == g["gut"] + g["topm"], str(g))
        check("the board clears the MEASURED header (never occluded)",
              g["boardTop"] >= g["hdrH"] - 1, str(g))
        check("scrollbars are the thin variant", g["sb"] == "thin", str(g))

        # ============================================================
        # 6. flex: translucent cards, handles clear of controls
        # ============================================================
        page.evaluate("() => setMode('flex')")
        page.wait_for_timeout(1200)
        f = page.evaluate("""() => {
          const wr = world.getBoundingClientRect();
          const zs = parseFloat(world.style.zoom) || 1;
          let hits = 0, prim = 0, bg = null;
          for (const n of nodes.values()) {
            if (!n.lay) continue;
            if (!bg) bg = getComputedStyle(n.el).backgroundColor;
            const ctrls = [...n.el.querySelectorAll(
              '.mini .track, .mini .chip, .kill, .onoff, .stripe.pwr')]
              .map(e => e.getBoundingClientRect());
            for (const H of n.lay.handles) {
              if (H.quiet) continue;
              prim++;
              const hx = wr.left + H.x * zs, hy = wr.top + H.y * zs;
              for (const c of ctrls)
                if (hx > c.left - 3 && hx < c.right + 3
                    && hy > c.top - 3 && hy < c.bottom + 3) hits++;
            }
          }
          return {prim, hits, bg};
        }""")
        page.screenshot(path=f"{SHOTS}/4_flex.png", full_page=True)
        check("flex cards are translucent (wires show through)",
              alpha_of(page, f["bg"]) < 1, str(f))
        check("no flex wire handle sits on top of a control",
              f["prim"] > 0 and f["hits"] == 0, str(f))
        page.evaluate("() => setMode('blocks')")
        page.wait_for_timeout(700)

        # ============================================================
        # 7. it makes SOUND (the check no headless suite can make)
        # ============================================================
        send({"type": "ctl_wire", "action": "remove",
              "from": bid, "to": f"{target}:pwr"})
        send({"type": "set_enabled", "key": target, "enabled": True})
        send({"type": "set_transport", "playing": True})
        page.evaluate("() => send({type: 'note_on', note: 60, velocity: 110})")
        peak = 0.0
        for _ in range(24):                       # ~2.4 s of meter frames
            page.wait_for_timeout(100)
            peak = max(peak, page.evaluate(
                "typeof outLevel === 'number' ? outLevel : 0") or 0)
        page.evaluate("() => send({type: 'note_off', note: 60})")
        check("the engine makes SOUND (master meters move on a held note)",
              peak > 0.0005, f"peak={peak}")

        check("no page errors during the whole live session",
              not errors, "; ".join(errors[:3]))
        page.screenshot(path=f"{SHOTS}/5_final.png", full_page=True)
        browser.close()

    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{'PASS' if not bad else 'FAIL'} — "
          f"{len(RESULTS) - len(bad)}/{len(RESULTS)} live checks green"
          f"   (screenshots in {SHOTS})")
    for n, ok, detail in RESULTS:
        if not ok:
            print(f"  failed: {n}  {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
