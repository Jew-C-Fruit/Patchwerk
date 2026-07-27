"""Power Shaper: ONE card that GENERATES, and acts as FX when audio is wired in.

Item 11's dual-mode card (Cole, 07-24). The psine law — T_p(A) = sgn(A)*|A|^(2/p),
memoryless, computed per sample — is input-agnostic, so ONE synthdef can shape
either its own internal sine or whatever arrives on `in_bus`:

    mode 0  GENERATE — the law over an internal SinOsc, enveloped by `gate`
                       and levelled by `amp` (== power_sine_shaper at drive=1)
    mode 1  FX       — the law over In.ar(in_bus), blended dry/wet by `mix`

`mode` is NOT a knob. It is DERIVED from the audio graph and pushed by
`App._sync_dual_modes`: a stored audio wire whose destination is this instance
means FX, no wire means GENERATE. The card shows which mode it is in and reacts
to the backend's `{"kind": "level", "ep": "<id>:mode"}` tap — a shaper silently
switching from generating to processing is exactly the invisible state change
the reactive-indicator doctrine exists to prevent.

Both chains are computed and crossfaded through a LAGGED `mode`, so a wire
landing (or being cut) is click-free rather than a hard switch.

    p = 2   -> exponent 1 -> identity (drive still applies)
    p -> 64 -> exponent -> 0 -> sgn(x)  -> hard square-off
    p < 2   -> exponent > 1 -> pinched / peaky
Like the generator it is NOT band-limited: as p climbs, fold-back aliasing is
the sonic fingerprint. `drive` pushes the signal into the curve (for p > 2 the
sub-unity exponent compresses hot signals).
"""

from supriya import Envelope, synthdef
from supriya.ugens import EnvGen, In, Lag, LeakDC, Out, SinOsc

from synthbase import module, param


@module(
    name="Power Shaper",
    kind="dual",
    params={
        "freq": param(20, 2000, 220, curve="exp"),   # GENERATE mode only
        "p": param(1, 64, 2.0, curve="exp"),         # 2 = identity, ->64 ~ square-off
        "drive": param(0.25, 8.0, 1.0, curve="exp"),
        "amp": param(0, 1, 0.3),                     # GENERATE mode only
        "mix": param(0.0, 1.0, 1.0),                 # FX mode only
    },
)
@synthdef()
def power_shaper(in_bus=0, out=0, mode=0, freq=220, p=2.0, drive=1.0,
                 amp=0.3, mix=1.0, gate=1):
    a = 2.0 / Lag.kr(source=p, lag_time=0.02)          # exponent 2/p
    dr = Lag.kr(source=drive, lag_time=0.02)

    # -- GENERATE: the law over an internal sine ----------------------------
    osc = SinOsc.ar(frequency=Lag.kr(source=freq, lag_time=0.01)) * dr
    gen = LeakDC.ar(source=osc.sign() * (abs(osc) ** a))   # sgn(x)*|x|^(2/p)
    env = EnvGen.kr(envelope=Envelope.adsr(0.01, 0.1, 0.85, 0.4), gate=gate)
    gen = gen * env * amp

    # -- FX: the same law over the incoming signal --------------------------
    dry = In.ar(bus=in_bus, channel_count=2)
    x = dry * dr
    wet = LeakDC.ar(source=x.sign() * (abs(x) ** a))      # DC guard
    m = Lag.kr(source=mix, lag_time=0.02)
    fx = dry * (1 - m) + wet * m

    # -- MODE crossfade (lagged => a wire landing is clickless) -------------
    mo = Lag.kr(source=mode, lag_time=0.02)
    g = gen * (1 - mo)
    Out.ar(bus=out, source=[g + fx[0] * mo, g + fx[1] * mo])
