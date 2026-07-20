# Patchwerk v2.0 "Wavetable" — pre-release

The Blocks overhaul: a new grid-native UI replaces the free-form canvas, a new
oscillator family lands, and a stack of engine reliability fixes ship with it.
Tagged as a **pre-release** — the UI is under active daily iteration.

## Patch notes

### Blocks UI (gui/blocks.html — now served at `/`)
- **Block grid layout.** Modules snap to S/M/L footprints (10x4.5, 10x10,
  22x10 units) on a 12x8 block board with 2-unit gutters. iOS-style
  quadrant-shove placement with chained live preview; smalls pair two to a
  block. No scrollbars, no overflow, ever: every card takes the smallest
  footprint that fits all content (L bodies flow params into two columns);
  titles always render whole on one line.
- **Gutter-routed wires.** Wires travel only in open gutters (Dijkstra with
  turn penalty and narrow-gutter bias), run in visible bundles centered on
  the grid line with concentric nested corners, minimize crossings, and
  layer curves over straights. Every wire has its own handle at both ends,
  a mid-wire label that expands on hover (balloon mode when short), splices
  modules when dragged, and cuts on double-click.
- **Port discipline.** Inputs live on Top/Left, outputs on Bottom/Right.
  Param-control inputs are strictly single-source: one lightly-drawn handle
  per param, exactly in line with its row, no + fan handle; drops replace.
  Single-input ports (a source's play-in, a drone's tonic-in) never
  advertise fan-in. Outputs branch; true fan-in inputs keep +.
- **Viewport.** Default 6x4-block view (24 cells), +/- zoom (+1 row/+1
  column per step), pan lock defaulting to locked with automatic unlock
  whenever placed modules would be hidden. Grouped view controls:
  [−][+][tidy][lock].
- **Tidy.** Compacts each connected tree into its own tight column grid in
  signal order — input top-left, output parked at the bottom of the first
  column, one empty column between trees.
- **LFO visualization.** A modulated parameter's slider rides the actual
  oscillation (all five shapes), the readout rolls, the track shows the
  amplitude band and a center marker; a psine card's waveform preview
  morphs live when `p` is modulated. Cutting an LFO wire keeps the module
  (re-arms in place) instead of deleting it.
- **Device I/O on the cards.** Master Out picks the output device, Audio In
  the input device, Keys/MIDI the MIDI port — dropdowns when several exist.
  A corner warning appears when active input and output sample rates
  mismatch (only relevant when both ends are in play).
- **Backend restart from the UI.** The ⟳ button snapshots the full rack —
  modules, params, audio/ctl wiring, voice targets, drums routing — re-execs
  the server, and restores everything on boot. Layout survives client-side.
- **flex and the original GUI are archived** to `gui/legacy/` — kept for
  reference, no longer served or maintained.

### Power Sine oscillator family (new modules)
- `power_sine_shaper`, `power_sine_additive` ("add"), `power_sine_blend`:
  one-knob sine→square morph built three ways (literal waveshaper with
  deliberate aliasing character; band-limited additive bank; wavetable-style
  crossfade), sharing `synthbase/harmonics.py` (odd-harmonic bank +
  coefficient laws). All three collapse to an identical pure sine at p=2
  (measured THD ≈ −130 dB); spectra verified offline against the square-law
  ideal. Family tag `psine`, cyan. Cards carry a computed waveform preview
  with a live-oscilloscope toggle.

### Engine and server fixes
- **Voice survives source swaps.** Removing the last playable source used to
  silently delete the mono voice, leaving the rack unrecoverably mute.
  Retargeting now resurrects a dead voice, and adding a playable source
  re-aims dead voices automatically.
- **Large synthdefs load reliably.** Synthdefs over ~8 KB were silently
  dropped by scsynth's UDP transport, hanging spawns forever; the engine now
  loads big defs via a temp file and `/d_load`.
- **Low-latency oscilloscope.** Scope taps keep a continuously-recording
  ring buffer per watched bus (reaped after 3 s idle); a capture is a pure
  read — ~29 ms warm versus ~70 ms + poll-cadence before. Note events
  trigger an immediate poll, so note→first-trace is ~35–45 ms.
- **Restart/resume plumbing** (`POST /restart`, `.resume.json`,
  `presets.write_resume`/`apply_resume`).
- Chrome 128+ CSS-zoom pointer-geometry fix (sliders/drags under the zoomed
  viewport), scope moved to the Monitors palette section, drums mini
  sequencer at M size, and a long tail of layout/handle polish.

## Process summary

This release was built in a tight human-in-the-loop cycle with Claude
(Anthropic) driving implementation in a cloud sandbox against the Mac over a
device bridge:

1. **Design by iteration.** The block UI began as a standalone prototype,
   refined through ~12 spec revisions (geometry, bundling, labels, handle
   rules), each verified headless before the next.
2. **Surgical port.** An anchor-asserting build script (`build.py` + four
   engine files) splices the block engine into the flex codebase, preserving
   every card builder, websocket handler, and header control. Any drift in
   the source fails the build loudly rather than mis-splicing.
3. **Dual validation gates.** Every change ran two Playwright suites before
   shipping: a mocked-backend interaction suite (~70 checks: placement,
   routing, handles, tidy, viewport, viz, devices) and a captured-real-state
   replay. Server-side changes were additionally probed live over the
   websocket against the playing rack (spawn/wire/cut/restore round-trips,
   note-RMS audio measurements, restart/resume verification).
4. **Audio verification without ears.** The psine family was rendered
   offline through scsynth NRT and FFT-analyzed (purity at p=2, square-law
   convergence, alias-floor measurement) before any listening test.
5. **Bugs root-caused, not patched around.** The mass-delete, dead-voice,
   oversized-synthdef, and CSS-zoom slider bugs were each reproduced with a
   dedicated probe, fixed at the mechanism, and pinned with a regression
   check.

Known state: pre-release. The Artifix PR (#1) predates this work and should
be rebased on this tag; its review notes stand.
