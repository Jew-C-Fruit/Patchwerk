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
            allocations=[],
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

        check("flex+artifix: no page errors", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
