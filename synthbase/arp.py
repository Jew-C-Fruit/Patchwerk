"""Arpeggiator: a note-pool layer between note sources and the mono voice.

Implements the same note-sink interface as MonoVoice (note_on/note_off/
set_sustain/set_bend/all_off), so the MIDI router and the GUI drive it
without knowing it exists. Disabled, it passes notes straight through.
Enabled, it collects held notes into a pool and steps through them on a
timer thread, retriggering the voice per step.

Sustain pedal in arp mode = latch: released notes stay in the pool until
the pedal comes up.
"""

from __future__ import annotations

import random
import threading
import time

from .midi import MonoVoice

PATTERNS = ("up", "down", "updown", "random", "played")


class Arpeggiator:
    def __init__(self, voice: MonoVoice) -> None:
        self.voice = voice
        self.enabled = False
        self.rate = 8.0      # steps per second
        self.gate = 0.6      # fraction of the step the note sounds
        self.octaves = 1     # 1..3
        self.pattern = "up"
        self.sustain = False

        self._pool: list[int] = []      # notes in play, insertion order
        self._held: set[int] = set()    # physically held right now
        self._sustained: set[int] = set()
        self._step = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()

    # -- note-sink interface (mirrors MonoVoice) ------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        with self._lock:
            self._held.add(note)
            self._sustained.discard(note)
            if note not in self._pool:
                self._pool.append(note)
        if self.enabled:
            self._ensure_thread()
        else:
            self.voice.note_on(note, velocity)

    def note_off(self, note: int) -> None:
        with self._lock:
            self._held.discard(note)
            if self.sustain:
                self._sustained.add(note)  # latch until pedal up
            elif note in self._pool:
                self._pool.remove(note)
        if not self.enabled:
            self.voice.note_off(note)

    def set_sustain(self, on: bool) -> None:
        self.sustain = on
        if not self.enabled:
            self.voice.set_sustain(on)
            return
        if not on:
            with self._lock:
                for n in list(self._sustained):
                    self._sustained.discard(n)
                    if n not in self._held and n in self._pool:
                        self._pool.remove(n)

    def set_bend(self, semitones: float) -> None:
        self.voice.set_bend(semitones)

    def all_off(self) -> None:
        with self._lock:
            self._pool.clear()
            self._held.clear()
            self._sustained.clear()
        self.voice.all_off()

    # -- configuration -----------------------------------------------------------

    def configure(self, **kw) -> None:
        if "rate" in kw and kw["rate"] is not None:
            self.rate = min(20.0, max(0.5, float(kw["rate"])))
        if "gate" in kw and kw["gate"] is not None:
            self.gate = min(1.0, max(0.05, float(kw["gate"])))
        if "octaves" in kw and kw["octaves"] is not None:
            self.octaves = min(3, max(1, int(kw["octaves"])))
        if "pattern" in kw and kw["pattern"] in PATTERNS:
            self.pattern = kw["pattern"]
        if "enabled" in kw and kw["enabled"] is not None:
            enabled = bool(kw["enabled"])
            if self.enabled and not enabled:
                self.enabled = False
                self.all_off()  # silence; replay to resume
            elif enabled and not self.enabled:
                self.enabled = True
                self._step = 0
                self._ensure_thread()

    def settings(self) -> dict:
        return {
            "enabled": self.enabled, "rate": self.rate, "gate": self.gate,
            "octaves": self.octaves, "pattern": self.pattern,
            "patterns": list(PATTERNS),
        }

    def shutdown(self) -> None:
        """Stop the tick thread for good (patch switch / app stop)."""
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the tick thread ------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _snapshot_pool(self) -> list[int]:
        with self._lock:
            base = list(self._pool) if self.pattern == "played" else sorted(self._pool)
        return [n + 12 * o for o in range(self.octaves) for n in base]

    def _pick(self, notes: list[int]) -> int:
        n = len(notes)
        if self.pattern == "random":
            return random.choice(notes)
        if self.pattern == "down":
            idx = self._step % n
            self._step += 1
            return notes[n - 1 - idx]
        if self.pattern == "updown" and n > 1:
            period = 2 * n - 2
            i = self._step % period
            self._step += 1
            return notes[i] if i < n else notes[period - i]
        idx = self._step % n  # "up" and "played"
        self._step += 1
        return notes[idx]

    def _sleep_until(self, t: float) -> bool:
        """Sleep to monotonic time t; False if asked to quit meanwhile."""
        while not self._quit.is_set():
            dt = t - time.monotonic()
            if dt <= 0:
                return True
            time.sleep(min(dt, 0.05))
        return False

    def _run(self) -> None:
        next_t = time.monotonic()
        while not self._quit.is_set() and self.enabled:
            notes = self._snapshot_pool()
            if not notes:
                self.voice.all_off()
                return  # thread parks; next note_on restarts it
            note = self._pick(notes)
            self.voice.note_on(note, 100)
            interval = 1.0 / self.rate
            if not self._sleep_until(next_t + interval * self.gate):
                break
            self.voice.note_off(note)
            next_t += interval
            if not self._sleep_until(next_t):
                break
        self.voice.all_off()
