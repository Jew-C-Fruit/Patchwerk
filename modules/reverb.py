"""FreeVerb reverb."""

from supriya import synthdef
from supriya.ugens import FreeVerb, In, Out

from synthbase import module, param


@module(
    name="Reverb",
    kind="effect",
    params={
        "room": param(0.0, 1.0, 0.6),
        "damp": param(0.0, 1.0, 0.5),
        "mix": param(0.0, 1.0, 0.3),
    },
)
@synthdef()
def reverb(in_bus=0, out=0, room=0.6, damp=0.5, mix=0.3):
    sig = In.ar(bus=in_bus, channel_count=2)
    Out.ar(bus=out, source=FreeVerb.ar(source=sig, mix=mix, room_size=room, damping=damp))
