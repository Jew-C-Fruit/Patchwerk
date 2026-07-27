"""Live probe for browser tab hygiene (item 37 Phase 1 follow-up; Mac only).

    .venv/bin/python -u tests/probe_browser.py

Proves the whole cycle against the REAL browser over AppleScript: a rig tab
is opened once, reused rather than duplicated, recognised as stale the moment
its rig dies, swept by `close_stale()`, and — separately — closed by the
driver's own teardown because the driver opened it.

It drives whichever scriptable browser is already running (Safari here; there
is no Chrome on this Mac). It NEVER launches one, it only ever opens and
closes tabs on `127.0.0.1:8799`, and it fails loudly rather than guessing if
Automation permission is missing. Your other tabs are counted before and
after, and a check fails if that count moves.

The rig under it is `tests/silent_rig.py` — no scsynth, no audio — because
none of this needs the engine, and audio may not start at all.
"""

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import browser as B  # noqa: E402
import rig as R  # noqa: E402

FAILURES = []
PORT = 8799


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def others(tabs):
    """Everything that is NOT ours — must be identical at the end."""
    return sorted(t.url for t in tabs if not B.is_patchwerk(t))


async def main(argv=None) -> int:
    argparse.ArgumentParser().parse_args(argv)

    check("running() answers without LAUNCHING a browser",
          isinstance(B.running(), list))
    apps = B.running()
    print(f"running: {apps or 'none'}   installed: {B.installed() or 'none'}")
    if not apps:
        print("no browser running — start one and re-run. (This probe will "
              "not launch yours.)")
        return 2
    try:
        baseline = B.all_tabs()
    except B.BrowserError as exc:
        check("the browser is scriptable", False, str(exc))
        print("\nFAIL — grant Automation permission and re-run")
        return 1
    check("the browser is scriptable", True)
    bystanders = others(baseline)
    print(f"{len(baseline)} tabs open, {len(bystanders)} of them not ours")

    url = f"http://127.0.0.1:{PORT}/"
    async with R.Rig(p=PORT, silent=True) as rig:
        check("the rig itself opened no tab (--no-browser)",
              not [t for t in B.all_tabs() if t.port == PORT],
              str([str(t) for t in B.all_tabs() if t.port == PORT]))

        reused = rig.open_ui()
        check("first open_ui() opens a tab", reused is False)
        mine = [t for t in B.patchwerk_tabs() if t.port == PORT]
        check("the rig's tab is open and classified as Patchwerk",
              len(mine) == 1, str([str(t) for t in mine]))
        check("a LIVE rig's tab is not stale",
              url not in {t.url for t in B.stale_tabs()},
              str([str(t) for t in B.stale_tabs()]))

        check("second open_ui() REUSES it instead of stacking another",
              rig.open_ui() is True)
        check("still exactly one tab for this rig",
              len([t for t in B.patchwerk_tabs() if t.port == PORT]) == 1,
              str([str(t) for t in B.patchwerk_tabs() if t.port == PORT]))
        check("find_tab matches on ORIGIN, not the exact route",
              B.find_tab(f"http://127.0.0.1:{PORT}/blocks") is not None)

    check("teardown closed the tab the driver opened",
          not [t for t in B.all_tabs() if t.port == PORT],
          str([str(t) for t in B.all_tabs() if t.port == PORT]))

    # -- the orphan case: a session that died without cleaning up ----------
    rig2 = R.Rig(p=PORT, silent=True)
    await rig2.start()
    rig2.open_ui()
    check("a second session's tab is open",
          bool([t for t in B.all_tabs() if t.port == PORT]))
    await rig2.shutdown()          # the rig dies...
    rig2._tabs.clear()             # ...and the session that owned the tab is gone
    for _ in range(20):
        if not await R.is_up(PORT):
            break
        await asyncio.sleep(0.25)
    stale = [t for t in B.stale_tabs() if t.port == PORT]
    check("the orphan is detected as STALE once its rig is gone",
          len(stale) == 1, str([str(t) for t in B.stale_tabs()]))
    closed = B.close_tabs(stale)
    check("close_tabs() sweeps it and reports it by RE-LISTING, not by "
          "trusting the script's return", closed == 1, str(closed))
    check("and it is gone",
          not [t for t in B.all_tabs() if t.port == PORT],
          str([str(t) for t in B.all_tabs() if t.port == PORT]))
    try:
        await rig2.stop(restore=False)
    except Exception:  # noqa: BLE001 — its rig is already down
        pass

    after = others(B.all_tabs())
    check("every tab that was not ours is untouched", after == bystanders,
          f"was {bystanders}\n       now {after}")

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
