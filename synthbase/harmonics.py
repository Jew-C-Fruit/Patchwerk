"""Odd-harmonic additive bank — the shared mechanism behind the power-sine family.

This is deliberately thin: a pure *graph emitter*. It has no engine, server,
rack, or state coupling — it just returns a mono UGen you drop into a synthdef,
exactly like calling a supriya UGen. It sits beside `keyshift.py` as a base
helper, not in `modules/`.

The split that keeps the base thin AND the vibecoding surface intact:
    - MECHANISM (shared, here): sum odd harmonics, gate above Nyquist,
      RMS-normalize. The boilerplate every additive variant repeats.
    - POLICY (per module, stays in the module file): the *coefficient law* —
      the one interesting line that makes a module what it is. A module passes
      its law in as `coeffs`; the law does not live here.

`coeffs` is any iterable of (n, amp) pairs for successive odd n (1, 3, 5, …),
where `amp` may be a Python float or a UGen expression (so it can depend on a
live control like p). See `power_law_coeffs` / `square_blend_coeffs` below for
the two laws currently in use; modules may also inline their own.
"""

from supriya.ugens import SinOsc


def odd_harmonic_bank(freq, coeffs, *, nyquist=21000.0, headroom=0.5):
    """Return a mono UGen: RMS-normalized sum of Nyquist-gated odd partials.

    freq     — fundamental (UGen or number)
    coeffs   — iterable of (n, amp) for odd n; amp float or UGen
    nyquist  — guard just under fs/2; partials above it contribute 0
    headroom — post-normalize scale (0.5 keeps unit-RMS peaks in check)
    """
    acc = None
    sumsq = None
    for n, amp in coeffs:
        gate_n = (freq * n < nyquist)          # 1.0 below Nyquist, else 0.0
        amp_n = amp * gate_n
        partial = SinOsc.ar(frequency=freq * n) * amp_n
        acc = partial if acc is None else acc + partial
        sumsq = amp_n * amp_n if sumsq is None else sumsq + amp_n * amp_n
    rms = (0.5 * sumsq + 1e-9) ** 0.5
    return (acc / rms) * headroom


def power_law_coeffs(a, partials=24):
    """Exact odd-harmonic coefficients of sgn(sin)|sin|^a, a = 2/p.

    Gamma-free recurrence (verified to machine precision against an FFT of the
    shaped sine): b_1 = 1, b_(n+2) = b_n·(n − a)/(n + a + 2). `a` may be a UGen.
    a=1 (p=2) → pure sine; a→0 (p→∞) → the 4/(π n) square series.
    """
    coef = 1.0
    n = 1
    for _ in range(partials):
        yield n, coef
        coef = coef * ((n - a) / (n + a + 2.0))
        n += 2


def square_blend_coeffs(u, partials=24):
    """2-frame sine↔square crossfade: n=1 → 1, odd n>1 → u·(1/n). `u` may be a UGen."""
    n = 1
    for _ in range(partials):
        yield n, (1.0 if n == 1 else u * (1.0 / n))
        n += 2
