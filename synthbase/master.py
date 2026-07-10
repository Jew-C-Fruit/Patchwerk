"""Master section: volume control and level meters.

Two service synths that sit AFTER the rack in execution order:

- ``_master``: reads the hardware output bus, applies smoothed master
  volume in place (ReplaceOut), and writes post-volume peak levels to
  control buses the GUI polls.
- ``_input_meter``: writes the hardware input level to a control bus.

These are infrastructure, not modules — they never appear in a patch.
"""

from __future__ import annotations

from supriya import AddAction, synthdef
from supriya.ugens import Amplitude, In, Lag, Limiter, Out, ReplaceOut

from .engine import Engine


@synthdef()
def _master(vol=0.8, meter_bus=0):
    sig = In.ar(bus=0, channel_count=2)
    sig = sig * Lag.kr(source=vol, lag_time=0.05)
    # Seatbelt: nothing (feedback, looper stacking, LFO extremes) may scream.
    sig = Limiter.ar(source=sig, level=0.95, duration=0.005)
    ReplaceOut.ar(bus=0, source=sig)
    Out.kr(bus=meter_bus, source=Amplitude.kr(source=sig, release_time=0.2))


@synthdef()
def _input_meter(meter_bus=0, hw_bus=2):
    sig = In.ar(bus=hw_bus, channel_count=1)
    Out.kr(bus=meter_bus, source=Amplitude.kr(source=sig, release_time=0.2))


class MasterSection:
    """Owns the master/meter nodes and buses on a booted engine."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.volume = 0.8
        self.out_meter_buses = None
        self.in_meter_bus = None
        self._master_node = None
        self._input_meter_node = None

    def start(self) -> None:
        server = self.engine.server
        server.add_synthdefs(_master, _input_meter)
        server.sync()

        self.out_meter_buses = server.add_bus_group(calculation_rate="control", count=2)
        # Master goes after the root group so it processes the rack's output.
        self._master_node = server.add_synth(
            _master,
            add_action=AddAction.ADD_AFTER,
            target_node=self.engine.root_group,
            vol=self.volume,
            meter_bus=int(self.out_meter_buses),
        )
        if self.engine.options.input_bus_channel_count > 0:
            self.in_meter_bus = server.add_bus(calculation_rate="control")
            self._input_meter_node = server.add_synth(
                _input_meter,
                add_action=AddAction.ADD_AFTER,
                target_node=self._master_node,
                meter_bus=int(self.in_meter_bus),
                hw_bus=self.engine.options.output_bus_channel_count,
            )

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))
        if self._master_node is not None:
            self._master_node.set(vol=self.volume)

    def levels(self) -> dict:
        """Poll meter buses. Returns {"out": [l, r], "in": x | None}."""
        out = [0.0, 0.0]
        inp = None
        try:
            if self.out_meter_buses is not None:
                out = [float(b.get()) for b in self.out_meter_buses]
            if self.in_meter_bus is not None:
                inp = float(self.in_meter_bus.get())
        except Exception:
            pass  # a missed meter frame is fine
        return {"out": out, "in": inp}

    def stop(self) -> None:
        for node in (self._master_node, self._input_meter_node):
            if node is not None:
                node.free()
        self._master_node = self._input_meter_node = None
        self.out_meter_buses = self.in_meter_bus = None
