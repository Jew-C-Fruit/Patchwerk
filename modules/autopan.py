"""Auto-panner: sweeps the stereo image side to side."""

from supriya import synthdef
from supriya.ugens import In, Out, Pan2, SinOsc

from synthbase import module, param


@module(
    name="Auto Pan",
    kind="effect",
    params={
        "rate": param(0.05, 10.0, 0.5, curve="exp"),
        "depth": param(0.0, 1.0, 0.7),
    },
)
@synthdef()
def autopan(in_bus=0, out=0, rate=0.5, depth=0.7):
    sig = In.ar(bus=in_bus, channel_count=2)
    mono = (sig[0] + sig[1]) * 0.5
    position = SinOsc.kr(frequency=rate) * depth
    Out.ar(bus=out, source=Pan2.ar(source=mono, position=position))
