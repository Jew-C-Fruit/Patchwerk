"""Compressor: tames peaks, raises perceived loudness."""

from supriya import synthdef
from supriya.ugens import Compander, In, Out

from synthbase import module, param


@module(
    name="Compressor",
    kind="effect",
    params={
        "threshold": param(0.01, 1.0, 0.3, curve="exp"),
        "ratio": param(1.0, 20.0, 4.0, curve="exp"),   # n:1 above threshold
        "attack": param(0.001, 0.2, 0.01, curve="exp"),
        "release": param(0.02, 1.0, 0.15, curve="exp"),
        "makeup": param(0.5, 4.0, 1.3, curve="exp"),
    },
)
@synthdef()
def compressor(in_bus=0, out=0, threshold=0.3, ratio=4.0, attack=0.01, release=0.15, makeup=1.3):
    dry = In.ar(bus=in_bus, channel_count=2)
    squeezed = Compander.ar(
        source=dry, control=dry, threshold=threshold,
        slope_below=1.0, slope_above=1.0 / ratio,
        clamp_time=attack, relax_time=release,
    )
    Out.ar(bus=out, source=squeezed * makeup)
