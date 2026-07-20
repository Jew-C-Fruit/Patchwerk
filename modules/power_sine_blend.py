"""Traditional wavetable-style morph: crossfade sine <-> band-limited square.

The "wavetable" foil to the math-faithful variants. Two fixed frames (pure
sine, ideal square) crossfaded by u = clip(1 - 2/p, 0, 1); the in-between is a
linear blend of frames, not the true reshaping spectrum — which is exactly the
audible difference worth testing against `power_sine_additive`.

Stack note: this reuses the shared `odd_harmonic_bank` MECHANISM but keeps its
coefficient LAW inline (below) — the law is this module's identity, so it
stays in the module file rather than in the base. Either style is supported.
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Lag, Out

from synthbase import module, odd_harmonic_bank, param

_PARTIALS = 24


@module(
    name="Power Sine (blend)",
    kind="source",
    params={
        "freq": param(20, 2000, 220, curve="exp"),
        "p": param(1, 64, 2.0, curve="exp"),   # mapped to a sine<->square crossfade
        "amp": param(0, 1, 0.3),
    },
)
@synthdef()
def power_sine_blend(freq=220, p=2.0, amp=0.3, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.01)
    u = (1.0 - 2.0 / Lag.kr(source=p, lag_time=0.02)).clip(0.0, 1.0)

    def frames():                       # coefficient law — the module's identity
        n = 1
        for _ in range(_PARTIALS):
            yield n, (1.0 if n == 1 else u * (1.0 / n))
            n += 2

    tone = odd_harmonic_bank(f, frames())
    env = EnvGen.kr(envelope=Envelope.adsr(0.01, 0.1, 0.85, 0.4), gate=gate)
    sig = tone * amp * env
    Out.ar(bus=out, source=[sig, sig])
