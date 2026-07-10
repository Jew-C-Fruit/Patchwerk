"""Bitcrusher: sample-rate and bit-depth reduction, lo-fi to gravel."""

from supriya import synthdef
from supriya.ugens import In, Lag, Latch, Impulse, Out

from synthbase import module, param


@module(
    name="Bitcrush",
    kind="effect",
    params={
        "srate": param(400, 44100, 8000, curve="exp"),   # resample frequency
        "bits": param(2, 16, 10),
        "mix": param(0.0, 1.0, 1.0),
    },
)
@synthdef()
def bitcrush(in_bus=0, out=0, srate=8000, bits=10, mix=1.0):
    dry = In.ar(bus=in_bus, channel_count=2)
    held = Latch.ar(source=dry, trigger=Impulse.ar(frequency=Lag.kr(source=srate, lag_time=0.05)))
    steps = (2 ** bits) * 0.5
    crushed = (held * steps).round(1.0) / steps
    Out.ar(bus=out, source=dry * (1 - mix) + crushed * mix)
