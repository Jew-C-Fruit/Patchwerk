"""Arpeggiator: a note-pool layer between note sources and the mono voice.

Implements the same note-sink interface as MonoVoice (note_on/note_off/
set_sustain/set_bend/all_off), so the MIDI router and the GUI drive it
without knowing it exists. Disabled, it passes notes straight through.

Timing: steps are QUANTIZED to the shared Transport's grid at a rhythmic
division (1/8, 1/16T, ...). The grid is absolute — chord changes never
touch the clock, so if the arp starts on the 1, the 1 stays the 1.

Chord continuity: for up/down/updown the pattern's state lives in PITCH
space, not index space — the next note is the nearest chord tone above
(or below) the last *played* pitch, evaluated against whatever the pool
contains right now. Change chords mid-stream and the line walks on from
where it was: Eb–Ab–C ascending, swap to Eb–Ab–Bb just after the Eb
sounded, and the Ab plays next — no restart. Shared tones, disjoint
chords, and changing chord sizes all follow from the same rule.

Sustain pedal in arp mode = latch: released notes stay in the pool until
the pedal comes up.
"""

from __future__ import annotations

import random
import threading
import time

from .midi import MonoVoice
from .transport import DIVISIONS, Transport

PATTERNS = ("up", "down", "updown", "random", "played")


class Arpeggiator:
    def __init__(self, voice: MonoVoice, transport: Transport) -> None:
        self.voice = voice
        self.transport = transport
        self.enabled = False
        self.division = "1/8"
        self.gate = 0.6      # fraction of the step the note sounds
        self.octaves = 1     # 1..3
        self.pattern = "up"
        self.sustain = False

        self.on_note = None             # optional tap: DroneBrain listens here
        self.on_note_in = None          # deck "pre" tap: (note, on)
        self._pool: list[int] = []      # notes in play, insertion order
        self._held: set[int] = set()    # physically held right now
        self._sustained: set[int] = set()
        self._last_pitch: int | None = None  # continuity state (pitch space)
        self._dir = 1                        # updown direction
        self._step = 0                       # for "played"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()

    # -- note-sink interface (mirrors MonoVoice) ------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        tap2 = self.on_note_in
        if tap2 is not None:
            try:
                tap2(note, True)
            except Exception:  # noqa: BLE001
                pass
        tap = self.on_note
        if tap is not None:
            try:
                tap(note)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._held.add(note)
            self._sustained.discard(note)
            if note not in self._pool:
                self._pool.append(note)
        if self.enabled and getattr(self.transport, "running", True):
            self._ensure_thread()
        else:
            self.voice.note_on(note, velocity)

    def note_off(self, note: int) -> None:
        tap2 = self.on_note_in
        if tap2 is not None:
            try:
                tap2(note, False)
            except Exception:  # noqa: BLE001
                pass
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
        self._last_pitch = None
        self.voice.all_off()

    # -- configuration -----------------------------------------------------------

    def configure(self, **kw) -> None:
        if kw.get("division") in DIVISIONS:
            self.division = kw["division"]
        if kw.get("gate") is not None:
            self.gate = min(1.0, max(0.05, float(kw["gate"])))
        if kw.get("octaves") is not None:
            self.octaves = min(3, max(1, int(kw["octaves"])))
        if kw.get("pattern") in PATTERNS:
            self.pattern = kw["pattern"]
        if kw.get("enabled") is not None:
            enabled = bool(kw["enabled"])
            if self.enabled and not enabled:
                self.enabled = False
                self.all_off()  # silence; replay to resume
            elif enabled and not self.enabled:
                self.enabled = True
                self._last_pitch = None
                self._step = 0
                self._dir = 1
                self._ensure_thread()

    def settings(self) -> dict:
        return {
            "enabled": self.enabled, "division": self.division, "gate": self.gate,
            "octaves": self.octaves, "pattern": self.pattern,
            "patterns": list(PATTERNS), "divisions": list(DIVISIONS),
        }

    def shutdown(self) -> None:
        """Stop the tick thread for good (patch switch / app stop)."""
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- pitch-space continuation ---------------------------------------------------

    def _snapshot_pool(self) -> list[int]:
        with self._lock:
            base = list(self._pool) if self.pattern == "played" else sorted(self._pool)
        return [n + 12 * o for o in range(self.octaves) for n in base]

    def _next_pitch(self, notes: list[int]) -> int:
        """The continuity rule. `notes` is the CURRENT pool (sorted unless
        'played'); state is the last pitch that actually sounded."""
        if self.pattern == "random":
            return random.choice(notes)
        if self.pattern == "played":
            idx = self._step % len(notes)
            self._step += 1
            return notes[idx]
        lp = self._last_pitch
        notes = sorted(notes)
        if self.pattern == "up":
            if lp is None:
                return notes[0]
            up = [n for n in notes if n > lp]
            return up[0] if up else notes[0]  # wrap to bottom
        if self.pattern == "down":
            if lp is None:
                return notes[-1]
            dn = [n for n in notes if n < lp]
            return dn[-1] if dn else notes[-1]  # wrap to top
        # updown: same rule with a direction that flips at the extremes
        if lp is None:
            self._dir = 1
            return notes[0]
        if self._dir >= 0:
            up = [n for n in notes if n > lp]
            if up:
                return up[0]
            self._dir = -1
            dn = [n for n in notes if n < lp]
            return dn[-1] if dn else notes[0]
        dn = [n for n in notes if n < lp]
        if dn:
            return dn[-1]
        self._dir = 1
        up = [n for n in notes if n > lp]
        return up[0] if up else notes[-1]

    # -- the tick thread ------------------------------------------------------------

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
        division = self.division
        grid_beat, t = self.transport.next_grid(DIVISIONS[division])
        while not self._quit.is_set() and self.enabled:
            if not getattr(self.transport, "running", True):
                self._safe_all_off()
                self._last_pitch = None
                return  # park; live notes pass through while stopped
            if not self._sleep_until(t):
                break
            notes = self._snapshot_pool()
            if not notes:
                self._safe_all_off()
                self._last_pitch = None
                return  # thread parks; next note_on restarts it
            note = self._next_pitch(notes)
            self.voice.note_on(note, 100)
            self._last_pitch = note
            div_beats = DIVISIONS[division]
            step_dur = div_beats * self.transport.beat_duration
            if not self._sleep_until(t + step_dur * self.gate):
                break
            self.voice.note_off(note)
            # advance one grid slot; re-derive the grid if division changed
            if self.division != division:
                division = self.division
                grid_beat, t = self.transport.next_grid(DIVISIONS[division])
            else:
                grid_beat += div_beats
                t = self.transport.time_of_beat(grid_beat)
                if t < time.monotonic():  # fell behind (huge tempo jump)
                    grid_beat, t = self.transport.next_grid(div_beats)
        self._safe_all_off()

    def _safe_all_off(self) -> None:
        try:
            self.voice.all_off()
        except Exception:  # noqa: BLE001 — rack may already be torn down
            pass
