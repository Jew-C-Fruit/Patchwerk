"""TonicDeriver: a ctl-plane node that listens to notes and derives a root.

v5 split of the old DroneBrain; reworked 2026-07 (drone rework, item 3):
the bespoke "tonic" signal is RETIRED. The deriver is now an ordinary
ctl-plane node — notes in, ONE mono note stream out:

  IN   note events (wired like any ctl node: keys/arp/deck/... -> tonic)
  OUT  the COMMITTED ROOT as a standard note stream (mono: each new root
       emits the previous root's note_off, then the new root's note_on),
       fanned over the deriver's outgoing ctl wires like any node. A drone
       is just a ctl note-sink now (app._DroneSink) — so is a voice, the
       arp, or a Key Shifter lane; transposition and monitoring all apply.

The estimation brain is extracted into RootEstimator: a time-decaying
pitch-class histogram with bass emphasis, harmonic-support scoring
(root, fifth, thirds, minor seventh) and hysteresis so near-ties don't
flip-flop. Decisions land only at transport grid points ("every"
1 beat ... 4 bars).

Emits {"kind": "tap", "src": "<id>", ...} viz taps for its OUTPUT stream
(the committed roots) and {"kind": "tonic_out", "id": "<id>", "root": "Eb"}
on root changes.
"""

from __future__ import annotations

import math
import threading
import time

from .transport import Transport

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
EVERY = {"1 beat": 1.0, "2 beats": 2.0, "1 bar": "bar", "2 bars": "2bar", "4 bars": "4bar"}
DECAY_TAU = 6.0          # seconds for the pitch-class memory to fade by 1/e
HYSTERESIS = 1.25        # new root must beat the incumbent by 25%
# Harmonic support profile: how much a present pitch class (at interval i
# above a candidate root) argues FOR that root.
PROFILE = {0: 1.0, 7: 0.55, 4: 0.35, 3: 0.30, 10: 0.18}


def midi_to_freq(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


class RootEstimator:
    """The extracted estimation brain: observe notes, estimate a root
    pitch class with decay, bass emphasis and hysteresis."""

    def __init__(self) -> None:
        self._weights = [0.0] * 12
        self._last_decay = time.monotonic()
        self._lock = threading.Lock()

    def observe(self, note: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._decay(now)
            # Bass emphasis: low notes are stronger root evidence.
            weight = 1.0 + max(0.0, (55 - note)) * 0.06
            self._weights[note % 12] += weight

    def _decay(self, now: float) -> None:
        dt = now - self._last_decay
        if dt > 0:
            factor = math.exp(-dt / DECAY_TAU)
            self._weights = [w * factor for w in self._weights]
            self._last_decay = now

    def weights(self) -> list[float]:
        with self._lock:
            self._decay(time.monotonic())
            return list(self._weights)

    @staticmethod
    def _score(weights: list[float], candidate: int) -> float:
        return sum(
            weights[(candidate + interval) % 12] * support
            for interval, support in PROFILE.items()
        )

    def estimate(self, incumbent: int | None) -> int | None:
        """Best root right now; the incumbent holds unless clearly beaten."""
        weights = self.weights()
        if sum(weights) < 0.1:
            return incumbent  # nothing heard lately — hold
        scores = [self._score(weights, r) for r in range(12)]
        best = max(range(12), key=lambda r: scores[r])
        if incumbent is None:
            return best
        if scores[best] > scores[incumbent] * HYSTERESIS:
            return best
        return incumbent


class TonicDeriver:
    """One spawnable ctl-plane deriver node (id "tonic", "tonic.2", ...)."""

    def __init__(self, app, tid: str = "tonic") -> None:
        self.app = app  # needs .rack, .transport, .ctl_wires, ._emit_midi_event
        self.id = tid
        self.every = "1 bar"
        self.octave = 2               # root lands at C{octave}..B{octave}
        self.root: int | None = None  # pitch class 0-11
        self.est = RootEstimator()
        self._open: set[int] = set()  # input notes on'd but not yet off'd
        self._out_note: int | None = None  # the emitted root note (mono out)

        self._thread: threading.Thread | None = None
        self._quit = threading.Event()
        self._ensure_thread()

    # -- note-sink interface (a ctl node: notes in are EVIDENCE only) -----------

    def _tap(self, note: int, on: bool) -> None:
        try:
            self.app._emit_midi_event(
                {"kind": "tap", "src": self.id, "note": int(note), "on": bool(on)})
        except Exception:  # noqa: BLE001
            pass

    def _thru(self, fn) -> None:
        for s in self.app._ctl_sinks(self.id):
            try:
                fn(s)
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    def note_on(self, note: int, velocity: int = 100) -> None:
        # input notes feed the estimator (and the held-set for future literal
        # extraction); they do NOT pass through — the out is the derived root
        self.est.observe(note)
        self._open.add(int(note))

    def note_off(self, note: int) -> None:
        self._open.discard(int(note))

    def all_off(self) -> None:
        # panic: clear input state, release the emitted root downstream and
        # close its tap — silencing paths must never strand an "on"
        self._open.clear()
        if self._out_note is not None:
            self._tap(self._out_note, False)
            self._out_note = None
        self._thru(lambda s: s.all_off())

    def set_sustain(self, on: bool) -> None:
        self._thru(lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        self._thru(lambda s: s.set_bend(semitones))

    # -- configuration ---------------------------------------------------------

    def configure(self, **kw) -> None:
        if kw.get("every") in EVERY:
            self.every = kw["every"]
        if kw.get("octave") is not None:
            self.octave = min(4, max(0, int(kw["octave"])))
            self._emit_root()  # re-voice the emitted root at the new octave

    def settings(self) -> dict:
        return {
            "id": self.id,
            "every": self.every,
            "everies": list(EVERY),
            "octave": self.octave,
            "root": NOTE_NAMES[self.root] if self.root is not None else None,
        }

    def shutdown(self) -> None:
        # release the emitted root downstream first — a removed deriver must
        # not leave its note ringing (drones HOLD by design; voices release)
        if self._out_note is not None:
            note = self._out_note
            self._tap(note, False)
            self._thru(lambda s: s.note_off(note))
            self._out_note = None
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the OUT: the committed root as a mono note stream ------------------------

    def _root_note(self) -> int | None:
        if self.root is None:
            return None
        return 12 * (self.octave + 1) + self.root

    def _emit_root(self) -> None:
        """Move the emitted root note to the current root/octave (mono:
        previous root's note_off first, then the new root's note_on)."""
        note = self._root_note()
        prev = self._out_note
        if note == prev:
            return
        if prev is not None:
            self._tap(prev, False)
            self._thru(lambda s: s.note_off(prev))
        self._out_note = note
        if note is not None:
            self._tap(note, True)
            self._thru(lambda s: s.note_on(note, 100))

    def current_note(self) -> int | None:
        """The note this deriver is currently emitting (None = none yet)."""
        return self._out_note

    def decide(self) -> None:
        """One grid-point decision: estimate, and on a root change emit the
        new root note downstream + the tonic_out event. (The thread calls
        this; tests may call it directly.)"""
        new_root = self.est.estimate(self.root)
        if new_root is None or new_root == self.root:
            return
        self.root = new_root
        self._emit_root()
        try:
            self.app._emit_midi_event(
                {"kind": "tonic_out", "id": self.id, "root": NOTE_NAMES[new_root]})
        except Exception:  # noqa: BLE001
            pass

    # -- the decision thread (grid-quantized) -------------------------------------

    def _interval_beats(self, transport: Transport) -> float:
        v = EVERY[self.every]
        if v == "bar":
            return float(transport.beats_per_bar)
        if v == "2bar":
            return 2.0 * transport.beats_per_bar
        if v == "4bar":
            return 4.0 * transport.beats_per_bar
        return float(v)

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _sleep_until(self, t: float) -> bool:
        while not self._quit.is_set():
            dt = t - time.monotonic()
            if dt <= 0:
                return True
            time.sleep(min(dt, 0.05))
        return False

    def _run(self) -> None:
        transport = self.app.transport
        while not self._quit.is_set():
            _, t = transport.next_grid(self._interval_beats(transport))
            if not self._sleep_until(t):
                return
            self.decide()
