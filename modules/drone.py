"""Drone: a sustained pedal tone with slow portamento.

An ordinary spawnable audio source (multiple instances allowed) with one
extra: a control-plane presence. Wire a Tonic Deriver's TONIC out into a
drone instance's tonic-in and root changes drive that instance's `freq`
(grid-quantized by the deriver); `glide` sets how slowly the drone slides
there. No gate — the drone sounds for as long as its node exists (toggle
it off with the module's bypass switch).
"""

from supriya import synthdef
from supriya.ugens import LFSaw, LPF, Lag, Out, Pulse, SinOsc

from synthbase import module, param


@module(
    name="Drone",
    kind="source",
    params={
        "freq": param(16, 500, 55, curve="exp"),
        "amp": param(0, 1, 0.16),
        "porta": param(0, 1, 1, curve="toggle"),       # portamento on/off
        "glide": param(0.05, 8.0, 1.5, curve="exp"),   # portamento rate (seconds to reach a new root)
        "shape": param(0, 1, 0.35),                    # waveform blend: saw <-> pulse
        "sub": param(0, 1, 0.4),                       # sub-octave sine level
        "cutoff": param(80, 8000, 900, curve="exp"),
    },
)
@synthdef()
def drone(freq=55, amp=0.16, porta=1, glide=1.5, shape=0.35, sub=0.4, cutoff=900, out=0):
    f = Lag.kr(source=freq, lag_time=0.02 + glide * porta)  # porta off -> 20 ms snap
    saw = LFSaw.ar(frequency=[f, f * 1.004])                 # slight stereo drift
    width = 0.5 + SinOsc.kr(frequency=0.11) * 0.2            # slow pulse-width motion
    pul = Pulse.ar(frequency=[f * 0.996, f], width=width)
    body = saw * (1 - shape) + pul * shape
    body = LPF.ar(source=body, frequency=Lag.kr(source=cutoff, lag_time=0.1))
    subsig = SinOsc.ar(frequency=f * 0.5) * sub
    Out.ar(bus=out, source=(body + [subsig, subsig]) * amp * 0.5)
