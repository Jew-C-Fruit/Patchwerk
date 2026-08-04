"""Engine: thin lifecycle wrapper around the SuperCollider server (scsynth).

Deliberately thin — everything supriya exposes remains reachable via
``engine.server``. This class only owns boot options, the top-level group
that racks live in, and synthdef registration.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

from supriya import AddAction, Options, Server, find_free_port

from .audio_devices import find_rate_matched_input
from .audio_session import resolve as resolve_devices
from .module import Module


def _ensure_synthdef_dir() -> None:
    """scsynth refuses to boot if its default synthdef dir is missing."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "SuperCollider"
    else:
        base = Path.home() / ".local" / "share" / "SuperCollider"
    (base / "synthdefs").mkdir(parents=True, exist_ok=True)


class Engine:
    def __init__(
        self,
        input_device: str | None = None,
        output_device: str | None = None,
        input_channels: int = 2,
        output_channels: int = 2,
        sample_rate: int | None = None,
        block_size: int = 64,
        hardware_buffer_size: int | None = 256,  # frames; ~5 ms @ 48 kHz
    ) -> None:
        self.options = Options(
            port=find_free_port(),  # never collide with a stale scsynth
            input_device=input_device,
            output_device=output_device,
            input_bus_channel_count=input_channels,
            output_bus_channel_count=output_channels,
            sample_rate=sample_rate,
            block_size=block_size,
            hardware_buffer_size=hardware_buffer_size,
        )
        self.server: Server | None = None
        self.root_group = None  # all racks/chains go inside this group
        self.boot_note: str | None = None  # human-readable boot fallback info
        # the reserved audio-in stub, when input is off (see _reserve_input_stub)
        self.input_stub_buses = None
        self._sent: set[str] = set()  # synthdef names already on the live server

    # -- lifecycle ---------------------------------------------------------

    def _preflight_devices(self) -> None:
        """Never hand scsynth a device configuration that cannot START.

        The CoreAudio device-start stall has NO timeout and raises NOTHING
        (see audio_session), so the fallback below — which keys off an
        exception — can never fire on it. `Server().boot()` would simply
        block forever. So the device choice is settled BEFORE we boot, by
        probing a throwaway scsynth rather than by catching a failure that
        never arrives.
        """
        in_dev, out_dev, in_ch, note = resolve_devices(
            self.options.input_device,
            self.options.output_device,
            self.options.input_bus_channel_count,
        )
        if note:
            self.boot_note = note
            print(f"[engine] {note}")
        self.options = dataclasses.replace(
            self.options,
            input_device=in_dev,
            output_device=out_dev,
            input_bus_channel_count=in_ch,
        )

    def boot(self) -> "Engine":
        _ensure_synthdef_dir()
        self._preflight_devices()
        try:
            self.server = Server().boot(options=self.options)
        except Exception as exc:
            # macOS: the default input and output devices often run at
            # different sample rates (bluetooth headset mics are locked to
            # 16 kHz), which scsynth refuses. Auto-select an input whose
            # rate matches the output; failing that, run output-only.
            if "sample rate" not in str(exc).lower():
                raise
            match = None
            if self.options.input_device is None:
                match = find_rate_matched_input(self.options.output_device)
            if match:
                try:
                    self.options = dataclasses.replace(
                        self.options, input_device=match
                    )
                    self.server = Server().boot(options=self.options)
                    self.boot_note = (
                        f"default input's sample rate can't pair with the "
                        f"output — using {match!r} instead"
                    )
                    print(f"[engine] {self.boot_note}")
                except Exception:  # noqa: BLE001
                    match = None
            if not match:
                print(
                    "[engine] no input device matches the output's sample "
                    "rate — running with audio input disabled."
                )
                self.boot_note = (
                    "audio input disabled (no device matches the output's "
                    "sample rate — see Audio MIDI Setup)"
                )
                self.options = dataclasses.replace(
                    self.options, input_bus_channel_count=0, input_device=None
                )
                self.server = Server().boot(options=self.options)
        self.root_group = self.server.add_group(add_action=AddAction.ADD_TO_TAIL)
        self._reserve_input_stub()
        self._sent = set()   # fresh server: nothing sent yet
        return self

    def _reserve_input_stub(self) -> None:
        """Claim the audio-in bus indices when there is no input device.

        `modules/audio_in.py` reads `NumOutputBuses.ir()` — bus 2 in the
        default 2-out configuration — because with hardware input enabled
        that is the microphone. With input DISABLED, scsynth allocates no
        input buses, so supriya's private pool *starts* at 2
        (`Options.first_private_bus_id`), and the first bus the rack asks
        for is handed the exact index `audio_in` reads. An Audio In module
        in that configuration hears a rack stage instead of nothing.

        Allocating the group here — before the rack exists, so it wins the
        race — turns that index into a genuinely private, permanently
        silent bus. It also gives `InputInjector` somewhere to write that
        collides with nobody, which is the whole reason a file can stand in
        for the microphone on a machine that cannot open one.

        Two audio buses out of ~1022. Not held when real input exists,
        because then scsynth already reserves them.
        """
        self.input_stub_buses = None
        if self.options.input_bus_channel_count > 0:
            return
        try:
            group = self.server.add_bus_group(
                calculation_rate="audio",
                count=max(2, self.options.output_bus_channel_count),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[engine] could not reserve the audio-in stub bus: {exc!r}")
            return
        first = int(group)
        if first != self.options.output_bus_channel_count:
            # The pool did not start where we predicted, so this group is
            # NOT the bus audio_in reads and holding it would just waste
            # two buses while leaving the collision in place. Say so.
            print(f"[engine] audio-in stub landed on bus {first}, expected "
                  f"{self.options.output_bus_channel_count} — not reserving")
            try:
                group.free()
            except Exception:  # noqa: BLE001
                pass
            return
        self.input_stub_buses = group

    def quit(self) -> None:
        if self.server is not None:
            self.server.quit()
            self.server = None
            self.root_group = None

    @property
    def booted(self) -> bool:
        return self.server is not None

    # -- synthdefs -----------------------------------------------------------

    def register(self, *modules: Module) -> None:
        """Send module synthdefs to the server and wait until they're ready.

        Each synthdef is sent AT MOST ONCE per server: a def already on the
        server is skipped, and if none are new we return without a sync() at
        all. That sync is a blocking round-trip — doing it on every module add
        (even for a type already loaded) was the residual add-lag, worst when
        the server is busy streaming scopes."""
        assert self.server is not None, "engine not booted"

        def _name(m: Module) -> str:
            return getattr(m.synthdef, "effective_name", None) or m.key

        fresh = [m for m in modules if _name(m) not in self._sent]
        if not fresh:
            return  # every synthdef already live — no send, no sync, instant
        # /d_recv rides a UDP datagram; scsynth silently DROPS oversized ones
        # (~8k), and the sync() below then blocks forever. Big synthdefs
        # (e.g. additive banks) go to disk and load via /d_load instead.
        MAX_DGRAM = 8000
        small, big = [], []
        for m in fresh:
            (big if len(m.synthdef.compile()) > MAX_DGRAM else small).append(m)
        if small:
            self.server.add_synthdefs(*(m.synthdef for m in small))
        if big:
            ddir = Path(tempfile.gettempdir()) / "patchwerk_synthdefs"
            ddir.mkdir(exist_ok=True)
            for m in big:
                path = ddir / f"{_name(m)}.scsyndef"
                path.write_bytes(m.synthdef.compile())  # full SCgf container
                self.server.send(["/d_load", str(path)])
        self.server.sync()
        self._sent.update(_name(m) for m in fresh)
