"""Golden Angle Phaser — GST Effects Master List #6 (sec. 1, PHASE/ALL-PASS).

Catalog row (GST-KB-EFFECTS-MASTER, Rev 1.2 Dark structure):

    6 | Golden Angle Phaser | * mu (x) | D | 137.5 deg from Z3 triadic limit
    cycle -- phi(t) from physical Hopf trajectory, not synthesized LFO.
    (x) Dual-path with #1 (Classic Phaser)

This is the digital port of that row, and the (x)-dual of ``phaser.py``
(same geometric type, different derivation — the catalog's dual-path pair):

* The sweep is NOT a synthesized LFO. A Hopf oscillator in normal form
  (dx = (1-r^2)x - w*y, dy = (1-r^2)y + w*x) is integrated at control rate
  inside the synthdef (LocalIn/LocalOut.kr, forward Euler at ControlDur).
  Its trajectory IS the modulator: x/y give the quadrature pair directly,
  so no phase accumulator or wavetable exists anywhere in the path.
* `react` couples the input's envelope into the oscillator as a forcing
  term — playing physically perturbs the limit cycle (the trajectory bends
  and relaxes back at the (1-r^2) rate) instead of riding a fixed clock.
  The GST hardware derives phi(t) from the Z3 triadic circuit; input
  forcing is this port's stand-in for that physicality.
* Six allpass notches per channel sit at golden-angle phase offsets
  (k * 137.507764 deg) around the cycle — the #4/#6 spacing, so notch
  motion never repeats a comb alignment. The right channel's offsets are
  rotated a further half golden angle for width.
* `mobius` honors the row's mu flag (2T spinor partner): the wet path's
  polarity flips once per limit-cycle revolution, returning to identity
  every 4*pi — a second tonal state, as the Mobius Band Modulation rows
  (#45/#170) describe.

Sweep depth follows house rule 9: semitones around `center`, converted in
the DSP with .semitones_to_ratio().
"""

import math

from supriya import synthdef
from supriya.ugens import (
    Amplitude, BAllPass, ControlDur, Impulse, In, Lag, LeakDC, LocalIn,
    LocalOut, Out, ToggleFF, XFade2,
)

from synthbase import module, param

GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))  # 2.399963... rad = 137.5077... deg
STAGES = 6


@module(
    name="Golden Phaser",
    kind="effect",
    params={
        "rate": param(0.05, 8.0, 0.4, curve="exp"),   # Hopf natural frequency (Hz)
        "center": param(200, 4000, 900, curve="exp"),  # sweep center (Hz)
        "depth": param(0, 48, 24),                     # sweep span (semitones)
        "react": param(0, 1, 0.35),                    # input -> limit-cycle forcing
        "feedback": param(0, 0.85, 0.3),
        "mix": param(0, 1, 0.5),
        "mobius": param(0, 1, 0, curve="toggle"),      # mu: wet flips per cycle (4pi)
    },
)
@synthdef()
def golden_phaser(in_bus=0, rate=0.4, center=900, depth=24, react=0.35,
                  feedback=0.3, mix=0.5, mobius=0, out=0):
    dry = In.ar(bus=in_bus, channel_count=2)
    rate = Lag.kr(source=rate, lag_time=0.05)
    center = Lag.kr(source=center, lag_time=0.02)
    depth = Lag.kr(source=depth, lag_time=0.02)
    feedback = Lag.kr(source=feedback, lag_time=0.02)

    # -- the physical modulator: a Hopf limit cycle, not an LFO ---------------
    env = Amplitude.kr(source=(dry[0] + dry[1]) * 0.5,
                       attack_time=0.005, release_time=0.15)
    state = LocalIn.kr(channel_count=2)
    x, y = state[0], state[1]
    dt = ControlDur.ir()
    w = rate * math.tau
    relax = (1.0 - (x * x + y * y)) * 3.0        # pull back onto the unit cycle
    dx = relax * x - w * y
    dy = relax * y + w * x + Lag.kr(source=react, lag_time=0.02) * env * 4.0
    # Impulse.kr(frequency=0) fires exactly once: drop the state onto the
    # cycle at t=0 (the origin is the Hopf system's unstable fixed point).
    x_new = x + dx * dt + Impulse.kr(frequency=0)
    y_new = y + dy * dt
    LocalOut.kr(source=[x_new, y_new])

    # Quadrature straight off the trajectory — no atan2, no phase wrap.
    # Soft-floored normalization keeps notch spread sane mid-perturbation.
    norm = 1.0 / ((x * x + y * y + 0.04) ** 0.5)
    cx, cy = x * norm, y * norm

    # -- mu flag: polarity flips once per revolution (spinor, 4pi period) -----
    flip = ToggleFF.kr(trigger=y)                # y's upward zero-cross = 1/cycle
    polarity = Lag.kr(source=1.0 - 2.0 * flip * mobius, lag_time=0.01)

    # -- six golden-angle notches per channel, feedback around the chain ------
    fb = LocalIn.ar(channel_count=2)
    wet = []
    for ch in range(2):
        sig = dry[ch] + fb[ch] * feedback
        for k in range(STAGES):
            theta = k * GOLDEN_ANGLE + ch * (GOLDEN_ANGLE * 0.5)
            s = cy * math.cos(theta) + cx * math.sin(theta)   # sin(phi + theta)
            freq = center * ((depth * 0.5 * s).semitones_to_ratio())
            sig = BAllPass.ar(source=sig, frequency=freq, reciprocal_of_q=1.5)
        wet.append(sig)
    LocalOut.ar(source=[LeakDC.ar(source=wet[0]), LeakDC.ar(source=wet[1])])

    mixed = XFade2.ar(in_a=dry, in_b=[wet[0] * polarity, wet[1] * polarity],
                      pan=mix * 2.0 - 1.0)
    Out.ar(bus=out, source=mixed)
