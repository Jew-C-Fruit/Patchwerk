"""Derivers: ctl-plane nodes that listen to notes and derive ONE note out.

v5 split of the old DroneBrain; reworked 2026-07 (drone rework, item 3;
deriver split, item 6). The bespoke "tonic" signal is RETIRED — a deriver
is an ordinary ctl-plane node: notes in, ONE mono note stream out, fanned
over its outgoing ctl wires. A drone is just a ctl note-sink
(app._DroneSink) — so is a voice, the arp, or a Key Shifter lane.

TWO deriver nodes share one timing model (_DeriverBase):

* TonicDeriver (the ESTIMATOR, ids "tonic", "tonic.2", ...) — statistical,
  deliberately sluggish (settle-and-land). RootEstimator: a time-decaying
  pitch-class histogram with bass emphasis, harmonic-support scoring and
  hysteresis. Its knobs are LIVE controls now: memory (decay tau),
  stickiness (hysteresis), bass (emphasis coefficient) and listening (a
  named support profile). It also exposes an analysis() snapshot (weights,
  per-candidate scores, leading candidate, confidence = winner-vs-runner-up
  gap) that the server broadcasts on a steady tick for the card's
  histogram viz.

* LiteralDeriver (ids "literal", "literal.2", ...) — deterministic,
  zero-lag (drop). At commit time it reads the live held-set/last-played
  and emits ONE note: extract = lowest-held / highest-held / last-played /
  first-played; place = absolute (keep the real octave) / fold (re-voice
  into a fixed octave) / transpose (±N semitones); hold-on-empty either
  holds the last note or releases it.

SHARED TIMING: a settable `every` grid timer drives commit(); if a ping
source is wired into the node's trigger-in the internal timer stands down
and each ping commits instead (unwire → timer resumes). The Literal adds
"immediate": commit on every input note event. Defaults: Estimator
"1 bar", Literal "immediate".

Emits {"kind": "tap", "src": "<id>", ...} viz taps for the OUTPUT stream
and {"kind": "tonic_out", "id": "<id>", "root": "Eb"} on root changes.
"""

from __future__ import annotations

import math
import threading
import time

from .transport import Transport

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
EVERY = {"1 beat": 1.0, "2 beats": 2.0, "1 bar": "bar", "2 bars": "2bar", "4 bars": "4bar"}
DECAY_TAU = 6.0          # default memory: seconds for the histogram to fade by 1/e
HYSTERESIS = 1.25        # default stickiness: new root must beat incumbent by 25%
BASS_COEF = 0.06         # default bass emphasis per semitone below 55
# Listening profiles: how much a present pitch class (at interval i above a
# candidate root) argues FOR that root.
PROFILES = {
    "triadic":    {0: 1.0, 7: 0.55, 4: 0.35, 3: 0.30, 10: 0.18},
    "root+fifth": {0: 1.0, 7: 0.5},
    "chromatic":  {0: 1.0},
}
PROFILE = PROFILES["triadic"]   # legacy alias

EXTRACTS = ("lowest-held", "highest-held", "last-played", "first-played")
PLACES = ("absolute", "fold", "transpose")


def midi_to_freq(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


class RootEstimator:
    """The estimation brain: observe notes, estimate a root pitch class
    with decay, bass emphasis and hysteresis — all LIVE controls now."""

    def __init__(self) -> None:
        self._weights = [0.0] * 12
        self._last_decay = time.monotonic()
        self._lock = threading.Lock()
        self.tau = DECAY_TAU          # "memory": decay seconds (1..30)
        self.hysteresis = HYSTERESIS  # "stickiness": winner margin (1..2)
        self.bass = BASS_COEF         # bass emphasis per semitone below 55
        self.profile = "triadic"      # "listening": PROFILES key

    def observe(self, note: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._decay(now)
            # Bass emphasis: low notes are stronger root evidence.
            weight = 1.0 + max(0.0, (55 - note)) * self.bass
            self._weights[note % 12] += weight

    def _decay(self, now: float) -> None:
        dt = now - self._last_decay
        if dt > 0:
            factor = math.exp(-dt / max(0.25, self.tau))
            self._weights = [w * factor for w in self._weights]
            self._last_decay = now

    def weights(self) -> list[float]:
        with self._lock:
            self._decay(time.monotonic())
            return list(self._weights)

    def _support(self) -> dict:
        return PROFILES.get(self.profile, PROFILES["triadic"])

    def _score(self, weights: list[float], candidate: int) -> float:
        return sum(
            weights[(candidate + interval) % 12] * support
            for interval, support in self._support().items()
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
        if scores[best] > scores[incumbent] * self.hysteresis:
            return best
        return incumbent

    def analysis(self, incumbent: int | None) -> dict:
        """Steady-tick snapshot for the histogram viz: raw presence
        (weights), per-candidate scores (why this root wins), the leading
        candidate right now, and confidence = winner-vs-runner-up gap."""
        weights = self.weights()
        total = sum(weights)
        if total < 0.1:
            return {"weights": [0.0] * 12, "scores": [0.0] * 12,
                    "leading": None, "confidence": 0.0}
        scores = [self._score(weights, r) for r in range(12)]
        order = sorted(range(12), key=lambda r: scores[r], reverse=True)
        top, runner = scores[order[0]], scores[order[1]]
        conf = 0.0 if top <= 0 else max(0.0, min(1.0, (top - runner) / top))
        wpeak = max(weights) or 1.0
        speak = max(scores) or 1.0
        return {
            "weights": [round(w / wpeak, 4) for w in weights],
            "scores": [round(s / speak, 4) for s in scores],
            "leading": order[0],
            "confidence": round(conf, 3),
        }


class _DeriverBase:
    """Shared deriver chassis: mono note out over ctl wires, the `every`
    grid timer, and the ping trigger-in override."""

    DEFAULT_EVERY = "1 bar"

    def __init__(self, app, nid: str) -> None:
        self.app = app  # needs .transport, .ctl_wires, ._ctl_sinks, ._emit_midi_event
        self.id = nid
        self.every = self.DEFAULT_EVERY
        self._out_note: int | None = None  # the emitted note (mono out)
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()
        self._ensure_thread()

    # -- mono note out ---------------------------------------------------------

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

    def _emit_out(self, note: int | None) -> None:
        """Move the emitted note (mono: off the old, on the new).
        note=None releases without a replacement."""
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

    # -- ping trigger-in (node-scoped) -----------------------------------------

    def trigger(self) -> None:
        """Commit NOW. While any ping source is wired into this node its
        internal grid timer stands down (see _run) — unwire to resume."""
        self.commit()

    def _ping_driven(self) -> bool:
        app = self.app
        try:
            return any(w.get("to") == self.id and app._is_ping_src(w.get("from"))
                       for w in (getattr(app, "ctl_wires", None) or []))
        except Exception:  # noqa: BLE001
            return False

    # -- the commit (subclasses implement) -------------------------------------

    def commit(self) -> None:  # pragma: no cover — abstract
        raise NotImplementedError

    # -- lifecycle -------------------------------------------------------------

    def shutdown(self) -> None:
        # release the emitted note downstream first — a removed deriver must
        # not leave its note ringing (drones HOLD by design; voices release)
        if self._out_note is not None:
            note = self._out_note
            self._tap(note, False)
            self._thru(lambda s: s.note_off(note))
            self._out_note = None
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the decision thread (grid-quantized) ----------------------------------

    def _interval_beats(self, transport: Transport) -> float | None:
        v = EVERY.get(self.every)
        if v is None:
            return None                     # "immediate" — no grid timer
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
            beats = self._interval_beats(transport)
            if beats is None:               # event-driven ("immediate")
                if not self._sleep_until(time.monotonic() + 0.2):
                    return
                continue
            _, t = transport.next_grid(beats)
            if not self._sleep_until(t):
                return
            # a STOPPED transport freezes beats_now — next_grid then returns
            # a constant past time forever; don't busy-spin decisions
            if not transport.running:
                time.sleep(0.1)
                continue
            # a wired ping source OWNS the commit timing (timer override)
            if self._ping_driven():
                continue
            self.commit()


class TonicDeriver(_DeriverBase):
    """The ESTIMATOR deriver (ids "tonic", "tonic.2", ...): statistical,
    settle-and-land. Input notes are estimator evidence only."""

    DEFAULT_EVERY = "1 bar"

    def __init__(self, app, tid: str = "tonic") -> None:
        self.octave = 2               # root lands at C{octave}..B{octave}
        self.root: int | None = None  # pitch class 0-11
        self.est = RootEstimator()
        self._open: set[int] = set()  # input notes on'd but not yet off'd
        super().__init__(app, tid)

    # -- note-sink interface (notes in are EVIDENCE only) -----------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
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
        if kw.get("memory") is not None:
            self.est.tau = min(30.0, max(1.0, float(kw["memory"])))
        if kw.get("stickiness") is not None:
            self.est.hysteresis = min(2.0, max(1.0, float(kw["stickiness"])))
        if kw.get("bass") is not None:
            self.est.bass = min(0.2, max(0.0, float(kw["bass"])))
        if kw.get("listening") in PROFILES:
            self.est.profile = kw["listening"]

    def settings(self) -> dict:
        return {
            "id": self.id,
            "every": self.every,
            "everies": list(EVERY),
            "octave": self.octave,
            "root": NOTE_NAMES[self.root] if self.root is not None else None,
            "memory": round(self.est.tau, 2),
            "stickiness": round(self.est.hysteresis, 3),
            "bass": round(self.est.bass, 3),
            "listening": self.est.profile,
            "listenings": list(PROFILES),
        }

    def analysis(self) -> dict:
        """Live snapshot for the card's histogram viz (server steady tick)."""
        a = self.est.analysis(self.root)
        a["id"] = self.id
        a["root"] = self.root
        return a

    # -- the commit ------------------------------------------------------------

    def _root_note(self) -> int | None:
        if self.root is None:
            return None
        return 12 * (self.octave + 1) + self.root

    def _emit_root(self) -> None:
        self._emit_out(self._root_note())

    def commit(self) -> None:
        """One decision: estimate, and on a root change emit the new root
        note downstream + the tonic_out event."""
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

    # legacy name (older tests/callers): a grid decision
    def decide(self) -> None:
        self.commit()


class LiteralDeriver(_DeriverBase):
    """The LITERAL deriver (ids "literal", "literal.2", ...): deterministic,
    zero-lag. At commit time it reads the live held-set/last-played and
    emits ONE note through extract × place."""

    DEFAULT_EVERY = "immediate"

    def __init__(self, app, lid: str = "literal") -> None:
        self.extract = "lowest-held"
        self.place = "absolute"
        self.fold_octave = 3          # "fold": re-voice to C{fold_octave}..B
        self.transpose = 0            # "transpose": ±N semitones
        self.hold_on_empty = True
        self._held: list[int] = []    # ordered by note_on time
        self._last_played: int | None = None
        self._first_played: int | None = None  # first of the current phrase
        super().__init__(app, lid)
        self.every = "immediate"

    # -- note-sink interface ---------------------------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        if not self._held:
            self._first_played = note      # a fresh phrase starts
        self._held.append(note)
        self._last_played = note
        if self.every == "immediate" and not self._ping_driven():
            self.commit()

    def note_off(self, note: int) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        if self.every == "immediate" and not self._ping_driven():
            self.commit()

    def all_off(self) -> None:
        self._held.clear()
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
        if kw.get("every") == "immediate" or kw.get("every") in EVERY:
            self.every = kw["every"]
        if kw.get("extract") in EXTRACTS:
            self.extract = kw["extract"]
        if kw.get("place") in PLACES:
            self.place = kw["place"]
        if kw.get("fold_octave") is not None:
            self.fold_octave = min(7, max(0, int(kw["fold_octave"])))
        if kw.get("transpose") is not None:
            self.transpose = min(24, max(-24, int(kw["transpose"])))
        if kw.get("hold_on_empty") is not None:
            self.hold_on_empty = bool(kw["hold_on_empty"])

    def settings(self) -> dict:
        return {
            "id": self.id,
            "every": self.every,
            "everies": ["immediate", *EVERY],
            "extract": self.extract,
            "extracts": list(EXTRACTS),
            "place": self.place,
            "places": list(PLACES),
            "fold_octave": self.fold_octave,
            "transpose": self.transpose,
            "hold_on_empty": self.hold_on_empty,
            "note": self._out_note,
        }

    # -- the commit ------------------------------------------------------------

    def _pick(self) -> int | None:
        if self.extract == "lowest-held":
            return min(self._held) if self._held else None
        if self.extract == "highest-held":
            return max(self._held) if self._held else None
        if self.extract == "last-played":
            return self._last_played
        if self.extract == "first-played":
            return self._held[0] if self._held else self._first_played
        return None

    def _place(self, note: int) -> int:
        if self.place == "fold":
            return 12 * (self.fold_octave + 1) + (note % 12)
        if self.place == "transpose":
            return min(127, max(0, note + self.transpose))
        return note

    def commit(self) -> None:
        """Sample the current notes and move the mono out accordingly."""
        picked = self._pick()
        if picked is None:
            if not self.hold_on_empty:
                self._emit_out(None)       # release; nothing replaces it
            return                          # hold: leave the out where it is
        self._emit_out(self._place(int(picked)))
