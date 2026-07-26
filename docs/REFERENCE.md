# Patchwerk Reference — the single source of truth

> **LIVING DOCUMENT — revision policy.** This file is the canonical,
> code-verified reference for every module, every signal system, and all
> IO routing in Patchwerk. It is **revised thoroughly at every release**:
> re-verify each section against the source it cites, update the stamp
> below, and prune anything the code no longer does. Section numbering is
> stable on purpose — the release-specific graphical user manual (a
> separate, image-rich document) is being written against these section
> numbers, so renumber only at a major restructure. When this doc and the
> code disagree, the code wins and this doc has a bug: fix it here.
>
> **The user manual is IN PROGRESS — it is not published yet.** Section
> numbers here were frozen in PR #45 so it could cite them, and it is
> being drafted on the `docs/manual-scaffold` branch: 17 chapters plus
> `DESIGN.md`, `FIGURES.md` and a headless `capture.py`. Its cross-
> references into this file are tracked as `XREF-TODO` anchors and are
> **not yet resolved**, so nothing outside this repo should link to a
> manual section by number yet. Until it lands, THIS file is the only
> reference — a pointer here to "the manual" is a statement of intent,
> not of something a reader can go and open.
>
> **Verified against:** `main` @ `1020d20` (v2.0 "Wavetable" pre-release
> line), 2026-07-26. First written against `eb360ef` and re-verified
> forward through PRs #37–#44: Batch B blocks polish, P4 mod-handle
> linkage, the item-20 top-margin REVERT (#40), tidy-into-frame (#41),
> M-default graphical cards (#42), the relay tweaks (#43) that added 1:1
> contacts and the `mod` circuit plane, and the wire fan-nesting rule
> (#44). #40–#44 are GUI-only and change nothing this doc specifies
> outside §13, which defers UI geometry to `docs/BLOCKS_SPEC.md`.
>
> **Unmerged work is marked, never stated as shipped.** This file describes
> `main`. Where a finished-but-unmerged branch would change what a section
> says, the pending behaviour is called out in a blockquote naming the
> branch, so nobody builds against something that is not there yet. Open as
> of 2026-07-26: item 11's `dual` kind (`feat/p11-dual-mode`) in §2.2 and
> §10.3/§10.3.1. Clear each marker as its branch merges.
>
> Re-verified 2026-07-26 through PRs #45–#53 and the item-32 merge:
> the T latch (#47) is now specified in §5.4; §8.1–8.2 carry item 32's
> stopped boot and the drone-pause INVARIANT, and §12.3 the
> resume-vs-preset play-state asymmetry; the test inventory in Appendix A
> was corrected — `check_real.py` is a HEADLESS Playwright suite that runs
> in CI, not a live-rig-only check.
> **The relay-audio and effects sections were revised with P1** — item 25
> (relay audio circuits became permanent lagged-gate synths; `state.wires`
> now broadcasts the stored graph) and item 11 (the new `power_shaper`
> effect). Everything else still stands as verified.

Sibling docs and their jobs (this doc does not duplicate them):
`CLAUDE.md` = the module-authoring contract + house rules for LLM work;
`docs/ARCHITECTURE.md` = the narrative map; `docs/BLOCKS_SPEC.md` = the
Blocks UI design spec; `docs/HISTORY.md` = how it got this way;
`docs/TROUBLESHOOTING.md` = symptom-indexed sharp edges;
`docs/RELEASE_WAVETABLE.md` = the current release's patch notes.

---

## 1. System overview

Patchwerk is a live-patchable synthesizer: DSP modules are small Python
files, hot-reloaded into a running audio engine; the signal path is a
live patch graph — audio wires and control wires alike — edited in the
browser; MIDI and GUI input land in one control layer.

Three processes/layers:

| Layer | What | Where |
| --- | --- | --- |
| Audio engine | `scsynth` (SuperCollider server) — a separate OS process; broken Python can never glitch running audio | booted/owned by `synthbase/engine.py` via [supriya](https://github.com/supriya-project/supriya) |
| Control plane | Python: graph bookkeeping, note routing, timing, MIDI, hot reload | `synthbase/` |
| GUI | one self-contained page, `gui/blocks.html`, over a websocket | served by `synthbase/server.py` at `http://127.0.0.1:8765` |

The engine package is named `synthbase` (predates the product's rename to
Patchwerk; deliberately stable — see CLAUDE.md's Don'ts). Entry point:
`python -m synthbase`.

### 1.1 Run modes

| Command | What runs |
| --- | --- |
| `python -m synthbase devices` | list MIDI inputs, audio-device help |
| `python -m synthbase test` | boot engine, 2 s sine — proves engine + audio out |
| `python -m synthbase play <patch.py> [--no-midi] [--no-reload]` | headless CLI: static patch, MIDI bindings, hot reload; no GUI process (`cli.py` drives engine/rack/midi/watcher directly) |
| `python -m synthbase gui [patch] [--port N] [--in-device D] [--out-device D] [--hw-buffer F] [--no-midi] [--no-reload] [--no-browser]` | full app: `SynthApp` (`app.py`) + `GuiServer` (`server.py`); default patch `demo`, default port 8765; opens a browser on fresh boots (never on ⟳-resume boots) |
| `./run.sh` | pidfile-managed clean relaunch of the GUI |

### 1.2 Repo layout (code-bearing paths)

| Path | Role |
| --- | --- |
| `modules/*.py` | DSP modules, one per file — the vibecoding surface (§10) |
| `patches/*.py` | plain-data chain + MIDI binding definitions (§12.1) |
| `presets/*.json` | saved full-state snapshots (§12.2) |
| `gui/blocks.html` | THE GUI (served at `/`); `gui/legacy/` = archived old GUIs, not served |
| `synthbase/` | engine core + feature layer (§1.3) |
| `tests/` | headless suites + Mac-only live probes (§15) |

### 1.3 `synthbase/` file-by-file

| File | Role | Doc section |
| --- | --- | --- |
| `module.py` | `@module`/`@synthdef` contract, `Param`, file loader, `FAMILIES` | §2.2, §10 |
| `engine.py` | scsynth boot/quit, root group, synthdef registration (dedupe + big-def `/d_load` path) | §3.6 |
| `rack.py` | instances, buses, audio graph overlay, live rewiring, bypass, swap, hot-reload respawn | §3 |
| `app.py` | `SynthApp`: the whole running system; ctl-wire router, node spawn/remove, state snapshot | §4–§8 |
| `server.py` | HTTP + websocket protocol (docstring = protocol source of truth) | §11 |
| `midi.py` | `MonoVoice`, `MidiRouter` (notes, CC bindings, bend, sustain) | §4.3, §8.4 |
| `arp.py` | `Arpeggiator` — note-pool layer on the transport grid | §4.4 |
| `looper.py` | `Looper` — the MIDI Loop Deck | §9 |
| `drone.py` | `TonicDeriver` (estimator), `LiteralDeriver`, `RootEstimator`, scale machinery | §4.5 |
| `keyshift.py` | `KeyShifter` — 4-lane transposer + bar-synced progression | §4.6 |
| `relay.py` | `RelayNode` — type-agnostic switched junction, audio resolution | §7 |
| `gate.py` | `GateManager` + `LogicGate` — the binary plane | §5 |
| `ping.py` | `ButtonTrigger`, `ClockTrigger` — binary level/pulse sources | §5.2 |
| `threshold.py` | `ThresholdManager`/`ThresholdNode` — CV crossing → binary | §5.2, §6.2 |
| `lfo.py` | `LFOManager` — routable LFO nodes + per-destination scaling | §6.1 |
| `transport.py` | `Transport` (clock), `TapTempo`, `DIVISIONS`, `_click` | §8.1 |
| `drums.py` | `DrumMachine` — 16-step sequencer + 4 one-shot voices | §8.3 |
| `master.py` | `MasterSection` — volume, limiter, meters | §3.5 |
| `scope.py` | `Scope` — continuous ring-buffer waveform capture | §6.3 |
| `presets.py` | preset save/load, `/restart` resume snapshot | §12.2–12.3 |
| `watcher.py` | `Reloader` — hot reload of `modules/` on save | §3.7 |
| `harmonics.py` | `odd_harmonic_bank` + coefficient laws (psine family helper) | §10.4 |
| `audio_devices.py` | CoreAudio device enumeration (cached 5 s) | §3.6 |
| `cli.py` / `__main__.py` | entry points | §1.1 |
| `__init__.py` | public exports: `Engine`, `Module`, `Param`, `Rack`, `module`, `param`, `odd_harmonic_bank`, `power_law_coeffs`, `square_blend_coeffs` | — |

---

## 2. Vocabulary and core concepts

### 2.1 Instance ids and types

Every module is spawnable multiple times. An **instance id** is
`"lowpass"`, `"lowpass.2"`, `"lowpass.3"`, … (`alloc_id` in `rack.py`
reuses freed suffixes). The **type** of an id is the part before the dot
(`type_of("lowpass.2") == "lowpass"`) — the registry/synthdef key. ALL
protocol messages are keyed by instance id; a bare type key is legacy
compat and resolves to the FIRST instance of that type (`Rack.find`).
Never treat an id as a type — derive it.

The same allocator names every spawnable control node: `voice`/`voice.2`,
`tonic.2`, `literal`, `keyshift.2`, `lfo`, `threshold`, `button`,
`clock`, `logic`, `relay`, ….

### 2.2 Module kinds and families

`kind` is the DSP contract: `"source"` (generates audio; no `in_bus`)
or `"effect"` (processes audio; takes `in_bus`, reads
`In.ar(in_bus, channel_count=2)`). Everyone takes `out`. Audio is
**stereo everywhere** between stages.

> ⚠ **PENDING — a third kind, `"dual"`, is built but NOT on `main`**
> (item 11, local-only branch `feat/p11-dual-mode`, 2026-07-26). A dual
> module generates AND processes: it always owns an `in_bus` even while
> generating, and `module.py` gains `KINDS = ("source", "effect", "dual")`
> plus the two predicates the engine branches on, `generates(kind)`
> (source + dual) and `takes_audio_in(kind)` (effect + dual).
>
> It changes what an incoming audio wire MEANS, which matters well beyond
> §2.2: a wire into a plain source sums into the running bus, while a wire
> into a dual lands on its `in_bus` (`Rack._dst_bus`). Its `mode` is not a
> param — `App._sync_dual_modes` derives it from the stored audio graph
> (wired ⇒ FX, unwired ⇒ generate) and announces it with a
> `{"kind": "level", "ep": "<id>:mode"}` tap per the reactive-indicator
> doctrine. On `main` today `kind` is validated against
> `("source", "effect")` only. **`CLAUDE.md` carries the authoring rules.**

`family` is a GUI-grouping label from `FAMILIES` in `module.py`
(fallback: the kind): `voice` (wobble_saw, pulse_pad, fm_bell, pluck,
wind), `input` (audio_in), `service` (drone), `filter` (lowpass,
telephone), `time` (echo, reverb, chorus, flanger, phaser, autopan),
`dirt` (drive, bitcrush, wavefolder), `dyn` (compressor), `vox`
(pitchshift, ringmod), `psine` (the power-sine trio). `scope_tap` is not
in the table, so its family is its kind (`effect`).

A source is **note-playable** iff its synthdef exposes both `freq` and
`gate` (`app._guess_voice_target`). Playable sources spawn with
`gate=0` (silent) — the synthdef default of `gate=1` would leave idle
voices droning after every rebuild.

### 2.3 The wire kinds

Everything patchable is a wire; there are four distinct wire systems:

| Wire kind | Carried in | Endpoints | Message |
| --- | --- | --- | --- |
| **Audio** | `app.graph_wires` | module instance ids, `"master"`, relay circuit endpoints `"relay:k"` | `graph_wire` |
| **Notes** (control) | `app.ctl_wires` | ctl node ids + lane/circuit grammar (§4.1) | `ctl_wire` |
| **Binary** (hi/lo) | `app.ctl_wires` (same list; kind inferred from the source endpoint) | binary sources → level-ins/trig-ins (§5.3) | `ctl_wire` |
| **Modulation** (CV) | LFO dest records / threshold `source`; `app.mod_wires` for relay-routed hops | LFO out → param handle; LFO out → threshold CV-in; LFO out → `"relay:k"` → `"<key>:<param>"` | `lfo_wire`, `threshold_wire`, `mod_wire` |

A **tap** (`{"kind": "tap", "src": <node id>, "note", "on"}`) is the viz
event a control node emits when it fires — ONE per source-fire, not per
edge. Monitors riding a wire filter client-side by `src`.

### 2.4 The global-vs-wired doctrine

Wire-defined (who hears whom): all audio routing, all note routing, all
binary routing, all modulation. Global (never wires): transport/clock
(one shared timeline), panic + sustain pedal + pitch bend (physical
gestures on the instrument — they apply to ALL voices), master volume +
IO device config, transpose (±24 semitones, all voices), persistence.

### 2.5 The closure doctrine

EVERY silencing path must emit its note-offs, including viz taps: panic,
arp stop/disable, deck stop, record-window exits, wire removal, node
removal, rebuilds. An unpaired note-on is a stuck note downstream and a
stuck full-width bar on every note monitor. Grep points:
`_close_taps`/`all_off` (app.py), `_close_open_take`/`_release_all`
(looper.py), `shutdown` on every deriver/keyshifter.

### 2.6 Blocks geometry nomenclature (GUI specs)

Cole's canonical words (2026-07-22): a **unit** (= grid square) is the
fine 16 px grid cell (`U = 16` in blocks.html). A **block** is the
10 u × 10 u snappable area (`BLK = 10`), separated by 2 u gutters. Card
footprints: **S** = 10×4.5 u (half a block), **M** = 10×10 u (one
block), **L** = 22×10 u (two blocks spanning their gutter), and **XS** =
4.5×4.5 u (a block quadrant — 2 per half-block, 4 per block, 1 u
internal gutters; opt-in per card via `cfg.allowXS`, used by the simple
binary cards). "3 units high" means 3 grid squares (48 px) — never 3
blocks.

NOTE (2026-07-24): an experimental +1-unit top margin (`TOPM`, item 20)
was shipped and then REVERTED in PR #40 — it desynchronised the card
grid from the wire router. There is no top margin; do not reintroduce
one without re-deriving the router geometry with it.

---

## 3. The audio graph

### 3.1 Chain build (`Rack.build`)

A patch's `chain` list is instantiated in order; duplicates auto-suffix
into fresh ids. First entry must be a source (an effect first raises).
For each stage: sources mid-chain **sum into the running bus** (a fresh
bus would orphan everything upstream — the "generators go dead" bug);
each non-last stage owning a bus gets a private stereo bus group feeding
the next stage; effects get `in_bus` = the previous stage's bus; the
last stage outs to hardware bus 0. A chain ending on a summed source
gets a `_bypass` **tail router** node forwarding the shared bus to
hardware. Nodes are added to the tail of the engine's root group.

### 3.2 The graph overlay (`app.graph_wires`)

`graph_wires` (list of `{"from": id, "to": id | "master" | None}`)
overlays the linear chain; `None` until the first structural edit adopts
the current derived wiring. Rules, enforced in `app.graph_wire` +
`rack.audio_rewire`:

- **One outgoing audio wire per source node.** Re-adding replaces.
- **Fan-in is free** — buses SUM. A wire into an effect lands on its
  `in_bus`; a wire into a source lands on that source's own out bus
  (summing); `"master"` = hardware bus 0.
- **Disconnect parks** the output on a persistent silent **null bus**
  (`Rack.null_bus`, lazily allocated per rack) — never bus 0.
- **Cycle guard**: a walk over the stored wires rejects any add that
  would loop (relay endpoints walk like nodes, regardless of closed
  state).
- **Execution order**: `reorder_for_wires` guarantees every wire's src
  node executes before its dst (cheap path: if current order already
  satisfies all wires, zero server ops; otherwise topological sort +
  `/n_before` moves). Cycles refuse to reorder. Service instances
  (drone riding the chain head, LFO norm synths) are pinned.
- Wires **survive rebuilds**: `_reapply_graph_wires` re-imposes stored
  wires for ids that still exist after any patch/preset rebuild.
- **Splice-heal on remove**: removing module X with A→X→B re-aims every
  feeder of X at X's own destination (A→B). `edit_chain("remove")` also
  scrubs X's ctl wires, `:pwr` gate wires, LFO dests, drone sink, and
  re-aims the drums target.

### 3.3 Live edits without rebuild

`edit_chain("add")` / `spawn_unconnected` spawn ONE node parked on the
null bus — no reorder, no wire reapply, nothing running is touched (one
exception: voices that died for lack of a target come back aimed at the
new source). `swap_synth` (Instrument card) replaces an instance's
module type IN PLACE: same id, buses, wires, node order (server
`REPLACE`); params shared by name carry over, the rest reset; sources
come up `gate=0`; LFO dests on params the new type lacks are unwired.
`edit_chain("move")` is a pure list reorder (audio order is
wire-defined).

### 3.4 Enable/bypass (`Rack.set_enabled`)

Sources pause/unpause their node (silence, state kept). Effects are
REPLACEd with a `_bypass` passthrough synth (true bypass — the chain
keeps flowing), and REPLACEd back with the real synthdef on re-enable.
Re-enable fires `on_node_replaced` so LFO mappings re-map (§6.1).

### 3.5 Master section (`master.py`)

Two infrastructure synths after the root group (never in a patch):
`_master` reads hardware bus 0, applies lagged volume (50 ms), a
**limiter at 0.95** (the seatbelt — feedback, looper stacking, LFO
extremes may not scream), `ReplaceOut`s in place, and writes stereo peak
amplitudes to meter control buses; `_input_meter` writes the hardware
input level to a third. `app.levels()` polls them for the GUI meters.

### 3.6 Engine and devices (`engine.py`, `audio_devices.py`)

Boot options: free UDP port (never collides with a stale scsynth), 2-in
2-out, block size 64, hardware buffer 256 frames (~5 ms @ 48 kHz;
`--hw-buffer` overrides). macOS sample-rate mismatch fallback: if boot
fails on rate, auto-pick a rate-matched input (preferring built-in),
else boot output-only; the reason lands in `boot_note` (surfaced in
`state`). Synthdef registration is deduped per server; synthdefs whose
compiled form exceeds ~8000 bytes ship via `/d_load` from a temp dir
(oversized `/d_recv` datagrams are silently dropped by scsynth and the
following sync hangs forever). Device lists come from
`system_profiler SPAudioDataType` with a 5 s cache — it takes seconds;
never call it per state snapshot. `set_devices` = full engine reboot
(stop → start, brief silence), preserving patch + volume; LFO/threshold
managers track the server OBJECT so a rebooted server always re-receives
synthdefs and the `/tr` callback.

### 3.7 Hot reload (`watcher.py`, `Rack.respawn`)

Watchdog on `modules/`, 0.25 s debounce. On save: recompile the file;
on error print and keep the old version running. On success: register
and REPLACE every running instance **matching by type** in place
(settings preserved, new params get defaults); disabled effects adopt
the new def on re-enable; disabled sources stay paused. Not-in-rack
modules just update the registry.

### 3.8 Audio IO routing summary

- Hardware out = SC bus 0 (stereo). Master volume/limiter/meter operate
  in place on it.
- Hardware in = first input bus after the output buses
  (`NumOutputBuses.ir()`); the `audio_in` module reads channel 1 and
  spreads to stereo. Input can be disabled entirely by the boot
  fallback (`input_enabled` in `state`).
- Inter-stage audio = private stereo bus groups; summing junctions are
  free fan-in.
- Disconnected outputs = the rack's persistent null bus.
- Drum hits spawn one-shot synths aimed at `drums.target`: `"master"`
  (bus 0, tail), a module id (effect → its `in_bus`, source → its out
  bus, head), or `None` (silent, no spawn).
- The deck's private voice node and the legacy drone service instance
  write into the chain head's bus (ride the whole effect chain).

---

## 4. The control plane: note routing

### 4.1 Node vocabulary and endpoint grammar

`app.ctl_wires` is the note router — **the graph IS the routing**; an
unwired node's events dead-end silently (honest patching). Endpoint
grammar: plain ids, plus `":"` suffixes for lane-like sub-endpoints —
`"keyshift.2:3"` = lane 3 of keyshift.2, `"relay:5"` = circuit 5,
`"logic:a"`, `"lowpass:pwr"`, `"deck:rec"`, `"transport:tap"`.

| Node | Ids | Note out? | Note in? | What it is |
| --- | --- | --- | --- | --- |
| Keys | `keys` (singleton) | yes | **never** (would re-enter the controllers) | where ALL controllers enter: GUI keys, hardware MIDI |
| Arpeggiator | `arp` (singleton) | yes | yes | note-pool layer (§4.4); disabled = pass-through |
| Loop Deck | `deck` (singleton) | yes (replay) | yes (record) | the MIDI looper (§9) |
| Mono voices | `voice`, `voice.2`, … | — (drives audio) | yes | each drives one playable source's `freq`/`gate` (§4.3) |
| Estimator deriver | `tonic`, `tonic.2`, … | yes (mono root) | yes (evidence) | scale-aware root estimation (§4.5) |
| Literal deriver | `literal`, `literal.2`, … | yes (mono) | yes | deterministic extract×place (§4.5) |
| Drone instances | `drone`, `drone.2`, … (module instances) | no | yes (mono, single-input) | ctl note-sink retargeting the drone module's `freq` (§4.7) |
| Key shifter | `keyshift:1`…`:4` per instance | per lane | per lane | 4 isolated transposition lanes (§4.6) |
| Relay circuits | `relay:1`…`:9` per instance | per circuit | per circuit | switched junction, kind-inferred (§7) |

Validation (`_ctl_src_ok`/`_ctl_dst_ok`): sources = `keys`, `arp`,
`deck`, derivers, keyshift lanes, relay circuits. Destinations = `arp`,
`deck`, voices, derivers, drone instances, keyshift lanes, relay
circuits. Self-wires are forbidden at the NODE level (even cross-lane).
`deck→arp→deck` is legal — the deck's `_self_fire` guard stops replayed
notes from re-recording. Binary-source wires take a different validation
path entirely (§5.3).

Default wiring (fresh patch / `select_patch`):
`keys→arp`, `arp→voice`, `arp→deck`, `deck→voice`.

### 4.2 Dispatch and removal hygiene

`_ctl_sinks(src)` resolves a node's outgoing wires to sink objects LIVE
on every event — wire edits apply on the very next note. Every sink
implements the MonoVoice-shaped interface: `note_on(note, velocity)`,
`note_off(note)`, `all_off()`, `set_sustain(on)`, `set_bend(semitones)`.

Removal rules: unhooking a node's LAST input silences it (stuck notes
are worse than dropped ones); unhooking `deck→voice` tears down the
deck's private node; a new wire into a drone drops the previous
controller's held state. Removing a deriver or keyshifter **snip-heals**
A→X→B to A→B, but only when unambiguous (exactly 1 in and 1 out;
keyshift heals per lane; binary/ping wires never heal). Removing a
voice/relay/binary source drops all its wires.

### 4.3 Mono voices (`MonoVoice`, midi.py)

Last-note-priority mono: a held-note stack; `note_on` re-aims the target
source's `freq` and sets `gate=1`; releasing the sounding note falls
back to the newest still-held note (freq only — no retrigger);
releasing the last note sets `gate=0` unless sustained. Pitch =
`midi_to_freq(note + transpose) * 2^(bend/12)`; bend range ±2
semitones. `on_voiced` emits `{"kind": "voiced"}` viz events for what
actually sounds. The primary voice `voice` cannot be removed; extra
voices spawn aimed at the first playable source and arrive **unwired**.
Each voice has one target (`set_voice_target` re-aims; it resurrects a
voice whose target was removed). A rebuild re-creates voices, keeping
ids and stored targets where the module still exists.

### 4.4 Arpeggiator (`arp.py`)

A note-pool between its inputs and its fan-out (`arp→X` wires resolved
live). Disabled: notes pass straight through. Enabled: inputs
enter/leave the pool; a transport-grid thread steps the pattern.
Settings: `division` (from the global `DIVISIONS` ladder, §8.1), `gate`
0.05–1.0 (fraction of the step that sounds, default 0.6), `octaves` 1–3,
`pattern` ∈ `up | down | updown | random | played`. Timing is quantized
to the transport's absolute grid — chord changes never touch the clock.
Chord continuity: for up/down/updown the state lives in PITCH space —
the next note is the nearest chord tone above/below the last *played*
pitch, evaluated against the pool as it is now (change chords mid-stream
and the line walks on, no restart). Sustain pedal in arp mode = latch
(released notes stay pooled until pedal up). Transport stopped: the arp
thread parks and live notes pass through. An empty pool or disable
all_offs downstream (closure doctrine).

### 4.5 Derivers (`drone.py`): estimator and literal

Both share the `_DeriverBase` chassis: a MONO note out fanned over their
outgoing ctl wires (`_emit_out`: off the old, on the new — never two
sounding), an `every` grid timer driving `commit()`, and a trigger-in
override — while any binary source is wired into the node's bare id, the
internal timer stands down and each rising edge commits instead (unwire
to resume). `every` values: `1 beat`, `2 beats`, `1 bar`, `2 bars`,
`4 bars`; the estimator adds `deck`; the literal adds `immediate`
(commit on every input note event; its default).

**TonicDeriver — the estimator** (default `every` = `1 bar`). Two
layers. Layer 1 (continuous, never gates): `RootEstimator` accumulates
duration-weighted pitch-class evidence — a held note gains weight while
down (capped at 6 s), decays only after release (tau = `memory`,
1–30 s, default 6); a grace note contributes ~0.35 onset credit; bass
emphasis `bass` (0–0.2/semitone below MIDI 55, default 0.06). Evidence
is matched Krumhansl-style against scale templates (7 diatonic modes,
harmonic + melodic minor, both pentatonics, blues) rotated across all 12
tonics → a running (tonic, mode, confidence), with near-ties preferring
the superset mode over its pentatonic subset and a chromatic fallback
below the fit floor. Layer 2 (instant, at commit): read the CURRENT held
set (refcounted across fan-in; 0.3 s release-grace catches staccato) and
snap to the most probable root GIVEN the scale — held chord → its root
per the `listening` profile (`triadic` | `root+fifth` | `chromatic`),
single note → its function in the scale; tonic/dominant get small
priors. No settling, no hysteresis. Empty held set → hold the current
root (first-ever commit with evidence → the scale tonic). Output note =
`12 * (octave + 1) + root`, `octave` 0–4 (default 2). Deck superpower
(§9.4): `deck_feed` context evidence + harmonic-map prior;
`every="deck"` phase-locks commits to chord-group boundaries
(anticipatory). Emits `{"kind": "tonic_out", "id", "root"}` on root
changes; `analysis()` broadcasts ~5 Hz as `{"type": "deriver", ...}`
(weights, scores, leading, confidence, scale, deck flag).

**LiteralDeriver** — deterministic, zero-lag. At commit: `extract` ∈
`lowest-held | highest-held | last-played | first-played`, then `place`
∈ `absolute` (keep octave) | `fold` (re-voice into octave
`fold_octave`, 0–7, default 3) | `transpose` (±24 semitones);
`hold_on_empty` (default true) holds the last note vs releasing it.

### 4.6 Key shifter (`keyshift.py`)

Transposes note streams into a different key: offset = semitone distance
from C to `key`, mapped to the NEAREST shift (>+6 wraps down; always
within ±6). **Four isolated lanes** (`"<id>:<lane>"`, lane 1–4): lane k
in → shift → lane k out only; multiple signals ride one shifter without
merging. Correctness invariant: an OFF is shifted by the SAME offset its
ON used, even if the key changed mid-note (per-lane open-note maps) —
else stuck notes. Progression time track: `length` 1–32 bars, `steps[]`
of key index or `null` (hold); when ANY step is set the active key
follows `bar % length`, stepping at beat 0 (`on_beat` from the
transport's beat thread); an empty track = static `key`. Emits taps for
shifted output and `{"kind": "keyshift", "id", "active"}` on
progression moves.

### 4.7 Drone note-sinks (`_DroneSink`, app.py)

Every instance of the `drone` MODULE also has a ctl-plane presence: a
mono, last-note-priority sink. `note_on` retargets the instance's
`freq` via `rack.set_param` (through the synthdef's Lag/glide path, so
portamento applies; respawn-safe because it re-aims by key). `note_off`
falls back to the newest still-held note; an EMPTY held set HOLDS the
last root — the drone's on/off is its bypass toggle, not the note
stream. The play-in is single-input (a new wire drops stale held
state). Transport stop/play pauses/unpauses every enabled drone node.

### 4.8 Globals on the note path

`note_on`/`note_off` from any controller enter at `keys`. Panic
(`all_notes_off`) is global: closes keys' open taps, then all_offs the
arp, every voice, every deriver, every keyshifter — whatever the wiring
says. Sustain (`sustain` message / CC 64) latches the arp pool and
applies to voices — except a voice fed exclusively by the ENABLED arp
(its latch carries the stream; a latched voice would defeat arp
gating). Bend (±2 st) applies to all voices. Transpose (±24 st) is
global state on every voice.

---

## 5. The binary plane (`gate.py`, `ping.py`)

### 5.1 The model

ONE hi/lo signal kind (the 07-23 binary rework unified the old
ping/gate kinds). **Sources own LEVELS; edges DERIVE from level
changes.** A "ping" is just a pulse (hi-then-lo) propagating through
the graph — which is what lets pulses pass THROUGH logic gates while
the other leg is hi. Binary wires ride `app.ctl_wires`; the kind is
inferred from the source endpoint (`_is_ping_src`). Levels settle
eagerly on any change via a bounded fixpoint pass (24 iterations;
feedback loops FREEZE rather than spin; 8 outer passes for relay-ctl
re-entry), then edge-diffed effects apply per destination endpoint.
Direct self-wires are rejected; cross-node feedback loops are legal.

### 5.2 Binary sources

| Source | Ids | Level behavior |
| --- | --- | --- |
| Button | `button`, `button.2`, … | momentary (default): hi WHILE held (`button_down`/`button_up`); latch: press toggles, release ignored. `fire_button` = click compat (press+release). Binding: `armed` pairing captures the next NON-TONAL input — a MIDI CC (server-side; the router never surfaces notes as events) or an unassigned computer key (client-side). A bound CC follows the level (momentary, ≥0.5 = down) or toggles on rising crossings (latch). Arming one button disarms the others. Mode switch drops the level. |
| Clock | `clock`, `clock.2`, … | the one PULSE-ONLY source: persistent level always lo; each transport-grid tick calls `gates.pulse(id)`. Division ladder = `CLOCK_DIVISIONS`: `8/1`, `4/1`, `2/1` (whole-note multiples, meter-independent, phase-locked to beat 0) + the full global `DIVISIONS`; the arp/deriver ladders deliberately do NOT get multi-bar entries. Stopped transport: parks, never fires. |
| Threshold | `threshold`, … | Schmitt comparator over an LFO's normalized CV (§6.2). Level is mode-mapped: `rising` → state, `falling` → NOT state, `both` → pinned LO with every crossing delivered as a PULSE (both-mode can never hold a level — documented oddity). |
| Logic out | `logic`, … | the gate's computed output (§5.4) |
| Relay circuit | `relay:k` with kind `binary` | OR of the circuit's in-levels AND closed; pulses pass while closed (§7) |

All level changes emit `{"kind": "gate", "id", "on"}` (GUI LEDs);
rising edges keep a `{"kind": "ping", "src"}` tap (pulse animations).

### 5.3 Binary destinations (complete endpoint table)

Valid binary-wire destinations (`gates.is_toggle_dst`):

| Endpoint | Kind | Effect |
| --- | --- | --- |
| `<module-id>:pwr` | level-in | module enable follows (any chain module) |
| `arp:pwr`, `drums:pwr` | level-in | arp / drum-machine enable follows |
| `<logic>:a`, `<logic>:b` | level-in (single-input) | the gate's two named inputs (legacy `:set`/`:reset` accepted, canonicalized to `:a`/`:b`) |
| `<relay>:ctl` | level-in (single-input) | relay closed follows the level (last writer vs. manual click wins) |
| `<relay>:1..9` | level-in | a binary circuit's in (lazy OR, §7) |
| `transport:run` / `:click` / `:accent` | level-in | the GLOBAL transport's play state / audible click / downbeat accent follow |
| `<tonic-id>`, `<literal-id>` (bare deriver id) | **trig-in** | rising edge = commit now; suppresses the deriver's grid timer while wired |
| `deck:rec` / `:play` / `:stop` / `:clear` | **trig-in** | rising edge presses the deck button once |
| `transport:tap` | **trig-in** | rising edge = one tap-tempo tap (§8.1) |

Level-ins apply on change in both directions **including first sight**;
trig-ins fire on rising edges only — attaching a wire whose source is
already hi is NOT an edge. Single-input endpoints STEAL: a new wire
replaces the occupant (mirroring the GUI's steal-on-drop). Level-in
applications deep in the settle pass emit `{"kind": "level", "ep",
"on"}` so indicators react to logic-driven changes (REACTIVE-INDICATOR
DOCTRINE, Cole 07-24: every button/state indicator must graphically
react to LOGIC input exactly as to a click).

### 5.4 Logic gates

`logic`, `logic.2`, … — one card, an op dropdown: `AND | OR | NOR |
XOR | SR latch | T latch`. EVERY op exposes exactly two single-input
endpoints `:a`/`:b` (the shape never changes across op swaps, so wires
never drop). NOR with one wired leg acts as NOT (an unwired in reads lo).
SR latch: `:a` = SET, `:b` = RESET, reset wins; the latch state starts
lo entering AND leaving SR. Bare-id destinations are invalid. Presets:
(id, op) persist; op `NOT` migrates to `NOR`; a legacy `switches` list
is ignored (the Switch node died — Relay replaced it).

**T latch** (`"T latch"`) is the toggle: a RISING edge at `:a` flips the
output, `:b` is RESET and wins exactly as SR's does (an edge arriving
while held in reset is EATEN, not queued). It is the only op that is
edge-triggered rather than combinational, so it settles in two phases —
the combinational net reaches a fixpoint with T latches HOLDING (a
toggle re-evaluated every iteration would flip forever and never
converge), then rising edges are sampled ONCE per real edge. The outer
pass then re-settles, which is what lets a toggle propagate into what it
feeds: **chained T latches divide by two per stage, one stage per outer
iteration.** Each gate remembers the level last sampled at `:a`, so a
steady hi is distinguishable from an edge, and attaching a wire whose
source is ALREADY hi is not an edge — the same rule every trig-in
follows. Unwiring `:a` forgets that sample, so the next source to land
there starts edge-fresh. `a_prev` is runtime state and is not persisted;
a T latch reloads lo with no sampled level.

---

## 6. The modulation plane

### 6.1 Routable LFOs (`lfo.py`)

First-class nodes (`lfo`, `lfo.2`, …). Each runs ONE `_lfo_norm` synth
writing a bipolar normalized signal (osc × depth, −1..1) to its own
control bus; every wired destination adds a tiny `_lfo_scale` synth
reading that shared bus and mapping onto the target param's real range
and curve (linear or exponential) — one LFO fans out to any number of
params, sample-accurately inside scsynth. Settings: `rate`, `depth`,
`shape` ∈ `sine | tri | ramp | square | s&h` (index or name).

**No center knob**: each destination orbits its OWN slider value.
Moving a mapped param's slider steers that destination's center
(`set_center_unit`) — the same value the slider would set unmapped, so
unwiring leaves the knob where you last put it. `rack.set_param` skips
`node.set` for mapped params (the bus mapping owns the node) and stores
the value for restore. Params are SINGLE-INPUT: wiring an already-mapped
param steals it from its current LFO. Module removed → its dests drop;
rack rebuild → ALL dests drop, LFO nodes survive (like every spawned
ctl node); node respawn (hot reload / bypass re-enable) → re-map.
Restore migrates the pre-item-7 per-assignment format.

**Relay-routed modulation (`mod` circuits, PR #43).** An LFO can also
reach a param THROUGH a relay. Those hops are stored verbatim in
`app.mod_wires` (`"lfo"` → `"relay:1"` → `"echo:mix"`) as their own
plane and are BROADCAST in `state`, so this plane needs no client-side
bookkeeping — and since item 25 the audio plane works the same way
(stored wires broadcast, no client store). `relay.resolve_mod(app)` walks
every LFO through CLOSED circuits to the params it actually drives and
diffs that against `LFOManager`'s destinations: closing a circuit maps
the param, opening it unmaps and the param settles back on its own knob
value (the honest "no modulation reaching you"). `app._mod_managed` is
the destination set this layer owns, so a DIRECT `lfo_wire` dest is
never yanked out from under the user; conversely, dropping a direct wire
onto a relay-driven param evicts the relay route. Endpoint grammar:
LFO id | `"<relay>:<k>"` | `"<key>:<param>"`, with the same cycle guard
and self-wire rejection `graph_wire` uses. Circuits are 1:1 (§7); LFO
fan-OUT stays unlimited. Hygiene: removing a relay, an LFO or a chain
module scrubs the wires that touched it and re-resolves; a rack rebuild
drops the PARAM ends and keeps the circuits; resume replays `mod_wires`
after the LFOs restore.

### 6.2 Thresholds — CV in (`threshold.py`)

The bridge rule: continuous stays server-side, discrete stays
Python-side, the only crossing is EDGE-NOTIFY. A `_threshold_watch`
synth reads the source LFO's normalized bus through
`Schmidt.kr(level − hyst, level + hyst)`; `SendTrig` fires exactly one
`/tr` per crossing (both edge triggers always on; mode filtering is
Python-side). Python never polls a bus. The `/tr` handler updates the
node's Schmitt state and notifies the gate manager. `level` is in the
LFO's NORMALIZED bipolar terms (post-depth: a depth-0.25 LFO swings
±0.25 — a level of 0.0 ticks twice a cycle, ±0.3 never fires).
Hysteresis (0–0.5, default 0.02) IS the debounce. The CV-in is
single-input (`threshold_wire`); re-wiring replaces; removing the
source LFO unwires it. A spawn/param-change re-arms a 0.15 s window
that swallows the phantom initial falling edge. `feed(value)` is the
same Schmitt for values already Python-side (the future serial-sensor
path; today exercised only by tests).

### 6.3 Scope capture (`scope.py`)

Per watched module: a 4-UGen `_ring` synth (Phasor→BufWr, write head
mirrored to a control bus) records the module's out bus CONTINUOUSLY
into a 2048-frame buffer; `capture()` just reads the buffer (no record
wait — the last 2048 frames are always there). Rings idle >3 s are
reaped; `reset()` drops everything on rack teardown; a rewired module
rebuilds its ring. Captures are coalesced per key server-side (§11.1
`scope`).

---

## 7. Relays (`relay.py`)

A type-agnostic switched junction (`relay`, `relay.2`, …; replaced the
old SwitchGate). One node = up to **9 independent circuits** + a
control-in. Circuit k's endpoint is `"<id>:<k>"`; the wire INTO it is
the circuit's in, the wire FROM it its out. A circuit's KIND is inferred
from its FIRST wire and then enforced (mixing kinds is rejected;
circuits no wire touches forget their kind): `audio` (graph_wires),
`notes` (ctl wires from note sources), `binary` (ctl wires from binary
sources), `mod` (LFO → param, §6.1). `closed` defaults to False (open);
`set_relay` is the manual click; a wired `:ctl` level-in makes closed
follow — last writer wins.

**Contacts are 1:1** (Cole, 2026-07-24, PR #43). A circuit port is
single-input on BOTH sides — neither handle grows a `+`, and the only
`+` on the card is the 5th-contact expansion that flips the card XS(4) →
S(9). Adding a second wire to either side STEALS, on all four planes:
`gates.is_single_input` covers every relay circuit in (not just
`relay:ctl`), the notes path steals both sides (a stolen note-in
`all_off`s downstream first, so the departing controller's held notes
cannot ring on under the new one), the binary path steals the circuit
OUT, and `graph_wire` steals a circuit IN, parking the displaced source.
A circuit in was a summing bus before this; it is a contact now.

Per kind: **notes** — a `_CircuitIn` adapter forwards
on/off/sustain/bend downstream only while closed; `all_off` passes
ALWAYS; opening all_offs every note circuit downstream (no stuck
notes). **binary** — out level = OR(in levels) AND closed, computed
lazily; pulses pass while closed; a closed change re-settles the plane.
**mod** — see §6.1; the stored `app.mod_wires` plane is broadcast, and
`resolve_mod` maps/unmaps LFO destinations as circuits close and open.
**audio** (item 25) — graph_wires store relay endpoints verbatim
(bypassing `rack.find`) and every CLAIMED audio circuit is a permanent
`_relay_gate` synth (`In.ar → × Lag.kr(gate, 10 ms) → Out.ar`) owned by
`RelayAudioManager`, which follows the `LFOManager` lifecycle:
registration tracked per server OBJECT, server touches guarded, records
released when a circuit is unclaimed or its relay removed. The wires are
therefore REAL open or closed — a source wired into `"relay:k"` writes
that circuit's own in-bus, the gate synth writes the circuit's out-wire
destination (the null bus when that side is unwired) — and `set_closed`
moves only the gate param, so opening is clickless and reverb/echo tails
downstream ring out honestly. `relay.apply_audio(app)` is the single
entry point (wire edits, rebuilds, chain edits, relay removal): it syncs
the gate synths, points everything at its bus, and topologically orders
every wire's src before its dst with the gate synths in the same
universe; with no claimed circuits it degrades to
`rack.reorder_for_wires`. Cycles are still refused at `graph_wire add`
by the stored-wire walk. Removing a relay silences its note circuits,
parks its audio feeders, drops all its wires and frees its gates.

GUI: a circuit allocates ONE line colour from its own family on first
sight and both halves draw in it (light green in, light green out), and
wire labels name the whole hop — `"Keys → Relay → Voice"`,
`"LFO → Relay → Echo.mix"` — with an unwired end simply left off.

**Item 25 (done, P1):** the audio plane STORES rather than resolves, and
`state.wires` is the stored graph — relay endpoints verbatim, parked
(`"to": null`) wires omitted — so audio relay hops render from server
truth on every client. `resolve_audio` / `resolved_wires` and the GUI's
`relayAW` store are deleted.

---

## 8. Transport, timing, drums, MIDI

### 8.1 Transport (`transport.py`)

The shared musical clock. Absolute beat timeline (beat 0 anchored at
construction; tempo changes re-anchor so the beat position is
continuous). BPM 20–300; meter 1–12 beats/bar; `running` freezes the
position on stop. A beat thread fires `on_beat(bar, beat)` per beat:
`app._handle_beat` spawns the `_click` tick when enabled (2000 Hz on
the accented beat, 1400 Hz otherwise), steps key-shifter progressions,
and forwards to the GUI (`{"type": "beat", ...}` with loop phase).
`downbeat` (0-based beat-in-bar) moves the click ACCENT and the beat
event's flag only — grid math stays anchored on beat 0. Everything
rhythmic quantizes via `next_grid(division_beats)` / `time_of_beat`.

`DIVISIONS` (beats; "." dotted, "T" triplet): `1/1`=4, `1/2`=2,
`1/4.`=1.5, `1/4`=1, `1/4T`=⅔, `1/8.`=0.75, `1/8`=0.5, `1/8T`=⅓,
`1/16`=0.25, `1/16T`=⅙, `1/32`=0.125.

`TapTempo` (the `transport:tap` trig-in): each valid interval
(0.25–2.0 s ≈ 240–30 BPM) joins a 4-interval window; bpm = 60/mean; an
out-of-range tap restarts the sequence silently. TEMPO ONLY — never
touches phase or running.

Transport cards (`play`, `tempo`) are canvas VIEWS of the one global
transport — presence only (`transport_cards` in state/presets); the
binary endpoints `transport:run/click/accent/tap` belong to the global
transport and survive card removal.

**Boot state: a fresh launch comes up STOPPED (item 32).**
`transport.running` defaults **False**, so a boot with no custom preset
populates the Play/Stop card and the top bar as stopped (both read
`running !== false`, so no GUI change was needed) and nothing
transport-driven — drums, clocks, the arp grid, drones — sounds until
play is actually triggered. The default control plane and the shipped
patches' `notes_to` bindings are untouched by this, so the rig is still
immediately hand-playable: `midi → gen → master out` stays intact.

### 8.2 What stopping the transport does

Position freezes; the arp parks (live notes pass through); clocks stop
firing; drum sequencer idles; deriver grid timers idle; deck replay
skips firing (and releases anything sounding); every enabled drone
node pauses. Play resumes all of it on the same grid.

**Drone silence is an INVARIANT, not a `set_transport` side effect
(item 32).** Drones are gateless, so "silent while stopped" cannot be
enforced only where the transport changes — a drone spawned while
stopped would sound immediately. `app._sync_drone_run_state()` pauses or
unpauses every ENABLED drone node to match `transport.running`, and
EVERY path that creates, replaces or re-enables a drone node calls it:
patch build (`_build_from`), `edit_chain` add, `swap_synth`,
`set_enabled`, and the legacy `_ensure_legacy_drone` pair. Disabled
instances are skipped — bypass pausing stays `rack.set_enabled`'s job.
Any future path that can produce a live drone node must call it too.

### 8.3 Drum machine (`drums.py`)

Four synthesized one-shot lanes (`kick`, `snare`, `hat`, `clap` —
self-freeing synthdefs) on a 16-step 1/16 grid (one 4/4 bar), walked on
the shared transport. Per-lane 16-step patterns + levels 0–1 (scaled by
per-lane trim: kick 0.55, snare 0.42, hat 0.3, clap 0.4). Audio target
per §3.8; a stale target falls back to master. Emits
`{"kind": "drum_step", "step"}` per step. Patterns/levels/target ride
presets.

### 8.4 MIDI (`midi.py`)

`MidiRouter` opens one input port (mido/rtmidi; preference: named port,
else first non-IAC hardware, else first). Notes → the `keys` node
(velocity respected; note_on vel 0 = off). Pitchwheel → global bend
(±2 st). CC 64 → global sustain. Other CCs → the patch's `cc` bindings
(`{cc_number: (instance_id, param)}` scaled through the param's curve),
else surfaced unbound. Every event is emitted to the GUI (`{"type":
"midi", "event": {...}}`); a bound CC also echoes a `param` message so
virtual sliders follow physical knobs. Armed buttons intercept the CC
stream first (§5.2). `set_midi` picks port / toggles MIDI (router
restart). MIDI callbacks run on the port's thread — never block there.

---

## 9. The Loop Deck (`looper.py`)

Bar-synced record/replay of NOTE events (v1 audio looping died —
scsynth buffer reads returned garbage through supriya; notes are
deterministic). States: `empty → armed → recording → playing ⇄
overdubbing → stopped`. Settings: `bars` 1–8 (change only while
empty/stopped), `level` 0–1 (scales replay velocity), `overdub` toggle.
Actions (`set_looper` / binary trig-ins): `record`, `play`, `stop`,
`clear`.

**Record** is wire-defined: `keys→deck` feeds `record_raw` (raw
controller), `arp→deck` feeds `record_voiced` (arp output); only wired
sources reach the taps. Arming quantizes the window start to the next
bar top; while armed, a note struck up to 0.35 beats early clamps to
beat 0 (its off clamps too if released before the window opens). The
window closes after `bars × beats_per_bar` beats; EVERY record-window
exit (finish, stop, overdub-off) synthesizes offs for still-open notes
(`_close_open_take`) — the take stays paired. Overdub adds passes on
top. Events store as `(beat_offset, note, on)`; ordering uses a STABLE
beat-only sort (a tuple sort would put offs before ons at equal beats
and scramble pairing).

**Replay** resolves from `deck→X` wires live (`_sink`): `deck→arp`
replays into the arp pool; `deck→voice` plays a PRIVATE second node of
the primary voice's target module (mono-voice semantics; never steals
the live voice); `deck→voice.N` drives that extra voice; `deck→tonic.N`
lets a deriver hear the replay; `deck→keyshift.N:k` rides shifter lane
k. No outgoing wire = the loop spins silently. Replays set `_self_fire`
so `deck→arp→deck` cannot re-record. Each replay fire emits ONE
`{"kind": "tap", "src": "deck"}`; a 0.15-beat grace window fires
just-passed events immediately (loop-top latency) without refiring on
rescan. Transport stopped: skip firing, release anything sounding.

Deck superpower for the estimator (§4.5): the recorded phrase is
onset-clustered (0.25-beat window) into chord groups; `deck_feed`
contributes duration-weighted context evidence (normalized to mass 6.0;
singletons count 0.4 as melody) and a per-position harmonic-map prior;
`every="deck"` commits anticipatorily at each group boundary.

---

## 10. Module reference (`modules/`)

Every module: one Python file, `@module(name, kind, params)` stacked on
supriya's `@synthdef()`. The function name is the module's identity
(patches and hot reload key on it; renaming = a new module). Params are
`param(min, max, default, curve)` with `curve` ∈ `lin` (default),
`exp` (frequencies/times), `toggle` (checkbox; value = min/max), or
options tuple → `select` dropdown (value = option index). Playable =
exposes `freq` + `gate` (§2.2). Full authoring contract: CLAUDE.md.

### 10.1 Sources — note-playable voices

| Key | Name | Family | Params (min–max, default, curve) | Character / notes |
| --- | --- | --- | --- | --- |
| `wobble_saw` | Wobble Saw | voice | freq 20–2000 · 110 exp; wobble 0.1–20 · 4 exp; depth 0–1 · 0.5; amp 0–1 · 0.25 | canonical source: saw with sine-LFO tremolo (dip by `depth` at `wobble` Hz), ADSR .01/.1/.8/.3. 0.45 amp makeup (level-matched to the voice family, 2026-07-22) |
| `pulse_pad` | PW Pulse Pad | voice | freq 20–2000 · 220 exp; wave select(pulse, saw, tri, sine); detune 0–50 · 12 (cents); porta toggle · off; glide 0.01–2 · 0.15 exp (s); pwm 0–0.45 · 0.2; attack 0.005–2 · 0.15 exp; release 0.05–5 · 0.8 exp; amp 0–1 · 0.22 | three-osc detuned pad (center + ±`detune` cents), selectable waveform, slow PWM motion, ADSR (attack, .2, .75, release). Renamed from "Signal Gen" |
| `fm_bell` | FM Bell | voice | freq 20–2000 · 440 exp; ratio 0.5–8 · 3.51; index 0–12 · 4; decay 0.1–8 · 2.5 exp; amp 0–1 · 0.25 | 2-op FM bell/EP; separate amp and index envelopes derived from `decay` |
| `pluck` | Pluck | voice | freq 40–1600 · 220 exp; decay 0.3–12 · 4 exp; damp 0–0.9 · 0.4; amp 0–1 · 0.35 | Karplus–Strong string (Pluck UGen over pink noise, retriggered by gate). 2.65 amp makeup |
| `power_sine_shaper` | Psine Waveshaper | psine | freq 20–2000 · 220 exp; p 1–64 · 2 exp; amp 0–1 · 0.3 | literal per-sample `sgn(sin) * abs(sin)^(2/p)`: p=2 sine → p→∞ square, p<2 peaky. NOT band-limited — aliasing grit is its fingerprint. LeakDC guard |
| `power_sine_additive` | Psine Harmonic Bank | psine | same as shaper | the same target spectrum as its exact odd-harmonic series — alias-free (Nyquist-gated). Uses `odd_harmonic_bank` + shared `power_law_coeffs` |
| `power_sine_blend` | Psine Crossfade | psine | same as shaper | wavetable-style foil: two fixed frames (sine, ideal square) crossfaded by u = clip(1 − 2/p, 0, 1); law inlined in the module (identity stays in the file) |

All psine voices share ADSR .01/.1/.85/.4 and a 24-partial bank.

### 10.2 Sources — non-playable (no `freq`/`gate`)

| Key | Name | Family | Params | Character / notes |
| --- | --- | --- | --- | --- |
| `audio_in` | Audio In | input | gain 0–4 · 1 | hardware input channel 1 (`In.ar(NumOutputBuses.ir())`) spread to stereo |
| `wind` | Wind | voice | center 150–4000 · 700 exp; gust 0–1 · 0.6; resonance 0.2–3 · 1; amp 0–1 · 0.3 | pink noise through drifting stereo BPFs with slow "weather" (LFNoise1 swell). 2.9 makeup |
| `drone` | Drone | service | freq 16–500 · 55 exp; amp 0–1 · 0.16; porta toggle · on; glide 0.05–8 · 1.5 exp (s); shape 0–1 · 0.35; sub 0–1 · 0.4; cutoff 80–8000 · 900 exp | sustained pedal tone: saw↔pulse blend + sub-octave sine, LPF, slow PW motion, stereo drift. No gate — sounds while the node exists (bypass = its off switch); portamento via `freq` Lag (`porta` off = 20 ms snap). Has a ctl-plane mono note-sink presence (§4.7) |

### 10.3 Effects

| Key | Name | Family | Params | Character / notes |
| --- | --- | --- | --- | --- |
| `lowpass` | Low-pass Filter | filter | cutoff 60–12000 · 1200 exp; resonance 0.1–1 · 0.5 | canonical effect: RLPF, lagged cutoff |
| `telephone` | Telephone | filter | low 100–1200 · 380 exp; high 1200–8000 · 3200 exp; crunch 1–12 · 3 exp; mix 0–1 · 1 | band-pass (LPF+HPF) + softclip crunch |
| `echo` | Echo | time | time 0.02–2 · 0.375; feedback 0–0.95 · 0.4; mix 0–1 · 0.35 | CombL feedback delay; decay derived from feedback (`time × (1 + fb × 12)`) |
| `reverb` | Reverb | time | room 0–1 · 0.6; damp 0–1 · 0.5; mix 0–1 · 0.3 | FreeVerb |
| `chorus` | Chorus | time | rate 0.05–4 · 0.4 exp; depth 0–1 · 0.5; mix 0–1 · 0.4 | two modulated DelayC taps (12 ms base, offset phases/rates per channel) |
| `flanger` | Flanger | time | rate 0.05–3 · 0.25 exp; depth 0–1 · 0.7; feedback 0–0.9 · 0.4; mix 0–1 · 0.5 | short swept delay (1.5–7.5 ms) with LocalIn/LocalOut feedback |
| `phaser` | Phaser | time | rate 0.05–4 · 0.3 exp; depth 0–1 · 0.8; mix 0–1 · 0.5 | 4 cascaded swept allpasses |
| `autopan` | Auto Pan | time | rate 0.05–10 · 0.5 exp; depth 0–1 · 0.7 | sums input to mono, Pan2 swept by sine. NOTE: mono-collapses the stereo image upstream |
| `drive` | Drive | dirt | gain 1–40 · 4 exp; tone 500–12000 · 4000 exp; mix 0–1 · 1 | tanh soft clip (×0.7) + post-clip LPF |
| `bitcrush` | Bitcrush | dirt | srate 400–44100 · 8000 exp; bits 2–16 · 10; mix 0–1 · 1 | Latch resampling + amplitude quantization |
| `wavefolder` | Wavefolder | dirt | fold 1–12 · 2.5 exp; symmetry −0.5–0.5 · 0; mix 0–1 · 1 | fold2 with pre-fold DC offset (`symmetry`), LeakDC |
| `power_shaper` | Power Shaper | psine | **on `main`:** p 1–64 · 2 exp; drive 0.25–8 · 1 exp; mix 0–1 · 1 | item 11: the psine waveshaper law over INCOMING audio — `sgn(x)·abs(x)^(2/p)` after `drive`, LeakDC, dry/wet. Same law and same aliasing fingerprint as `power_sine_shaper`. ⚠ **Superseded on `feat/p11-dual-mode` — see below; on that branch this is `kind="dual"`, not an effect, and it is the one module in this section that is not purely an effect.** |
| `compressor` | Compressor | dyn | threshold 0.01–1 · 0.3 exp; ratio 1–20 · 4 exp; attack 0.001–0.2 · 0.01 exp; release 0.02–1 · 0.15 exp; makeup 0.5–4 · 1.3 exp | Compander, downward only |
| `pitchshift` | Pitch Shift | vox | semitones −24–24 · 0; mix 0–1 · 1; window 0.02–0.2 · 0.04 exp; smear 0–0.02 · 0.002 | granular PitchShift; `window` is grain size AND latency; `smear` 0 = robotic |
| `ringmod` | Ring Mod | vox | carrier 20–4000 · 200 exp; mix 0–1 · 0.8 | multiply by lagged sine carrier |
| `scope_tap` | Scope Tap | effect | gain 0–2 · 1 | transparent inline probe: the GUI's oscilloscope card; splice anywhere, the scope draws its out bus (§6.3) |

#### 10.3.1 Power Shaper as a DUAL module — PENDING, not on `main`

> Built and finished on the local-only branch **`feat/p11-dual-mode`**
> (item 11, 2026-07-26); unmerged and unpushed. On `main`, `power_shaper`
> is the plain effect in the table above. Documented here because when it
> lands it stops being an effect, and §10.3's framing would otherwise be
> silently wrong.

| Key | Name | Kind | Family | Params (branch) |
| --- | --- | --- | --- | --- |
| `power_shaper` | Power Shaper | **`dual`** | `psine` | freq 20–2000 · 220 exp *(GENERATE only)*; p 1–64 · 2 exp; drive 0.25–8 · 1 exp; amp 0–1 · 0.3 *(GENERATE only)*; mix 0–1 · 1 *(FX only)* |

ONE card and ONE synthdef that either generates or shapes what you wire
in. The psine law `T_p(A) = sgn(A)·|A|^(2/p)` is memoryless and computed
per sample, so it is input-agnostic — the same law applies to an internal
`SinOsc` or to `In.ar(in_bus)`:

- **mode 0 — GENERATE:** the law over an internal sine, enveloped by
  `gate` and levelled by `amp`. Equivalent to `power_sine_shaper` at
  `drive=1`.
- **mode 1 — FX:** the law over the incoming signal, blended dry/wet by
  `mix`.

`mode` is **not a knob**. `App._sync_dual_modes` derives it from the audio
graph — a stored wire whose destination is this instance means FX, no wire
means GENERATE — and pushes it to the node. Both chains are computed and
crossfaded through a **lagged** `mode`, so a wire landing or being cut is
click-free rather than a hard switch. A rebuild spawns every dual at the
synthdef default (`mode=0`), so the sync is re-pushed with `force=True`
afterwards, the same hazard class as playable sources spawning `gate=0`.

The card reacts to `{"kind": "level", "ep": "<id>:mode", "on": …}` per the
reactive-indicator doctrine: a shaper that silently switches from
generating to processing is exactly the invisible state change that
doctrine exists to catch. Params not owned by the active mode stay on the
card; they simply do nothing until the mode changes.

Aliasing note is unchanged from the generator: not band-limited, so as `p`
climbs, fold-back is the sonic fingerprint. `p = 2` is identity; `p → 64`
approaches `sgn(x)`; `p < 2` is pinched.

### 10.4 Shared DSP helper (`harmonics.py`)

`odd_harmonic_bank(freq, coeffs, nyquist=21000, headroom=0.5)` — pure
graph emitter (no engine/rack coupling): RMS-normalized sum of
Nyquist-gated odd partials; `coeffs` iterates `(n, amp)` for odd n, amp
may be a float or UGen. Shared laws: `power_law_coeffs(a)` (exact
odd-harmonic series of `sgn(sin)|sin|^a`, gamma-free recurrence) and
`square_blend_coeffs(u)`. MECHANISM lives here; each module's
coefficient LAW (its identity) stays in the module file.

### 10.5 Internal (non-module) synthdefs

Not in the palette, never in a patch: `_bypass` (rack passthrough/tail
router), `_click` (metronome), `_master` + `_input_meter` (§3.5),
`_kick`/`_snare`/`_hat`/`_clap` (drums), `_lfo_norm` + `_lfo_scale`
(§6.1), `_threshold_watch` (§6.2), `_ring` (scope), `_test_sine`
(CLI test), the looper's private deck voice (a second node of an
existing module synthdef).

---

## 11. The websocket protocol (`server.py`)

`GuiServer` HTTP routes: `GET /` → `gui/blocks.html` (blocks IS the UI;
flex + the original are archived under `gui/legacy/`, not served);
`GET /blocks` → alias of `/`; `POST /restart` → full backend reload
(resume snapshot → re-exec in place, §12.3); `GET /ws` → the websocket.
On connect a client immediately receives a full `state`. When the last
client disconnects, held notes are silenced. Handler errors return
`{"type": "error", "message"}` — the GUI must never crash the synth.
The docstring atop `server.py` is the protocol's in-code source of
truth; this section mirrors it.

### 11.1 Client → server messages

Module/instance `key`s are INSTANCE ids (legacy type keys → first
instance). `sender` semantics: most `set_*` broadcasts exclude the
sender (its UI already updated); structural changes broadcast to all.

| Message | Fields | Effect |
| --- | --- | --- |
| `set_param` | key, name, unit (0–1) | set via the param curve; echoes a tiny `param` msg to OTHER clients (never full state — knob streams must stay light). If LFO-mapped, steers that dest's center (§6.1) |
| `set_enabled` | key, enabled | bypass toggle (§3.4) |
| `set_volume` | volume | master volume (no broadcast) |
| `note_on` / `note_off` | note, [velocity] | GUI keys → the `keys` node |
| `sustain` | on | global pedal (§4.8) |
| `all_notes_off` | — | panic (§4.8) |
| `set_transpose` | semitones | global ±24 |
| `select_patch` | patch | full rebuild; ctl wires reset to default |
| `set_devices` | input, output | full engine reboot (seconds) |
| `set_midi` | port, enabled | MIDI port select / off |
| `set_arp` | enabled, division, gate, octaves, pattern | §4.4 |
| `set_transport` | bpm, beats_per_bar, click, accent, playing, downbeat | §8.1; broadcast INCLUDES sender (play button must flip) |
| `spawn_transport_card` / `remove_transport_card` | which ∈ play, tempo | card presence only (§8.1) |
| `graph_wire` | action add/remove, from, to | audio wiring (§3.2; relay endpoints legal, §7) |
| `ctl_wire` | action add/remove, from, to | note AND binary wiring (§4.1, §5.3) |
| `spawn_module` | key (TYPE) | fresh instance, parked (§3.3) |
| `edit_chain` | action add/remove/move, key, [index] | live chain surgery (§3.2–3.3) |
| `swap_synth` | id, key (new type) | in-place type swap (§3.3) |
| `spawn_voice` / `remove_voice` | [id] | §4.3 (primary `voice` not removable) |
| `set_voice_target` | key, [voice] | re-aim a mono voice |
| `spawn_tonic` / `remove_tonic` / `set_tonic` | id; every, octave, memory, bass, listening, deck_feed | estimator deriver (§4.5) |
| `spawn_literal` / `remove_literal` / `set_literal` | id; every, extract, place, fold_octave, transpose, hold_on_empty | literal deriver (§4.5) |
| `spawn_keyshift` / `remove_keyshift` / `set_keyshift` | id; key, length, steps | §4.6 |
| `spawn_logic` / `remove_logic` / `set_logic` | id; op | §5.4 |
| `spawn_relay` / `remove_relay` / `set_relay` | id; closed | §7 |
| `spawn_button` / `remove_button` / `set_button` | id; binding, armed, latch | §5.2 |
| `fire_button` / `button_down` / `button_up` | id | hot paths — no state broadcast |
| `spawn_clock` / `remove_clock` / `set_clock` | id; division | §5.2 |
| `spawn_lfo` / `remove_lfo` / `lfo_set` | id; rate, depth, shape | §6.1 |
| `lfo_wire` | action, id, key, name | modulation fan-out (single-input params steal) |
| `mod_wire` | action add/remove, from, to | relay-routed modulation (§6.1); endpoints LFO id \| `"<relay>:<k>"` \| `"<key>:<param>"`; cycle-guarded |
| `spawn_threshold` / `remove_threshold` / `set_threshold` | id; level, hysteresis, mode | §6.2 |
| `threshold_wire` | action, id, lfo | the CV-in (single-input) |
| `set_looper` | action, bars, level, overdub | §9 (`position` accepted and ignored — pre/post is wiring) |
| `set_drums` | enabled, patterns, levels, target, [to_chain] | §8.3; `target: null` is meaningful (disconnected) |
| `set_drone` | enabled, every, octave | LEGACY: maps onto a `tonic` deriver + drone-instance pair wired arp→tonic→drone |
| `scope` | key | one capture; coalesced per key (one in flight; duplicates dropped), runs off the message loop so knob/note traffic never queues behind it |
| `save_preset` / `load_preset` / `delete_preset` | name | §12.2 |

### 11.2 Server → client messages

| Message | Cadence | Payload |
| --- | --- | --- |
| `state` | on connect + after every structural change | full snapshot (§11.3) |
| `param` | on another client's `set_param` / bound CC | key, name, value, unit |
| `meters` | 20 Hz (`METER_INTERVAL = 1/20`) while clients exist | out [l, r], in (or null) |
| `tonic` | every 4th meter tick (~5 Hz) | legacy header strip: first deriver's normalized weights + root |
| `deriver` | ~5 Hz per estimator | id, weights, scores, leading, confidence, scale{tonic, mode, conf, label}, root, deck |
| `beat` | every transport beat | bar, beat, downbeat flag, loop phase |
| `midi` | per input/engine event | `event` object — see kinds below |
| `scope_data` | reply to `scope` | key, sr, samples[2048] oldest→newest |
| `error` | on handler failure | message |

`midi` event kinds (the GUI's live-event bus, all via
`app._emit_midi_event`): `cc` (cc, unit, [bound, value]), `bend`,
`sustain`, `tap` (src, note, on — §2.3), `voiced` (note, on, [deck]),
`loop_note` (beat, note, on — deck viz feed), `looper` (full deck
settings on state change), `drum_step` (step), `gate` (id, on — every
binary level change), `level` (ep, on — level-in applications, §5.3),
`ping` (src — rising-edge pulse anims), `ping_bound` (id, binding —
button pairing landed), `tonic_out` (id, root name), `keyshift` (id,
active).

### 11.3 The `state` snapshot (`app.state()`)

Keys: `patch`, `patches`, `chain` (per instance: key, type, name —
suffix-decorated, kind, family, enabled, service, params{min, max,
curve, options, default, lfo-mapped flag, value}), `volume`, `devices`
(inputs/outputs), `current_input`, `current_output`, `input_enabled`,
`boot_note`, `voice_target` (legacy), `voices` [{id, target}], `tonics`,
`literals`, `keyshifts`, `buttons`, `clocks`, `transpose`,
`midi_inputs`, `midi_port`, `midi_enabled`, `wires` (audio: the STORED
graph wires since item 25 — relay endpoints verbatim, parked wires
omitted; derived live from the rack only before the first structural
edit), `ctl_wires`, `mod_wires` (relay-routed modulation hops, stored
verbatim — §6.1), `drums_target`, `arp`, `transport`,
`transport_cards`, `drone` (legacy shape), `drums`, `looper`, `lfos`,
`thresholds`, `logics`, `relays`, `presets`, `available` (palette:
key/name/kind/family, sources first), `module_errors`.

---

## 12. Persistence

### 12.1 Patch files (`patches/*.py`)

Plain data: `PATCH = {"chain": [(type_key, {overrides}), ...],
"bindings": {"midi_in": name|None, "notes_to": type_key, "cc":
{num: (key, param)}}, ["arp": {...}]}`. Chain order = execution order =
signal flow; first entry must be a source. Shipped patches: `demo`
(wobble_saw → lowpass → echo), `pad_space` (pulse_pad → drive → lowpass
→ echo → reverb → autopan), `bells` (fm_bell → chorus → echo → reverb,
arp preset), `strings` (pluck → phaser → compressor → reverb, arp
preset), `vox` (audio_in → pitchshift → ringmod → telephone → echo →
reverb), `mic_fx` (audio_in → lowpass → echo).

### 12.2 Presets (`presets/*.json`, presets.py)

A preset (format v2) snapshots everything performable: patch name,
per-instance `{type, settings, enabled, service}` keyed by instance id,
volume, transport (+ card presence), arp, legacy drone shape, tonics,
literals, keyshifts, buttons (binding + latch; never armed), clocks,
relays (id + closed), drums, lfos (instances + dests), thresholds,
gates (logics). Load: switch patch if needed → transport → derivers →
keyshifts/buttons/clocks → arp → module params/enabled (with in-place
type swap when a saved Instrument differs) → volume → drums → lfos →
thresholds (after lfos: CV-ins need their sources) → gates → relays.
Migration tolerances: pre-v2 type-keyed modules, old LFO format,
`stickiness` ignored, logic op NOT → NOR, `:set`/`:reset` → `:a`/`:b`,
legacy `switches` ignored.

### 12.3 Restart resume (`.resume.json`)

`POST /restart` writes the preset snapshot PLUS the graph — audio
wires, ctl wires, voice targets, drums routing — then re-execs the
process in place (scsynth dies with it; the GUI's watchdog reconnects;
canvas layout lives client-side in localStorage and survives). On boot,
`apply_resume` respawns non-service instances the patch didn't bring
(by id so wires re-resolve), applies the preset body, replays graph +
ctl wires, restores voice targets and drums target, then deletes the
file. A resumed boot never auto-opens a browser.

**Play state: resume carries it, named presets deliberately do NOT
(item 32).** The `resume` block records `running`, and it is applied
LAST — after the graph is rebuilt — so the drone walk (§8.2) sees the
finished rack. Named presets (§12.2) carry no play state at all, and
that asymmetry is the point: loading a preset mid-performance can
neither stop nor start the rig. A pre-item-32 resume file, which has no
`running` key, resumes PLAYING — which is what it was when written.

---

## 13. The GUI (`gui/blocks.html`)

One self-contained page (no build step), served at `/` and at `/blocks`
(an alias kept for bookmarks — the same file, not a second UI). It is
the ONLY page served; there is no `/legacy` route. Everything it
shows derives from `state` + the event stream (§11.2); everything it
does is protocol messages — the backend is the only authority. Cards on
a block grid (§2.6) with gutter-routed subway wires; the full UI design
contract (grid, shove placement, wire routing, port discipline,
viewport, tidy, LFO viz, device pickers on cards) is
`docs/BLOCKS_SPEC.md`; per-release behavior changes land in the release
notes. Layout/positions are client-side only (localStorage per patch) —
the server never sees card geometry.

Monitors doctrine: Note/Waveform monitors and the scope are LOCAL when
wired/riding a wire (they show that path's traffic, filtered by tap
`src`) and GLOBAL when unwired (master feed / all taps). Reactive-
indicator doctrine: every indicator reacts to logic-driven state
changes via the `level`/`gate` events, not only to clicks (§5.3).

Headless GUI checks: `tests/gui_check8.py` (current; earlier
`gui_check*.py` are kept snapshots) and `tests/check_blocks.py` drive
the page with Playwright against mock state — no server needed.

---

## 14. Threading model (who runs where)

| Thread | Owner | Runs |
| --- | --- | --- |
| asyncio event loop | `GuiServer` | websocket handling, state broadcasts, meter loop (blocking work → executor) |
| transport beat thread | `Transport` | `on_beat` → click spawn, keyshift progressions, GUI beat events |
| arp tick thread | `Arpeggiator` | pattern steps (parks when idle/stopped) |
| looper replay thread + arm/finish timers | `Looper` | replay fires, window transitions |
| per-deriver decision thread | `_DeriverBase` | grid/deck-synced commits |
| per-clock tick thread | `ClockTrigger` | pulses |
| drums sequencer thread | `DrumMachine` | step fires |
| MIDI callback thread | mido | note/CC dispatch (never block; `on_event` hops to the loop via `run_coroutine_threadsafe`) |
| watchdog observer | `Reloader` | debounced hot reloads |
| OSC callback thread | supriya | `/tr` threshold crossings (keep light) |

`SynthApp._lock` (RLock) serializes GUI-thread and MIDI-thread entry
into app state. The binary settle pass is re-entrancy-latched
(`_busy`/`_again`).

---

## 15. Testing map

Headless (CI runs these on every PR — structure, not sound):
`tests/smoke.py` (every module loads, synthdefs compile, params sane,
patches parse, keyshift math), `tests/test_graph.py` (audio-wire
derivation, graph/ctl bookkeeping, instance ids, multi-voice,
tonic→drone, keyshift lanes/progression, tap-closure, snip-heal),
`tests/test_looper.py` (deck timing, take pairing), plus unit suites
(`test_deriver`, `test_gate`, `test_lfo`, `test_ping`, `test_power_sine`,
`test_threshold`, `test_transport`), and the THREE Playwright suites
`gui_check8.py` + `check_blocks.py` + `check_real.py` (needs
`requirements-dev.txt` + `playwright install chromium`). All three are
HEADLESS and run in CI — `check_real.py` replays a captured real-rig
state broadcast into `blocks.html` through the mock websocket and needs
no server and no audio, despite the fixture coming off Cole's live rig.
CI runs 9 Python + 3 Playwright suites; `test_power_sine.py` exists but
is NOT wired into `ci.yml`, so run it by hand.
Live-rig only (Mac, real audio — these talk to a running
`python -m synthbase gui` over websocket): `test_mixed_sources.py`,
`diag_*.py`, `hear_check.py`, `probe_*.py`.
On the Mac, `python -m synthbase test` is the real proof.
New checks are written failing-first against broken behavior.

---

## Appendix A. Per-release revision checklist

Work this list top to bottom at every release; each row names the
section to re-verify and its source of truth in code.

| § | Re-verify against |
| --- | --- |
| 1 | `cli.py` (commands/flags), repo tree, `run.sh` |
| 2 | `rack.py` (`type_of`/`alloc_id`), `module.py` (`FAMILIES`, `Param`), CLAUDE.md nomenclature |
| 3 | `rack.py`, `master.py`, `engine.py`, `watcher.py`, `audio_devices.py` |
| 4 | `app.py` (`CTL_SOURCES/TARGETS`, `default_ctl_wires`, `_ctl_sinks`, sink classes), `midi.py`, `arp.py`, `drone.py`, `keyshift.py` |
| 5 | `gate.py` (docstring + `is_toggle_dst` — the endpoint table MUST match it), `ping.py` |
| 6 | `lfo.py`, `threshold.py`, `scope.py` |
| 7 | `relay.py` |
| 8 | `transport.py` (`DIVISIONS`), `drums.py`, `midi.py` |
| 9 | `looper.py`, `drone.py` (deck superpower) |
| 10 | **every file in `modules/`** — regenerate the param tables from `@module` blocks; check FAMILIES; note new/renamed/removed modules |
| 11 | `server.py` — the docstring AND the `_handle` dispatch; diff both against §11.1 |
| 12 | `presets.py`, `patches/` |
| 13 | `docs/BLOCKS_SPEC.md`, release notes, `gui/blocks.html` constants |
| 14–15 | thread spawns per file; `tests/` listing + CI workflow |

Update the header stamp (verified-against commit + date) last, and cut
anything the release removed — a stale claim in this doc is a bug.
