"""Transport: the shared musical clock — tempo, meter, click track.

This is the foundation the sequencer will build on. It owns an absolute
beat timeline (beat 0 anchored at construction; tempo changes re-anchor so
the current beat position is preserved), a beat thread that fires
``on_beat(bar, beat_in_bar)`` once per beat, and helpers for quantizing
events onto rhythmic grids.

Everything rhythmic (arp now, sequencer next) asks the transport for grid
times instead of free-running — which is what keeps the downbeat on the 1
no matter what happens to the notes.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Out, SinOsc

# Rhythmic divisions in beats (beat = quarter note). "." = dotted, "T" = triplet.
DIVISIONS = {
    "1/1": 4.0,
    "1/2": 2.0,
    "1/4.": 1.5,
    "1/4": 1.0,
    "1/4T": 2 / 3,
    "1/8.": 0.75,
    "1/8": 0.5,
    "1/8T": 1 / 3,
    "1/16": 0.25,
    "1/16T": 1 / 6,
    "1/32": 0.125,
}


@synthdef()
def _click(freq=1500, amp=0.25, out=0):
    """Short metronome tick; frees itself when the envelope ends."""
    env = EnvGen.kr(envelope=Envelope.percussive(0.001, 0.04), done_action=2)
    sig = SinOsc.ar(frequency=freq) * env * amp
    Out.ar(bus=out, source=[sig, sig])


class Transport:
    def __init__(self, bpm: float = 100.0, beats_per_bar: int = 4) -> None:
        self.bpm = float(bpm)
        self.beats_per_bar = int(beats_per_bar)
        self.click_enabled = False
        self.on_beat: Callable[[int, int], None] | None = None  # (bar, beat_in_bar)

        self._epoch = time.monotonic()  # wall time of...
        self._epoch_beat = 0.0          # ...this beat position
        self._lock = threading.Lock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the timeline -----------------------------------------------------------

    @property
    def beat_duration(self) -> float:
        return 60.0 / self.bpm

    def beats_now(self) -> float:
        with self._lock:
            return self._epoch_beat + (time.monotonic() - self._epoch) / self.beat_duration

    def time_of_beat(self, beat: float) -> float:
        """Monotonic wall time of a beat position (valid across tempo changes)."""
        with self._lock:
            return self._epoch + (beat - self._epoch_beat) * self.beat_duration

    def set_bpm(self, bpm: float) -> None:
        bpm = min(300.0, max(20.0, float(bpm)))
        with self._lock:
            now = time.monotonic()
            # Re-anchor so the current beat position is continuous.
            self._epoch_beat += (now - self._epoch) / self.beat_duration
            self._epoch = now
            self.bpm = bpm

    def set_meter(self, beats_per_bar: int) -> None:
        self.beats_per_bar = min(12, max(1, int(beats_per_bar)))

    def next_grid(self, division_beats: float) -> tuple[float, float]:
        """(beat, wall_time) of the next grid point on the given division."""
        b = self.beats_now()
        k = math.floor(b / division_beats + 1e-9) + 1
        gb = k * division_beats
        return gb, self.time_of_beat(gb)

    def position(self) -> tuple[int, int]:
        """(bar, beat_in_bar), zero-based."""
        b = int(math.floor(self.beats_now()))
        return b // self.beats_per_bar, b % self.beats_per_bar

    # -- the beat thread (click + beat events) --------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def settings(self) -> dict:
        return {
            "bpm": self.bpm,
            "beats_per_bar": self.beats_per_bar,
            "click": self.click_enabled,
            "divisions": list(DIVISIONS),
        }

    def _sleep_until(self, t: float) -> bool:
        while not self._quit.is_set():
            dt = t - time.monotonic()
            if dt <= 0:
                return True
            time.sleep(min(dt, 0.05))
        return False

    def _run(self) -> None:
        nb = math.floor(self.beats_now()) + 1
        while not self._quit.is_set():
            if not self._sleep_until(self.time_of_beat(nb)):
                return
            callback = self.on_beat
            if callback is not None:
                try:
                    callback(int(nb) // self.beats_per_bar, int(nb) % self.beats_per_bar)
                except Exception:  # noqa: BLE001 — a click hiccup must not kill the clock
                    pass
            nb += 1
            # If tempo jumped wildly and we're behind, resync rather than spray.
            if self.time_of_beat(nb) < time.monotonic():
                nb = math.floor(self.beats_now()) + 1
