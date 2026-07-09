"""Hardware audio input (e.g. the Mac's mic) as a source module.

Reads one hardware input channel and spreads it to stereo. Hardware inputs
sit right after the output buses in SC's bus numbering, so with the default
2-channel output config, input 1 is bus 2 (hence hw_channel default 0 ->
bus offset handled in the graph).
"""

from supriya import synthdef
from supriya.ugens import In, NumOutputBuses, Out

from synthbase import module, param


@module(
    name="Audio In",
    kind="source",
    params={
        "gain": param(0.0, 4.0, 1.0),
    },
)
@synthdef()
def audio_in(gain=1.0, out=0):
    mono = In.ar(bus=NumOutputBuses.ir(), channel_count=1) * gain
    Out.ar(bus=out, source=[mono, mono])
