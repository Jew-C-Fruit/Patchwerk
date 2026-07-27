"""The internal listener: record the running rig's audio to a WAV.

Build plan Phase 4. This is the thing that lets an agent HEAR Patchwerk
without a microphone and without anything acoustic — the master bus is
recorded inside scsynth and written to disk, and `tests/analysis.py` turns
that into numbers.

It has to live in the engine. There is no outside-in way to record
scsynth's master bus: the signal never leaves the server except through the
audio device. The build plan says so plainly (§6.2) and this is the cost.

**How it relates to the meters.** `MasterSection` already exposes an
`Amplitude.kr` reading of the post-volume output bus at 20 Hz, and
`tests/audio_proof.py` uses it to prove real sound is happening. That is a
LEVEL — one smoothed number per frame. It answers "is it loud"; it cannot
answer "at what frequency", "did the tail decay honestly", "was there a
click", or "is the second harmonic where the law says". This records the
SAMPLES, so those questions become arithmetic. The meter stays; it is the
cheap continuous sense and this is the expensive precise one.

Three constraints shape the implementation:

* **`RecordBuf` is a documented scsynth crasher when misused** — a SCALED
  source and an EnvGen-driven `record_level` are both in `CLAUDE.md`'s
  landmine list. So the source is passed through untouched and the record
  level is the literal 1.0. Start/stop is the `run` input and the synth's
  own lifetime, never an envelope.
* **Registration must track the server OBJECT, not a boolean.** A device
  switch builds a NEW scsynth, and a manager that remembers "already sent"
  as a flag silently never re-sends. Same pattern as `LFOManager` and
  `ThresholdManager`.
* **Node placement must survive `reorder_for_wires`.** The topo-sort moves
  rack instance nodes around freely, so a capture synth placed after an
  instance would be relocated out from under its source. Every capture node
  therefore sits AFTER the master section — outside the root group, where
  the sort cannot reach it — and simply reads whichever BUS it was asked
  about. Buses hold their block's value, so reading one at the tail of the
  graph is correct for any writer earlier in the same block.

  One honest consequence: capturing an instance whose `out` is the hardware
  bus gives you the POST-master signal, because `_master`'s `ReplaceOut`
  has already rewritten bus 0 by then. Capture `master` if that is what you
  meant; capture an instance in the middle of a chain and you get its own
  pre-master output.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from supriya import AddAction, CalculationRate, synthdef
from supriya.ugens import A2K, In, Out, Phasor, RecordBuf

#: Ceiling on a single capture, in seconds. A stereo float buffer costs
#: ~350 kB/s of scsynth's real-time memory pool; 120 s is ~42 MB, which is
#: comfortably inside the default and far longer than any check needs.
MAX_SECONDS = 120.0

DEFAULT_SECONDS = 4.0

#: Where captures land unless the caller names a path.
CAPTURE_DIR = Path("/tmp/patchwerk-captures")


def _free(*entities) -> None:
    """Free nodes, buffers and buses, forcing NODES.

    supriya's `Node.free()` emits `/n_set gate 0` for any synth that HAS a
    gate and `/n_free` only for one without — `rack.py`'s `_free_node_note`
    has the full account and the node counts that proved it. Neither synth
    here owns a gate today, so an unforced free would work; forcing anyway
    means that adding one later cannot silently convert "the capture is
    over" into "the capture synth runs forever". Buffers and buses take no
    `force`, hence the two arms.
    """
    for ent in entities:
        if ent is None:
            continue
        try:
            ent.free(force=True)
        except TypeError:
            try:
                ent.free()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass


@synthdef()
def _capture(buf=0, bus=0, phase_out=0):
    """Record a stereo bus into `buf`, mirroring the write head to a k-bus.

    The mirror is how we learn the EXACT number of frames recorded, so the
    WAV can be trimmed to what was actually captured instead of carrying a
    tail of buffer zeros that would drag every RMS measurement down.

    `Phasor`'s `stop` is deliberately far past the buffer end: the phasor
    wraps at `stop` and `RecordBuf` (loop=0) simply halts at the end of the
    buffer, so a phasor that wrapped would report a tiny frame count for a
    capture that actually completed. Running it long means "phase greater
    than the buffer" unambiguously reads as "the buffer filled".
    """
    sig = In.ar(bus=bus, channel_count=2)
    RecordBuf.ar(
        buffer_id=buf,
        source=sig,            # UNSCALED — a scaled source crashes scsynth
        record_level=1.0,      # a literal, never an EnvGen
        preexisting_level=0.0,
        loop=0,
        run=1,
        trigger=1,
        done_action=0,         # the synth outlives the recording; we free it
    )
    Out.kr(bus=phase_out, source=A2K.kr(source=Phasor.ar(rate=1, start=0,
                                                        stop=1e9)))


class CaptureManager:
    """Arm/disarm one capture at a time on the running engine.

    One at a time on purpose: two concurrent captures of the same bus is a
    question nobody has, and serialising means `stop()` never has to guess
    which recording a caller meant.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._lock = threading.RLock()
        # See the module docstring: the OBJECT, not a boolean. `is not`
        # comparison against the live server is what makes a device-switch
        # reboot re-send the synthdef.
        self._registered_server = None
        self._active: dict | None = None

    # -- lifecycle ---------------------------------------------------------

    def _server(self):
        server = self.app.engine.server if self.app.engine else None
        if server is None:
            raise RuntimeError("no engine — capture needs a booted server")
        if self._registered_server is not server:
            server.add_synthdefs(_capture)
            server.sync()
            self._registered_server = server
        return server

    def reset(self) -> None:
        """Engine is going away. Drop the node and forget registration."""
        with self._lock:
            self._free_nodes()
            self._active = None
            self._registered_server = None

    def _free_nodes(self) -> None:
        rec = self._active
        if not rec:
            return
        _free(rec.get("synth"), rec.get("buf"), rec.get("kbus"))

    # -- the tap -----------------------------------------------------------

    def _resolve_bus(self, target: str) -> int:
        """`"master"` -> the hardware output bus; anything else -> instance."""
        if target in ("master", "out", "", None):
            return 0
        inst = self.app.rack.find(target) if self.app.rack else None
        if inst is None:
            raise KeyError(f"no module instance {target!r} to capture")
        return int(inst.settings.get("out", 0))

    def arm(self, target: str = "master", seconds: float = DEFAULT_SECONDS,
            path: str | None = None) -> dict:
        """Start recording `target`. Returns the capture record."""
        with self._lock:
            if self._active is not None:
                raise RuntimeError(
                    "a capture is already armed — stop it before arming another")
            seconds = max(0.05, min(float(seconds), MAX_SECONDS))
            server = self._server()
            bus = self._resolve_bus(target)
            try:
                sr = float(server.status.actual_sample_rate)
            except Exception:  # noqa: BLE001
                sr = 44100.0
            frames = int(sr * seconds)

            buf = server.add_buffer(channel_count=2, frame_count=frames)
            kbus = server.add_bus(calculation_rate=CalculationRate.CONTROL)
            server.sync()
            # AFTER the master section, so `reorder_for_wires` (which only
            # sorts inside the root group) can never move this node.
            anchor = self._anchor()
            synth = server.add_synth(
                _capture, add_action=AddAction.ADD_AFTER, target_node=anchor,
                buf=int(buf), bus=bus, phase_out=int(kbus),
            )
            self._active = {
                "target": target, "bus": bus, "seconds": seconds,
                "frames": frames, "sr": sr, "path": path,
                "synth": synth, "buf": buf, "kbus": kbus,
                "t0": time.monotonic(),
            }
            return self.status()

    def _anchor(self):
        """The node every capture sits after: master if there is one."""
        master = getattr(self.app, "master", None)
        node = getattr(master, "_master_node", None) if master else None
        return node if node is not None else self.app.engine.root_group

    def status(self) -> dict:
        with self._lock:
            rec = self._active
            if rec is None:
                return {"armed": False}
            return {
                "armed": True,
                "target": rec["target"],
                "bus": rec["bus"],
                "seconds": rec["seconds"],
                "elapsed": round(time.monotonic() - rec["t0"], 3),
                "path": rec["path"],
            }

    def stop(self, path: str | None = None) -> dict:
        """Stop recording and write a WAV. Returns {path, frames, sr, ...}.

        The frame count comes from the mirrored write head, so the file is
        exactly as long as the audio that was actually recorded. A capture
        left running past its buffer simply reports the full buffer.
        """
        with self._lock:
            rec = self._active
            if rec is None:
                raise RuntimeError("no capture is armed")
            server = self.app.engine.server
            try:
                phase = int(rec["kbus"].get())
            except Exception:  # noqa: BLE001
                phase = int((time.monotonic() - rec["t0"]) * rec["sr"])
            written = max(1, min(phase, rec["frames"]))

            # Free the recorder BEFORE the write so nothing is appending to
            # the buffer while scsynth reads it out.
            _free(rec["synth"])
            server.sync()

            out = Path(path or rec["path"] or self._default_path(rec["target"]))
            out.parent.mkdir(parents=True, exist_ok=True)
            rec["buf"].write(
                out, frame_count=written,
                header_format="wav",
                # FLOAT, not the int24 default: a pre-master instance bus can
                # legitimately exceed 0 dBFS and int24 would clip it silently,
                # turning "this module is too hot" into "this module is
                # distorted". `synthbase.wavio` reads float32 WAV.
                sample_format="float",
            )
            server.sync()
            _free(rec["buf"], rec["kbus"])
            self._active = None
            return {
                "path": str(out), "frames": written, "sr": rec["sr"],
                "channels": 2, "target": rec["target"], "bus": rec["bus"],
                "duration": round(written / rec["sr"], 6),
            }

    @staticmethod
    def _default_path(target: str) -> Path:
        stamp = time.strftime("%H%M%S")
        safe = str(target).replace("/", "_").replace(":", "_") or "master"
        return CAPTURE_DIR / f"{safe}-{stamp}.wav"

    # -- the whole thing, synchronously ------------------------------------

    def record(self, target: str = "master", seconds: float = DEFAULT_SECONDS,
               path: str | None = None) -> dict:
        """Arm, wait, stop. For callers with nothing to do meanwhile.

        Blocks — call it off the event loop. The wait is padded by one
        control block so the last samples are in the buffer before the
        recorder is freed.
        """
        self.arm(target=target, seconds=seconds, path=path)
        time.sleep(seconds + 0.05)
        return self.stop()
