"""Tape-deck looper: bar-synced record / loop / overdub of the master output.

Records what you hear (the summed bus after all chains and drums, before the
master volume node reads it) into a server-side buffer sized to a whole
number of bars at the tempo when recording starts. Recording arms and starts
at the NEXT DOWNBEAT; after N bars it flips seamlessly into looped playback.
Overdub keeps the record head running with feedback < 1 so layers pile up
tape-style. All timing rides the shared transport.
"""

from __future__ import annotations

import threading
import time

from supriya import AddAction, synthdef
from supriya.ugens import In, Lag, Out, PlayBuf, RecordBuf

STATES = ("empty", "armed", "recording", "playing", "overdubbing", "stopped")


@synthdef()
def _loop_rec(buf=0, run=1, pre=0.0, out=0):
    sig = In.ar(bus=0, channel_count=2)
    RecordBuf.ar(source=sig, buffer_id=buf, loop=1, run=run,
                 preexisting_level=pre, record_level=1.0)
    Out.ar(bus=out, source=[0.0, 0.0])  # silent; exists for its side effect


@synthdef()
def _loop_play(buf=0, level=0.9, out=0):
    sig = PlayBuf.ar(channel_count=2, buffer_id=buf, loop=1)
    Out.ar(bus=out, source=sig * Lag.kr(source=level, lag_time=0.05))


class Looper:
    def __init__(self, app) -> None:
        self.app = app
        self.state = "empty"
        self.bars = 2
        self.level = 0.9
        self.overdub = False
        self._buffer = None
        self._rec_node = None
        self._play_node = None
        self._registered = False
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None

    # -- public API -------------------------------------------------------------

    def configure(self, action=None, bars=None, level=None, overdub=None) -> None:
        with self._lock:
            if bars is not None and self.state in ("empty", "stopped"):
                self.bars = min(8, max(1, int(bars)))
            if level is not None:
                self.level = min(1.0, max(0.0, float(level)))
                if self._play_node is not None:
                    try:
                        self._play_node.set(level=self.level)
                    except Exception:  # noqa: BLE001
                        pass
            if overdub is not None:
                self._set_overdub(bool(overdub))
            if action == "record":
                self._arm()
            elif action == "stop":
                self._stop()
            elif action == "play":
                self._play()
            elif action == "clear":
                self._clear()

    def settings(self) -> dict:
        return {"state": self.state, "bars": self.bars, "level": self.level,
                "overdub": self.overdub}

    def shutdown(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._teardown_nodes()
            if self._buffer is not None:
                try:
                    self._buffer.free()
                except Exception:  # noqa: BLE001
                    pass
                self._buffer = None
            self.state = "empty"

    # -- internals -----------------------------------------------------------------

    def _ensure_registered(self) -> None:
        if not self._registered and self.app.engine and self.app.engine.server:
            self.app.engine.server.add_synthdefs(_loop_rec, _loop_play)
            self.app.engine.server.sync()
            self._registered = True

    def reset(self) -> None:
        self._registered = False

    def _schedule(self, delay: float, fn) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(max(0.0, delay), fn)
        self._timer.daemon = True
        self._timer.start()

    def _arm(self) -> None:
        if self.state in ("armed", "recording"):
            return
        self._ensure_registered()
        transport = self.app.transport
        server = self.app.engine.server
        self._teardown_nodes()
        # size the buffer to N bars at the current tempo
        try:
            sr = float(server.status.actual_sample_rate)
        except Exception:  # noqa: BLE001
            sr = float(self.app.engine.options.sample_rate or 44100)
        frames = int(
            self.bars * transport.beats_per_bar * transport.beat_duration * sr
        )
        if self._buffer is not None:
            try:
                self._buffer.free()
            except Exception:  # noqa: BLE001
                pass
        self._buffer = server.add_buffer(channel_count=2, frame_count=max(frames, 1024))
        server.sync()
        # start recording at the next downbeat
        bar_beats = float(transport.beats_per_bar)
        _, t_start = transport.next_grid(bar_beats)
        self.state = "armed"
        self._emit()

        def start_recording():
            with self._lock:
                if self.state != "armed":
                    return
                try:
                    self._rec_node = self.app.engine.root_group.add_synth(
                        _loop_rec, add_action=AddAction.ADD_TO_TAIL,
                        buf=int(self._buffer), run=1, pre=0.0,
                    )
                    self.state = "recording"
                    self._emit()
                except Exception as exc:  # noqa: BLE001
                    print(f"[looper] record failed: {exc}")
                    self.state = "empty"
                    return
                loop_dur = self.bars * bar_beats * self.app.transport.beat_duration
                self._schedule(loop_dur, self._finish_recording)

        self._schedule(t_start - time.monotonic(), start_recording)

    def _finish_recording(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            # flip: recorder becomes overdub-gated, player starts at loop top
            try:
                if self.overdub:
                    self._rec_node.set(pre=0.75)  # tape-style layering
                else:
                    self._rec_node.set(run=0)
                self._play_node = self.app.engine.root_group.add_synth(
                    _loop_play, add_action=AddAction.ADD_TO_TAIL,
                    buf=int(self._buffer), level=self.level,
                )
                self.state = "overdubbing" if self.overdub else "playing"
            except Exception as exc:  # noqa: BLE001
                print(f"[looper] playback failed: {exc}")
                self.state = "empty"
            self._emit()

    def _set_overdub(self, on: bool) -> None:
        self.overdub = on
        if self._rec_node is not None and self.state in ("playing", "overdubbing"):
            try:
                self._rec_node.set(run=1 if on else 0, pre=0.75 if on else 1.0)
                self.state = "overdubbing" if on else "playing"
                self._emit()
            except Exception:  # noqa: BLE001
                pass

    def _play(self) -> None:
        if self.state != "stopped" or self._buffer is None:
            return
        self._ensure_registered()
        try:
            self._play_node = self.app.engine.root_group.add_synth(
                _loop_play, add_action=AddAction.ADD_TO_TAIL,
                buf=int(self._buffer), level=self.level,
            )
            self.state = "playing"
            self._emit()
        except Exception as exc:  # noqa: BLE001
            print(f"[looper] play failed: {exc}")

    def _stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._teardown_nodes()
        self.state = "stopped" if self._buffer is not None else "empty"
        self._emit()

    def _clear(self) -> None:
        self._stop()
        if self._buffer is not None:
            try:
                self._buffer.free()
            except Exception:  # noqa: BLE001
                pass
            self._buffer = None
        self.state = "empty"
        self._emit()

    def _teardown_nodes(self) -> None:
        for attr in ("_rec_node", "_play_node"):
            node = getattr(self, attr)
            if node is not None:
                try:
                    node.free()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)

    def _emit(self) -> None:
        emit = getattr(self.app, "_emit_midi_event", None)
        if emit is not None:
            emit({"kind": "looper", **self.settings()})
