"""Karplus-Strong plucked string."""

from supriya import synthdef
from supriya.ugens import Impulse, Lag, Out, PinkNoise, Pluck, Trig

from synthbase import module, param


@module(
    name="Pluck",
    kind="source",
    params={
        "freq": param(40, 1600, 220, curve="exp"),
        "decay": param(0.3, 12.0, 4.0, curve="exp"),
        "damp": param(0.0, 0.9, 0.4),      # string brightness loss
        "amp": param(0, 1, 0.35),
    },
)
@synthdef()
def pluck(freq=220, decay=4.0, damp=0.4, amp=0.35, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.005)
    trig = Trig.kr(source=gate, duration=0.01)
    sig = Pluck.ar(
        source=PinkNoise.ar() * 0.8, trigger=trig,
        maximum_delay_time=1 / 40, delay_time=1 / f,
        decay_time=decay, coefficient=damp,
    ) * (amp * 2.65)  # makeup: level-matched to the voice family at default
                      # params (probe_voice_levels_ws, 2026-07-22)
    Out.ar(bus=out, source=[sig, sig])
