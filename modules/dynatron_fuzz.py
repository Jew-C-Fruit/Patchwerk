"""Dynatron Fuzz — GST Effects Master List #27 (sec. 3, DISTORTION / FUZZ).

Catalog row (GST-KB-EFFECTS-MASTER, Rev 1.2 Dark structure):

    27 | Dynatron Fuzz | * | D | Negative resistance.
    Default: D, threshold active

The dynatron kink: a tetrode's plate curve has a region where MORE input
gives LESS output (secondary emission — negative resistance). That fold-
back valley is what this fuzz is built around, and it's why it snarls
where a tanh fuzz just saturates:

    y = tanh(v) - kink * 1.7 * v * exp(-v^2)

kink=0 is a plain tube clip; past ~0.5 the curve genuinely folds back
(verified non-monotonic), so peaks punch INTO the valley and come out with
strong upper harmonics and intermodulation snarl. "Threshold active" from
the row is the valley edge: where your signal sits against it decides the
character, which is what `bias` and `gate` move.

* `drive` slams the signal into the curve; `tight` is a pre-clip low cut
  so the fold works on note energy, not mud.
* `bias` offsets the operating point — asymmetric fold, even harmonics.
* `gate` is bias STARVATION: an envelope follower slides the operating
  point off into the plateau as the note decays, so sustained notes decay
  through sputter and velcro instead of politely fading. 0 = no gating.
* DC from the moving operating point is cancelled exactly (the same curve
  evaluated at the operating point, subtracted), then `tone` tames fizz.

No mu flag on this row (unlike #29), so no mobius toggle — the catalog is
the contract. Louder, ruder sibling of bias_drift_fuzz.py: that one
breathes, this one bites.
"""

from supriya import synthdef
from supriya.ugens import HPF, In, Lag, LeakDC, LPF, Out, XFade2, Amplitude

from synthbase import module, param


def _dynatron(v, kink):
    """The negative-resistance transfer curve (works at ar and kr)."""
    return v.tanh() - kink * 1.7 * v * (-(v * v)).exponential()


@module(
    name="Dynatron Fuzz",
    kind="effect",
    params={
        "drive": param(1, 40, 12, curve="exp"),
        "kink": param(0, 1.5, 0.8),                 # negative-resistance depth
        "bias": param(-1, 1, 0.2),                  # operating point offset
        "gate": param(0, 1, 0.3),                   # bias starvation on decay
        "tight": param(40, 400, 90, curve="exp"),   # pre-clip low cut
        "tone": param(500, 9000, 4200, curve="exp"),
        "level": param(0, 1, 0.5),
        "mix": param(0, 1, 1.0),
    },
)
@synthdef()
def dynatron_fuzz(in_bus=0, drive=12, kink=0.8, bias=0.2, gate=0.3,
                  tight=90, tone=4200, level=0.5, mix=1.0, out=0):
    dry = In.ar(bus=in_bus, channel_count=2)
    drive = Lag.kr(source=drive, lag_time=0.02)
    kink = Lag.kr(source=kink, lag_time=0.02)
    tone = Lag.kr(source=tone, lag_time=0.02)
    level = Lag.kr(source=level, lag_time=0.02)

    # bias starvation: quiet input slides the operating point toward the
    # plateau -> decay sputter ("threshold active"). Loud input restores it.
    env = Amplitude.kr(source=(dry[0] + dry[1]) * 0.5,
                       attack_time=0.003, release_time=0.25)
    starve = Lag.kr(source=gate, lag_time=0.02) * (1.0 - (env * 6.0).min(1.0))
    op_point = Lag.kr(source=bias, lag_time=0.02) + starve * 0.9

    dc = _dynatron(op_point, kink)      # exact DC of the current op point
    wet = []
    for ch in range(2):
        v = HPF.ar(source=dry[ch], frequency=Lag.kr(source=tight, lag_time=0.02))
        y = _dynatron(v * drive + op_point, kink) - dc
        y = LPF.ar(source=LeakDC.ar(source=y), frequency=tone)
        wet.append(y * level)
    Out.ar(bus=out, source=XFade2.ar(in_a=dry, in_b=wet, pan=mix * 2.0 - 1.0))
