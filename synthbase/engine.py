"""Engine: thin lifecycle wrapper around the SuperCollider server (scsynth).

Deliberately thin — everything supriya exposes remains reachable via
``engine.server``. This class only owns boot options, the top-level group
that racks live in, and synthdef registration.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from supriya import AddAction, Options, Server

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
    ) -> None:
        self.options = Options(
            input_device=input_device,
            output_device=output_device,
            input_bus_channel_count=input_channels,
            output_bus_channel_count=output_channels,
            sample_rate=sample_rate,
            block_size=block_size,
        )
        self.server: Server | None = None
        self.root_group = None  # all racks/chains go inside this group

    # -- lifecycle ---------------------------------------------------------

    def boot(self) -> "Engine":
        _ensure_synthdef_dir()
        try:
            self.server = Server().boot(options=self.options)
        except Exception as exc:
            # macOS: the default input and output devices often run at
            # different sample rates (bluetooth headset mics especially),
            # which scsynth refuses. Fall back to output-only rather than
            # failing to start.
            if "sample rate" not in str(exc).lower():
                raise
            print(
                "[engine] input/output sample rates don't match (bluetooth "
                "headset mic?) — retrying with audio input disabled.\n"
                "[engine] to use audio input, pass --in-device with a device "
                "whose rate matches the output (see Audio MIDI Setup)."
            )
            self.options = dataclasses.replace(
                self.options, input_bus_channel_count=0, input_device=None
            )
            self.server = Server().boot(options=self.options)
        self.root_group = self.server.add_group(add_action=AddAction.ADD_TO_TAIL)
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
        """Send module synthdefs to the server and wait until they're ready."""
        assert self.server is not None, "engine not booted"
        self.server.add_synthdefs(*(m.synthdef for m in modules))
        self.server.sync()
