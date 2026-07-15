"""KeyShifter: an experimental ctl-plane control-modifier node.

Spawnable multiple times ("keyshift", "keyshift.2", ...). Transposes note
streams into a different key: the offset is the semitone distance from C
(home) to the selected key, mapped to the NEAREST shift (distance > 6 wraps
to distance - 12, so shifts stay within ±6 semitones).

MULTI-LANE: 4 paired lanes let several independent signals ride one shifter
WITHOUT merging. The ctl-wire endpoint grammar grows a ":<lane>" suffix:
"keyshift.2:3" is lane 3 of instance "keyshift.2" (nodes without lanes are
unchanged). Lane k in → shift → lane k out only; the dispatcher in app.py
routes per-lane via the lane sink adapters below.

PROGRESSION TIME TRACK: per instance, length 1..32 bars, steps[] holding a
key index or None per bar (None = hold the previous key). When ANY step is
set, the active key follows the transport's bar position (bar % length) —
app._handle_beat calls on_beat and the step lands at beat 0 of each bar.
An empty track = static key (the `key` setting).

Correctness invariant: an OFF is shifted by the SAME offset its ON used,
even if the key changed mid-note (per-lane open-note maps), else notes
stick. Emits {"kind": "tap", "src": "<id>"} on output fires so monitors
stay honest, and {"kind": "keyshift", "id", "active"} when the progression
moves the active key.
"""

from __future__ import annotations

LANES = 4
MAX_LENGTH = 32
KEY_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def nearest_offset(key: int) -> int:
    """Semitone distance from C (home) to `key`, mapped to the nearest
    shift: offsets above +6 wrap down an octave (7 → -5), so every shift
    stays within ±6 semitones."""
    o = int(key) % 12
    return o - 12 if o > 6 else o


class _LaneIn:
    """Note-sink adapter for one lane's IN port (what a ctl wire into
    "<id>:<lane>" resolves to)."""

    def __init__(self, ks: "KeyShifter", lane: int) -> None:
        self.ks = ks
        self.lane = lane

    def note_on(self, note: int, velocity: int = 100) -> None:
        self.ks.lane_note_on(self.lane, note, velocity)

    def note_off(self, note: int) -> None:
        self.ks.lane_note_off(self.lane, note)

    def all_off(self) -> None:
        self.ks.lane_all_off(self.lane)

    def set_sustain(self, on: bool) -> None:
        self.ks.lane_each(self.lane, lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        self.ks.lane_each(self.lane, lambda s: s.set_bend(semitones))


class KeyShifter:
    """One spawnable key-shifter instance (id "keyshift", "keyshift.2", ...)."""

    def __init__(self, app, kid: str = "keyshift") -> None:
        self.app = app  # needs .ctl_wires, ._ctl_sinks, ._emit_midi_event
        self.id = kid
        self.key = 0                      # static key (pc distance from C)
        self.active = 0                   # key currently applied
        self.length = 8                   # progression length in bars
        self.steps: list[int | None] = [None] * self.length
        # per-lane open notes: original note -> the SHIFTED note its on used
        self._open: list[dict[int, int]] = [dict() for _ in range(LANES)]
        self._lane_ins = [_LaneIn(self, lane) for lane in range(1, LANES + 1)]

    # -- wiring ------------------------------------------------------------------

    def lane_in(self, lane: int) -> _LaneIn:
        if not 1 <= int(lane) <= LANES:
            raise ValueError(f"{self.id} has lanes 1..{LANES}, not {lane!r}")
        return self._lane_ins[int(lane) - 1]

    def lane_each(self, lane: int, fn) -> None:
        """Apply fn to every sink wired from this lane's OUT port."""
        for s in self.app._ctl_sinks(f"{self.id}:{int(lane)}"):
            try:
                fn(s)
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    # -- the shift ---------------------------------------------------------------

    def _tap(self, note: int, on: bool) -> None:
        try:
            self.app._emit_midi_event(
                {"kind": "tap", "src": self.id, "note": int(note), "on": bool(on)})
        except Exception:  # noqa: BLE001
            pass

    def lane_note_on(self, lane: int, note: int, velocity: int = 100) -> None:
        shifted = int(note) + nearest_offset(self.active)
        opens = self._open[int(lane) - 1]
        prev = opens.get(int(note))
        opens[int(note)] = shifted
        if prev is not None and prev != shifted:
            # re-fired note under a new key: close the old pitch first
            self._tap(prev, False)
            self.lane_each(lane, lambda s: s.note_off(prev))
        self._tap(shifted, True)
        self.lane_each(lane, lambda s: s.note_on(shifted, velocity))

    def lane_note_off(self, lane: int, note: int) -> None:
        # the off is shifted by the SAME offset its on used — even if the key
        # changed mid-note — else the downstream voice holds a stuck note
        shifted = self._open[int(lane) - 1].pop(
            int(note), int(note) + nearest_offset(self.active))
        self._tap(shifted, False)
        self.lane_each(lane, lambda s: s.note_off(shifted))

    def lane_all_off(self, lane: int) -> None:
        opens = self._open[int(lane) - 1]
        for shifted in list(opens.values()):
            self._tap(shifted, False)
        opens.clear()
        self.lane_each(lane, lambda s: s.all_off())

    def all_off(self) -> None:
        for lane in range(1, LANES + 1):
            self.lane_all_off(lane)

    # -- configuration -------------------------------------------------------------

    def configure(self, key=None, length=None, steps=None) -> None:
        if length is not None:
            self.length = max(1, min(MAX_LENGTH, int(length)))
            self.steps = (self.steps + [None] * self.length)[: self.length]
        if steps is not None:
            clean: list[int | None] = []
            for s in list(steps)[:MAX_LENGTH]:
                clean.append(None if s is None else int(s) % 12)
            self.steps = (clean + [None] * self.length)[: self.length]
        if key is not None:
            self.key = int(key) % 12
        if not self._progressing():
            self.active = self.key  # empty track = static key

    def _progressing(self) -> bool:
        return any(s is not None for s in self.steps)

    def settings(self) -> dict:
        return {"id": self.id, "key": self.key, "length": self.length,
                "steps": list(self.steps), "active": self.active}

    def shutdown(self) -> None:
        self.all_off()  # no thread — the shifter rides the app's transport beat

    # -- the progression time track --------------------------------------------------

    def on_beat(self, bar: int, beat: int) -> None:
        """Called from app._handle_beat (the transport's beat thread). The
        active key steps at beat 0 of each bar; None steps hold."""
        if beat != 0 or not self._progressing():
            return
        s = self.steps[int(bar) % self.length]
        if s is None or s == self.active:
            return
        self.active = int(s)
        try:
            self.app._emit_midi_event(
                {"kind": "keyshift", "id": self.id, "active": self.active})
        except Exception:  # noqa: BLE001
            pass
