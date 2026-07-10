"""Flanger: short swept delay with feedback for jet-engine combing."""

from supriya import synthdef
from supriya.ugens import DelayC, In, LFPar, LocalIn, LocalOut, Out

from synthbase import module, param


@module(
    name="Flanger",
    kind="effect",
    params={
        "rate": param(0.05, 3.0, 0.25, curve="exp"),
        "depth": param(0.0, 1.0, 0.7),
        "feedback": param(0.0, 0.9, 0.4),
        "mix": param(0.0, 1.0, 0.5),
    },
)
@synthdef()
def flanger(in_bus=0, out=0, rate=0.25, depth=0.7, feedback=0.4, mix=0.5):
    dry = In.ar(bus=in_bus, channel_count=2)
    fb = LocalIn.ar(channel_count=2)
    t = 0.0015 + (LFPar.kr(frequency=rate) * 0.5 + 0.5) * 0.006 * depth
    wet = DelayC.ar(source=dry + fb * feedback, maximum_delay_time=0.02, delay_time=t)
    LocalOut.ar(source=wet)
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
