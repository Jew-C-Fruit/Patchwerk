"""Headless GUI checks for the Artifix package — Spectrum + Sphere monitors.

    python tests/gui_check_artifix.py

Spawns the two new monitors against mock state, feeds a mock scope_data
window and a trajectory frame, and asserts they spawn + render with no page
errors and that the Spectrum polls a scope source. No synth server needed
(reuses the gui_check8 harness).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from gui_check8 import FLEX, LAYOUT, base_state, mod, open_page, param  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    with sync_playwright() as p:
        browser, page, errors = open_page(p, FLEX.as_uri(), layout=LAYOUT)
        gen = mod("artifix_gen", "Artifix Gen", "source", "voice",
                  {"morph": param(), "amp": param()})
        st = base_state(
            [gen], [{"from": "artifix_gen", "to": "master"}],
            living=[{"id": "artifix_gen.morph", "key": "artifix_gen",
                     "param": "morph", "life": 0.35, "wander": 0.3,
                     "depth": 0.4, "center": 0.5}],
            allocations=[{
                "id": "alloc", "dims": ["wave", "harm", "filt", "stereo", "res", "det"],
                "r": 1.0, "w0": 0.5, "w1": 0.35, "w2": 0.45, "w3": 0.4,
                "w4": 0.25, "w5": 0.3,
                "targets": {"1": {"key": "artifix_gen", "param": "harm"}},
            }],
        )
        page.evaluate("(s) => __msg({type: 'state', ...s})", st)
        page.wait_for_timeout(200)

        # spawn Spectrum + Sphere from the palette
        page.evaluate("""() => {
          for (const b of document.querySelectorAll('#palette button')) {
            const t = b.textContent.trim();
            if (t === 'Spectrum' || t === 'Sphere') b.click();
          }
        }""")
        page.wait_for_timeout(200)

        # feed a scope window (20-cycle sine) + a trajectory frame
        page.evaluate("""() => {
          const N = 2048, s = [];
          for (let i = 0; i < N; i++) s.push(Math.sin(2*Math.PI*i*20/N) * 0.5);
          __msg({type: 'scope_data', key: 'artifix_gen', sr: 44100, samples: s});
          __msg({type: 'trajectory', traj: {'artifix_gen.morph': [0.3, -0.2, 0.13]}});
        }""")
        page.wait_for_timeout(400)

        specs = page.evaluate(
            "document.querySelectorAll('canvas[data-viz=spectrum]').length")
        sphs = page.evaluate(
            "document.querySelectorAll('canvas[data-viz=sphere]').length")
        sent = page.evaluate("window.__sent")
        check("spectrum monitor spawned", specs >= 1, str(specs))
        check("sphere monitor spawned", sphs >= 1, str(sphs))
        # Sphere containment: the raw trajectory (x,y) each in [-1,1] reaches
        # radius √2 at the corners; sphereUnit must map EVERY point into the
        # unit disk so the dot never escapes the drawn circle (the reported bug
        # where "the indicator dot is not mapped to within the sphere").
        contained = page.evaluate("""() => {
          const pts = [[1,1],[-1,1],[1,-1],[-1,-1],[3.2,3.2],[0.3,-0.2],[0,0]];
          return pts.every(([x,y]) => {
            const [ux,uy] = sphereUnit(x,y);
            return ux*ux + uy*uy <= 1.0000001;
          });
        }""")
        check("sphere dot stays inside the circle (containment)", contained)
        check("spectrum polls a scope source",
              any(m.get("type") == "scope" for m in sent))

        # the Living Oscillator card renders from state.living, and its palette
        # entry exists
        living_card = page.evaluate(
            "!!document.getElementById('card-living:artifix_gen.morph') "
            "|| [...document.querySelectorAll('.mod .title')]"
            ".some(t => /Living/.test(t.textContent))")
        pal_living = page.evaluate(
            "[...document.querySelectorAll('#palette button')]"
            ".some(b => b.textContent.trim().startsWith('Living'))")
        check("Living Oscillator card renders from state.living", living_card)
        check("palette offers a Living Oscillator", pal_living)

        # arming a Living Oscillator and dropping it on a knob sends living_assign
        page.evaluate("""() => {
          const n = buildArmedLiving();
          window.__armGid = n.gid;
        }""")
        page.wait_for_timeout(150)
        armed_kind = page.evaluate(
            "(() => { for (const [g, n] of nodes) if (g === window.__armGid) "
            "return n.modKind; return null; })()")
        check("armed Living card is tagged modKind=living", armed_kind == "living",
              str(armed_kind))

        # Allocation card renders from state.allocations, its 6 dim out-ports are
        # slot-tagged, the wired slot draws a mod wire, and the palette offers it
        alloc = page.evaluate("""() => {
          let card = null;
          for (const [g, nd] of nodes) if (g === 'alloc:alloc') card = nd;
          if (!card) return {ok: false};
          const outs = card.ports.filter(p => p.dir === 'out' && p.sig === 'mod');
          const slots = outs.map(p => p.slot).filter(s => s != null);
          const pal = [...document.querySelectorAll('#palette button')]
            .some(b => b.textContent.trim().startsWith('Allocation'));
          return {ok: true, modKind: card.modKind, nOuts: outs.length,
                  slots: slots.length, pal};
        }""")
        check("Allocation card renders from state.allocations", alloc.get("ok"))
        check("Allocation has 6 slot-tagged dim out-ports",
              alloc.get("nOuts") == 6 and alloc.get("slots") == 6, str(alloc))
        check("Allocation card is tagged modKind=alloc",
              alloc.get("modKind") == "alloc", str(alloc.get("modKind")))
        check("palette offers an Allocation Intent", alloc.get("pal"))

        # exercise connectAction directly: dropping dim slot 0 on the morph knob
        # must emit alloc_wire(id, slot=0, key, name=morph) — verifies the new
        # branch without simulating a full pointer drag
        wired = page.evaluate("""() => {
          let a = null, tgt = null;
          for (const [g, nd] of nodes) {
            if (g === 'alloc:alloc') a = nd;
            if (g === 'm:artifix_gen') tgt = nd;
          }
          if (!a || !tgt) return {ok: false, why: 'no cards'};
          const outP = a.ports.find(p => p.dir === 'out' && p.sig === 'mod' && p.slot === 0);
          const inP = tgt.ports.find(p => p.quiet && p.param === 'morph');
          if (!outP || !inP) return {ok: false, why: 'no ports', hasOut: !!outP, hasIn: !!inP};
          const fn = connectAction({node: a, port: outP}, {node: tgt, port: inP});
          if (!fn) return {ok: false, why: 'no action'};
          window.__sent.length = 0; fn();
          return {ok: true, sent: window.__sent};
        }""")
        ok = wired.get("ok") and any(
            m.get("type") == "alloc_wire" and m.get("slot") == 0
            and m.get("name") == "morph" and m.get("id") == "alloc"
            for m in (wired.get("sent") or []))
        check("dropping alloc dim 0 on a knob sends alloc_wire(slot=0)", ok, str(wired))

        check("flex+artifix: no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
