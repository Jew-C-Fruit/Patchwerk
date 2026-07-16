"""Artifix Generator — the voice half of the Artifix package.

A continuous generative source ported from the Artifix web demonstrator's
oscillator bank: a small near-unison of morphable voices + a sub, low-passed
and gently drive-warmed, with a built-in slow stereo chorus for movement.

The six modulation dimensions from the web app are exposed as params so an
Allocation Intent, a Living Oscillator, plain LFOs, or your hand can steer
them: morph (waveform), harm (harmonic balance), bright (filter movement),
res (resonance), detune (unison spread), stereo (width). Plus pitch, amp, and
`phase` (the chorus depth/mix).

Voicing note (the "glass" default): the voices sit almost in unison
(detune ~0.03) so they don't beat into a harsh warble at low pitch — the
movement instead comes from `phase`, a soft slow chorus that swirls the phase
rather than throbbing detuned oscillators. `drive` (internal) warms the tone
and brings it to a healthy level; the old build was ~15 dB too quiet.

It plays on its own the moment it's in the chain — no note needed — matching
the demonstrator. Wire nothing and it's a calm breathing pad; wire a Living
Oscillator into `morph` and it drifts.
"""

from supriya import synthdef
from supriya.ugens import (
    DelayC, RLPF, Lag, LeakDC, Out, Saw, SinOsc, Splay, VarSaw,
)

from synthbase import module, param

NV = 5          # near-unison voices
DRIVE = 3.0     # internal tanh drive — warmth + makeup gain (old build used 1.1)
SUB = 0.24      # sub-oscillator level (an octave below the fundamental)


@module(
    name="Artifix Gen",
    kind="source",
    params={
        "pitch": param(40, 440, 110, curve="exp"),
        "morph": param(0, 1, 0.30),     # waveform shape (triangle -> ramp/saw)
        "harm": param(0, 1, 0.10),      # harmonic balance (soft <-> bright saw)
        "bright": param(0, 1, 0.36),    # filter movement (cutoff)
        "res": param(0, 1, 0.13),       # resonance
        "detune": param(0, 1, 0.03),    # unison spread (kept tiny = no beat)
        "stereo": param(0, 1, 0.55),    # stereo width
        "phase": param(0, 1, 0.60),     # chorus depth/mix — the "gentle phase"
        "amp": param(0, 1, 0.40),
    },
)
@synthdef()
def artifix_gen(pitch=110, morph=0.30, harm=0.10, bright=0.36, res=0.13,
                detune=0.03, stereo=0.55, phase=0.60, amp=0.40, out=0):
    pitch = Lag.kr(source=pitch, lag_time=0.05)
    morph = Lag.kr(source=morph, lag_time=0.03)
    harm = Lag.kr(source=harm, lag_time=0.03)
    bright = Lag.kr(source=bright, lag_time=0.03)
    res = Lag.kr(source=res, lag_time=0.03)
    detune = Lag.kr(source=detune, lag_time=0.03)

    # cutoff sweeps ~360 Hz .. 5.8 kHz with bright, via semitone math (house rule 9)
    cutoff = 120 * ((1.6 + 4.0 * bright) * 12).semitones_to_ratio()
    rq = 1.0 / (0.7 + 6.0 * res)                      # reciprocal_of_q: res up -> sharper
    width = 0.5 - 0.46 * morph                        # VarSaw: 0.5 = triangle, ->0 ramp

    voices = []
    for i in range(NV):
        # symmetric detune spread in cents, scaled by the (small) spread knob
        dcents = (i - (NV - 1) / 2.0) * (3.0 + 60.0 * detune)
        f = pitch * (dcents / 100.0).semitones_to_ratio()
        tri = VarSaw.ar(frequency=f, width=width)
        saw = Saw.ar(frequency=f)
        v = tri * (1.0 - harm) + saw * harm
        voices.append(v * (0.7 if i == 0 else 0.5))

    spread = Splay.ar(source=voices, spread=stereo)   # mono voices -> stereo field
    filt = RLPF.ar(source=spread, frequency=cutoff, reciprocal_of_q=rq)
    sub = SinOsc.ar(frequency=pitch * 0.5) * SUB
    dry = [LeakDC.ar(source=((filt[0] + sub) * DRIVE).tanh()) * amp,
           LeakDC.ar(source=((filt[1] + sub) * DRIVE).tanh()) * amp]

    # gentle stereo chorus = smooth moving phase (replaces wide detune beating).
    # three slow, incommensurate modulated delays per side, mixed in by `phase`.
    dep = 0.002 + 0.006 * phase
    def cho(sig, seed, rates):
        taps = [DelayC.ar(
            source=sig, maximum_delay_time=0.06,
            delay_time=0.017 + dep * (0.5 + 0.5 * SinOsc.kr(
                frequency=r, phase=seed + k * 1.3)))
            for k, r in enumerate(rates)]
        return sum(taps) * (1.0 / len(taps))
    wet_l = cho(dry[0], 0.0, [0.09, 0.14, 0.20])
    wet_r = cho(dry[1], 2.1, [0.11, 0.17, 0.23])
    mix = [dry[0] * (1.0 - phase) + wet_l * phase,
           dry[1] * (1.0 - phase) + wet_r * phase]
    Out.ar(bus=out, source=mix)
