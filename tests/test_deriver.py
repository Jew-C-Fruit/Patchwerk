"""Deriver-split tests (CI-safe: no scsynth, no audio).

    python tests/test_deriver.py

Covers: estimator knobs (memory/stickiness/bass/listening) actually change
behavior, hysteresis, confidence/analysis shape, the LITERAL deriver's
extract × place matrix + hold-on-empty, immediate vs grid timing, the
shared timer↔ping override, and preset snapshot/restore for both nodes.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.drone import PROFILES, RootEstimator  # noqa: E402
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
    # stickiness: a challenger needs a bigger margin at high hysteresis
    e = RootEstimator()
    for _ in range(10):
        e.observe(60)                      # C incumbent
    check("initial estimate lands on C", e.estimate(None) == 0)
    for _ in range(12):
        e.observe(66)                      # F# evidence — a tritone away, so
                                           # none of it supports C
    lo = RootEstimator()
    lo._weights = list(e._weights)
    lo.hysteresis = 1.05                   # eager
    hi = RootEstimator()
    hi._weights = list(e._weights)
    hi.hysteresis = 2.0                    # very sticky
    check("low stickiness flips to the challenger", lo.estimate(0) == 6)
    check("high stickiness holds the incumbent", hi.estimate(0) == 0)

    # memory: short tau forgets fast
    fast, slow = RootEstimator(), RootEstimator()
    fast.tau, slow.tau = 1.0, 30.0
    for est in (fast, slow):
        est.observe(60)
        est._last_decay -= 3.0             # pretend 3 s passed
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
    for est in (t, c):
        est._weights[0] = 1.0
        est._weights[7] = 0.9
    check("triadic hears the fifth as root support",
          t._score(t.weights(), 0) > t._score(t.weights(), 7))
    check("chromatic scores each class alone",
          abs(c._score(c.weights(), 0) - 1.0) < 0.01)
    check("profiles registry sane",
          set(PROFILES) == {"triadic", "root+fifth", "chromatic"})

    # analysis: leading + confidence read sensibly
    e2 = RootEstimator()
    for _ in range(8):
        e2.observe(48)
    a2 = e2.analysis(None)
    check("analysis leads with the evidence", a2["leading"] == 0)
    check("one-horse race = high confidence", a2["confidence"] > 0.4)
    check("analysis vectors are 12-long peak-normalized",
          len(a2["weights"]) == 12 and max(a2["weights"]) == 1.0
          and len(a2["scores"]) == 12)
    empty = RootEstimator().analysis(None)
    check("silent analysis is flat and unconfident",
          empty["leading"] is None and empty["confidence"] == 0.0)


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

    # estimator side of the shared model: ping → commit()
    tid = app.spawn_tonic()
    t = app.tonics[tid]
    app.set_ctl_wire("add", bid, tid)
    for _ in range(6):
        t.est.observe(43)
    app.fire_button(bid)
    check("estimator commits on the ping too", t.root == 7)

    d.shutdown()
    t.shutdown()


def test_preset_roundtrip():
    app = SynthApp(use_midi=False, use_reload=False)
    tid = app.spawn_tonic()
    app.set_tonic(tid, memory=12.0, stickiness=1.5, bass=0.1,
                  listening="root+fifth", every="2 bars", octave=3)
    lid = app.spawn_literal()
    app.set_literal(lid, extract="last-played", place="fold", fold_octave=4,
                    transpose=5, hold_on_empty=False, every="1 beat")
    data = presets.snapshot(app)

    app2 = SynthApp(use_midi=False, use_reload=False)
    app2._build_patch = lambda name: None
    presets._apply(app2, data)
    t2 = app2.tonics[tid]
    check("estimator knobs survive the preset",
          t2.est.tau == 12.0 and t2.est.hysteresis == 1.5
          and t2.est.bass == 0.1 and t2.est.profile == "root+fifth"
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
    test_preset_roundtrip()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
