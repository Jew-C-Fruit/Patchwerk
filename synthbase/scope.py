"""Scope: capture a window of any module's output waveform for the GUI.

A one-shot probe synth (plain RecordBuf — see looper.py landmines) is
dropped just AFTER the chosen module's node, records ~46 ms of its output
bus, frees itself, and Python reads the buffer back for the browser to
draw. One capture at a time; the GUI polls while its scope is open.
"""

from __future__ import annotations

import threading
import time

from supriya import AddAction, synthdef
from supriya.ugens import In, RecordBuf

FRAMES = 2048


@synthdef()
def _probe(buf=0, bus=0):
    sig = In.ar(bus=bus, channel_count=1)
    RecordBuf.ar(source=sig, buffer_id=buf, loop=0, done_action=2,
                 record_level=1.0, preexisting_level=0.0)


class Scope:
    def __init__(self, app) -> None:
        self.app = app
        self._buf = None
        self._registered = False
        self._lock = threading.Lock()

    def reset(self) -> None:
        self._registered = False
        self._buf = None

    def capture(self, key: str) -> dict:
        with self._lock:
            server = self.app.engine.server
            rack = self.app.rack
            inst = rack.find(key)
            if not self._registered:
                server.add_synthdefs(_probe)
                server.sync()
                self._registered = True
            if self._buf is None:
                self._buf = server.add_buffer(channel_count=1, frame_count=FRAMES)
                server.sync()
            try:
                sr = float(server.status.actual_sample_rate)
            except Exception:  # noqa: BLE001
                sr = 44100.0
            bus = int(inst.settings.get("out", 0))
            server.add_synth(
                _probe, add_action=AddAction.ADD_AFTER, target_node=inst.node,
                buf=int(self._buf), bus=bus,
            )
            time.sleep(FRAMES / sr + 0.015)
            samples: list[float] = []
            for offset in range(0, FRAMES, 512):
                samples.extend(float(v) for v in self._buf.get_range(offset, 512))
            return {"key": key, "sr": sr,
                    "samples": [round(s, 4) for s in samples]}
