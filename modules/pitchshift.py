"""Pitch shifter: granular transposition in semitones — works on vox."""

from supriya import synthdef
from supriya.ugens import In, Lag, Out, PitchShift

from synthbase import module, param


@module(
    name="Pitch Shift",
    kind="effect",
    params={
        "semitones": param(-24, 24, 0),
        "mix": param(0.0, 1.0, 1.0),
        "smear": param(0.0, 0.02, 0.002),   # time dispersion: 0 = robotic, up = airy
    },
)
@synthdef()
def pitchshift(in_bus=0, out=0, semitones=0, mix=1.0, smear=0.002):
    dry = In.ar(bus=in_bus, channel_count=2)
    ratio = Lag.kr(source=semitones, lag_time=0.05).semitones_to_ratio()
    wet = PitchShift.ar(source=dry, window_size=0.12, pitch_ratio=ratio,
                        pitch_dispersion=0.0, time_dispersion=smear)
    Out.ar(bus=out, source=dry * (1 - mix) + wet * mix)
