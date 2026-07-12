"""Loop deck logic test — no audio server needed.

Exercises the v2 deck: pre/post positioning, the _self_fire guard (no
phantom overdub), overdub-off purity, wrap-around events, and phase().
Run: python tests/test_looper.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synthbase.looper import Looper  # noqa: E402

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
    """Stands in for arp/voice; feeds notes back into the looper taps
    the way the real app does (this is what caused phantom overdub)."""

    def __init__(self):
        self.ons, self.offs = [], []
        self.looper = None
        self.tap = None  # set to looper.observe or observe_input

    def note_on(self, note, velocity=100):
        self.ons.append(note)
        if self.tap:
            self.tap(note, True)

    def note_off(self, note):
        self.offs.append(note)
        if self.tap:
            self.tap(note, False)


class FakeApp:
    def __init__(self):
        self.transport = FakeTransport()
        self.arp = FakeSink()
        self.voice = None
        self.events = []
        self._emit_midi_event = self.events.append


def make(position):
    app = FakeApp()
    lp = Looper(app)
    lp.configure(position=position, bars=1)
    # post-mode _deck_voice falls back to app.arp when there's no engine —
    # same code path, still exercises _self_fire through the sink
    app.arp.tap = lp.observe if position == "post" else lp.observe_input
    return app, lp


def record_pass(app, lp, notes=(60, 64)):
    lp.configure(action="record")
    deadline = time.monotonic() + 3
    while lp.state != "recording" and time.monotonic() < deadline:
        time.sleep(0.005)
    check("reached recording state", lp.state == "recording")
    for n in notes:  # play through the tap the app would use
        (lp.observe if lp.position == "post" else lp.observe_input)(n, True)
        time.sleep(0.05)
        (lp.observe if lp.position == "post" else lp.observe_input)(n, False)
    while lp.state == "recording" and time.monotonic() < deadline:
        time.sleep(0.01)


def main():
    # --- post mode: records, replays, never re-records itself ---------------
    app, lp = make("post")
    check("position accepted", lp.settings()["position"] == "post")
    record_pass(app, lp)
    n0 = len(lp._events)
    check("events recorded", n0 == 4)
    check("state playing (overdub off)", lp.state == "playing")
    # let it replay ~2 loops; sink feeds back into observe()
    time.sleep(app.transport.beat_duration * 9)
    check("no phantom overdub (event count stable)", len(lp._events) == n0)
    check("replay reached sink", len(app.arp.ons) >= 2)
    check("phase() live and in range",
          lp.phase() is not None and 0 <= lp.phase() < lp._loop_beats)
    check("settings exposes notes/loop_beats",
          lp.settings()["loop_beats"] == 4.0 and len(lp.settings()["notes"]) == n0)
    lp.configure(action="stop")
    check("phase() none when stopped", lp.phase() is None)
    check("position editable when stopped", (lp.configure(position="pre"),
                                             lp.position)[1] == "pre")
    lp.shutdown()

    # --- pre mode: only the input tap records --------------------------------
    app, lp = make("pre")
    record_pass(app, lp)
    n0 = len(lp._events)
    check("pre-mode records via observe_input", n0 == 4)
    lp.observe(72, True)   # post tap must be ignored in pre mode
    lp.observe(72, False)
    check("post tap ignored in pre mode", len(lp._events) == n0)
    time.sleep(app.transport.beat_duration * 9)
    check("pre-mode replay is not re-recorded", len(lp._events) == n0)
    # position locked while playing
    lp.configure(position="post")
    check("position locked while playing", lp.position == "pre")
    lp.shutdown()

    print(f"\n{'FAIL — ' + str(len(FAILS)) if FAILS else 'PASS — all'} checks"
          f" ({len(FAILS)} failures)" if FAILS else "\nPASS — all checks")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
