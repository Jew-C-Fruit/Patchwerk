# synthbase — house rules for vibecoding

This repo is a synth base on the SuperCollider server (scsynth), controlled
entirely from Python via **supriya**. The audio engine is a separate process:
broken Python can never glitch running audio.

## Architecture in one breath

`modules/*.py` (DSP recipes) → loaded by `synthbase/module.py` → instantiated
in order by `synthbase/rack.py` (buses between stages) → running on scsynth →
controlled live by `synthbase/midi.py` (notes/CCs) → hot-swapped on file save
by `synthbase/watcher.py`.

## Writing a new module (the main vibecoding activity)

Copy an existing file in `modules/` and change the body. The contract:

```python
from supriya import synthdef
from supriya.ugens import In, Out, ...   # 400+ UGens available

from synthbase import module, param

@module(
    name="Display Name",
    kind="source",              # "source" = generates audio; "effect" = processes it
    params={                    # every knob a human/MIDI/GUI may turn
        "cutoff": param(60, 12000, 1200, curve="exp"),  # min, max, default
    },
)
@synthdef()
def my_module(cutoff=1200, out=0):            # function name = stable key
    ...
    Out.ar(bus=out, source=[sig_l, sig_r])    # ALWAYS stereo out
```

Rules:

1. **Function name is the module's identity.** Patches and hot reload key on
   it. Renaming the function = a new module.
2. **Stereo everywhere.** `Out.ar` gets a 2-channel source. Mono signals:
   `[sig, sig]`.
3. **Effects must take `in_bus` and read `In.ar(bus=in_bus, channel_count=2)`.**
   Sources must not take `in_bus`. Everyone takes `out`.
4. **Every human-facing knob goes in `params`** with a sensible range and
   `curve="exp"` for frequencies/times. Defaults in the function signature
   should match the param defaults.
5. **MIDI-playable sources** additionally take `freq` and `gate` and wrap the
   signal in `EnvGen.kr(envelope=Envelope.adsr(...), gate=gate)`. Keep
   `done_action` unset (0) so the node survives release — mono voices are
   persistent nodes.
6. **Keep one module per file** unless variants truly belong together.
7. Smoothing: wrap params that will be twiddled in `Lag.kr(source=p,
   lag_time=0.02)` to avoid zipper noise.

UGen naming: supriya mirrors SuperCollider UGens with snake_case keyword args
(`SinOsc.ar(frequency=...)`, `RLPF.ar(source=..., frequency=...,
reciprocal_of_q=...)`). When unsure of an argument name, check
`python3 -c "import inspect; from supriya.ugens import X; print(inspect.signature(X.ar))"`.

## Patches

`patches/*.py` define `PATCH = {"chain": [...], "bindings": {...}}` — plain
data. Chain order = execution order = signal flow. First entry must be a
source. See `patches/demo.py`.

## Running

```bash
source .venv/bin/activate         # created by setup
python -m synthbase devices       # list MIDI inputs
python -m synthbase test          # 2s sine — verifies engine + audio out
python -m synthbase play patches/demo.py
```

Hot reload is on by default under `play`: edit any file in `modules/`, save,
hear the change without stopping.

## Testing changes

There's no audio in CI/cloud contexts. `python -m pytest tests/` (or
`tests/smoke.py`) exercises module loading and synthdef compilation without
booting a server; use it before claiming a module works. On the Mac,
`python -m synthbase test` is the real proof.

## Don'ts

- Don't use sclang or .scd files — Python only, we talk straight to scsynth.
- Don't add heavyweight wrapper abstractions; modules use supriya UGens
  directly. The base stays thin.
- Don't block in MIDI callbacks (they run on the port's thread).
