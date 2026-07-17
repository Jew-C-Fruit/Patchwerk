"""Artifix Voice — the playable twin of Artifix Gen.

Same "glass" voicing as `artifix_gen` (near-unison morphable voices + sub,
tanh-warmed, with a built-in gentle stereo chorus) but MIDI/keyboard-playable:
it takes `freq` + `gate` and wraps the voice in a slow pad ADSR, so held notes
bloom and ring. Wire the keys into it (see patches/artifix_play.py) to perform
it; reach for the continuous `artifix_gen` when you want the hands-off drone.

The voicing params match artifix_gen so both twins sound identical when held —
`attack`/`release` shape the note, and it's silent until a key is pressed.
"""

from supriya import Envelope, synthdef
from supriya.ugens import (
    DelayC, EnvGen, RLPF, Lag, LeakDC, Out, Saw, SinOsc, Splay, VarSaw,
)

from synthbase import module, param

NV = 5          # near-unison voices
DRIVE = 3.0     # internal tanh drive — warmth + makeup gain
SUB = 0.24      # sub-oscillator level (an octave below the played note)


@module(
    name="Artifix Voice",
    kind="source",
    params={
        "freq": param(20, 2000, 110, curve="exp"),
        "morph": param(0, 1, 0.30),     # waveform shape (triangle -> ramp/saw)
        "harm": param(0, 1, 0.10),      # harmonic balance (soft <-> bright saw)
        "bright": param(0, 1, 0.36),    # filter movement (cutoff)
        "res": param(0, 1, 0.13),       # resonance
        "detune": param(0, 1, 0.03),    # unison spread (tiny = no beat)
        "stereo": param(0, 1, 0.55),    # stereo width
        "phase": param(0, 1, 0.60),     # chorus depth/mix — the "gentle phase"
        "attack": param(0.005, 4.0, 0.30, curve="exp"),   # note bloom
        "release": param(0.05, 6.0, 1.60, curve="exp"),   # note ring-out
        "amp": param(0, 1, 0.40),
    },
)
@synthdef()
def artifix_voice(freq=110, morph=0.30, harm=0.10, bright=0.36, res=0.13,
                  detune=0.03, stereo=0.55, phase=0.60, attack=0.30,
                  release=1.60, amp=0.40, gate=1, out=0):
    freq = Lag.kr(source=freq, lag_time=0.02)         # glide-free but zipper-safe
    morph = Lag.kr(source=morph, lag_time=0.03)
    harm = Lag.kr(source=harm, lag_time=0.03)
    bright = Lag.kr(source=bright, lag_time=0.03)
    res = Lag.kr(source=res, lag_time=0.03)
    detune = Lag.kr(source=detune, lag_time=0.03)

    cutoff = 120 * ((1.6 + 4.0 * bright) * 12).semitones_to_ratio()
    rq = 1.0 / (0.7 + 6.0 * res)
    width = 0.5 - 0.46 * morph

    voices = []
    for i in range(NV):
        dcents = (i - (NV - 1) / 2.0) * (3.0 + 60.0 * detune)
        f = freq * (dcents / 100.0).semitones_to_ratio()
        tri = VarSaw.ar(frequency=f, width=width)
        saw = Saw.ar(frequency=f)
        v = tri * (1.0 - harm) + saw * harm
        voices.append(v * (0.7 if i == 0 else 0.5))

    spread = Splay.ar(source=voices, spread=stereo)
    filt = RLPF.ar(source=spread, frequency=cutoff, reciprocal_of_q=rq)
    sub = SinOsc.ar(frequency=freq * 0.5) * SUB
    dry = [LeakDC.ar(source=((filt[0] + sub) * DRIVE).tanh()),
           LeakDC.ar(source=((filt[1] + sub) * DRIVE).tanh())]

    # gentle stereo chorus = smooth moving phase (same as artifix_gen)
    dep = 0.002 + 0.006 * phase
    def cho(sig, seed, rates):
        taps = [DelayC.ar(
            source=sig, maximum_delay_time=0.06,
            delay_time=0.017 + dep * (0.5 + 0.5 * SinOsc.kr(
                frequency=r, phase=seed + k * 1.3)))
            for k, r in enumerate(rates)]
        return sum(taps) * (1.0 / len(taps))
    voice = [dry[0] * (1.0 - phase) + cho(dry[0], 0.0, [0.09, 0.14, 0.20]) * phase,
             dry[1] * (1.0 - phase) + cho(dry[1], 2.1, [0.11, 0.17, 0.23]) * phase]

    # pad ADSR — held notes bloom and ring. done_action unset (0): the mono
    # voice node is persistent, so the envelope idles at 0 rather than freeing.
    env = EnvGen.kr(envelope=Envelope.adsr(attack, 0.25, 0.85, release), gate=gate)
    Out.ar(bus=out, source=[voice[0] * env * amp, voice[1] * env * amp])
