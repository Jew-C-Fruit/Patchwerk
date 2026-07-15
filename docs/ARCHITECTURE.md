# Architecture

This is the map of how Patchwerk's pieces fit together. For the module
authoring contract specifically, see [`CLAUDE.md`](../CLAUDE.md) — this
document is the wider picture around it. For known sharp edges, see
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md). For how it got this way, one
version at a time, see [`HISTORY.md`](HISTORY.md).

## Design principles

These haven't changed since the project's first commit:

1. **Text is the source of truth.** Modules, patches, and bindings are plain
   code or plain data in git. Nothing binary, nothing position-encoded.
2. **Separate the DSP recipe from the wiring.** A *module* says what it does
   (oscillator, filter, delay). A *patch* — or, in the GUI, the live graph —
   says which modules exist and how they're connected. Wiring is data, not
   code.
3. **Bad module code must not kill the sound.** The audio engine
   (`scsynth`) is a separate OS process from the Python control plane. A
   broken module fails to load with a readable error while everything else
   keeps playing.
4. **Control is just signals.** MIDI notes/CCs, GUI wires, and (planned)
   sensor input all land in the same control-bus layer — a module doesn't
   care where a value came from.

## The three layers

```
 MIDI hw ──┐                          ┌──────────────┐
           │   Python control plane   │   scsynth    │
 GUI ──────┼─► synthbase/ (engine +   │ audio server ├─► audio interface
 (flex.html)  feature layer + loader) │ (node tree,  │◄── audio in
           │   modules/ loader        │  buses)      │
 (sensors, ┘   hot-reload watcher     └──────────────┘
  planned)
```

1. **Module** (`modules/*.py`) — one small Python file. Declares a SynthDef
   (the DSP) plus metadata: display name, `kind` (source/effect), params
   (name, range, default, curve). This is the main vibecoding surface — see
   `CLAUDE.md` for the contract.
2. **Patch** (`patches/*.py`, or the GUI's live graph) — which module
   instances exist, their settings, their order, and how buses connect them.
   In the CLI, this is a static `PATCH = {"chain": [...], "bindings": {...}}`
   file. In the GUI, the same model is edited live on the patch canvas —
   dragging modules, splicing wires — and can be saved as a preset
   (`presets/`, via `synthbase/presets.py`).
3. **Bindings** — mappings from control sources (MIDI CC/note, GUI wire, and
   eventually a sensor channel) to named params. Also data, not code.

## Two ways to run it

**CLI (`python -m synthbase play <patch>`).** Loads a static patch file,
boots the engine, wires MIDI if the patch defines bindings, and hot-reloads
`modules/` on save. No GUI process involved — `synthbase/cli.py` drives
`engine.py`, `rack.py`, `midi.py`, and `watcher.py` directly.

**GUI (`python -m synthbase gui`, or `./run.sh`).** `synthbase/app.py`
(`SynthApp`) wraps the same engine/rack/midi/watcher core and adds live
graph state; `synthbase/server.py` (`GuiServer`) serves `gui/flex.html` and
speaks a websocket protocol for params, notes, patches, device lists, and
meter/scope data. `gui/flex.html` is the current front end — a graph/wire
canvas where audio connections *and* control connections are both literal,
draggable wires. `gui/index.html` is an earlier, simpler front end kept
around as a fallback (`/legacy` route); `gui/graph.html` is a read-only
signal-flow view.

## Engine core (`synthbase/`)

| File | Role |
| --- | --- |
| `module.py` | The `@module`/`@synthdef` contract and the file loader (`load_all_modules`) |
| `engine.py` | Boots/quits `scsynth`, registers synthdefs, picks audio devices |
| `rack.py` | Builds the chain in order, wires audio buses between stages, live param control. In the GUI world this is graph-capable: arbitrary rewiring, not just a fixed chain (`alloc_id`, `type_of`, instance ids like `lowpass.2` for multiple instances of one module type) |
| `midi.py` | MIDI notes → mono voice (`MonoVoice`), CC bindings → rack params |
| `watcher.py` | Hot reload: recompiles a changed module file and hot-swaps it into the running rack |

## Feature layer (`synthbase/`)

Built on the engine core, these are musical features rather than DSP
modules — they don't live in `modules/` because they're not one SynthDef,
they're control-plane behavior:

| File | Role |
| --- | --- |
| `arp.py` | Arpeggiator — a note-pool layer between controllers and the voice |
| `transport.py` | Shared musical clock (tempo/meter) that the arp, drums, and key shifter ride |
| `drone.py` | Root-following pedal tone: a time-decaying pitch-class histogram picks a root, moves are quantized to the transport grid |
| `drums.py` | Drum machine (`DrumMachine`), on the transport grid |
| `keyshift.py` | Transposes note streams by nearest offset from a settable key, with a steppable per-bar progression track (`KeyShifter`, `nearest_offset`) |
| `lfo.py` | Engine-native control buses mappable onto any param |
| `looper.py` | Loop deck: wire-defined record taps, wiring-derived replay sink (`_FanSink`) |
| `presets.py` | Save/recall full chain + feature-layer settings |
| `scope.py` | Inline oscilloscope tap |
| `master.py` | Master volume/limiter |
| `audio_devices.py` | CoreAudio device listing/selection helpers |

## The control plane is wire-based, not just "bindings"

Beyond static MIDI-CC-to-param bindings, the GUI models control connections
as first-class wires alongside audio wires (`app.ctl_wires`): the arpeggiator,
key shifter, deck, drone tonic, and voice targets are all things you can
patch together on the canvas, not just configure in a dropdown. A "tap"
(`{"kind": "tap", "src": ...}`) is how a stage exposes its output to be
wired elsewhere — both audio taps and control taps use the same concept.
This is why `rack.py` and `app.py` carry most of the graph-bookkeeping
complexity: cycle rejection, one-output-per-source rules, reconnect-on-
rebuild, and closing off open notes/taps cleanly when a voice or module is
removed (see `TROUBLESHOOTING.md` for the bugs this history produced).

## Testing philosophy

No audio hardware exists in CI or in this repo's cloud-assisted development
sessions. `tests/smoke.py`, `test_graph.py`, and `test_looper.py` exercise
module loading, synthdef compilation, and graph/control-plane logic without
booting a server. `tests/gui_check8.py` drives `gui/flex.html` with a
headless browser (Playwright) to check GUI behavior without a synth server.
Anything that needs a live server and real audio (`test_mixed_sources.py`,
`diag_*.py`, `hear_check.py`, `probe_ws.py`) is a Mac-only manual check —
see `CONTRIBUTING.md`.

## What's not built yet

Sensor input over serial (the same binding layer as MIDI, per the design
principles above) hasn't landed as of this doc. `pyserial` is already a
dependency in anticipation of it.
