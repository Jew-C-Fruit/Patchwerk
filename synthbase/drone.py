"""DroneBrain: listens to played notes, infers a root, moves the drone.

The drone SOUND is an ordinary module (modules/drone.py) — this class is
the control-plane brain, structured like the arp: it taps every note from
any controller, keeps a time-decaying pitch-class histogram with bass
emphasis, scores candidate roots by harmonic support (root, fifth, thirds,
minor seventh), and moves the drone's freq — but only at transport grid
points ("every" 1 beat … 4 bars), with hysteresis so near-ties don't
cause flip-flopping. The drone node writes into the same bus as the
chain's first source, so it rides the whole effect chain.
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


class DroneBrain:
    def __init__(self, app) -> None:
        self.app = app  # needs .rack, .registry, .transport, ._emit_midi_event
        self.enabled = False
        self.every = "1 bar"
        self.octave = 2          # root lands at C{octave}..B{octave}
        self.root: int | None = None  # pitch class 0-11

        self._weights = [0.0] * 12
        self._last_decay = time.monotonic()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()

    # -- note tap (called from any controller thread) ---------------------------

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

    # -- root estimation -----------------------------------------------------------

    def _score(self, weights: list[float], candidate: int) -> float:
        return sum(
            weights[(candidate + interval) % 12] * support
            for interval, support in PROFILE.items()
        )

    def estimate(self) -> int | None:
        with self._lock:
            self._decay(time.monotonic())
            weights = list(self._weights)
        if sum(weights) < 0.1:
            return self.root  # nothing heard lately — hold
        scores = [self._score(weights, r) for r in range(12)]
        best = max(range(12), key=lambda r: scores[r])
        if self.root is None:
            return best
        # Hysteresis: incumbent keeps the seat unless clearly beaten.
        if scores[best] > scores[self.root] * HYSTERESIS:
            return best
        return self.root

    # -- configuration ---------------------------------------------------------------

    def configure(self, **kw) -> None:
        if kw.get("every") in EVERY:
            self.every = kw["every"]
        if kw.get("octave") is not None:
            self.octave = min(4, max(0, int(kw["octave"])))
            if self.enabled and self.root is not None:
                self._apply_root(self.root)  # re-pitch immediately at new octave
        if kw.get("enabled") is not None:
            enabled = bool(kw["enabled"])
            if enabled and not self.enabled:
                self.enabled = True
                self.spawn()
                self._ensure_thread()
            elif self.enabled and not enabled:
                self.enabled = False
                self._despawn()

    def settings(self) -> dict:
        return {
            "enabled": self.enabled,
            "every": self.every,
            "everies": list(EVERY),
            "octave": self.octave,
            "root": NOTE_NAMES[self.root] if self.root is not None else None,
        }

    def shutdown(self) -> None:
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- drone node management ----------------------------------------------------------

    def spawn(self) -> None:
        """(Re)create the drone instance in the current rack (idempotent).
        Called on enable and again after every patch rebuild."""
        rack = self.app.rack
        mod = self.app.registry.get("drone")
        if not (self.enabled and rack and mod):
            return
        try:
            rack.find("drone")
            return  # already present
        except KeyError:
            pass
        overrides = {}
        if self.root is not None:
            overrides["freq"] = midi_to_freq(12 * (self.octave + 1) + self.root)
        try:
            rack.add_service_source(mod, overrides)
        except Exception as exc:  # noqa: BLE001
            print(f"[drone] could not spawn: {exc}")

    def _despawn(self) -> None:
        rack = self.app.rack
        if rack is None:
            return
        try:
            rack.remove_instance("drone")
        except Exception:  # noqa: BLE001
            pass

    def _apply_root(self, root: int) -> None:
        rack = self.app.rack
        if rack is None:
            return
        try:
            rack.set_param("drone", "freq", midi_to_freq(12 * (self.octave + 1) + root))
        except Exception:  # noqa: BLE001
            pass  # rack mid-rebuild; next tick will land

    # -- the decision thread (grid-quantized) ----------------------------------------------

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
            if not self.enabled:
                return  # parked; re-enabled via configure -> _ensure_thread
            _, t = transport.next_grid(self._interval_beats(transport))
            if not self._sleep_until(t):
                return
            if not self.enabled:
                return
            new_root = self.estimate()
            if new_root is not None and new_root != self.root:
                self.root = new_root
                self._apply_root(new_root)
                try:
                    self.app._emit_midi_event(
                        {"kind": "drone", "root": NOTE_NAMES[new_root]}
                    )
                except Exception:  # noqa: BLE001
                    pass
            elif self.root is None and new_root is not None:
                self.root = new_root
                self._apply_root(new_root)
