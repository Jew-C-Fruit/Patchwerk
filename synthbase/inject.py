"""Play an audio FILE onto the audio-in bus, as if it were the microphone.

Job 2 of "make audio testable without hardware in the loop": feed known
signal into the input path so an input-processing chain can be checked
against something whose answer we already know.

**Why the input BUS and not a new module.** `modules/audio_in.py` reads one
channel at `NumOutputBuses.ir()` — that IS the audio-in device, as far as
the graph is concerned. Writing there means every existing patch, wire and
effect downstream sees the file exactly where it would have seen the mic,
with no rewiring, no patch edit and no module to keep in sync. A `file_in`
source module would have been a second, parallel notion of "input" that
patches would have to opt into.

`ReplaceOut`, not `Out`: when the machine DOES have a live input device,
the driver has already written the mic into these buses by the time the
graph runs, and summing would give you the file plus the room. Replacing
is what "as if it were the audio in" means.

## The bus-collision problem, and why the engine has a stub

With hardware input enabled, scsynth reserves input buses and supriya's
allocator starts the private pool after them — bus 2..3 are the mic and
nothing else can be handed them.

With input DISABLED (`-i 0`, which is what a TCC-disclaimed agent session
gets — see `audio_session`), `first_private_bus_id` is **2**. So
`NumOutputBuses.ir()` is also 2, and the first private bus the rack
allocates lands on the very index `audio_in` reads. That is a real
pre-existing hazard, not one this file introduces: with input off,
`audio_in` today reads whatever rack stage happens to own bus 2.

`Engine._reserve_input_stub` closes it by claiming those two channels
before the rack allocates anything. The injector then writes a bus that is
genuinely nobody else's, in both configurations.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from supriya import AddAction, synthdef
from supriya.ugens import BufRateScale, NumOutputBuses, PlayBuf, ReplaceOut

from .capture import _free as free_entities
from .wavio import wav_info

#: Longest file we will pull into scsynth's memory. Test signal is seconds
#: long; a caller reaching for a 20-minute file wants a different tool.
MAX_SECONDS = 300.0


def _inject_body(channel_count: int):
    def body(buf=0, gain=1.0, loop=0.0, start=0.0):
        sig = PlayBuf.ar(
            buffer_id=buf,
            # File and server sample rates differ constantly (48k engine,
            # 44.1k file). BufRateScale is the conversion; without it a
            # 44.1k file plays 8.8% sharp and every frequency assertion
            # made against it is quietly wrong.
            rate=BufRateScale.kr(buffer_id=buf),
            loop=loop,
            start_position=start,
            done_action=0,        # freed explicitly; a self-freeing node
            channel_count=channel_count,   # would race the caller's stop()
        ) * gain
        if channel_count == 1:
            sig = [sig, sig]
        ReplaceOut.ar(bus=NumOutputBuses.ir(), source=sig)
    body.__name__ = f"_inject{channel_count}"
    return body


_inject1 = synthdef()(_inject_body(1))
_inject2 = synthdef()(_inject_body(2))


class InputInjector:
    """Owns the file buffer and the player node on the running engine.

    One injection at a time — `ReplaceOut` means a second player would just
    be fighting the first for the same bus, and "which file am I hearing"
    is not a question worth being able to ask.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._lock = threading.RLock()
        # The OBJECT, not a boolean: `set_devices` builds a new scsynth and
        # a flag would silently skip re-sending the synthdefs to it.
        self._registered_server = None
        self._active: dict | None = None

    # -- lifecycle ---------------------------------------------------------

    def _server(self):
        server = self.app.engine.server if self.app.engine else None
        if server is None:
            raise RuntimeError("no engine — injection needs a booted server")
        if self._registered_server is not server:
            server.add_synthdefs(_inject1, _inject2)
            server.sync()
            self._registered_server = server
        return server

    def reset(self) -> None:
        """Engine going away: drop the node/buffer and forget registration."""
        with self._lock:
            self._free()
            self._registered_server = None

    def _free(self) -> None:
        rec, self._active = self._active, None
        if not rec:
            return
        # Forced, via capture.py's shared helper — see its docstring and
        # `rack.py:_free_node_note`. An injector node that survived its own
        # free would go on writing the audio-in bus with nothing on screen
        # to stop it.
        free_entities(rec.get("synth"), rec.get("buf"))

    # -- playing -----------------------------------------------------------

    def play(self, path: str, gain: float = 1.0, loop: bool = False) -> dict:
        """Start `path` on the audio-in bus. Returns file info + state."""
        with self._lock:
            src = Path(path).expanduser()
            if not src.is_file():
                raise FileNotFoundError(f"no such audio file: {src}")
            info = wav_info(src)
            if info["duration"] > MAX_SECONDS:
                raise ValueError(
                    f"{src.name} is {info['duration']:.0f}s; the injector "
                    f"caps at {MAX_SECONDS:.0f}s")
            if info["channels"] not in (1, 2):
                raise ValueError(
                    f"{src.name} has {info['channels']} channels; "
                    f"the audio-in bus is mono or stereo")

            self._free()                       # a second play replaces the first
            server = self._server()
            buf = server.add_buffer(file_path=str(src))
            server.sync()
            sdef = _inject1 if info["channels"] == 1 else _inject2
            # BEFORE the root group, not at its head: `reorder_for_wires`
            # sorts root-group children and would happily move a foreign
            # node out of first place. A sibling ahead of the group is
            # outside the sort and always runs first.
            synth = server.add_synth(
                sdef, add_action=AddAction.ADD_BEFORE,
                target_node=self.app.engine.root_group,
                buf=int(buf), gain=float(gain), loop=1.0 if loop else 0.0,
            )
            self._active = {
                "path": str(src), "synth": synth, "buf": buf,
                "loop": bool(loop), "gain": float(gain),
                "t0": time.monotonic(), **info,
            }
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._free()
            return {"playing": False}

    def set_gain(self, gain: float) -> dict:
        with self._lock:
            if self._active:
                self._active["gain"] = float(gain)
                self._active["synth"].set(gain=float(gain))
            return self.status()

    def status(self) -> dict:
        with self._lock:
            rec = self._active
            if rec is None:
                return {"playing": False, "input_bus": self.input_bus()}
            elapsed = time.monotonic() - rec["t0"]
            return {
                "playing": True,
                "path": rec["path"],
                "channels": rec["channels"],
                "sample_rate": rec["sample_rate"],
                "duration": round(rec["duration"], 6),
                "elapsed": round(elapsed, 3),
                "loop": rec["loop"],
                "gain": rec["gain"],
                "finished": (not rec["loop"]) and elapsed >= rec["duration"],
                "input_bus": self.input_bus(),
            }

    def input_bus(self) -> int:
        """The bus `audio_in` reads — i.e. where the file is being written."""
        engine = self.app.engine
        if engine is None:
            return 2
        return int(engine.options.output_bus_channel_count)

    # -- convenience -------------------------------------------------------

    def play_and_wait(self, path: str, gain: float = 1.0,
                      pad: float = 0.1) -> dict:
        """Play once, block until it finishes, stop. Never loops.

        Blocks — keep it off the event loop.
        """
        st = self.play(path, gain=gain, loop=False)
        time.sleep(st["duration"] + pad)
        self.stop()
        return st
