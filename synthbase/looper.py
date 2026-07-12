"""MIDI looper: bar-synced record / replay of note events.

Records what you PLAY (voiced note on/offs with their beat positions) for N
bars from the next downbeat, then replays the phrase in a loop through the
current patch — change modules under a running loop and the loop wears the
new sound. Overdub adds passes on top. All timing rides the transport.

(v1 was an audio looper; scsynth buffer reads returned allocation garbage
through supriya on this setup — see docs/HISTORY.md. Notes are deterministic.)
"""

from __future__ import annotations

import threading
import time

STATES = ("empty", "armed", "recording", "playing", "overdubbing", "stopped")


class Looper:
    def __init__(self, app) -> None:
        self.app = app
        self.state = "empty"
        self.bars = 2
        self.level = 0.9          # kept for GUI compat; scales velocity
        self.overdub = False
        self._events: list[tuple[float, int, bool]] = []  # (beat offset, note, on)
        self._loop_beats = 8.0
        self._record_start_beat = 0.0
        self._lock = threading.RLock()
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._sounding: set[int] = set()
        self.position = "post"      # "pre" = raw input, arp hears replay;
                                    # "post" = records arp output, own voice replays
        self._self_fire = False     # replayed notes must never be re-recorded
        self._deck_node = None      # private voice node for "post" replay
        self._deck_key = None

    # -- note taps ---------------------------------------------------------------

    def observe(self, note: int, on: bool) -> None:
        """Voiced (post-arp) tap."""
        if self.position != "post" or self._self_fire:
            return
        self._record(note, on)

    def observe_input(self, note: int, on: bool) -> None:
        """Raw controller (pre-arp) tap."""
        if self.position != "pre" or self._self_fire:
            return
        self._record(note, on)

    def _record(self, note: int, on: bool) -> None:
        if self.state not in ("armed", "recording", "overdubbing"):
            return
        beat = self.app.transport.beats_now() - self._record_start_beat
        if self.state == "armed":
            # a note struck just ahead of the loop top belongs at beat 0
            if not on or beat < -0.35:
                return
            beat = 0.0
        b = round(beat % self._loop_beats, 4)
        with self._lock:
            self._events.append((b, int(note), bool(on)))
        emit = getattr(self.app, "_emit_midi_event", None)
        if emit:  # live feed for the deck visualizer
            emit({"kind": "loop_note", "beat": b, "note": int(note), "on": bool(on)})

    # -- public API -------------------------------------------------------------

    def configure(self, action=None, bars=None, level=None, overdub=None,
                  position=None) -> None:
        with self._lock:
            if position in ("pre", "post") and self.state in ("empty", "stopped"):
                self.position = position
            if bars is not None and self.state in ("empty", "stopped"):
                self.bars = min(8, max(1, int(bars)))
            if level is not None:
                self.level = min(1.0, max(0.0, float(level)))
            if overdub is not None:
                self.overdub = bool(overdub)
                if self.state == "playing" and self.overdub:
                    self.state = "overdubbing"
                    self._emit()
                elif self.state == "overdubbing" and not self.overdub:
                    self.state = "playing"
                    self._emit()
            if action == "record":
                self._arm()
            elif action == "stop":
                self._stop()
            elif action == "play":
                self._play()
            elif action == "clear":
                self._clear()

    def phase(self) -> float | None:
        """Current position within the loop, in beats (None when not rolling)."""
        if self.state in ("recording", "playing", "overdubbing"):
            return (self.app.transport.beats_now()
                    - self._record_start_beat) % self._loop_beats
        return None

    def settings(self) -> dict:
        with self._lock:
            notes = sorted(self._events)[:400]
        return {"state": self.state, "bars": self.bars, "level": self.level,
                "overdub": self.overdub, "midi": True, "position": self.position,
                "loop_beats": self._loop_beats, "notes": notes,
                "events": len(self._events)}

    def shutdown(self) -> None:
        self._deck_teardown()
        self._quit.set()
        if self._timer:
            self._timer.cancel()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._release_all()

    def reset(self) -> None:
        pass  # no server resources

    # -- internals -----------------------------------------------------------------

    def _sink(self):
        if self.position == "pre":
            return self.app.arp or self.app.voice  # replay INTO the arp
        return self._deck_voice()                   # post: private voice

    def _deck_voice(self):
        """A private second node of the target module — deck plays alongside
        your live voice instead of stealing it."""
        target = self.app.voice.target_key if self.app.voice else None
        if target is None:
            return self.app.arp or self.app.voice
        if self._deck_node is not None and self._deck_key == target:
            return self
        self._deck_teardown()
        try:
            rack = self.app.rack
            inst = rack.find(target)
            from supriya import AddAction
            self._deck_node = self.app.engine.server.add_synth(
                inst.module.synthdef, add_action=AddAction.ADD_TO_HEAD,
                target_node=self.app.engine.root_group,
                **{**inst.settings, "gate": 0},
            )
            self._deck_key = target
            return self
        except Exception as exc:  # noqa: BLE001
            print(f"[looper] deck voice failed ({exc}); sharing the main voice")
            return self.app.arp or self.app.voice

    # MonoVoice-compatible surface driving the private node
    def note_on(self, note: int, velocity: int = 100) -> None:
        if self._deck_node is not None:
            freq = 440.0 * 2 ** ((note - 69) / 12)
            self._deck_node.set(freq=freq, gate=1, amp_scale=1)
            emit = getattr(self.app, "_emit_midi_event", None)
            if emit:
                emit({"kind": "voiced", "note": int(note), "on": True, "deck": True})

    def note_off(self, note: int) -> None:
        if self._deck_node is not None:
            self._deck_node.set(gate=0)
            emit = getattr(self.app, "_emit_midi_event", None)
            if emit:
                emit({"kind": "voiced", "note": int(note), "on": False, "deck": True})

    def _deck_teardown(self) -> None:
        if self._deck_node is not None:
            try:
                self._deck_node.free()
            except Exception:  # noqa: BLE001
                pass
        self._deck_node = None
        self._deck_key = None

    def _release_all(self) -> None:
        sink = self._sink()
        if sink is None:
            self._sounding.clear()
            return
        for n in list(self._sounding):
            try:
                sink.note_off(n)
            except Exception:  # noqa: BLE001
                pass
        self._sounding.clear()

    def _arm(self) -> None:
        if self.state in ("armed", "recording"):
            return
        transport = self.app.transport
        self._loop_beats = float(self.bars * transport.beats_per_bar)
        if not self.overdub:
            self._events = []
        start_beat, t_start = transport.next_grid(float(transport.beats_per_bar))
        # known now — lets the armed-state grace clamp early notes to beat 0
        self._record_start_beat = start_beat
        self.state = "armed"
        self._emit()

        def start_recording():
            with self._lock:
                if self.state != "armed":
                    return
                self._record_start_beat = start_beat
                self.state = "recording"
                self._emit()
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(
                self._loop_beats * transport.beat_duration, self._finish_recording)
            self._timer.daemon = True
            self._timer.start()

        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(max(0.0, t_start - time.monotonic()),
                                      start_recording)
        self._timer.daemon = True
        self._timer.start()

    def _finish_recording(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            # close notes still held when the window ends so nothing rings on
            depth: dict[int, int] = {}
            for _, note, on in sorted(self._events):
                depth[note] = depth.get(note, 0) + (1 if on else -1)
            for note, n in depth.items():
                for _ in range(max(0, n)):
                    self._events.append(
                        (round(self._loop_beats - 0.02, 4), note, False))
            self.state = "overdubbing" if self.overdub else "playing"
            self._emit()
        self._ensure_thread()

    def _play(self) -> None:
        if self.state == "stopped" and self._events:
            self.state = "playing"
            self._emit()
            self._ensure_thread()

    def _stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        self.state = "stopped" if self._events else "empty"
        self._release_all()
        self._deck_teardown()
        self._emit()

    def _clear(self) -> None:
        self._stop()
        self._events = []
        self.state = "empty"
        self._emit()

    # -- the replay thread ------------------------------------------------------

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
        GRACE = 0.15  # beats — a just-passed event (loop-top thread latency,
        #               beat-0 quantized notes) still fires, immediately
        while not self._quit.is_set() and self.state in ("playing", "overdubbing"):
            with self._lock:
                events = sorted(self._events)
            if not events:
                time.sleep(0.1)
                continue
            rel = transport.beats_now() - self._record_start_beat
            cycle = int(rel // self._loop_beats)
            phase = rel - cycle * self._loop_beats
            idx = next((i for i, e in enumerate(events) if e[0] >= phase - GRACE),
                       None)
            if idx is None:  # nothing left this cycle — hold at the loop top
                cycle += 1
                idx = 0
                if not self._sleep_until(transport.time_of_beat(
                        self._record_start_beat + cycle * self._loop_beats)):
                    break
            interrupted = False
            for beat_off, note, on in events[idx:]:
                if self._quit.is_set() or self.state not in ("playing", "overdubbing"):
                    interrupted = True
                    break
                target = self._record_start_beat + cycle * self._loop_beats + beat_off
                if not self._sleep_until(transport.time_of_beat(target)):
                    interrupted = True
                    break
                if not getattr(transport, "running", True):
                    continue  # transport stopped: skip firing
                sink = self._sink()
                if sink is None:
                    continue
                try:
                    self._self_fire = True
                    if on:
                        sink.note_on(note, int(100 * self.level))
                        self._sounding.add(note)
                    else:
                        sink.note_off(note)
                        self._sounding.discard(note)
                finally:
                    self._self_fire = False
            if interrupted:
                continue
            # cycle tail done — park at the next loop top before rescanning so
            # the grace window can't refire what just played
            if not self._sleep_until(transport.time_of_beat(
                    self._record_start_beat + (cycle + 1) * self._loop_beats)):
                break
        self._release_all()

    def _emit(self) -> None:
        emit = getattr(self.app, "_emit_midi_event", None)
        if emit is not None:
            emit({"kind": "looper", **self.settings()})
