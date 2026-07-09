"""Detuned pulse-wave pad — a fatter keyboard voice than wobble_saw.

Three pulse oscillators (center + two detuned), slow pulse-width motion,
soft attack. MIDI/keyboard-playable (freq + gate).
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Lag, Out, Pulse, SinOsc

from synthbase import module, param


@module(
    name="Pulse Pad",
    kind="source",
    params={
        "freq": param(20, 2000, 220, curve="exp"),
        "detune": param(0.0, 0.03, 0.007),      # ratio spread of the side oscs
        "pwm": param(0.0, 0.45, 0.2),           # pulse-width wobble depth
        "attack": param(0.005, 2.0, 0.15, curve="exp"),
        "release": param(0.05, 5.0, 0.8, curve="exp"),
        "amp": param(0, 1, 0.22),
    },
)
@synthdef()
def pulse_pad(freq=220, detune=0.007, pwm=0.2, attack=0.15, release=0.8, amp=0.22, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.03)      # slight glide between notes
    width = 0.5 + SinOsc.kr(frequency=0.3) * pwm
    a = Pulse.ar(frequency=f, width=width)
    b = Pulse.ar(frequency=f * (1 + detune), width=width)
    c = Pulse.ar(frequency=f * (1 - detune), width=width)
    sig = (a + b + c) * (1 / 3)
    env = EnvGen.kr(
        envelope=Envelope.adsr(attack, 0.2, 0.75, release), gate=gate
    )
    sig = sig * env * amp
    Out.ar(bus=out, source=[sig, sig])
