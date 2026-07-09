"""Resonant low-pass filter — the canonical effect module.

Effects read stereo from `in_bus` and write stereo to `out`.
`cutoff` is the classic thing to bind to a MIDI knob or mod wheel.
"""

from supriya import synthdef
from supriya.ugens import In, Lag, Out, RLPF

from synthbase import module, param


@module(
    name="Low-pass Filter",
    kind="effect",
    params={
        "cutoff": param(60, 12000, 1200, curve="exp"),
        "resonance": param(0.1, 1.0, 0.5),
    },
)
@synthdef()
def lowpass(in_bus=0, out=0, cutoff=1200, resonance=0.5):
    sig = In.ar(bus=in_bus, channel_count=2)
    smooth_cutoff = Lag.kr(source=cutoff, lag_time=0.02)  # de-zipper knob moves
    Out.ar(bus=out, source=RLPF.ar(source=sig, frequency=smooth_cutoff, reciprocal_of_q=resonance))
