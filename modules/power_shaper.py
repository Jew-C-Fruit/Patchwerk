"""Power Shaper FX: the psine waveshaper's law applied to INCOMING audio.

Same transform as modules/power_sine_shaper.py — T_p(A) = sgn(A)*|A|^(2/p),
memoryless, computed per sample — but as an EFFECT over whatever is wired
in, not over an internal sine (item 11; the law is input-agnostic, so the
source/effect contract stays hard: sources take no audio in, this does).
    p = 2   -> exponent 1 -> identity (drive still applies)
    p -> 64 -> exponent -> 0 -> sgn(x)  -> hard square-off
    p < 2   -> exponent > 1 -> pinched / peaky
Like the generator it is NOT band-limited: as p climbs, fold-back aliasing
is the sonic fingerprint. `drive` pushes the input into the curve (for
p > 2 the sub-unity exponent compresses hot signals), `mix` blends dry/wet.
"""

from supriya import synthdef
from supriya.ugens import In, Lag, LeakDC, Out

from synthbase import module, param


@module(
    name="Power Shaper",
    kind="effect",
    params={
        "p": param(1, 64, 2.0, curve="exp"),      # 2 = identity, ->64 ~ square-off
        "drive": param(0.25, 8.0, 1.0, curve="exp"),
        "mix": param(0.0, 1.0, 1.0),
    },
)
@synthdef()
def power_shaper(in_bus=0, out=0, p=2.0, drive=1.0, mix=1.0):
    dry = In.ar(bus=in_bus, channel_count=2)
    a = 2.0 / Lag.kr(source=p, lag_time=0.02)          # exponent 2/p
    x = dry * Lag.kr(source=drive, lag_time=0.02)
    wet = x.sign() * (abs(x) ** a)                     # sgn(x) * |x|^(2/p)
    wet = LeakDC.ar(source=wet)                        # DC guard (belt & suspenders)
    m = Lag.kr(source=mix, lag_time=0.02)
    Out.ar(bus=out, source=dry * (1 - m) + wet * m)
