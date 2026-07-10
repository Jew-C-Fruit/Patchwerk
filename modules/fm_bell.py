"""FM bell/EP voice: two-operator FM with velocity-friendly decay."""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, Lag, Out, SinOsc

from synthbase import module, param


@module(
    name="FM Bell",
    kind="source",
    params={
        "freq": param(20, 2000, 440, curve="exp"),
        "ratio": param(0.5, 8.0, 3.51),           # modulator : carrier
        "index": param(0.0, 12.0, 4.0),           # FM depth
        "decay": param(0.1, 8.0, 2.5, curve="exp"),
        "amp": param(0, 1, 0.25),
    },
)
@synthdef()
def fm_bell(freq=440, ratio=3.51, index=4.0, decay=2.5, amp=0.25, gate=1, out=0):
    f = Lag.kr(source=freq, lag_time=0.01)
    env = EnvGen.kr(envelope=Envelope.adsr(0.002, decay * 0.5, 0.15, decay * 0.6), gate=gate)
    idx_env = EnvGen.kr(envelope=Envelope.adsr(0.001, decay * 0.3, 0.3, decay * 0.5), gate=gate)
    mod = SinOsc.ar(frequency=f * ratio) * f * index * idx_env
    sig = SinOsc.ar(frequency=f + mod) * env * amp
    Out.ar(bus=out, source=[sig, sig])
