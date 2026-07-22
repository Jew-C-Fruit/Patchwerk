"""Sign-preserving power-law waveshaper: sine morphs to square with one knob.

Straight math (no coinage): T_p(A) = sgn(A)*|A|^(2/p), where A = sin(2*pi*f*t).
    p = 2  -> exponent 1 -> identity          -> pure sine
    p -> inf -> exponent 0 -> sgn(sin)         -> square wave
    p < 2  -> exponent > 1 -> pinched / peaky  -> extra odd harmonics

This is the LITERAL transform, computed per sample. It is the cheapest of the
three variants and `p` is free to sweep live (just a knob -- nothing to
recompute). Trade-off: a memoryless power curve is NOT band-limited, so as p
climbs it generates harmonics above Nyquist that fold back as aliasing. That
grit IS its sonic fingerprint. A/B it against `power_sine_additive` (identical
target spectrum, but band-limited) to hear precisely what the aliasing adds.
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Lag, LeakDC, Out, SinOsc

from synthbase import module, param


@module(
    name="Psine Waveshaper",
    kind="source",
    params={
        "freq": param(20, 2000, 220, curve="exp"),
        "p": param(1, 64, 2.0, curve="exp"),   # 2 = sine, ->64 ~ square, <2 = peaky
        "amp": param(0, 1, 0.3),
    },
)
@synthdef()
def power_sine_shaper(freq=220, p=2.0, amp=0.3, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.01)
    a = 2.0 / Lag.kr(source=p, lag_time=0.02)      # exponent 2/p
    x = SinOsc.ar(frequency=f)
    shaped = x.sign() * (abs(x) ** a)              # sgn(x) * |x|^(2/p)
    shaped = LeakDC.ar(source=shaped)              # DC guard (belt & suspenders)
    env = EnvGen.kr(envelope=Envelope.adsr(0.01, 0.1, 0.85, 0.4), gate=gate)
    sig = shaped * env * amp
    Out.ar(bus=out, source=[sig, sig])
