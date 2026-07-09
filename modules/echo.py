"""Feedback echo/delay effect."""

from supriya import synthdef
from supriya.ugens import CombL, In, Out

from synthbase import module, param


@module(
    name="Echo",
    kind="effect",
    params={
        "time": param(0.02, 2.0, 0.375),
        "feedback": param(0.0, 0.95, 0.4),
        "mix": param(0.0, 1.0, 0.35),
    },
)
@synthdef()
def echo(in_bus=0, out=0, time=0.375, feedback=0.4, mix=0.35):
    dry = In.ar(bus=in_bus, channel_count=2)
    # decay_time is how long the echo tail rings; derive it from feedback.
    decay = time * (1 + feedback * 12)
    wet = CombL.ar(source=dry, maximum_delay_time=2.0, delay_time=time, decay_time=decay)
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
