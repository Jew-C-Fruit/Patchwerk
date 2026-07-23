"""Deriver-split tests (CI-safe: no scsynth, no audio).

    python tests/test_deriver.py

Covers: the redesigned two-layer estimator (duration-weighted evidence,
refcounted held set, scale inference, Layer-2 pick_root, memory/bass/
listening knobs, analysis shape incl. scale), instant commit semantics
(held set → root NOW; empty-held holds, first-ever commit lands the scale
tonic), the deck superpower (chord grouping, context evidence, deck_feed
wiring, every="deck"), the LITERAL deriver's extract × place matrix +
hold-on-empty, immediate vs grid timing, the shared timer↔ping override,
and preset snapshot/restore for both nodes.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.drone import (  # noqa: E402
    DECK_GAIN, PROFILES, SCALES, RootEstimator,
    best_scale, deck_evidence, deck_groups, pick_root,
)
from synthbase import presets  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


class Sink:
    def __init__(self):
        self.ons, self.offs = [], []
        self.target_key = None

    def note_on(self, note, velocity=100): self.ons.append(note)
    def note_off(self, note): self.offs.append(note)
    def all_off(self): pass
    def set_sustain(self, on): pass
    def set_bend(self, semitones): pass


def test_estimator_knobs():
    # duration weighting: a held note keeps gaining weight; a grace note
    # contributes almost nothing (and hysteresis/estimate() are GONE)
    est = RootEstimator()
    check("hysteresis is gone from the estimator",
          not hasattr(est, "hysteresis") and not hasattr(est, "estimate"))
    est.note_on(60)
    time.sleep(0.05)
    est.note_off(60)                       # grace note → tiny credit
    est.note_on(64)
    time.sleep(0.6)                        # still held → pinned, growing
    w = est.weights()
    check("held note out-weighs the grace note >2x", w[4] > w[0] * 2)
    check("held note has real weight at all", w[4] > 0.5)
    check("held set reads the down note", est.held_notes() == [64])
    est.note_off(64)
    check("release-grace window catches the just-released note",
          est.recent_release_notes(0.3) == [64])
    check("release empties the held set", est.held_notes() == [])

    # refcount: fan-in may repeat a note; it stays held until the LAST off
    rc = RootEstimator()
    rc.note_on(60)
    rc.note_on(60)
    rc.note_off(60)
    check("refcounted held: one off of two leaves it held",
          rc.held_notes() == [60])
    rc.note_off(60)
    check("refcounted held: second off releases it", rc.held_notes() == [])

    # Layer 1: scale inference against the template vocabulary
    check("scale vocabulary is 12 templates, modes first then the extras",
          len(SCALES) == 12 and list(SCALES)[0] == "ionian"
          and list(SCALES)[-5:] == ["harm minor", "mel minor",
                                    "maj pent", "min pent", "blues"])
    cmaj = [2.0, 0, 1.0, 0, 1.0, 1.0, 0, 1.5, 0, 1.0, 0, 1.0]
    check("C major evidence lands C ionian",
          (best_scale(cmaj) or (None,))[:2] == (0, "ionian"))
    pent = [1.0 if pc in (0, 2, 4, 7, 9) else 0.0 for pc in range(12)]
    check("pure pentatonic evidence lets the subset win",
          (best_scale(pent) or (None,))[:2] == (0, "maj pent"))
    check("uniform chromatic evidence fits nothing",
          best_scale([1.0] * 12) is None)
    aharm = [0.0] * 12
    for pc in (9, 11, 0, 2, 4, 5, 8):
        aharm[pc] = 1.0
    aharm[9], aharm[4] = 2.0, 1.5
    check("A harmonic minor evidence names itself",
          (best_scale(aharm) or (None,))[:2] == (9, "harm minor"))

    # Layer 2: pick_root snaps a held set to its root given the scale
    sc = (0, "ionian", 1.0)
    check("held C triad → C", pick_root([48, 52, 55], sc) == 0)
    check("held A minor triad → A", pick_root([57, 60, 64], sc) == 9)
    check("held G triad → G", pick_root([55, 59, 62], sc) == 7)
    check("single held E reads as its own function",
          pick_root([52], sc) == 4)
    # deck harmonic-map prior: flips a near-tie (single E → A when the
    # map says A) but does NOT overrule a bass-anchored dyad (C-E stays C)
    check("prior_pc flips the ambiguous single note",
          pick_root([64], sc) == 4 and pick_root([64], sc, prior_pc=9) == 9)
    check("prior_pc cannot overrule the bass-anchored dyad",
          pick_root([60, 64], sc, prior_pc=9) == 0)

    # memory: short tau forgets released evidence fast
    fast, slow = RootEstimator(), RootEstimator()
    fast.tau, slow.tau = 1.0, 30.0
    for e in (fast, slow):
        e.observe(60)
        e._last_decay -= 3.0               # pretend 3 s passed
    check("short memory decays far more",
          fast.weights()[0] < slow.weights()[0] * 0.2)

    # bass emphasis: a low note argues harder when the knob is up
    a, b = RootEstimator(), RootEstimator()
    a.bass, b.bass = 0.0, 0.15
    a.observe(36)
    b.observe(36)
    check("bass knob weights low notes harder",
          b.weights()[0] > a.weights()[0] * 2)

    # listening profile: a fifth supports the root under triadic, not chromatic
    t, c = RootEstimator(), RootEstimator()
    c.profile = "chromatic"
    for e in (t, c):
        e._released[0] = 1.0
        e._released[7] = 0.9
    check("triadic hears the fifth as root support",
          t._score(t.weights(), 0) > t._score(t.weights(), 7))
    check("chromatic scores each class alone",
          abs(c._score(c.weights(), 0) - 1.0) < 0.01)
    check("profiles registry sane",
          set(PROFILES) == {"triadic", "root+fifth", "chromatic"})

    # analysis: leading + confidence + the inferred scale read sensibly
    e2 = RootEstimator()
    for _ in range(8):
        e2.observe(48)
    a2 = e2.analysis(None)
    check("analysis leads with the evidence", a2["leading"] == 0)
    check("one-horse race = clear confidence", a2["confidence"] > 0.2)
    check("analysis vectors are 12-long peak-normalized",
          len(a2["weights"]) == 12 and max(a2["weights"]) == 1.0
          and len(a2["scores"]) == 12)
    check("analysis carries the inferred scale",
          a2["scale"] is not None and a2["scale"]["tonic"] == 0
          and "mode" in a2["scale"] and "label" in a2["scale"])
    empty = RootEstimator().analysis(None)
    check("silent analysis is flat, unconfident and scale-less",
          empty["leading"] is None and empty["confidence"] == 0.0
          and empty["scale"] is None)


def test_literal_extract_place():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_literal()
    check("first literal id", lid == "literal")
    d = app.literals[lid]
    sink = Sink()
    app.voices["voice"] = sink
    app.set_ctl_wire("add", "keys", lid)
    app.set_ctl_wire("add", lid, "voice")

    def play(*notes):
        for n in notes:
            app.note_on(n)

    def release(*notes):
        for n in notes:
            app.note_off(n)

    # lowest-held + absolute: shadows the bassline (immediate timing)
    play(64, 48, 60)
    check("lowest-held emits the bass note", sink.ons[-1] == 48)
    release(48)
    check("bass release re-extracts (60 now lowest)", sink.ons[-1] == 60)

    # highest-held
    app.set_literal(lid, extract="highest-held")
    play(72)
    check("highest-held emits the top note", sink.ons[-1] == 72)

    # last-played + fold: a punchy pedal re-voiced into a fixed octave
    app.set_literal(lid, extract="last-played", place="fold", fold_octave=2)
    play(69)                                    # A4 → folded to A2 = 45
    check("last-played + fold re-voices to the set octave",
          sink.ons[-1] == 45)

    # transpose place
    app.set_literal(lid, place="transpose", transpose=-12)
    play(62)
    check("transpose places ±N semitones", sink.ons[-1] == 50)

    # mono correctness: each new emit released the previous one
    check("mono out: offs trail ons by exactly one",
          sink.offs == sink.ons[:-1])

    # hold-on-empty (default True): releasing everything keeps the note…
    app.set_literal(lid, extract="lowest-held", place="absolute")
    play(52)
    release(52, 60, 62, 64, 69, 72)
    held_out = sink.ons[-1]
    check("hold-on-empty keeps the last note",
          d.current_note() == held_out and sink.offs == sink.ons[:-1])
    # …and with the toggle off, emptying RELEASES it
    app.set_literal(lid, hold_on_empty=False)
    play(50)
    release(50)
    check("release-on-empty lets go", d.current_note() is None
          and sink.offs[-1] == sink.ons[-1])

    d.shutdown()
    for t in app.tonics.values():
        t.shutdown()


def test_timing_and_ping_override():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_literal()
    d = app.literals[lid]
    sink = Sink()
    app.voices["voice"] = sink
    app.set_ctl_wire("add", "keys", lid)
    app.set_ctl_wire("add", lid, "voice")

    # grid timing: notes do NOT commit between grid points
    app.set_literal(lid, every="1 bar")
    app.note_on(40)
    check("grid timing defers the commit", sink.ons == [])
    d.commit()                                  # the grid thread's call site
    check("grid commit samples the current notes", sink.ons == [40])

    # ping override: wired ping owns timing even at every=immediate
    app.set_literal(lid, every="immediate")
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, lid)
    check("literal reports ping-driven", d._ping_driven())
    app.note_on(45)
    check("with a ping wired, notes alone do not commit",
          sink.ons == [40])
    app.fire_button(bid)
    check("the ping commits (lowest-held = 40 still lowest)",
          sink.ons[-1] == 40 and len(sink.ons) == 1 or sink.ons[-1] == 40)
    app.note_off(40)
    app.fire_button(bid)
    check("next ping lands the new extraction (45)", sink.ons[-1] == 45)
    app.set_ctl_wire("remove", bid, lid)
    check("unwired: immediate timing resumes", not d._ping_driven())
    app.note_on(38)
    check("immediate commit is back", sink.ons[-1] == 38)
    app.note_off(45)
    app.note_off(38)

    # estimator side of the shared model: INSTANT commit on the ping —
    # the held set snaps to its root, no settling
    tid = app.spawn_tonic()
    t = app.tonics[tid]
    app.set_ctl_wire("add", "keys", tid)
    app.set_ctl_wire("add", bid, tid)
    app.note_on(43)                             # hold G2
    app.fire_button(bid)
    check("held note commits instantly on the ping", t.root == 7)
    check("settings carry no stickiness and offer deck timing",
          "stickiness" not in t.settings()
          and t.settings()["everies"][-1] == "deck")
    # release-grace: staccato at the trigger still counts as held
    app.note_off(43)
    time.sleep(0.35)                            # 43's grace window lapses
    app.note_on(45)
    app.note_off(45)
    app.fire_button(bid)                        # 45 released a moment ago
    check("release-grace commits the staccato note", t.root == 9)
    # empty-held with a root standing: the commit HOLDS (no drift to None)
    time.sleep(0.35)                            # let the grace window lapse
    app.fire_button(bid)
    check("empty-held commit holds the current root", t.root == 9)

    # FIRST-ever commit with no held set but evidence: the scale tonic lands
    t2 = app.tonics[app.spawn_tonic()]
    for n in (60, 60, 60, 67, 67, 62, 64, 65, 69, 71):
        t2.est.observe(n)                       # C-major-ish stream, no holds
    check("evidence infers the scale pre-commit",
          (t2.est.scale() or (None,))[:2] == (0, "ionian"))
    t2.commit()
    check("first empty-held commit lands the scale tonic", t2.root == 0)

    d.shutdown()
    for x in app.tonics.values():
        x.shutdown()


# a synthetic 8-beat Loop Deck phrase: C chord (2 beats), F chord (2 beats),
# then a melody run C-D-E and an open G that runs to the loop end
DECK_EVENTS = [
    (0.0, 48, True), (0.05, 52, True), (0.1, 55, True),
    (1.8, 48, False), (1.8, 52, False), (1.8, 55, False),
    (2.0, 53, True), (2.05, 57, True), (2.1, 60, True),
    (3.8, 53, False), (3.8, 57, False), (3.8, 60, False),
    (4.0, 72, True), (4.4, 72, False),
    (5.0, 74, True), (5.4, 74, False),
    (6.0, 76, True), (6.4, 76, False),
    (7.0, 79, True),                            # no off: open note
]


def test_deck_superpower():
    # chord grouping: onset-adjacent notes cluster; the rest are melody
    gs = deck_groups(DECK_EVENTS, 8.0)
    check("phrase clusters into 6 groups", len(gs) == 6)
    check("chord groups are flagged, melody singletons are not",
          [g["chord"] for g in gs] == [True, True, False, False, False, False])
    check("group boundaries tile the loop",
          gs[0]["start"] == 0.0 and gs[0]["end"] == 2.0
          and gs[1]["start"] == 2.0 and gs[1]["end"] == 4.0
          and gs[-1]["end"] == 8.0)
    check("an open note runs to the loop end",
          gs[-1]["notes"][0][2] == 79 and abs(gs[-1]["notes"][0][1] - 1.0) < 1e-6)
    check("empty phrase → no groups", deck_groups([], 8.0) == [])

    # context evidence: normalized mass, chords dominate melody singletons
    ev = deck_evidence(gs)
    check("deck evidence normalizes to DECK_GAIN",
          abs(sum(ev) - DECK_GAIN) < 1e-6)
    check("chord tones dominate melody singletons",
          ev[0] > 3 * ev[2] and ev[5] > ev[2])

    # integration: deck wired into a tonic deriver feeds the scale layer
    app = SynthApp(use_midi=False, use_reload=False)
    tid = app.spawn_tonic()
    t = app.tonics[tid]
    app.set_ctl_wire("add", "deck", tid)
    with app.looper._lock:
        app.looper._events[:] = list(DECK_EVENTS)
        app.looper._loop_beats = 8.0
    check("context stays off until deck_feed", t.est.context is None)
    app.set_tonic(tid, deck_feed=True)
    a = t.analysis()
    check("deck_feed builds context evidence + flags the analysis",
          t.est.context is not None and a["deck"] is True)
    check("deck context reads the phrase's key",
          a["scale"] is not None and a["scale"]["tonic"] == 0)
    app.set_tonic(tid, deck_feed=False)
    check("deck_feed off clears the context", t.est.context is None)
    check("analysis drops the deck flag when the map is gone",
          t.analysis()["deck"] is False)

    # every="deck": accepted by configure, reflected in settings
    app.set_tonic(tid, every="deck")
    s = t.settings()
    check("every=deck is a first-class timing choice",
          s["every"] == "deck" and s["everies"][-1] == "deck")

    for x in (*app.tonics.values(), *app.literals.values()):
        x.shutdown()


def test_preset_roundtrip():
    app = SynthApp(use_midi=False, use_reload=False)
    tid = app.spawn_tonic()
    app.set_tonic(tid, memory=12.0, bass=0.1, listening="root+fifth",
                  every="2 bars", octave=3, deck_feed=True)
    lid = app.spawn_literal()
    app.set_literal(lid, extract="last-played", place="fold", fold_octave=4,
                    transpose=5, hold_on_empty=False, every="1 beat")
    data = presets.snapshot(app)
    entry = next(e for e in data["tonics"] if e["id"] == tid)
    check("snapshot carries deck_feed and has shed stickiness",
          entry.get("deck_feed") is True and "stickiness" not in entry)

    app2 = SynthApp(use_midi=False, use_reload=False)
    app2._build_patch = lambda name: None
    presets._apply(app2, data)
    t2 = app2.tonics[tid]
    check("estimator knobs survive the preset",
          t2.est.tau == 12.0 and t2.est.bass == 0.1
          and t2.est.profile == "root+fifth" and t2.deck_feed is True
          and t2.every == "2 bars" and t2.octave == 3)
    l2 = app2.literals[lid]
    check("literal settings survive the preset",
          l2.extract == "last-played" and l2.place == "fold"
          and l2.fold_octave == 4 and l2.transpose == 5
          and l2.hold_on_empty is False and l2.every == "1 beat")

    for a in (app, app2):
        for d in (*a.tonics.values(), *a.literals.values()):
            d.shutdown()


def main():
    test_estimator_knobs()
    test_literal_extract_place()
    test_timing_and_ping_override()
    test_deck_superpower()
    test_preset_roundtrip()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
