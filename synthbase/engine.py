"""Engine: thin lifecycle wrapper around the SuperCollider server (scsynth).

Deliberately thin — everything supriya exposes remains reachable via
``engine.server``. This class only owns boot options, the top-level group
that racks live in, and synthdef registration.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from supriya import AddAction, Options, Server, find_free_port

from .audio_devices import find_rate_matched_input
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
        self._sent: set[str] = set()  # synthdef names already on the live server

    # -- lifecycle ---------------------------------------------------------

    def boot(self) -> "Engine":
        _ensure_synthdef_dir()
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
        self._sent = set()   # fresh server: nothing sent yet
        return self

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
        self.server.add_synthdefs(*(m.synthdef for m in fresh))
        self.server.sync()
        self._sent.update(_name(m) for m in fresh)
