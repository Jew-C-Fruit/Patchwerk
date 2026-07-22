"""Wind: filtered-noise texture source with slow internal weather."""

from supriya import synthdef
from supriya.ugens import BPF, LFNoise1, Lag, Out, PinkNoise

from synthbase import module, param


@module(
    name="Wind",
    kind="source",
    params={
        "center": param(150, 4000, 700, curve="exp"),  # gust band center
        "gust": param(0.0, 1.0, 0.6),                  # how much the weather moves
        "resonance": param(0.2, 3.0, 1.0),
        "amp": param(0, 1, 0.3),
    },
)
@synthdef()
def wind(center=700, gust=0.6, resonance=1.0, amp=0.3, out=0):
    c = Lag.kr(source=center, lag_time=0.2)
    drift_l = c * (1 + LFNoise1.kr(frequency=0.13) * gust * 0.8)
    drift_r = c * (1 + LFNoise1.kr(frequency=0.11) * gust * 0.8)
    swell = 0.5 + (LFNoise1.kr(frequency=0.07) * 0.5 + 0.5) * gust
    sig = [
        BPF.ar(source=PinkNoise.ar(), frequency=drift_l, reciprocal_of_q=resonance),
        BPF.ar(source=PinkNoise.ar(), frequency=drift_r, reciprocal_of_q=resonance),
    ]
    # 2.9 = the old 2x headroom boost × 1.45 makeup: level-matched to the
    # voice family at default params (probe_voice_levels_ws, 2026-07-22)
    Out.ar(bus=out, source=[s * swell * amp * 2.9 for s in sig])
