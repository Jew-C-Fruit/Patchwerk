"""Ring modulator: metallic robot voices and inharmonic bells."""

from supriya import synthdef
from supriya.ugens import In, Lag, Out, SinOsc

from synthbase import module, param


@module(
    name="Ring Mod",
    kind="effect",
    params={
        "carrier": param(20, 4000, 200, curve="exp"),
        "mix": param(0.0, 1.0, 0.8),
    },
)
@synthdef()
def ringmod(in_bus=0, out=0, carrier=200, mix=0.8):
    dry = In.ar(bus=in_bus, channel_count=2)
    wet = dry * SinOsc.ar(frequency=Lag.kr(source=carrier, lag_time=0.03))
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
