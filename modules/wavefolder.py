"""Wavefolder: drives the signal into folds for west-coast harmonics."""

from supriya import synthdef
from supriya.ugens import In, Lag, LeakDC, Out

from synthbase import module, param


@module(
    name="Wavefolder",
    kind="effect",
    params={
        "fold": param(1.0, 12.0, 2.5, curve="exp"),   # drive into the folder
        "symmetry": param(-0.5, 0.5, 0.0),            # DC offset pre-fold
        "mix": param(0.0, 1.0, 1.0),
    },
)
@synthdef()
def wavefolder(in_bus=0, out=0, fold=2.5, symmetry=0.0, mix=1.0):
    dry = In.ar(bus=in_bus, channel_count=2)
    driven = dry * Lag.kr(source=fold, lag_time=0.02) + symmetry
    folded = LeakDC.ar(source=driven.fold2(1.0)) * 0.8
    Out.ar(bus=out, source=dry * (1 - mix) + folded * mix)
