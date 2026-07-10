"""Chorus: modulated multi-tap delays thicken the signal."""

from supriya import synthdef
from supriya.ugens import DelayC, In, LFPar, Out

from synthbase import module, param


@module(
    name="Chorus",
    kind="effect",
    params={
        "rate": param(0.05, 4.0, 0.4, curve="exp"),
        "depth": param(0.0, 1.0, 0.5),
        "mix": param(0.0, 1.0, 0.4),
    },
)
@synthdef()
def chorus(in_bus=0, out=0, rate=0.4, depth=0.5, mix=0.4):
    dry = In.ar(bus=in_bus, channel_count=2)
    base = 0.012
    sweep = depth * 0.008
    t1 = base + LFPar.kr(frequency=rate) * sweep
    t2 = base + LFPar.kr(frequency=rate * 0.87, initial_phase=1.5) * sweep
    wet = [
        DelayC.ar(source=dry[0], maximum_delay_time=0.05, delay_time=t1),
        DelayC.ar(source=dry[1], maximum_delay_time=0.05, delay_time=t2),
    ]
    Out.ar(bus=out, source=dry * (1 - mix) + [w * mix for w in wet])
