"""Scope Tap: a transparent inline probe for the oscilloscope.

An EFFECT that passes audio straight through (with a trim knob so the card
isn't empty). The GUI treats a spawned scope_tap as its oscilloscope card:
splice it anywhere in the audio graph and the scope draws whatever flows
out of it (Scope.capture reads this module's out bus).
"""

from supriya import synthdef
from supriya.ugens import In, Lag, Out

from synthbase import module, param


@module(
    name="Scope Tap",
    kind="effect",
    params={
        "gain": param(0.0, 2.0, 1.0),
    },
)
@synthdef()
def scope_tap(in_bus=0, out=0, gain=1.0):
    sig = In.ar(bus=in_bus, channel_count=2)
    Out.ar(bus=out, source=sig * Lag.kr(source=gain, lag_time=0.02))
