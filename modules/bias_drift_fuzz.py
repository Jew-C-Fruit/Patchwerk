"""Bias Drift Fuzz — GST Effects Master List #29 (sec. 3, DISTORTION / FUZZ).

Catalog row (GST-KB-EFFECTS-MASTER, Rev 1.2 Dark structure):

    29 | Bias Drift Fuzz | * mu | D | Sub-audio g2 drift ~10s.
    Default: D, tau-5 Koopman active

The GST hardware drifts an EF86 pentode's screen-grid (g2) bias on a
sub-audio cycle, so the transfer curve — and with it the fuzz's asymmetry,
harmonic mix, and gating feel — slowly breathes instead of sitting still.

This port keeps that shape:

* The shaper is an asymmetric tanh pair: tanh(drive*(x + bias)) minus the
  DC that bias introduces. Moving `bias` slides the operating point along
  the curve exactly like a drifting g2 — symmetric (odd harmonics) at
  center, progressively asymmetric (even harmonics, earlier cutoff) as it
  drifts out.
* The drift is a `drift_time`-period sine plus a slower LFNoise2 wander
  (the tau-5 Koopman "slow mode" stand-in), scaled by `drift_depth`.
  At depth 0 it's a static fuzz; at 1 the character wanders audibly.
* `mobius` honors the mu flag the same way golden_phaser.py does: the wet
  path's polarity flips once per drift cycle (4*pi spinor period) — odd
  harmonics invert against the dry blend, a second tonal state.
* `tone` is a post-shaper lowpass (fizz control); `level` compensates for
  the gain the shaper adds.
"""

from supriya import synthdef
from supriya.ugens import (
    In, LFNoise2, Lag, LeakDC, LPF, Out, SinOsc, ToggleFF, XFade2,
)

from synthbase import module, param


@module(
    name="Bias Drift Fuzz",
    kind="effect",
    params={
        "drive": param(1, 30, 8, curve="exp"),
        "drift_time": param(2, 60, 10, curve="exp"),   # seconds per breath
        "drift_depth": param(0, 1, 0.5),
        "tone": param(500, 8000, 3500, curve="exp"),   # post-fuzz lowpass
        "level": param(0, 1, 0.5),
        "mix": param(0, 1, 1.0),
        "mobius": param(0, 1, 0, curve="toggle"),      # mu: wet flips per cycle
    },
)
@synthdef()
def bias_drift_fuzz(in_bus=0, drive=8, drift_time=10, drift_depth=0.5,
                    tone=3500, level=0.5, mix=1.0, mobius=0, out=0):
    dry = In.ar(bus=in_bus, channel_count=2)
    drive = Lag.kr(source=drive, lag_time=0.02)
    drift_depth = Lag.kr(source=drift_depth, lag_time=0.02)
    tone = Lag.kr(source=tone, lag_time=0.02)
    level = Lag.kr(source=level, lag_time=0.02)

    # -- the drifting bias point (sub-audio, ~drift_time per breath) ----------
    cycle = SinOsc.kr(frequency=1.0 / drift_time)          # the g2 breath
    wander = LFNoise2.kr(frequency=0.25 / drift_time)      # tau-5 slow mode
    bias = drift_depth * (0.8 * cycle + 0.2 * wander) * 0.75

    # -- mu flag: wet polarity flips once per drift cycle (spinor, 4pi) -------
    flip = ToggleFF.kr(trigger=cycle)                      # upward zero-cross
    polarity = Lag.kr(source=1.0 - 2.0 * flip * mobius, lag_time=0.01)

    # -- asymmetric tanh shaper: bias slides the operating point --------------
    dc = (drive * bias).tanh()                             # cancel bias's DC
    wet = []
    for ch in range(2):
        shaped = (drive * (dry[ch] + bias)).tanh() - dc
        shaped = LPF.ar(source=LeakDC.ar(source=shaped), frequency=tone)
        wet.append(shaped * level * polarity)
    Out.ar(bus=out, source=XFade2.ar(in_a=dry, in_b=wet, pan=mix * 2.0 - 1.0))
