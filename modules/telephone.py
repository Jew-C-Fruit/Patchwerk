"""Telephone/radio: band-limited, gently crunched voice."""

from supriya import synthdef
from supriya.ugens import BPF, HPF, In, LPF, Out

from synthbase import module, param


@module(
    name="Telephone",
    kind="effect",
    params={
        "low": param(100, 1200, 380, curve="exp"),
        "high": param(1200, 8000, 3200, curve="exp"),
        "crunch": param(1.0, 12.0, 3.0, curve="exp"),
        "mix": param(0.0, 1.0, 1.0),
    },
)
@synthdef()
def telephone(in_bus=0, out=0, low=380, high=3200, crunch=3.0, mix=1.0):
    dry = In.ar(bus=in_bus, channel_count=2)
    wet = HPF.ar(source=LPF.ar(source=dry, frequency=high), frequency=low)
    wet = (wet * crunch).softclip() * 0.7
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
