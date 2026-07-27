"""Transcript replay checks — the engine's OUTPUT joined to the GUI's render.

    python tests/check_replay.py                 # both planes, what's installed
    python tests/check_replay.py --emission      # no browser needed
    python tests/check_replay.py --dom           # no engine needed
    python tests/check_replay.py --rerecord      # refresh the fixtures

Item 37 Phase 2. `tests/replay.py` is the engine; this file is the
assertions. Everything here runs with **no audio, no scsynth and no rig** —
the transcripts are recorded through `tests/silent_rig.py`, which serves the
real `GuiServer` over an engine-less `SynthApp` in under a second.

WHAT THIS ASSERTS THAT NOTHING IN THE REPO DID BEFORE
=====================================================

1. **The backend still emits it.** Re-records each scenario against THIS
   tree and diffs the event skeleton against the committed transcript. The
   nine Python suites observe applied state — `tr.click_enabled is True` —
   and the plan proved empirically that replacing all four `_emit_level`
   call sites with `pass` leaves every one of them green. This is the check
   that goes red.

2. **Every surface, not just the first one.** An endpoint x surface matrix,
   asserted at BOTH edges. `check_blocks` injects five `level` events and
   reads one surface each, rising-edge only; the click injection at
   `check_blocks.py:3442` reads the card LED and never `#click-on`.

3. **Against real messages, not invented ones.** The three Playwright suites
   contain the string `synthbase` zero times — every `{"kind":"level"}` they
   assert is a shape they wrote themselves. These are recordings. A field the
   engine renamed, a `pulse` tag the GUI never learned to read, an event that
   arrives in a different order than the mock assumed: all of it is in scope
   here and none of it is in scope for a mock.

4. **The action is observable at all.** A scenario that changes an audible
   global must produce at least one message that moves the client's view of
   it. This is a statement about the message STREAM, and a state-shape
   assertion cannot see an absence.

5. **No unpaired notes, over a recording.** CLAUDE.md's stuck-note invariant,
   walked across real traffic rather than a constructed event list.

6. **The coincidence rule** (a standing lesson, 2026-07-26: two sessions
   shipped assertions that passed because a fixture sat at a default). Every
   surface reading records what the surface said BEFORE the event too, and
   only readings where those differ count as evidence. A matrix that is green
   but uninformative FAILS here — see `NO EVIDENCE` in the report.

7. **One proof per DRIVER.** Where two events legitimately drive the same
   surface, each must move it ALONE, asserted in its own pass with the other
   withheld. A surface whose second driver masks its first one going dead is
   bug C's shape exactly; redundancy is only redundancy while both paths
   still work. See DRIVERS below for the measurement that forced this and
   for the repair that was rejected.

PENDING ROWS ARE ASSERTED, NOT SKIPPED
======================================

`gui/blocks.html` is fenced to the item 11 session this cycle, so this file
never edits it — it specifies. Rows whose GUI half is not written are marked
`PENDING`, and a PENDING row is asserted to be STILL UNIMPLEMENTED. When the
handler lands, the row turns XPASS and FAILS with the exact line to flip.
That is deliberate: a pending row that silently passes forever is the rot
this suite exists to prevent, and the merge cost is one word per row.

BRANCH ORDER. This is `feat/p37-transcript-replay`, branched from
`feat/p37-rig-driver` (Phase 1), which it needs for `rig.py`, `transcript.py`
and `silent_rig.py`. The committed transcript `transcript_indicators.jsonl`
was recorded against `feat/p3-reactive-taps`' engine, so it carries the
`pulse` tag and `{"kind":"transport"}`. Neither branch is on `main`. On a
tree without the reactive-taps engine the emission plane reports those rows
as `PRE-CONTRACT` rather than as regressions — `replay.engine_capabilities()`
probes rather than assumes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import replay as R                                  # noqa: E402
from transcript import Transcript, read_transcript  # noqa: E402

_SCRATCH = None


def _scratch() -> Path:
    """A private scratch dir for this process's re-recordings.

    Torn down with the interpreter. Keeping it out of a shared constant
    path is not tidiness: two sessions writing one filename corrupt each
    other's evidence, and a corrupted transcript fails as a backend
    regression rather than as an I/O collision.
    """
    global _SCRATCH
    if _SCRATCH is None:
        import atexit
        import shutil
        import tempfile
        _SCRATCH = Path(tempfile.mkdtemp(prefix="patchwerk-replay-"))
        atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
    return _SCRATCH


FAILURES: list[str] = []
RAN: list[str] = []
NOTES: list[str] = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    RAN.append(name)
    if not cond:
        FAILURES.append(name)


def note(msg):
    print("note  " + msg)
    NOTES.append(msg)


# =============================================================================
# The endpoint x surface matrix — DATA, one row per surface.
# =============================================================================
#
# Built from the frozen contract in `continuity/reactive-taps-gui-handoff.md`
# section 2.1 and cross-checked against the recording, so a contract endpoint
# with no row here is reported (NO ROW) rather than silently uncovered.
#
# `js` is evaluated in the page and returns the surface's reading:
#   mode "level"  -> a bool: is the indicator showing ON?
#   mode "change" -> a signature string: any movement means it flashed.
# It must return null when the surface is absent from the board, which the
# report distinguishes from "present and wrong".
#
# `status`:
#   "live"    -> the GUI implements this; a miss is a REGRESSION.
#   "pending" -> the GUI does not; a hit is an XPASS, and the row is stale.

def _row_led(gid, title):
    """A `.onoff` LED on the row whose label title is `title`."""
    return (f"(() => {{ const n = nodes.get({json.dumps(gid)}); if (!n) return null;"
            f" const r = [...n.el.querySelectorAll('.mini')].find(x =>"
            f" (x.querySelector('label')||{{}}).title === {json.dumps(title)});"
            f" const o = r && r.querySelector('.onoff');"
            f" return o ? o.classList.contains('on') : null; }})()")


def _sig(el_js):
    """A flash-sensitive signature of an element: classes + the computed
    properties any plausible flash implementation would move."""
    return (f"(() => {{ const el = {el_js}; if (!el) return null;"
            f" const c = getComputedStyle(el);"
            f" return [el.className, c.boxShadow, c.backgroundColor, c.outlineColor,"
            f" c.opacity, c.transform, c.animationName].join('|'); }})()")


def _port_row(gid, ep):
    return (f"(() => {{ const n = nodes.get({json.dumps(gid)}); if (!n) return null;"
            f" const p = n.ports.find(q => q.ep === {json.dumps(ep)});"
            f" return p && p.rowEl || null; }})()")


#: Which event kinds a surface is expected to follow. DEFAULT is the level
#: tap alone; a row lists more only when the contract genuinely gives the
#: surface a second driver.
#:
#: WHY THIS EXISTS. `{"kind":"transport"}` (reactive-taps handoff 1.2)
#: refreshes the same surfaces the `transport:*` level taps drive, and the
#: engine emits it 0.0-0.1 ms BEFORE the tap — same settle pass, same tick
#: (measured: 8.5346 vs 8.5347 on the falling edge). So a probe straddling
#: only the tap reads a surface the transport handler has already moved,
#: every time, on both edges: ten rows reporting "none informative". The
#: guard was RIGHT to refuse them.
#:
#: The suggested repair was to open the probe window before BOTH events.
#: That was measured and REJECTED. It turns all ten green — and it still
#: turns them green with the entire `transport:*` branch of the level
#: handler DELETED from blocks.html, because the transport handler alone
#: moves all five surfaces. A row named "follows the rising edge" would be
#: asserting a causal fact it no longer tests: exactly the green-but-
#: worthless reading the coincidence rule exists to refuse.
#:
#: So two legitimate drivers get two independent proofs. Each is replayed
#: with the OTHER driver's events withheld, which RESTORES attribution
#: rather than abandoning it. This is not a workaround for the collision —
#: it is the same bug class the suite was built for. A surface whose second
#: driver masks its first one going dead is bug C's shape exactly;
#: redundancy is only redundancy while both paths still work, and nothing
#: asserted that until now.
#:
#: Narrowing the transport handler in blocks.html to dodge the collision was
#: also considered and rejected: handoff 1.2 asks for that refresh, so
#: removing it would be changing the code to fit the test.
#: A row's drivers, mapped to a per-driver status OVERRIDE (or None to
#: inherit the row's). Status has to be per-driver, not per-row: on a base
#: without item 11's `gui/blocks.html`, `transport:run -> Play/Stop card` is
#: LIVE via the level tap and has no transport handler to follow at all. One
#: row-level flag cannot say that, and forcing the whole row to `pending`
#: would stop asserting a level path that demonstrably works.
DRIVERS_DEFAULT = {"level": None}

#: For the `transport` driver, which field of the event payload each
#: endpoint's surface must follow. One event carries all three, so exactly
#: the field that changed yields an informative reading — attribution falls
#: out of the payload instead of having to be inferred.
TRANSPORT_FIELD = {"transport:run": "running", "transport:click": "click",
                   "transport:accent": "accent"}

MATRIX = [
    # ---- steady level-ins ---------------------------------------------------
    dict(ep="transport:run", mode="level", status="live",
         drivers={"level": None, "transport": None},
         surface="Play/Stop card",
         js="(() => { const n = nodes.get('tplay');"
            " return n ? n.el.classList.contains('playing') : null; })()"),
    dict(ep="transport:run", mode="level", status="live",
         drivers={"level": None, "transport": None},
         surface="top bar #play-btn",
         js="(() => { const e = document.getElementById('play-btn');"
            " return e ? e.textContent === '\\u23f9' : null; })()"),

    dict(ep="transport:click", mode="level", status="live",
         drivers={"level": None, "transport": None},
         surface="Tempo card click LED", js=_row_led("ttempo", "click")),
    dict(ep="transport:click", mode="level", status="live",
         drivers={"level": None, "transport": None},
         owner="feat/p11-dual-mode — syncTopBarClick(), already written there",
         surface="top bar #click-on",
         js="(() => { const e = document.getElementById('click-on');"
            " return e ? !!e.checked : null; })()"),

    # accent has ONE surface by Cole's decision (2026-07-26): no top-bar
    # element exists, so there is nothing to mirror. A row asserting a
    # top-bar accent control would be asserting a fiction — do not add one.
    dict(ep="transport:accent", mode="level", status="live",
         drivers={"level": None, "transport": None},
         surface="Tempo card accent LED", js=_row_led("ttempo", "accent")),

    dict(ep="drums:pwr", mode="level", status="live",
         surface="Drums power stripe",
         js="(() => { const n = nodes.get('drums'); if (!n) return null;"
            " const s = n.el.querySelector('.stripe.pwr');"
            " return s ? s.classList.contains('on') : null; })()"),

    # ---- pulse trig-ins -----------------------------------------------------
    # Every one of these is unwritten on this base. The contract for them is
    # handoff section 1.1: flash on `on:true`, IGNORE `on:false`, and survive a
    # zero-duration pair (both halves in one tick) still visibly lit.
    dict(ep="transport:tap", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.2(b), flash the tap port's rowEl",
         surface="Tempo card tap-port row",
         js=_sig(_port_row("ttempo", "transport:tap"))),
    dict(ep="deck:rec", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.3, note ep 'rec' vs data-a 'record'",
         surface="Deck record button",
         js=_sig("(nodes.get('deck')||{el:{querySelector:()=>null}}).el"
                 ".querySelector('.deckbtns button[data-a=\"record\"]')")),
    dict(ep="deck:play", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.3",
         surface="Deck play button",
         js=_sig("(nodes.get('deck')||{el:{querySelector:()=>null}}).el"
                 ".querySelector('.deckbtns button[data-a=\"play\"]')")),
    dict(ep="deck:stop", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.3",
         surface="Deck stop button",
         js=_sig("(nodes.get('deck')||{el:{querySelector:()=>null}}).el"
                 ".querySelector('.deckbtns button[data-a=\"stop\"]')")),
    dict(ep="deck:clear", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.3",
         surface="Deck clear button",
         js=_sig("(nodes.get('deck')||{el:{querySelector:()=>null}}).el"
                 ".querySelector('.deckbtns button[data-a=\"clear\"]')")),
    dict(ep="tonic", mode="change", status="live",
         owner="gui/blocks.html — handoff section 2.4, the deriver's trigger row",
         surface="Theory Wizard trigger row",
         js=_sig("(() => { const n = nodes.get('tonic'); if (!n) return null;"
                 " const p = n.ports.find(q => q.sig === 'bin' && q.dir === 'in');"
                 " return p && p.rowEl || null; })()")),
]

#: Endpoints the contract deliberately gives NO level-driven surface. Listed
#: so `NO ROW` stays meaningful: an endpoint absent from both is a gap.
#: Endpoints with no level-driven surface row, each carrying its OWN reason
#: and its OWN staleness test — `CI_EXEMPT`'s shape (batch-merge-protocol
#: 1.3): the excuse lives with the thing it excuses, so the two cannot
#: quietly disagree, and the excuse dies when its premise does.
#:
#: `stale_when` is a predicate over the transcript. When it fires, the
#: exemption's premise has stopped holding and the check FAILS asking for a
#: real matrix row. Without it an exemption is just an uncovered indicator
#: with a sentence attached — which is what the matrix exists to prevent, so
#: exempting a row from the matrix must not exempt it from that.
#: `backed_by` names the check that carries the endpoint instead, or None
#: when nothing does; `None` is the honest admission of a coverage gap and
#: is reported as one.
NO_SURFACE_BY_DESIGN = {
    # handoff 2.1: the relay card's indicator is its `closed` state, which
    # arrives as {"kind":"gate"}, so the level tap is not its driver.
    "relay:ctl": dict(
        why="driven by {'kind':'gate'}, not by the level tap",
        backed_by="check_relay_gate",
        # Stale if the tap ever starts carrying the surface itself — which
        # would show up as the level handler being expected to act on it.
        stale_when=None),
    # `arp:pwr` HAS a surface (the Arp card's stripe); the card only exists
    # when state.arp is non-null, and that needs a rack. So this is a real
    # coverage GAP, honestly bounded, not a design decision like relay:ctl.
    "arp:pwr": dict(
        why="no Arp card on a rackless board — emission covered, surface not",
        backed_by=None,
        # The premise, tested: no state in the recording carries an arp. The
        # day a recording does, the card renders, the surface is reachable,
        # and this exemption has to become a matrix row.
        stale_when=lambda t: any(m.get("arp") for m in t.recv("state"))),
}


# =============================================================================
# Emission plane — re-derive the transcript and diff it
# =============================================================================

#: One row per committed transcript.
#:
#: `needs_pulse` says the fixture was recorded against the reactive-taps
#: engine and cannot be re-derived on an older tree. Do NOT try to make such
#: a fixture pass everywhere by filtering the difference away: the first
#: attempt here dropped pulse and transport events from both sides, and the
#: comparison still failed — because reactive-taps also added a STEADY tap
#: (`relay:ctl`, which `main` applies with no emit at all, gate.py:406-408).
#: A partial-contract filter is a fiction about a contract nobody shipped;
#: an explicit "this fixture needs that engine" is the truth.
#:
#: `levels` is the floor: `>=n`, or `0` to assert the recording carries NO
#: level taps at all, which is what item 37 section 3.3's bug looks like from
#: the emission side.
FIXTURE_SCENARIOS = [
    # 15 on `main` (the 5 steady endpoints, minus relay:ctl, x 3 edges);
    # 34 once reactive-taps lands, which adds relay:ctl and the 6 pulse
    # endpoints. The gap between those two numbers IS the contract shift.
    dict(fixture="transcript_indicators.jsonl", scenario="indicators.json",
         needs_pulse=True, levels=15, post_contract_levels=34),
    dict(fixture="transcript_transport_levels.jsonl",
         scenario="transport_levels.json", needs_pulse=False, levels=6),
    # This one is mostly a RENDER fixture — its level traffic is thin by
    # design (transport:run via a logic chain, transport:click; relay:ctl
    # and deck:rec only exist post-contract). The floor is set from measured
    # counts, not from a guess about how many "ought" to be there.
    dict(fixture="transcript_board_dense.jsonl", scenario="board_dense.json",
         needs_pulse=True, levels=2, post_contract_levels=4),
    dict(fixture="transcript_transport_tap.jsonl", scenario="transport_tap.json",
         needs_pulse=False, levels=0,
         pre_contract_note="transport:tap emits NOTHING on this tree — item 37 "
                           "section 3.3's bug, recorded",
         post_contract_levels=6),
]


def _owed_checks(spec: dict, derivable: bool, rerecord: bool) -> list[str]:
    """Exactly the checks this fixture must produce — the coverage LEDGER.

    Every `continue` in the loop below used to drop the remaining checks
    silently. A run that failed to record one scenario therefore reported
    13 checks where a healthy run reported 16: it asserted less while still
    looking like a run, and the shortfall was visible only to someone who
    happened to know what 16 meant. Two sessions independently hit that
    without being able to name it.

    A check that COULD NOT BE EVALUATED is a FAILURE, not an absence. That
    is the premise of this whole suite, and it has to hold for the suite
    itself — so the owed set is declared up front and anything unsettled at
    the end is reported red, by name.
    """
    fixture, scenario = spec["fixture"], spec["scenario"]
    if rerecord:
        return [] if not derivable else [f"re-record {scenario} against this tree"]
    owed = [f"re-record {scenario} against this tree",
            f"{fixture}: no unpaired note-ons in the recording"]
    if derivable:
        owed.append(f"{fixture}: the backend still emits what it emitted")
    want = (spec.get("post_contract_levels", spec["levels"])
            if R.engine_capabilities()["pulse"] else spec["levels"])
    owed.append(f"{fixture}: the recording carries >= {want} level taps" if want
                else f"{fixture}: the recording carries ZERO level taps "
                     f"({spec.get('pre_contract_note', '')})")
    return owed


def emission_plane(rerecord: bool = False) -> None:
    caps = R.engine_capabilities()
    if not caps["engine"]:
        note("emission plane SKIPPED — no synthbase importable "
             "(the GUI CI job installs playwright only)")
        return
    have_pulse = caps["pulse"]
    print(f"\n--- emission plane (this tree's engine: "
          f"{'reactive-taps contract' if have_pulse else 'PRE-CONTRACT (main)'}) ---")

    for spec in FIXTURE_SCENARIOS:
        fixture, scenario = spec["fixture"], spec["scenario"]
        fpath = R.FIXTURES / fixture
        spath = R.SCENARIOS / scenario
        if not spath.exists():
            check(f"{scenario} exists", False, str(spath))
            continue
        derivable = have_pulse or not spec["needs_pulse"]
        if not derivable and rerecord:
            note(f"{fixture}: NOT re-recorded — it needs the reactive-taps "
                 "engine and this tree does not have it")
            continue
        # NOT a fixed /tmp path. Several agent sessions share this Mac and
        # run this suite from different worktrees; a constant filename means
        # two processes truncate and interleave into the SAME file, and the
        # corrupted result reads as a backend regression. Measured: a
        # concurrent session's run produced a 46-entry "recording" of a
        # 22-entry scenario, with an extra ping, and the diff blamed the
        # engine. Per-process, and cleaned up with the process.
        out = fpath if rerecord else _scratch() / f"rerecord_{fixture}"
        try:
            fresh = R.record(spath, out)
        except Exception as exc:  # noqa: BLE001
            check(f"re-record {scenario} against this tree", False, str(exc)[:400])
            continue
        check(f"re-record {scenario} against this tree", True)
        if rerecord:
            note(f"fixture REWRITTEN: {fpath}")
            continue
        if not fpath.exists():
            check(f"{fixture} is committed", False)
            continue

        got = R.skeleton(fresh)
        if derivable:
            d = R.diff_skeletons(R.skeleton(read_transcript(fpath)), got)
            check(f"{fixture}: the backend still emits what it emitted",
                  d["same"],
                  f"{d['why']} at {d['at']} want={d['want']} got={d['got']} "
                  f"n={d['n_want']}/{d['n_got']} missing={d['missing'][:4]}")
            # Say out loud how much cross-lane interleaving was absorbed. A
            # relaxation nobody can see is one nobody re-examines, and the
            # lane map is exactly the kind of thing that gets widened once
            # to fix a flake and never narrowed again.
            if d["tolerated"]:
                note(f"{fixture}: {d['tolerated']} segment(s) differed only by "
                     f"interleaving across lanes "
                     f"({'/'.join(sorted(R.CONCURRENT_EMITTERS))} vs "
                     f"{R.SETTLE_LANE}) — tolerated, contents and per-lane "
                     f"order identical")
        else:
            # The fixture is the TARGET contract, replayed by the DOM plane.
            # It cannot be re-derived here, and pretending otherwise would
            # report a red nobody on this branch can act on.
            note(f"{fixture}: PRE-CONTRACT — recorded against "
                 f"feat/p3-reactive-taps, not re-derivable on this tree. "
                 f"The diff auto-arms the moment that branch merges.")

        # The floor. A skeleton with no level events compares 'same' against
        # an equally empty recording, so 'same' alone proves nothing.
        want_levels = (spec.get("post_contract_levels", spec["levels"])
                       if have_pulse else spec["levels"])
        n = sum(1 for k in got if k[0] == "level")
        if want_levels:
            check(f"{fixture}: the recording carries >= {want_levels} level taps",
                  n >= want_levels, f"{n} level events")
        else:
            check(f"{fixture}: the recording carries ZERO level taps "
                  f"({spec.get('pre_contract_note', '')})",
                  n == 0, f"{n} level events — the endpoint now taps; raise "
                          f"'levels' for {fixture} in FIXTURE_SCENARIOS")

        # CLAUDE.md's stuck-note invariant, over real traffic.
        open_ = R.unpaired_from(fresh)
        check(f"{fixture}: no unpaired note-ons in the recording",
              not open_, str(open_[:3]))

    # Settle the ledger. Nothing below this line can shorten a run silently:
    # an owed check that never ran is reported red, with its own name, so a
    # 13-check run cannot masquerade as a 16-check one.
    ran = set(RAN)
    for spec in FIXTURE_SCENARIOS:
        derivable = have_pulse or not spec["needs_pulse"]
        for name in _owed_checks(spec, derivable, rerecord):
            if name not in ran:
                check(name, False,
                      "NOT EVALUATED — the block exited before reaching it "
                      "(a recording that failed, or an early return). This is "
                      "a coverage hole, not a passing check.")


# =============================================================================
# DOM plane — replay the recording into the real page
# =============================================================================

def dom_plane() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        note(f"DOM plane SKIPPED — no playwright ({exc})")
        return
    fpath = R.FIXTURES / "transcript_indicators.jsonl"
    if not fpath.exists():
        check("transcript_indicators.jsonl is committed", False, str(fpath))
        return
    t = read_transcript(fpath)
    print(f"\n--- DOM plane ({len(t)} records, "
          f"recorded {t.header.get('started')} on the "
          f"{'silent' if t.header.get('silent') else 'live'} rig) ---")

    with sync_playwright() as pw:
        # One pass per DRIVER, each on its own page. A driver's pass withholds
        # the other drivers' events so a surface's movement is attributable;
        # every withheld event is probed in its own pass, so nothing is
        # excused. See DRIVERS for why the alternative was rejected.
        by_driver = {}
        for driver in sorted({d for r in MATRIX
                              for d in r.get("drivers", DRIVERS_DEFAULT)}):
            browser, rp = R.open_replay(pw)
            by_driver[driver] = _observe(rp, t, driver)
            check(f"no page errors replaying the {driver!r} driver", not rp.errors,
                  "; ".join(rp.errors[:3]))
            browser.close()
        _report_matrix(by_driver, t)

        # The relay pass is the FULL-FIDELITY one — nothing withheld, the
        # recording exactly as recorded. It is the only pass that proves the
        # real interleaving renders, so the unqualified page-error check
        # belongs to it rather than to a withholding pass.
        browser, rp = R.open_replay(pw)
        check_relay_gate(rp, t)
        check("no page errors replaying the recording UNWITHHELD",
              not rp.errors, "; ".join(rp.errors[:3]))
        browser.close()

        browser, rp = R.open_replay(pw)
        check_observable_bpm(rp, t)
        browser.close()

        dense = R.FIXTURES / "transcript_board_dense.jsonl"
        if dense.exists():
            browser, rp = R.open_replay(pw)
            check_dense_board(rp, read_transcript(dense))
            check("dense board: no page errors", not rp.errors,
                  "; ".join(rp.errors[:3]))
            browser.close()
        else:
            check("transcript_board_dense.jsonl is committed", False, str(dense))


def check_dense_board(rp: R.ReplayPage, t: Transcript) -> None:
    """A dense board, replayed from a real capture rather than hand-written.

    `check_real.py` does the same job for `real_state.json`, a 19-wire module
    board captured in July and deliberately kept old-format. This is its
    control-plane twin: 20 spawned entities and 27 ctl wires that nobody
    placed by hand, replayed as the SEQUENCE that built them rather than as
    one final snapshot — so the incremental rebuild path is exercised too,
    which a single-state fixture cannot reach.

    It does NOT discharge todo.md P2-5. That asks for a ~40-card MODULE
    board, and modules need a rack; this is recordable precisely because
    nothing in it does. The state at the `settled` mark is also extracted to
    `tests/fixtures/real_state_dense_control.json` for suites that want a
    snapshot rather than a stream — a SECOND fixture, next to
    `real_state.json`, never replacing it.
    """
    R.walk(rp, t, stop_mark="settled")
    rp.settle(600)
    st = (t.segment("settled").recv("state") or [{}])[0]
    want = set()
    for sec in ("buttons", "clocks", "logics", "relays", "keyshifts",
                "tonics", "literals", "lfos", "thresholds"):
        want |= {row["id"] for row in (st.get(sec) or []) if isinstance(row, dict)}
    want |= {"tplay" if w == "play" else "ttempo"
             for w in (st.get("transport_cards") or [])}
    check(f"dense board: all {len(want)} captured entities got a card",
          bool(want) and len(want) >= 20,
          f"only {len(want)} entities in the capture — scenario shrank?")
    got = set(rp.probe("[...nodes.keys()]"))
    check("dense board: every captured entity rendered",
          want <= got, f"missing {sorted(want - got)}")

    n_wires = rp.probe("wires.length")
    check(f"dense board: the router drew wires for {len(st.get('ctl_wires') or [])} "
          "captured ctl wires", n_wires >= 20, f"{n_wires} drawn")

    # Nothing overflows its card — the density check that matters.
    over = rp.probe("""(() => {
      const bad = [];
      for (const [gid, n] of nodes) {
        if (!n.el || !n.el.isConnected) continue;
        if (n.el.scrollWidth > n.el.clientWidth + 2 ||
            n.el.scrollHeight > n.el.clientHeight + 2)
          bad.push([gid, n.el.scrollWidth, n.el.clientWidth,
                    n.el.scrollHeight, n.el.clientHeight]);
      }
      return bad; })()""")
    check("dense board: no card overflows its box", not over, str(over[:3]))


def _driver_status(row, driver):
    """(status, owner) for ONE driver of one row — the override if it has
    one, else the row's own. A row can be live via one driver and pending
    via another; see DRIVERS_DEFAULT."""
    over = (row.get("drivers") or DRIVERS_DEFAULT).get(driver)
    if over:
        return over["status"], over.get("owner")
    return row["status"], row.get("owner")


def _rows_for(driver: str) -> list[dict]:
    return [r for r in MATRIX
            if driver in r.get("drivers", DRIVERS_DEFAULT)]


def _observe(rp: R.ReplayPage, t: Transcript, driver: str) -> list[R.Observation]:
    """Walk the transcript probing ONE driver, with the others withheld.

    The probe pair straddles the injection — before and after, with nothing
    else fed in between. That window is the only place an event's effect is
    attributable to that event: this scenario pokes a full `state` broadcast
    at the end of each phase, and a probe taken after one reads a board that
    `onState` has just re-synced from scratch.

    A straddling window is necessary but not sufficient once a surface has
    two drivers 0.1 ms apart, which is why `driver` exists — see DRIVERS.
    Every event withheld here is probed in the OTHER pass, so no handler
    escapes assertion; that reciprocity is the whole licence for withholding.
    """
    rows = _rows_for(driver)
    by_ep: dict[str, list[dict]] = {}
    for row in rows:
        by_ep.setdefault(row["ep"], []).append(row)
    obs: list[R.Observation] = []
    pending_pre: dict = {}

    probed = driver
    # Withhold the OTHER drivers of these same surfaces, and nothing else.
    # `owned` is the level-tap side: only the endpoints this driver also
    # drives are withheld, so every unrelated tap stays in the stream and the
    # board is still built by real traffic rather than a thinned fiction.
    colliding = {"transport"} if driver == "level" else set()
    owned = {r["ep"] for r in rows} if driver == "transport" else set()

    def skip(ev):
        if ev.get("kind") in colliding:
            return True
        return ev.get("kind") == "level" and str(ev.get("ep")) in owned

    def on_event(page, ev, i, mark, when):
        if ev.get("kind") != probed:
            return
        if probed == "level":
            hit = by_ep.get(str(ev.get("ep")))
            if not hit:
                return
            want = {r["surface"]: bool(ev.get("on")) for r in hit}
        else:
            # One transport event carries every field; each row reads its own.
            hit = [r for r in rows if TRANSPORT_FIELD.get(r["ep"]) in ev]
            if not hit:
                return
            want = {r["surface"]: bool(ev.get(TRANSPORT_FIELD[r["ep"]]))
                    for r in hit}
        if when == "before":
            pending_pre.clear()
            pending_pre.update(page.probes({r["surface"]: r["js"] for r in hit}))
            return
        page.settle(80)
        post = page.probes({r["surface"]: r["js"] for r in hit})
        for r in hit:
            obs.append(R.Observation(
                ep=r["ep"], surface=r["surface"], expect=want[r["surface"]],
                pre=pending_pre.get(r["surface"]), post=post.get(r["surface"]),
                mode=r["mode"], pulse=bool(ev.get("pulse")), index=i, mark=mark))

    w = R.walk(rp, t, on_event=on_event, skip_event=skip)
    print(f"      driver {driver!r}: fed {w.fed} messages "
          f"({w.withheld} withheld) across {len(w.marks)} marks "
          f"in {w.seconds}s; {len(obs)} surface readings")
    return obs


def _report_matrix(by_driver: dict, t: Transcript) -> None:
    """One check per matrix row, plus the coverage floor.

    `seen_eps` comes from the TRANSCRIPT, not from `obs`. Reading it off the
    observations was the first version and it was worthless: observations
    only exist for endpoints that already have a matrix row, so the coverage
    check could only ever find endpoints it was already covering. A
    completeness check derived from the thing it is checking is not a check.
    """
    seen_eps = {str(e.get("ep")) for e in t.events("level")}
    for row in MATRIX:
        for driver in row.get("drivers", DRIVERS_DEFAULT):
            obs = by_driver.get(driver, [])
            mine = [o for o in obs
                    if o.ep == row["ep"] and o.surface == row["surface"]]
            # Name the driver only where there is more than one, so the 30-odd
            # single-driver rows read exactly as they did before.
            multi = len(row.get("drivers", DRIVERS_DEFAULT)) > 1
            tag = (f"{row['ep']} -> {row['surface']}"
                   + (f" [via {driver}]" if multi else ""))
            if not mine:
                check(f"{tag}: the transcript drives it", False,
                      f"no {driver} event for this endpoint in the recording")
                continue
            if all(o.missing_surface for o in mine):
                check(f"{tag}: the surface exists on the replayed board", False,
                      "probe returned null every time — card absent or "
                      "selector stale")
                continue

            status, owner = _driver_status(row, driver)
            if row["mode"] == "level":
                _report_steady(row, mine, tag, status, owner)
            else:
                _report_pulse(row, mine, tag, status, owner)

    # An endpoint the engine emits with no row and no by-design exemption is
    # an uncovered indicator — exactly what the matrix exists to prevent.
    covered = {r["ep"] for r in MATRIX} | set(NO_SURFACE_BY_DESIGN)
    gaps = sorted(seen_eps - covered)
    check("every level endpoint in the recording has a matrix row "
          "or a by-design exemption", not gaps, f"NO ROW: {gaps}")
    for ep, ex in sorted(NO_SURFACE_BY_DESIGN.items()):
        if ep not in seen_eps:
            continue
        note(f"{ep}: no level-driven surface row — {ex['why']}"
             + (f" (covered by {ex['backed_by']})" if ex["backed_by"]
                else " — UNCOVERED, bounded on purpose"))
        if ex["stale_when"] is not None:
            check(f"{ep}: its exemption's premise still holds",
                  not ex["stale_when"](t),
                  f"the premise stopped holding ({ex['why']}) — this endpoint "
                  f"now has a reachable surface; give it a MATRIX row and "
                  f"delete its NO_SURFACE_BY_DESIGN entry")


def _report_steady(row, mine, tag, status, owner) -> None:
    """Steady endpoints: assert BOTH edges, and only on evidence.

    The falling edge is the one that bites. On the rising edge the surface is
    usually already lit from the preceding full-state render, so an assertion
    there false-passes on a surface that never moves — item 11 hit exactly
    this on `transport:click`, whose fail-first signature was
    `{'led': False, 'bar': True}` on the FALL.
    """
    for want, edge in ((True, "rising"), (False, "falling")):
        edge_obs = [o for o in mine if bool(o.expect) is want]
        good = [o for o in edge_obs if o.informative]
        if not good:
            check(f"{tag}: {edge} edge is proven, not coincidental", False,
                  f"{len(edge_obs)} readings, none informative — the surface "
                  f"already read {want} every time")
            continue
        ok = all(o.reacted for o in good)
        if status == "live":
            check(f"{tag}: follows the {edge} edge", ok,
                  f"pre={good[0].pre} post={good[0].post} want={want} "
                  f"@mark={good[0].mark}")
        else:
            check(f"{tag}: {edge} edge still PENDING ({owner})", not ok,
                  "XPASS — the GUI now handles this; change status to 'live' "
                  f"for {tag} in tests/check_replay.py's MATRIX")


def _report_pulse(row, mine, tag, status, owner) -> None:
    """Pulse endpoints: the pair, not the halves.

    A pulse has ZERO duration — both halves are emitted from the same settle
    pass and arrive in the same tick (verified in the recording: identical
    `t` to 4 decimal places). Routing them through a plain level setter sets
    and unsets inside one frame and renders NOTHING. So the assertion is not
    "did it light on the rising half" but "is it STILL lit after the falling
    half landed", which is the only form that separates a working stretcher
    from an implemented-looking no-op.
    """
    rising = [o for o in mine if bool(o.expect) is True]
    falling = [o for o in mine if bool(o.expect) is False]
    if not rising or not falling:
        check(f"{tag}: the recording carries a full pulse PAIR", False,
              f"{len(rising)} on / {len(falling)} off")
        return
    check(f"{tag}: the pair is zero-duration in the recording",
          all(o.pulse for o in mine), "events are not tagged pulse:true")
    # "still lit after the pair" = the surface has not returned to what it
    # read before the rising half.
    lit_after_pair = any(f.post is not None and f.post != r.pre
                         for r, f in zip(rising, falling))
    if status == "live":
        check(f"{tag}: flashes and SURVIVES the zero-duration pair",
              lit_after_pair,
              f"pre={rising[0].pre!r} after-pair={falling[0].post!r}")
    else:
        check(f"{tag}: still PENDING ({owner})", not lit_after_pair,
              "XPASS — the GUI now flashes this; change status to 'live' "
              f"for {tag} in tests/check_replay.py's MATRIX")


# =============================================================================
# Observability — the check a state assertion structurally cannot make
# =============================================================================

def check_observable_bpm(rp: R.ReplayPage, t: Transcript) -> None:
    """Tap the tempo: does ANY message move the client's view of bpm?

    Item 37 section 3.3. `_transport_tap` changes an audible global and
    `set_transport` broadcasts nothing, so on `main` the rig speeds up while
    every number on screen lies until an unrelated structural broadcast
    happens to land. `test_transport.py:169-193` monkeypatches
    `_transport_tap` and counts calls — a call count cannot see this, because
    the defect is an ABSENCE in the message stream.

    Read purely off the rendered page, in TWO windows, because the defect is
    a lag rather than a permanent lie:

    * window A replays the tap segment with `state` broadcasts WITHHELD. That
      is not rigging the test — it is the definition of the bug. A full state
      broadcast re-renders the entire board from scratch and would resync bpm
      no matter how broken the event path is, so the only window in which the
      defect exists at all is between broadcasts. That window is real: pokes
      are a driver idiom, and in live use nothing broadcasts between taps.
    * window B then feeds the withheld broadcasts and asserts the number DOES
      snap. Without it, window A alone is consistent with "#bpm-v never
      updates, and the probe is broken" — B is what makes A mean what it says.
    """
    marks = t.marks()
    seg = "pulse-tap" if "pulse-tap" in marks else "taps"
    if seg not in marks:
        check("bpm observability: the transcript has a tap segment", False,
              str(marks))
        return
    after_marks = marks[marks.index(seg) + 1:]
    stop = after_marks[0] if after_marks else None

    R.walk(rp, t, stop_mark=seg)
    rp.settle(120)
    parse = lambda s: float(str(s).split()[0]) if s else None   # noqa: E731
    before = parse(rp.probe("document.getElementById('bpm-v').textContent"))

    # -- window A: the segment, every `state` withheld ------------------------
    R.walk(rp, t, start_mark=seg, stop_mark=stop,
           skip_types=R.SKIP_TYPES + ("state",))
    rp.settle(150)
    no_state = parse(rp.probe("document.getElementById('bpm-v').textContent"))

    # What the RIG's bpm actually became — from the recording, not the page.
    body = t.segment(seg, stop)
    rig_bpm = None
    for e in body.events("transport"):
        rig_bpm = e.get("bpm")
    if rig_bpm is None:
        for m in body.recv("state")[::-1]:
            if (m.get("transport") or {}).get("bpm") is not None:
                rig_bpm = m["transport"]["bpm"]
                break
    check("bpm observability: the rig's tempo really did change over the taps",
          rig_bpm is not None and abs(rig_bpm - (before or 0)) > 1.0,
          f"rig={rig_bpm} shown-before={before}")

    moved = no_state is not None and abs(no_state - (before or 0)) > 1.0
    check("bpm observability: a tapped tempo moves the page with NO state "
          "broadcast",
          moved,
          f"the shown bpm did not follow the taps ({before} -> {no_state}); "
          "blocks.html's {'kind':'transport'} handler is the surface that "
          "carries this")

    # -- window B: release the broadcasts -------------------------------------
    for r in body.records:
        if r.get("rec") == "recv" and r["msg"].get("type") == "state":
            rp.feed(r["msg"])
    rp.settle(150)
    with_state = parse(rp.probe("document.getElementById('bpm-v').textContent"))
    check("bpm observability: a full state broadcast DOES snap the number "
          "(so window A is a lag, not a dead probe)",
          with_state is not None and rig_bpm is not None
          and abs(with_state - rig_bpm) <= 1.0,
          f"before={before} without-state={no_state} with-state={with_state} "
          f"rig={rig_bpm}")


def check_relay_gate(rp: R.ReplayPage, t: Transcript) -> None:
    """`relay:ctl` has no level-driven surface — so prove the gate drives it.

    The contract exempts this endpoint (handoff 2.1). An exemption with
    nothing behind it is just an uncovered indicator with a note attached,
    so assert the thing the exemption CLAIMS: the relay's power button
    follows the paired `{"kind":"gate"}`.
    """
    ctl = [e for e in t.events("level") if str(e.get("ep")).endswith(":ctl")]
    if not ctl:
        note("relay:ctl not present in this recording — nothing to cross-check")
        return
    rid = str(ctl[0]["ep"]).split(":")[0]
    js = (f"(() => {{ const n = nodes.get({json.dumps(rid)}); if (!n) return null;"
          " const b = n.el.querySelector('.relaybtn');"
          " return b ? b.classList.contains('on') : null; })()")
    seen = {}

    def on_event(page, ev, i, mark, when):
        if when != "after" or ev.get("kind") != "gate" or ev.get("id") != rid:
            return
        # Longer than blocks.html's PULSE_MS (140 ms). The gate LED shows the
        # STRETCHED level, so a read taken 45 ms after a falling edge still
        # sees the previous hi being held — the first version of this check
        # passed and failed run to run for exactly that reason.
        page.settle(220)
        seen[bool(ev.get("on"))] = page.probe(js)

    R.walk(rp, t, on_event=on_event)
    check(f"{rid}:ctl exemption holds — the relay button follows its "
          "{'kind':'gate'} event",
          seen.get(True) is True and seen.get(False) is False, str(seen))


# =============================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--emission", action="store_true", help="emission plane only")
    ap.add_argument("--dom", action="store_true", help="DOM plane only")
    ap.add_argument("--rerecord", action="store_true",
                    help="overwrite the committed transcripts from this tree")
    args = ap.parse_args(argv)
    both = not (args.emission or args.dom)

    if args.emission or args.rerecord or both:
        emission_plane(rerecord=args.rerecord)
    if (args.dom or both) and not args.rerecord:
        dom_plane()

    pend = sum(1 for n in RAN if "PENDING" in n)
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(RAN)} checks, "
          f"{len(FAILURES)} failures, {pend} pending (expected-not-implemented)")
    if pend:
        print("      PENDING rows assert the GUI half is STILL missing. When it "
              "lands they turn red on purpose — flip the row to 'live'.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
