"""Scope: capture a window of any module's output waveform for the GUI.

v2 — LATENCY over refresh rate. The old probe recorded on demand: every
capture paid the full ~46 ms record window (plus a sleep and syncs) before
the first sample could ship, so note→first-render sat at poll period + ~70 ms.

Now a tiny RING synth (Phasor→BufWr, its write head mirrored to a control
bus) records each polled bus CONTINUOUSLY, and capture() just READS the
buffer — a few OSC roundtrips, no record wait. The last 2048 frames are
always already there, so a capture fired right after a note returns that
note's onset immediately.

Cost model: one 4-UGen synth + one 2048-frame buffer per key being watched,
only while it is being watched — rings idle for >3 s are reaped on the next
capture, and reset() drops everything on rack teardown.
"""

from __future__ import annotations

import threading
import time

from supriya import AddAction, CalculationRate, synthdef
from supriya.ugens import A2K, BufWr, In, Out, Phasor

FRAMES = 2048
RING_TTL = 3.0          # seconds unpolled before a ring is reaped


@synthdef()
def _ring(buf=0, bus=0, kout=0):
    sig = In.ar(bus=bus, channel_count=1)
    phase = Phasor.ar(rate=1, start=0, stop=FRAMES)
    BufWr.ar(source=sig, buffer_id=buf, phase=phase, loop=1)
    Out.kr(bus=kout, source=A2K.kr(source=phase))


class Scope:
    def __init__(self, app) -> None:
        self.app = app
        self._registered = False
        self._rings: dict[str, dict] = {}   # key -> {synth,buf,kbus,bus,last}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            for rec in self._rings.values():
                for ent, meth in ((rec.get("synth"), "free"),
                                  (rec.get("buf"), "free"),
                                  (rec.get("kbus"), "free")):
                    try:
                        getattr(ent, meth)()
                    except Exception:  # noqa: BLE001
                        pass
            self._rings = {}
            self._registered = False

    def _reap(self, now: float) -> None:
        for key in [k for k, r in self._rings.items()
                    if now - r["last"] > RING_TTL]:
            rec = self._rings.pop(key)
            for ent in (rec.get("synth"), rec.get("buf"), rec.get("kbus")):
                try:
                    ent.free()
                except Exception:  # noqa: BLE001
                    pass

    def capture(self, key: str) -> dict:
        with self._lock:
            server = self.app.engine.server
            rack = self.app.rack
            inst = rack.find(key)
            now = time.monotonic()
            self._reap(now)
            if not self._registered:
                server.add_synthdefs(_ring)
                server.sync()
                self._registered = True
            bus = int(inst.settings.get("out", 0))
            rec = self._rings.get(key)
            if rec is not None and rec["bus"] != bus:
                # module was rewired to a different bus — rebuild the ring
                for ent in (rec.get("synth"), rec.get("buf"), rec.get("kbus")):
                    try:
                        ent.free()
                    except Exception:  # noqa: BLE001
                        pass
                rec = None
                self._rings.pop(key, None)
            if rec is None:
                buf = server.add_buffer(channel_count=1, frame_count=FRAMES)
                kbus = server.add_bus(calculation_rate=CalculationRate.CONTROL)
                server.sync()
                synth = server.add_synth(
                    _ring, add_action=AddAction.ADD_AFTER,
                    target_node=inst.node,
                    buf=int(buf), bus=bus, kout=int(kbus),
                )
                rec = {"synth": synth, "buf": buf, "kbus": kbus,
                       "bus": bus, "last": now}
                self._rings[key] = rec
                # let the fresh ring fill once so the first window is real
                try:
                    sr0 = float(server.status.actual_sample_rate)
                except Exception:  # noqa: BLE001
                    sr0 = 44100.0
                time.sleep(FRAMES / sr0 + 0.01)
            rec["last"] = now
            try:
                sr = float(server.status.actual_sample_rate)
            except Exception:  # noqa: BLE001
                sr = 44100.0
            try:
                head = int(rec["kbus"].get()) % FRAMES
            except Exception:  # noqa: BLE001
                head = 0
            samples: list[float] = []
            for offset in range(0, FRAMES, 512):
                samples.extend(float(v)
                               for v in rec["buf"].get_range(offset, 512))
            ordered = samples[head:] + samples[:head]   # oldest → newest
            return {"key": key, "sr": sr,
                    "samples": [round(s, 4) for s in ordered]}
