"""Phaser: cascaded allpass sweeps notch a moving comb into the sound."""

from supriya import synthdef
from supriya.ugens import AllpassL, In, LFPar, Out

from synthbase import module, param


@module(
    name="Phaser",
    kind="effect",
    params={
        "rate": param(0.05, 4.0, 0.3, curve="exp"),
        "depth": param(0.0, 1.0, 0.8),
        "mix": param(0.0, 1.0, 0.5),
    },
)
@synthdef()
def phaser(in_bus=0, out=0, rate=0.3, depth=0.8, mix=0.5):
    dry = In.ar(bus=in_bus, channel_count=2)
    sweep = (LFPar.kr(frequency=rate) * 0.5 + 0.5) * depth
    wet = dry
    for i in range(4):
        t = 0.0002 + sweep * 0.004 * (1 + i * 0.35)
        wet = AllpassL.ar(source=wet, maximum_delay_time=0.01,
                          delay_time=t, decay_time=0.0)
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
