"""Soft-clip drive/distortion with tone control and dry/wet mix."""

from supriya import synthdef
from supriya.ugens import In, LPF, Lag, Out

from synthbase import module, param


@module(
    name="Drive",
    kind="effect",
    params={
        "gain": param(1.0, 40.0, 4.0, curve="exp"),
        "tone": param(500, 12000, 4000, curve="exp"),  # post-clip low-pass
        "mix": param(0.0, 1.0, 1.0),
    },
)
@synthdef()
def drive(in_bus=0, out=0, gain=4.0, tone=4000, mix=1.0):
    dry = In.ar(bus=in_bus, channel_count=2)
    g = Lag.kr(source=gain, lag_time=0.02)
    wet = (dry * g).tanh() * 0.7           # soft clip, tamed
    wet = LPF.ar(source=wet, frequency=Lag.kr(source=tone, lag_time=0.02))
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
