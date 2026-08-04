# Architecture

This is the map of how Patchwerk's pieces fit together — the shape of the
system and why it has that shape. It is deliberately the *wide* view:

- For the module-authoring contract, see [`CLAUDE.md`](../CLAUDE.md).
- For the exhaustive, code-verified specification of every module, signal
  plane, endpoint and IO route, see [`REFERENCE.md`](REFERENCE.md) — that
  file is the SSOT, and where this document and it disagree, it wins.
- For GUI geometry, see [`BLOCKS_SPEC.md`](BLOCKS_SPEC.md).
- For known sharp edges, see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
- For how it got this way, see [`HISTORY.md`](HISTORY.md).

The engine package is called `synthbase`, from before the project's rename
to Patchwerk. The name stuck; the import path is intentionally stable.

## Design principles

These haven't changed since the project's first commit:

1. **Text is the source of truth.** Modules, patches, and bindings are plain
   code or plain data in git. Nothing binary, nothing position-encoded.
   (Card *geometry* is the deliberate exception — it lives in the browser's
   localStorage and the server never sees it.)
2. **Separate the DSP recipe from the wiring.** A *module* says what it does
   (oscillator, filter, delay). A *patch* — or, in the GUI, the live graph —
   says which modules exist and how they're connected. Wiring is data, not
   code.
3. **Bad module code must not kill the sound.** The audio engine
   (`scsynth`) is a separate OS process from the Python control plane. A
   broken module fails to load with a readable error while everything else
   keeps playing.
4. **Control is just signals.** MIDI notes/CCs, GUI wires, and (planned)
   sensor input all land in the same control layer — a module doesn't care
   where a value came from.

A fifth principle emerged later and now drives most of the design:

5. **The graph IS the routing.** There is no hidden config deciding who
   hears whom. If two things are connected, a wire says so; if nothing is
   wired, the event dead-ends silently. This is "honest patching", and it
   is why so much of the complexity lives in graph bookkeeping.

## The three layers

```
 MIDI hw ──┐                          ┌──────────────┐
           │   Python control plane   │   scsynth    │
 GUI ──────┼─► synthbase/ (engine +   │ audio server ├─► audio interface
(blocks.html) feature layer + loader) │ (node tree,  │◄── audio in
           │   modules/ loader        │  buses)      │
 (sensors, ┘   hot-reload watcher     └──────────────┘
  planned)
```

1. **Module** (`modules/*.py`, 26 of them) — one small Python file.
   Declares a SynthDef (the DSP) plus metadata: display name, `kind`
   (source/effect/`dual` — item 11 HAS landed, see below), params
   (name, range, default, curve). This is the main vibecoding surface —
   see `CLAUDE.md` for the contract.
2. **Patch** (`patches/*.py`, or the GUI's live graph) — which module
   instances exist, their settings, their order, and how buses connect them.
   In the CLI this is a static `PATCH = {"chain": [...], "bindings": {...}}`
   file. In the GUI the same model is edited live on the patch canvas and
   can be saved as a preset (`presets/`, via `synthbase/presets.py`).
3. **Bindings** — mappings from control sources (MIDI CC/note, GUI wire,
   eventually a sensor channel) to named params. Also data, not code.

## Instance ids: everything is spawnable

Every module can exist MULTIPLE times. An instance id is `"lowpass"`,
`"lowpass.2"`, … (`alloc_id` reuses freed suffixes), and the TYPE is
`type_of(id)` — the part before the dot, which is the registry/synthdef key.
**Every protocol message is keyed by instance id**; a bare type key resolves
to the FIRST instance of that type, a compatibility affordance for older
clients. An id is never a type: derive it, don't assume it.

## Four signal planes

This is the part of the architecture that has grown most since the first
version, and the part worth internalising. Patchwerk carries four distinct
kinds of connection, each with its own rules. `REFERENCE.md` specifies them
exhaustively; this is the shape:

| Plane | Carries | Stored in | Rule of thumb |
| --- | --- | --- | --- |
| **Audio** | stereo signal | `app.graph_wires` | one outgoing wire per source; fan-in is free (buses SUM) |
| **Control** | note events | `app.ctl_wires` | the note router — keys, arp, deck, voices, tonic derivers, key shifters |
| **Binary** | one hi/lo level | `ctl_wires` (kind inferred from source) | sources own LEVELS; edges DERIVE from level changes |
| **Mod** | modulation | `app.mod_wires` | LFO fan-out onto params; a param is single-input, so wiring steals |

**Audio.** `graph_wires` overlays the linear chain: rewiring points a
source's `out` at the destination's in-bus. Extra sources sum into a running
bus — allocating a fresh bus instead is the classic "generators go dead"
bug. Disconnected outputs park on a persistent silent null bus, and
`reorder_for_wires` topo-sorts nodes so every wire's source executes before
its destination. Wires survive rebuilds; removing a module splice-heals
A→X→B into A→B.

> ⚠ **PENDING (item 11, local-only branch `feat/p11-dual-mode`, not on
> `main`).** A third module kind, `dual`, generates AND processes. It
> changes the rule above: a wire into a plain source sums into the running
> bus, but a wire into a dual lands on its `in_bus` instead
> (`Rack._dst_bus`), so fan-in semantics become kind-dependent rather than
> universal. A dual's mode is derived from the graph, not set by the user —
> which makes "the graph IS the routing" load-bearing for DSP behaviour and
> not only for routing. See `REFERENCE.md` §2.2 and §10.3.1.

**Control.** The node vocabulary: `keys` (every controller enters here, and
it is never a destination), `arp`, `deck` (the MIDI looper), mono voices
(each driving one target source), tonic derivers (`tonic.N`, emitting a THRU
out and an amber TONIC out that lands only on drone instances), and key
shifters (`keyshift.N`, with four isolated lanes addressed as
`"keyshift.2:3"`). Unwired events dead-end silently.

**Binary.** Unified in the 07-23 rework: what used to be separate "ping"
(edge) and "gate" (level) kinds became ONE kind. Sources own a level;
trig-ins fire on RISING edges. Sources are buttons, clocks, thresholds,
logic outs and relay circuit outs. Logic gates (`AND | OR | NOR | XOR |
SR latch | T latch`) always expose exactly two endpoints `:a`/`:b`, whatever
the op, so wires never drop across an op swap.

**Mod.** LFOs are first-class nodes with fan-out, each running one
normalized bipolar control-bus synth that any number of destinations read.

**Global-vs-wired doctrine.** Transport/clock, panic + sustain, master
volume + IO config, pitch reference, and persistence stay GLOBAL.
Everything else — who hears whom — is wire-defined.

## Two ways to run it

**CLI (`python -m synthbase play <patch>`).** Loads a static patch file,
boots the engine, wires MIDI if the patch defines bindings, and hot-reloads
`modules/` on save. No GUI process — `synthbase/cli.py` drives `engine.py`,
`rack.py`, `midi.py` and `watcher.py` directly.

**GUI (`python -m synthbase gui`, or `./run.sh`).** `synthbase/app.py`
(`SynthApp`) wraps the same engine/rack/midi/watcher core and adds the live
graph state; `synthbase/server.py` (`GuiServer`) serves the page and speaks
a websocket protocol for params, notes, patches, device lists, meters and
scope data. **The full protocol is documented in `server.py`'s docstring.**

**`gui/blocks.html` IS the UI** — the only page served, at `/` and at
`/blocks` (an alias kept for bookmarks). It is one self-contained page with
no build step: cards plus gutter-routed subway wires, all derived from the
`state` message. It has TWO GEOMETRY MODES inside it — **blocks** (a
snapping grid) and **flex** (free positions, automatic height) — swapped
from the top bar, so new geometry behavior goes through one `uiMode`
dispatch rather than parallel per-mode code.

The earlier pages (`flex.html`, `index.html`, `graph.html`) are ARCHIVED
under `gui/legacy/`, **unserved and unmaintained**. There is no `/legacy`
route and they speak a dead protocol — don't "check both pages", and don't
edit them. (One live wrinkle: `tests/gui_check8.py` still loads two of them
by path, so if those files move, that suite breaks first.)

## Engine core (`synthbase/`)

| File | Role |
| --- | --- |
| `module.py` | The `@module`/`@synthdef` contract and the file loader (`load_all_modules`) |
| `engine.py` | Boots/quits `scsynth`, registers synthdefs, picks audio devices |
| `rack.py` | Builds the chain, wires audio buses, live param control; graph-capable — arbitrary rewiring, `alloc_id`, `type_of`, instance ids |
| `app.py` | `SynthApp` — the whole running system: ctl-wire router, node spawn/remove, state snapshot. The largest file, and deliberately so: it is where the planes meet |
| `server.py` | `GuiServer` — HTTP + websocket, the full client protocol |
| `midi.py` | MIDI notes → mono voices, CC bindings → rack params |
| `watcher.py` | Hot reload: recompiles a changed module file and hot-swaps it live |

## Feature layer (`synthbase/`)

Musical features rather than DSP modules — they aren't in `modules/`
because they aren't one SynthDef, they're control-plane behavior:

| File | Role |
| --- | --- |
| `arp.py` | Arpeggiator — a note-pool layer between controllers and the voice |
| `transport.py` | The shared musical clock (tempo/meter) that the arp, drums, deck and key shifter all ride |
| `drone.py` | Tonic derivers: a time-decaying pitch-class histogram picks a root; the literal deriver is the deterministic counterpart |
| `drums.py` | 16-step drum machine on the transport grid |
| `keyshift.py` | Transposes note streams by nearest offset from a settable key, with a steppable per-bar progression track |
| `looper.py` | Loop deck: wire-defined record taps, wiring-derived replay sink |
| `lfo.py` | Routable LFOs — standalone modulation nodes with fan-out |
| `gate.py` | The BINARY plane: the hi/lo model, logic gates, the settle algorithm |
| `ping.py` | Binary LEVEL sources — Button and Clock |
| `threshold.py` | CV crossing → binary level; a Schmitt comparator that edge-notifies rather than polling |
| `relay.py` | Type-agnostic switched junction — circuits that carry audio, notes, binary or mod |
| `presets.py` | Save/recall the full chain + feature-layer settings; the restart-resume snapshot |
| `scope.py` | Oscilloscope capture of any module's output |
| `master.py` | Master volume, limiter and level meters |
| `harmonics.py` | Odd-harmonic additive bank — a pure graph emitter with no engine coupling, shared by the power-sine family |
| `audio_devices.py` | CoreAudio device listing/selection helpers |

## Why the graph bookkeeping is the hard part

`rack.py` and `app.py` carry most of the system's complexity, and it is all
the same class of problem: keeping a live, rewireable graph consistent while
audio is running. Cycle rejection, one-output-per-source, reconnect-on-
rebuild, execution ordering, splice-healing on removal — and above all
**closure**: every silencing path (panic, arp stop, deck stop, rebuilds,
record-window exits) must emit its note-offs AND close their monitor taps.
An unpaired note-on is both a stuck note and a stuck monitor bar.

Two consequences worth knowing before changing anything here:

- **Monitors are local when wired and global when not.** A note/waveform
  monitor or scope riding a wire shows that path's traffic; unwired, it
  shows the master feed. A source fires ONE tagged tap per fire, not one
  per outgoing edge.
- **Indicators must react to logic, not just to clicks.** State applied
  inside the gate settle pass does not broadcast, so the backend emits its
  own `{"kind": "level", "ep", "on"}` tap for the GUI to route. Any new
  indicator is wired the same way — and because a headless mock can only
  prove the GUI reacts to a message we invented, this class of change is
  verified on the real rig.

## Testing philosophy

No audio hardware exists in CI or in cloud-assisted development sessions,
so the suite is split by what it actually needs.

**CI runs 9 Python suites and 3 Playwright suites, all headless:**
`smoke.py` (every module loads, synthdefs compile, patches parse),
`test_graph.py` (wire derivation, bookkeeping, instance ids, tap-closure
and snip-heal invariants), `test_looper.py`, plus `test_gate`,
`test_transport`, `test_ping`, `test_deriver`, `test_lfo`, `test_threshold`
— then `check_blocks.py`, `gui_check8.py` and `check_real.py` under
Playwright. **`check_real.py` is headless despite its name**: it replays a
state broadcast captured from the live rig into `blocks.html` through a
mock websocket, and needs neither a server nor audio.

`tests/test_power_sine.py` exists but is **not** wired into `ci.yml` — run
it by hand.

Anything that talks to a *running* `python -m synthbase gui` over a real
websocket — `test_mixed_sources.py`, `diag_*.py`, `hear_check.py`,
`probe_*.py` — is a Mac-only manual check. On the Mac,
`python -m synthbase test` is the real proof.

New checks are written failing-first against the broken behavior.

## What's not built yet

Sensor input over serial — the same binding layer as MIDI, per design
principle 4 — still hasn't landed. `pyserial` remains a dependency in
anticipation of it.
