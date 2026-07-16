"""Artifix Generator — the voice half of the Artifix package.

A continuous generative source ported from the Artifix web demonstrator's
oscillator bank: a small unison of morphable voices + a sub, with the six
"allocation dimensions" exposed as params so an Allocation Intent modulator
(or a Living Oscillator, or plain LFOs, or your hand) can steer them.

It plays on its own the moment it's in the chain — no note needed — which
matches the demonstrator (hit Start and it drifts). It works completely
standalone; the rest of the Artifix package just gives it things to react
to. Wire nothing and it's a fine drone; wire a Living Oscillator into
`morph` and it breathes.

The six modulation targets (mirroring the web app's allocation vector):
morph (waveform), harm (harmonic balance), bright (filter movement),
res (resonance), detune (shimmer), stereo (width). Plus pitch and amp.

Deliberately NOT the analog signal path — this is the demonstrator's
behaviour on scsynth, per the Artifix footer.
"""

from supriya import synthdef
from supriya.ugens import RLPF, Lag, LeakDC, Out, Saw, SinOsc, Splay, VarSaw

from synthbase import module, param

NV = 5  # unison voices


@module(
    name="Artifix Gen",
    kind="source",
    params={
        "pitch": param(40, 440, 110, curve="exp"),
        "morph": param(0, 1, 0.5),      # waveform shape (triangle -> ramp/saw)
        "harm": param(0, 1, 0.35),      # harmonic balance (soft <-> bright saw)
        "bright": param(0, 1, 0.5),     # filter movement (cutoff)
        "res": param(0, 1, 0.25),       # resonance
        "detune": param(0, 1, 0.3),     # unison detune shimmer
        "stereo": param(0, 1, 0.4),     # stereo width
        "amp": param(0, 1, 0.22),
    },
)
@synthdef()
def artifix_gen(pitch=110, morph=0.5, harm=0.35, bright=0.5, res=0.25,
                detune=0.3, stereo=0.4, amp=0.22, out=0):
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
        # symmetric detune spread in cents, scaled by the shimmer knob
        dcents = (i - (NV - 1) / 2.0) * (3.0 + 60.0 * detune)
        f = pitch * (dcents / 100.0).semitones_to_ratio()
        tri = VarSaw.ar(frequency=f, width=width)
        saw = Saw.ar(frequency=f)
        v = tri * (1.0 - harm) + saw * harm
        voices.append(v * (0.7 if i == 0 else 0.5))

    spread = Splay.ar(source=voices, spread=stereo)   # mono voices -> stereo field
    filt = RLPF.ar(source=spread, frequency=cutoff, reciprocal_of_q=rq)
    sub = SinOsc.ar(frequency=pitch * 0.5) * 0.18
    mix = [filt[0] + sub, filt[1] + sub]
    mix = [LeakDC.ar(source=(m * 1.1).tanh()) * amp for m in mix]
    Out.ar(bus=out, source=mix)
