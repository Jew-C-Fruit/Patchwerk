# Artifix — the package, and how to drive it

Artifix is a five-piece group that works **independently but is designed to
play together**: a generator, two modulators, and two visualizers. Wire none
of it and the generator is a fine drone; wire the group and it breathes.

This doc is the working memory for the build — revise it as the design moves.

## The five pieces

| Piece | Kind | Lives in | What it is |
|---|---|---|---|
| **Artifix Gen** | source module | `modules/artifix_gen.py` | Continuous generative voice: 5-voice unison + sub through an RLPF. Six modulation targets exposed as params. Plays the moment it's in the chain — no note needed. |
| **Living Oscillator** | modulator | `synthbase/living.py` (+ GUI in `gui/flex.html`) | Bounded-aperiodic drift (Thomas attractor). Maps onto any one param via a control bus; never quite repeats. Emits a trajectory the Sphere draws. |
| **Allocation Intent** | modulator | `synthbase/allocation.py` (+ GUI) | A conserved "intensity" budget split across six dims (Σ mᵢ² = r²). Wire dims to params: as one rises the others yield. A held balance, not movement. |
| **Spectrum** | visualizer | `gui/flex.html` (`drawSpectrumViz`) | Client-side FFT bars. Reuses the scope capture — no new server data. Rides an audio wire, or watches the master feed when unwired. |
| **Sphere** | visualizer | `gui/flex.html` (`drawSphereViz`) | Radius-conserving trajectory of a Living Oscillator. Auto-binds to the first living assignment present. |

### Artifix Gen params (the six dims + pitch/amp)

`pitch` (40–440, exp) · `morph` (waveform triangle→saw) · `harm` (harmonic
balance) · `bright` (filter movement) · `res` (resonance) · `detune` (unison
shimmer) · `stereo` (width) · `amp`.

Allocation dim slots map to these: `0 wave→morph`, `1 harm→harm`,
`2 filt→bright`, `3 stereo→stereo`, `4 res→res`, `5 det→detune`.

## The full preset — `patches/artifix.py`

A one-load "everything on" patch for hearing/seeing the whole group without
patching by hand. Run it:

```bash
python -m synthbase gui artifix        # then open http://127.0.0.1:8765
# or headless audio:
python -m synthbase play patches/artifix.py
```

It loads: Gen → master; a Living Oscillator on `morph`; an Allocation Intent
holding a conserved balance across `harm`/`bright`/`res`; and a slow LFO on
`detune`. The Living and Allocation **cards are already on the canvas,
running** — then click **Spectrum** and **Sphere** in the palette to watch.

**Note Monitor stays empty on this patch** — Artifix Gen is continuous and
fires no note events. That monitor is for note-played chains
(keys → voice → source). For Artifix, Spectrum + Sphere are the monitors.

## Patch modulation schema (new)

Patches gained three **optional** sections, applied once on fresh load by
`SynthApp._apply_patch_mods` (start / patch-switch / preset restore — never
inside `_build_from`, so `edit_chain`'s snapshot/restore isn't double-applied).
Entries are best-effort: a bad key/param logs and is skipped.

```python
"lfos":        [{"key","param", "rate"?, "shape"?, "depth"?, "center"?}, ...]
"living":      [{"key","param", "life"?, "wander"?, "depth"?, "center"?}, ...]
"allocations": [{"r"?, "w"?:[6] | "w0".."w5"?,
                 "targets":[{"slot","key","param", "gain"?}, ...]}, ...]
```

`smoke.py` validates these resolve to real modules/params/slots.
`tests/test_preset.py` exercises the loader against the preset.

## How the monitors connect (the gesture)

Monitors are client-side. They attach by **dragging the whole card onto a
wire**, not by port-to-port dragging:

- **Spectrum / Waveform** ride an **audio** wire. Unwired, they watch the
  master feed (global) — so they show output immediately.
- **Note Monitor** rides a **ctl** wire (the note-routing plane).
- **Sphere** isn't wired at all — it auto-binds to a Living Oscillator.

## Recently fixed

- **Living attractor pinned in one octant → Sphere dot stuck in a corner.**
  The old code kicked the Thomas attractor with an impulse of 1.0 and clipped
  at ±3.2. The natural span is ~±5, so it slammed into the +x wall and never
  crossed zero — the trajectory lived in a single octant and the Sphere dot
  huddled top-right (and it skewed `morph` too). Fixed: seed of 0.1, clip rail
  at ±8, chaos term re-centred on 0. The Living now roams all 8 octants.
- **Sphere mapping.** `_living` now publishes a point ON the unit sphere
  (`(x,y,z)/|v|`), so the dot rides the surface with real depth (front/back
  shading) and sweeps the whole sphere. Radius = 1 is finally the literal
  conserved invariant. Preset `life` bumped 0.35 → 0.50 for livelier drift.

## Open items

- **Allocation card label legibility.** The six dim out-ports overlap the
  card's percent text when connecting. In progress: give the dim ports real
  labels positioned *outboard* of the right edge so they never sit over the
  body text, and hide the row's under-text while a port label shows.

## Tests (no audio needed)

```bash
python tests/smoke.py             # modules + patches incl. artifix
python tests/test_preset.py       # preset mods wire correctly
python tests/gui_check_artifix.py # Spectrum/Sphere/Living/Allocation in the GUI
python tests/gui_check8.py        # core flex regressions
```
