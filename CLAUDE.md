# synthbase — house rules for vibecoding

This repo is a synth base on the SuperCollider server (scsynth), controlled
entirely from Python via **supriya**. The audio engine is a separate process:
broken Python can never glitch running audio.

Patchwerk's engine package is called `synthbase` — a name from before the
project's own rename to Patchwerk. It stuck; renaming a working import path
across every module for cosmetic reasons isn't worth the risk (see Don'ts).
This is the canonical AI-guidance file; `AGENTS.md` at the repo root just
points here so tools that look for that name find it too.

## Architecture in one breath

`modules/*.py` (DSP recipes) → loaded by `synthbase/module.py` → instantiated
by `synthbase/rack.py` as a rewireable AUDIO GRAPH on scsynth (stereo buses
between stages) → note flow defined by a wire-based CONTROL PLANE in
`synthbase/app.py` → performed from the flex GUI (`synthbase/server.py`,
browser at `/`) and MIDI (`synthbase/midi.py`) → hot-swapped on file save by
`synthbase/watcher.py`.

## The graph world (v5+): ids, audio wires, control wires

**Instance ids.** Every module is spawnable MULTIPLE times. An instance id
is `"lowpass"`, `"lowpass.2"`, ... (`alloc_id` reuses freed suffixes); the
TYPE is `type_of(id)` = the part before the dot = the registry/synthdef key.
ALL protocol messages are keyed by instance id; a bare type key resolves to
the FIRST instance (legacy clients). Never treat an id as a type — derive it.

**Audio graph.** `app.graph_wires` overlays the linear chain: one outgoing
wire per source (rewiring = point its `out` at the destination's in-bus),
fan-in is free (buses SUM — extra sources sum into the running bus, never a
fresh orphaned one), disconnected outputs park on a persistent silent null
bus, and `reorder_for_wires` topo-sorts nodes so every wire's src executes
before its dst. Wires survive rebuilds; removal splice-heals A→X→B to A→B.

**Control plane.** `app.ctl_wires` is the note router — the graph IS the
routing. Node vocabulary: `keys` (all controllers enter here; never a
destination), `arp`, `deck` (the MIDI looper: keys→deck records raw,
arp→deck records voiced, deck→X replays), mono voices (`voice`,
`voice.2`, ...; each drives one target source), tonic derivers (`tonic.N`:
notes in → ctl THRU out + amber TONIC out; tonic outs land only on drone
instances' tonic-ins), and key shifters (`keyshift.N` with four isolated
LANES — endpoint grammar `"keyshift.2:3"` = lane 3; lane k in → shift →
lane k out only). Unwired events dead-end silently — honest patching.
EVERY silencing path must emit its note-offs (taps included): an unpaired
on is a stuck note and a stuck monitor bar.

**Global-vs-wired doctrine.** Transport/clock, panic + sustain, master
volume + IO config, pitch reference (transpose/bend), and persistence stay
GLOBAL. Everything else — who hears whom — is wire-defined.

## GUIs

The flex patch canvas is the front door (`/`): cards + subway-routed wires
derived from every `state` message, positions persisted per patch in
localStorage. The legacy panel lives at `/legacy` — when changing protocol
semantics, check BOTH pages (v8's "vanishing palette" bug lived only in the
legacy page's stale already-placed filter). The full websocket protocol is
documented in `synthbase/server.py`'s docstring.

**Monitors: local vs global.** Note/Waveform monitors and the scope are
LOCAL when wired/riding a wire (they show that path's traffic) and GLOBAL
when unwired (master feed / all taps). Source-fires emit ONE tagged tap
(`{"kind": "tap", "src": <node id>}`) per fire, not per edge.

**Blocks-geometry nomenclature (Cole, 2026-07-22 — use these words in
specs and asks).** A **unit** (= grid square) is the fine 16px grid cell
(`U = 16` in blocks.html). A **block** is the 10u×10u snappable area
(`BLK = 10`), separated by 2u gutters. Card sizes in those terms:
S = 10×4.5u (half a block), M = 10×10u (one block), L = 22×10u (two
blocks spanning their gutter). So "3 units high" means 3 grid squares
(48px) — never 3 blocks.

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
8. Param types: `curve="toggle"` renders a checkbox; `param(..., options=("a","b"))`
   renders a dropdown (value = option index; use `Select.ar/kr` in the DSP).
9. **Pitch offsets are always in semitones or cents**, never raw frequency
   ratios — convert inside the DSP with `.semitones_to_ratio()` (e.g.
   `(cents / 100).semitones_to_ratio()`). Voice-level pitch bend already
   follows this convention (±2 semitones).

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

## Running the GUI

```bash
./run.sh                          # relaunch cleanly (pidfile-managed)
python -m synthbase gui pad_space # or directly; GUI at http://127.0.0.1:8765
```

## Testing changes

There's no audio in CI/cloud contexts. Before claiming anything works, run:

- `python3 tests/smoke.py` — every module loads, synthdefs compile, patches
  parse, keyshift math sane.
- `python3 tests/test_graph.py` — audio-wire derivation, graph/ctl wire
  bookkeeping, instance ids, multi-voice, tonic→drone, key-shifter lanes and
  progression, tap-closure and snip-heal invariants (no server needed).
- `python3 tests/test_looper.py` — deck record/replay/overdub timing and
  take pairing.
- `python3 tests/gui_check8.py` — headless Playwright checks of flex.html
  against mock state/events (cards, wires, monitors, splices, key shifter,
  closure regressions). This is the current one; `gui_check.py`/`gui_check6.py`/
  `gui_check7.py` are earlier snapshots kept for reference, not upkeep. Write
  NEW checks failing-first against the broken behavior.

`test_mixed_sources.py`, `diag_*.py`, `hear_check.py`, and `probe_ws.py`
talk to a **live** server over websocket instead of running headless — they
need `python -m synthbase gui` actually running with real audio, so treat
them as Mac-only manual checks, not something CI or a cloud session can run.

On the Mac, `python -m synthbase test` is the real proof.

## Landmines (learned the hard way)

- scsynth crashers: `.clip()`, scaled `RecordBuf` sources, EnvGen-driven
  `record_level`; `In.ar(0)` at the root's tail reads junk.
- Playable sources must spawn `gate=0` (the synthdef default of 1 leaves
  idle voices droning after every rebuild).
- Extra sources SUM into the running bus — a fresh bus orphans everything
  upstream ("generators go dead").
- Sort looper events by beat with a STABLE key-only sort — tuple sort puts
  offs before ons at equal beats and scrambles pairing.
- Every all-off/silencing path must close its open notes AND their viz taps
  (panic, arp stop, deck stop, rebuilds, record-window exits).
- `system_profiler` (device lists) takes seconds — cache it; never call it
  per state snapshot.
- GUI sends during a websocket reconnect gap must queue, not drop (note-offs
  especially); macOS swallows letter keyups while ⌘ is held.

`docs/TROUBLESHOOTING.md` has the complementary, symptom-indexed list —
runtime/hardware gotchas (sample rate, Bluetooth, MIDI controllers) that
aren't code-facing enough to belong here.

## Don'ts

- Don't use sclang or .scd files — Python only, we talk straight to scsynth.
- Don't add heavyweight wrapper abstractions; modules use supriya UGens
  directly. The base stays thin.
- Don't block in MIDI callbacks (they run on the port's thread).
- Don't rename the `synthbase` package or its `python -m synthbase` entry
  point as a side effect of an unrelated change — it's intentionally stable
  even though the product name around it is now Patchwerk.
