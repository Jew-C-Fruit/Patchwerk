"""Band-limited additive rendering of the power-law morph — alias-free.

Same target as `power_sine_shaper` (sgn(sin)·|sin|^(2/p)), synthesized as its
exact odd-harmonic series so nothing is ever generated above Nyquist. Sweeping
`p` is a control-rate update — no buffers, no recompute.

Stack note: the summation/gating/normalize boilerplate lives in the shared
`odd_harmonic_bank`; this module reuses the shared coefficient law too, since
the power law is the whole family's namesake.
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Lag, Out

from synthbase import module, odd_harmonic_bank, param, power_law_coeffs


@module(
    name="Psine Harmonic Bank",
    kind="source",
    params={
        "freq": param(20, 2000, 220, curve="exp"),
        "p": param(1, 64, 2.0, curve="exp"),   # 2 = sine, ->64 ~ square, <2 = peaky
        "amp": param(0, 1, 0.3),
    },
)
@synthdef()
def power_sine_additive(freq=220, p=2.0, amp=0.3, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.01)
    a = 2.0 / Lag.kr(source=p, lag_time=0.02)
    tone = odd_harmonic_bank(f, power_law_coeffs(a))       # mechanism + shared law
    env = EnvGen.kr(envelope=Envelope.adsr(0.01, 0.1, 0.85, 0.4), gate=gate)
    sig = tone * amp * env
    Out.ar(bus=out, source=[sig, sig])
