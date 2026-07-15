"""Loop deck logic test — no audio server needed.

Exercises the v3 deck: wire-defined record taps (record_raw / record_voiced),
the _self_fire guard (no phantom overdub), wiring-derived replay sink
(deck→voice / deck→arp / dead-end), wrap-around events, phase(), and the
private deck-voice note stack. `position` is gone — configure() must accept
and ignore it. Run: python tests/test_looper.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synthbase.looper import Looper, _FanSink  # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILS.append(name)


class FakeTransport:
    def __init__(self, bpm=600.0):  # fast: 0.1 s per beat
        self.bpm = bpm
        self.beats_per_bar = 4
        self.running = True
        self._t0 = time.monotonic()

    @property
    def beat_duration(self):
        return 60.0 / self.bpm

    def beats_now(self):
        return (time.monotonic() - self._t0) / self.beat_duration

    def next_grid(self, division_beats):
        b = self.beats_now()
        nb = (int(b // division_beats) + 1) * division_beats
        return nb, self._t0 + nb * self.beat_duration

    def time_of_beat(self, beat):
        return self._t0 + beat * self.beat_duration


class FakeSink:
    """Stands in for arp/voice; feeds notes back into the looper's record
    taps the way the real app does (this is what caused phantom overdub)."""

    def __init__(self):
        self.ons, self.offs = [], []
        self.tap = None  # set to looper.record_raw or record_voiced

    def note_on(self, note, velocity=100):
        self.ons.append(note)
        if self.tap:
            self.tap(note, True)

    def note_off(self, note):
        self.offs.append(note)
        if self.tap:
            self.tap(note, False)


class FakeApp:
    """Wire-aware host: the looper reads ctl_wires for its replay sink."""

    def __init__(self, wires):
        self.transport = FakeTransport()
        self.arp = FakeSink()
        self.voice = None
        self.ctl_wires = list(wires)
        self.events = []
        self._emit_midi_event = self.events.append


# convenience wire sets
VOICED = [{"from": "arp", "to": "deck"}, {"from": "deck", "to": "voice"}]
RAW = [{"from": "keys", "to": "deck"}, {"from": "deck", "to": "arp"}]


def make(wires, tap="voiced"):
    app = FakeApp(wires)
    lp = Looper(app)
    lp.configure(bars=1)
    # deck→voice with no engine/voice falls back to app.arp — same code
    # path, still exercises _self_fire through the sink
    app.arp.tap = lp.record_voiced if tap == "voiced" else lp.record_raw
    return app, lp


def record_pass(app, lp, tap, notes=(60, 64)):
    lp.configure(action="record")
    deadline = time.monotonic() + 3
    while lp.state != "recording" and time.monotonic() < deadline:
        time.sleep(0.005)
    check("reached recording state", lp.state == "recording")
    for n in notes:  # play through the tap the app's dispatcher would use
        tap(n, True)
        time.sleep(0.05)
        tap(n, False)
    while lp.state == "recording" and time.monotonic() < deadline:
        time.sleep(0.01)


def main():
    # --- voiced loop (old "post"): records arp output, replays, never
    # --- re-records itself --------------------------------------------------
    app, lp = make(VOICED)
    check("derived position reads post", lp.settings()["position"] == "post")
    record_pass(app, lp, lp.record_voiced)
    n0 = len(lp._events)
    check("events recorded", n0 == 4)
    check("state playing (overdub off)", lp.state == "playing")
    # let it replay ~2 loops; sink feeds back into record_voiced()
    time.sleep(app.transport.beat_duration * 9)
    check("no phantom overdub (event count stable)", len(lp._events) == n0)
    check("replay reached sink", len(app.arp.ons) >= 2)
    check("phase() live and in range",
          lp.phase() is not None and 0 <= lp.phase() < lp._loop_beats)
    check("settings exposes notes/loop_beats",
          lp.settings()["loop_beats"] == 4.0 and len(lp.settings()["notes"]) == n0)
    lp.configure(action="stop")
    check("phase() none when stopped", lp.phase() is None)
    # position is accepted and IGNORED (compat with old clients)
    lp.configure(position="pre")
    check("position param ignored", lp.settings()["position"] == "post"
          and not hasattr(lp, "position"))
    lp.shutdown()

    # --- loop-top boundary ----------------------------------------------------
    # a note recorded at exactly beat 0 must voice on every replay cycle
    app, lp = make(VOICED)
    lp._events = [(0.0, 60, True), (0.5, 60, False)]
    lp._loop_beats = 4.0
    lp._record_start_beat = 0.0
    lp.state = "playing"
    lp._ensure_thread()
    time.sleep(app.transport.beat_duration * 9)
    lp.configure(action="stop")
    check("beat-0 note voices every cycle", len(app.arp.ons) >= 2)
    lp.shutdown()

    # armed grace: a note struck just before the window opens lands at beat 0
    app, lp = make(VOICED)
    lp._loop_beats = 4.0
    lp.state = "armed"
    lp._record_start_beat = app.transport.beats_now() + 0.2  # top is 0.2 beats away
    lp.record_voiced(55, True)
    check("armed grace clamps early note to beat 0",
          lp._events and lp._events[0] == (0.0, 55, True))
    lp.record_voiced(55, False)  # offs before the top are dropped
    check("armed off ignored", len(lp._events) == 1)
    check("loop_note emitted live", any(
        e.get("kind") == "loop_note" for e in app.events))
    lp.state = "empty"
    lp.shutdown()

    # a note still held when the window closes gets an off at the loop end
    app, lp = make(VOICED)
    lp._loop_beats = 4.0
    lp._record_start_beat = app.transport.beats_now()
    lp.state = "recording"
    lp._record(61, True)
    lp._finish_recording()
    offs = [e for e in lp._events if e[1] == 61 and not e[2]]
    check("held note closed at window end",
          len(offs) == 1 and abs(offs[0][0] - 3.98) < 0.01)
    lp.configure(action="stop")
    lp.shutdown()

    # --- deck voice note stack --------------------------------------------------
    class FakeNode:
        def __init__(self): self.sets = []
        def set(self, **kw): self.sets.append(kw)
        def free(self): pass

    app, lp = make(VOICED)
    lp._deck_node = FakeNode()
    lp._deck_key = "x"
    lp.note_on(60)
    lp.note_on(64)          # takes over the mono deck voice
    lp.note_off(60)         # background release — must NOT kill 64
    voiced = [(e["note"], e["on"]) for e in app.events if e.get("deck")]
    check("deck stack: background off doesn't close the gate",
          not any(s.get("gate") == 0 for s in lp._deck_node.sets))
    check("deck stack emits sounding transitions only",
          voiced == [(60, True), (60, False), (64, True)])
    lp.note_off(64)
    check("deck gate closes when stack empties",
          lp._deck_node.sets[-1].get("gate") == 0)
    voiced = [(e["note"], e["on"]) for e in app.events if e.get("deck")]
    check("deck emits balanced on/off", voiced[-1] == (64, False))
    lp._deck_node = None
    lp.shutdown()

    # equal-beat events keep fire order (tuple sort used to put offs first)
    ev = [(1.0, 60, True), (1.0, 60, False), (0.5, 62, True), (0.5, 61, False)]
    srt = sorted(ev, key=lambda e: e[0])
    check("stable sort keeps fire order at equal beats",
          srt == [(0.5, 62, True), (0.5, 61, False), (1.0, 60, True), (1.0, 60, False)])

    # --- raw loop (old "pre"): keys→deck records, deck→arp replays ------------
    app, lp = make(RAW, tap="raw")
    check("derived position reads pre", lp.settings()["position"] == "pre")
    record_pass(app, lp, lp.record_raw)
    n0 = len(lp._events)
    check("raw tap records via record_raw", n0 == 4)
    time.sleep(app.transport.beat_duration * 9)
    check("deck→arp replay reaches the arp", len(app.arp.ons) >= 2)
    check("replay is not re-recorded", len(lp._events) == n0)
    lp.configure(action="stop")
    lp.shutdown()

    # --- wiring-derived sink resolution ----------------------------------------
    app, lp = make(VOICED)
    check("deck→voice resolves (falls back to arp w/o engine)",
          lp._sink() is app.arp)
    app.ctl_wires = list(RAW)
    check("deck→arp resolves to the arp", lp._sink() is app.arp)
    app.ctl_wires = [{"from": "keys", "to": "deck"}]  # record-only patching
    check("no deck outgoing wire → sink None (silent dead-end)",
          lp._sink() is None)
    check("derived position without record source reads pre",
          lp.settings()["position"] == "pre")
    app.ctl_wires = []
    check("derived position with deck unwired reads off",
          lp.settings()["position"] == "off")
    app.ctl_wires = RAW + [{"from": "deck", "to": "voice"}]
    s = lp._sink()
    check("multiple deck outputs fan out", isinstance(s, _FanSink)
          and len(s.sinks) == 2)
    s.note_on(70)   # a dead sink must not break the fan
    check("fan delivers to every target", app.arp.ons.count(70) == 2)
    # v5: deck→voice.2 drives that EXTRA mono voice directly (the private
    # deck node is only for the primary "voice")
    extra = FakeSink()
    app.voices = {"voice.2": extra}
    app.ctl_wires = [{"from": "deck", "to": "voice.2"}]
    check("deck→voice.2 resolves to the extra voice", lp._sink() is extra)
    lp._sink().note_on(71)
    check("replay reaches the extra voice", extra.ons == [71])
    lp.shutdown()

    # a dead-end replay must not crash the run thread or _release_all
    app, lp = make([{"from": "arp", "to": "deck"}])   # record yes, replay no
    lp._events = [(0.0, 60, True), (0.5, 60, False)]
    lp._loop_beats = 4.0
    lp._record_start_beat = 0.0
    lp.state = "playing"
    lp._ensure_thread()
    time.sleep(app.transport.beat_duration * 5)
    check("dead-end replay spins silently", lp.state == "playing"
          and not app.arp.ons)
    lp.configure(action="stop")
    lp.shutdown()

    print(f"\n{'FAIL — ' + str(len(FAILS)) if FAILS else 'PASS — all'} checks"
          f" ({len(FAILS)} failures)" if FAILS else "\nPASS — all checks")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
