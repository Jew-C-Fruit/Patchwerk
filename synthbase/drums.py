"""Drum machine v1: synthesized voices + a transport-locked step sequencer.

Voices are one-shot synthdefs (they free themselves); the sequencer walks a
16-step grid (one bar of 1/16s in 4/4) on the shared transport, so drums,
click, arp and drone all breathe on the same clock. Patterns are plain data
and live inside presets.
"""

from __future__ import annotations

import threading
import time

from supriya import AddAction, Envelope, synthdef
from supriya.ugens import (
    BPF, EnvGen, HPF, LFTri, LPF, Out, Pan2, PinkNoise, SinOsc, WhiteNoise, XLine,
)

LANES = ("kick", "snare", "hat", "clap")
STEPS = 16


@synthdef()
def _kick(amp=0.5, out=0):
    pitch = XLine.kr(start=150, stop=52, duration=0.09)
    env = EnvGen.kr(envelope=Envelope.percussive(0.002, 0.28), done_action=2)
    body = SinOsc.ar(frequency=pitch) * env
    clicky = (WhiteNoise.ar() * EnvGen.kr(envelope=Envelope.percussive(0.001, 0.01))) * 0.15
    sig = (body + clicky).tanh() * amp
    Out.ar(bus=out, source=[sig, sig])


@synthdef()
def _snare(amp=0.4, out=0):
    env = EnvGen.kr(envelope=Envelope.percussive(0.001, 0.18), done_action=2)
    tone = SinOsc.ar(frequency=XLine.kr(start=330, stop=180, duration=0.08)) * 0.4
    noise = HPF.ar(source=WhiteNoise.ar(), frequency=1200) * 0.8
    sig = (tone + noise) * env * amp
    Out.ar(bus=out, source=[sig, sig])


@synthdef()
def _hat(amp=0.25, decay=0.05, out=0):
    env = EnvGen.kr(envelope=Envelope.percussive(0.001, decay), done_action=2)
    sig = HPF.ar(source=WhiteNoise.ar(), frequency=6500) * env * amp
    Out.ar(bus=out, source=[sig, sig])


@synthdef()
def _clap(amp=0.35, out=0):
    env = EnvGen.kr(
        envelope=Envelope(
            amplitudes=[0, 1, 0.3, 0.8, 0.2, 0.6, 0],
            durations=[0.001, 0.01, 0.005, 0.01, 0.005, 0.12],
        ),
        done_action=2,
    )
    sig = BPF.ar(source=WhiteNoise.ar(), frequency=1600, reciprocal_of_q=1.2) * env * amp * 2
    Out.ar(bus=out, source=[sig, sig])


_DEFS = {"kick": _kick, "snare": _snare, "hat": _hat, "clap": _clap}

DEFAULT_PATTERNS = {
    "kick": [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
    "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
    "hat": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1],
    "clap": [0] * 16,
}


class DrumMachine:
    def __init__(self, app) -> None:
        self.app = app
        self.enabled = False
        self.patterns = {lane: list(steps) for lane, steps in DEFAULT_PATTERNS.items()}
        self.levels = {"kick": 0.8, "snare": 0.7, "hat": 0.6, "clap": 0.7}
        self._registered = False
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None

    # -- config / persistence ----------------------------------------------------

    def configure(self, enabled=None, patterns=None, levels=None) -> None:
        if patterns is not None:
            for lane in LANES:
                if lane in patterns:
                    steps = [1 if s else 0 for s in patterns[lane]][:STEPS]
                    steps += [0] * (STEPS - len(steps))
                    self.patterns[lane] = steps
        if levels is not None:
            for lane in LANES:
                if lane in levels:
                    self.levels[lane] = min(1.0, max(0.0, float(levels[lane])))
        if enabled is not None:
            enabled = bool(enabled)
            if enabled and not self.enabled:
                self.enabled = True
                self._ensure_thread()
            elif self.enabled and not enabled:
                self.enabled = False

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "patterns": self.patterns, "levels": self.levels}

    def restore(self, data: dict) -> None:
        self.configure(enabled=data.get("enabled"), patterns=data.get("patterns"),
                       levels=data.get("levels"))

    def settings(self) -> dict:
        return {**self.snapshot(), "lanes": list(LANES), "steps": STEPS}

    def shutdown(self) -> None:
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the sequencer thread -------------------------------------------------------

    def _ensure_registered(self) -> None:
        if not self._registered and self.app.engine and self.app.engine.server:
            self.app.engine.server.add_synthdefs(*_DEFS.values())
            self.app.engine.server.sync()
            self._registered = True

    def reset(self) -> None:
        self._registered = False

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _fire(self, lane: str) -> None:
        try:
            self._ensure_registered()
            self.app.engine.root_group.add_synth(
                _DEFS[lane], add_action=AddAction.ADD_TO_TAIL,
                amp=self.levels[lane] * {"kick": 0.55, "snare": 0.42,
                                         "hat": 0.3, "clap": 0.4}[lane],
            )
        except Exception:  # noqa: BLE001 — a dropped hit must not kill the groove
            pass

    def _sleep_until(self, t: float) -> bool:
        while not self._quit.is_set():
            dt = t - time.monotonic()
            if dt <= 0:
                return True
            time.sleep(min(dt, 0.05))
        return False

    def _run(self) -> None:
        transport = self.app.transport
        step_beats = 0.25  # 1/16 grid; 16 steps = one 4/4 bar
        grid_beat, t = transport.next_grid(step_beats)
        while not self._quit.is_set() and self.enabled:
            if not self._sleep_until(t):
                return
            step = int(round(grid_beat / step_beats)) % STEPS
            for lane in LANES:
                if self.patterns[lane][step]:
                    self._fire(lane)
            emit = getattr(self.app, "_emit_midi_event", None)
            if emit is not None:
                emit({"kind": "drum_step", "step": step})
            grid_beat += step_beats
            t = transport.time_of_beat(grid_beat)
            if t < time.monotonic():
                grid_beat, t = transport.next_grid(step_beats)
