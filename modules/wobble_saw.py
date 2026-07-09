"""A sawtooth voice with an amplitude wobble — the canonical source module.

MIDI-playable: exposes `freq` and `gate`, so `notes_to` in a patch can
target it. The ADSR releases when gate goes to 0 but the node stays alive
(mono voice: one persistent node, retriggered by gate).
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, LFSaw, Out, SinOsc

from synthbase import module, param


@module(
    name="Wobble Saw",
    kind="source",
    params={
        "freq": param(20, 2000, 110, curve="exp"),
        "wobble": param(0.1, 20, 4, curve="exp"),
        "depth": param(0, 1, 0.5),
        "amp": param(0, 1, 0.25),
    },
)
@synthdef()
def wobble_saw(freq=110, wobble=4, depth=0.5, amp=0.25, gate=1, out=0):
    lfo = SinOsc.kr(frequency=wobble) * 0.5 + 0.5          # 0..1 wobble
    tremolo = 1 - (lfo * depth)                            # dip by `depth`
    env = EnvGen.kr(envelope=Envelope.adsr(0.01, 0.1, 0.8, 0.3), gate=gate)
    sig = LFSaw.ar(frequency=freq) * tremolo * env * amp
    Out.ar(bus=out, source=[sig, sig])
