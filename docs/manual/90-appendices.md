# Appendices

---

## Appendix A — Glossary

Every term this manual uses as a term of art, once, in alphabetical order.
Where REFERENCE defines a term precisely, the § is cited and wins.

- **arp** — the arpeggiator: a singleton note-pool layer on the notes plane.
  Disabled, it passes notes straight through (REFERENCE §4.4).
- **binary** — the plane carrying one hi/lo signal. Sources own levels; edges
  are derived from level changes (REFERENCE §5.1).
- **block** — a 10 × 10-unit snappable area of the grid, separated from its
  neighbours by 2-unit gutters (REFERENCE §2.6).
- **card** — one module, control node or monitor drawn on the canvas. Cards are
  keyed by instance id, never by type.
- **chain** — the ordered list of modules a patch file declares. The wire
  overlay sits on top of it (§5.1).
- **closure** — the doctrine that every path which silences something must emit
  its note-offs, monitor taps included (REFERENCE §2.5).
- **deck** — the Loop Deck: the singleton, bar-synced recorder and replayer of
  note events (REFERENCE §9).
- **drone** — two distinct things. The `drone` **module** is a sustained source
  with no gate, so bypass is its off switch (§13.3). The **Drone Voice**
  allocation node holds the last root it was given (REFERENCE §4.3.2).
- **dual** — a module that both generates and processes. A wire into its input
  is what puts it in FX mode (REFERENCE §2.2, §14.8).
- **effect** — a module that processes audio and owns an input bus.
- **family** — the palette's grouping label for a module, and the card's
  colour. Purely cosmetic (REFERENCE §2.2).
- **fan-in** — several outputs landing on one input. Buses sum, so it is free
  and needs no mixer (§5.3).
- **flex mode** — the free-pixel geometry mode: fixed card width, automatic
  height, no grid snap. Blocks is the mode the manual shows (§3.1).
- **gate** — the on/off signal that opens and closes a playable source's
  envelope. A **logic gate** is a different thing entirely (Ch 7).
- **gutter** — the 2-unit gap between blocks. Wires travel only in gutters,
  along their centrelines (§3.5.2).
- **handle** — the small pill a wire attaches to. Inputs sit on a card's top
  and left edges, outputs on its bottom and right.
- **hot reload** — saving a module file and hearing the change without stopping
  the audio (§5.9, §15.7).
- **instance id** — a module or node's identity: `lowpass`, `lowpass.2`. Every
  message and every endpoint is keyed by it (REFERENCE §2.1).
- **lane** — one of a key shifter's four isolated transposition paths;
  `keyshift.2:3` is lane 3 of `keyshift.2` (REFERENCE §4.6).
- **LFO** — a standalone modulation source. One output fans out to any number
  of param handles, each orbiting its own slider value (REFERENCE §6.1).
- **module** — one DSP file in `modules/`, and the card it spawns.
- **null bus** — the rack's single persistent silent destination. Every
  disconnected output parks there, still alive (§5.4).
- **overlay** — the layer of audio wires you draw on top of the chain. After
  the first structural edit, the wires are the truth (§5.1).
- **patch** — the chain plus the note bindings, stored as a Python file in
  `patches/`. **It contains no wires** (REFERENCE §12.1).
- **ping** — a pulse: hi then lo, on the binary plane. Because sources own
  levels, a ping passes through a logic gate whose other leg is held hi
  (REFERENCE §5.1).
- **plane** — one of the four wire systems: audio, notes, binary, modulation
  (Ch 4).
- **playable** — a source is playable if it exposes both `freq` and `gate`.
  Only a playable source can be a voice's target (REFERENCE §2.2).
- **preset** — a JSON snapshot of everything performable — params, enables,
  volume, transport, nodes — but not the audio graph (§15.6,
  REFERENCE §12.2).
- **rack** — the live instrument: the module instances and the audio graph the
  server owns.
- **relay** — a type-agnostic switched junction with up to nine independent
  circuits. Contacts are 1:1 and a circuit's kind is fixed by its first wire
  (REFERENCE §7).
- **respawn** — replacing a running node with a fresh one. Removing and
  re-adding a module respawns it; hot reload and in-place type swaps
  deliberately do not.
- **resume** — `.resume.json`: the preset-plus-graph snapshot the ⟳ button
  writes and the next boot consumes and deletes (REFERENCE §12.3).
- **size class** — a card's footprint: XS, S, M or L, measured automatically
  from its content (§3.3.3).
- **source** — a module that generates audio and has no input bus.
- **splice-heal** — the automatic repair when a module is removed: its feeders
  are re-aimed at its destination, so A→X→B becomes A→B (§5.6).
- **tap** — the small event a control node emits when it fires, one per
  source-fire. It is what monitors draw (REFERENCE §2.3).
- **threshold** — a comparator that turns an LFO's continuous value into a
  binary level or a pulse (REFERENCE §6.2).
- **tonic** — the derived root of what is being played, and the name of the
  estimator deriver that emits it (REFERENCE §4.5).
- **type** — the part of an instance id before the dot: `lowpass.2` is of type
  `lowpass`. Never treat an id as a type — derive it (REFERENCE §2.1).
- **unit** — one 16 px grid square. "Three units high" means three squares,
  never three blocks (REFERENCE §2.6).
- **voice** — a note-routing node that drives one playable source's pitch and
  gate. Mono, poly and drone policies, all on one card (REFERENCE §4.3).
- **wire** — a connection on one of the four planes. The graph *is* the
  routing: an unwired event dead-ends silently.

## Appendix B — Keyboard and MIDI reference

One page, meant to be printed. The top bar's **?** button shows the same list
live, plus whatever your trigger cards are currently bound to.

[FIG-A-01]

**Computer keyboard.** Notes play only while **keys** is ticked in the top bar.

| Keys | Does |
| --- | --- |
| `a w s e d f t g y h u j k o l p ;` | play notes — an octave and a bit, chromatic from C |
| `z` / `x` | octave down / up (C1–C7; held notes are released) |
| Caps Lock | sustain pedal |
| Space | arm the Loop Deck's record, in any input mode |

> **WARN.** If a slider, button or text field on a card has keyboard focus, it
> eats your key presses. Click empty canvas and try again.

**Mouse.** Drag a handle to wire it to another card. Drag a wire's end onto
another handle to repatch, or into empty space to cut. Double-click a wire to
cut it. Drag a wire's label onto a card to splice that module into the wire.
Hover a block edge while dragging to shove the neighbours aside. Drag from
empty board to lasso-select a group.

**MIDI, by default** (REFERENCE §8.4). Patchwerk opens one input port. Notes
land on the `keys` node with their velocity; the pitch wheel bends every voice
by ±2 semitones; CC 64 is sustain. Every other CC follows the patch's own `cc`
bindings and is otherwise surfaced unbound, so you can see it arrive before you
bind it. A bound CC moves the on-screen slider too. An **armed** button card
intercepts the CC stream first — that is how pairing captures a knob.

## Appendix C — Wire colour and data type key

The whole legend on one page, duplicated from `[FIG-04-02]` on purpose so it
can be torn out and kept next to the instrument.

[FIG-A-02]

| Plane | Colour | The word | Carries |
| --- | --- | --- | --- |
| Audio | `#3987e5` | `audio` | stereo sound between stages, and out to hardware |
| Control | `#1baf7a` | **`notes`** | note-on/off, sustain, bend — who plays whom |
| Modulation | `#9085e9` | `mod` | a continuous value into a param's range |
| Binary | `#e6c34a` | `binary` | one hi/lo level; edges derived from level changes |

Every wire is drawn **solid**. Colour is the only distinction between the
planes, and the handle label spells the kind out in words. A wire carrying
signal right now also wears a moving white overlay — that marks it live, not a
different kind.

> **Maintainer's note.** This page and `[FIG-04-02]` are the same legend. A
> revision that changes one must change the other.

## Appendix D — When something goes wrong

`docs/TROUBLESHOOTING.md` is symptom-indexed and owns this material. This page
is a pointer, not a guide.

| Symptom | Section of `docs/TROUBLESHOOTING.md` |
| --- | --- |
| *"Setting sample rate failed"*, or no audio at all on boot | Runtime / hardware gotchas |
| Everything feels laggy on Bluetooth headphones | Runtime / hardware gotchas |
| A controller's panel knobs send nothing, though its pedals work | Runtime / hardware gotchas |
| Silence, or the engine will not start, after a rough restart | Runtime / hardware gotchas |
| A slider or button swallows your keyboard notes | Runtime / hardware gotchas |
| scsynth crashes while you are authoring a module | scsynth / DSP gotchas |
| A new source drones on its own, or reads junk instead of its input | scsynth / DSP gotchas |
| A GUI test drops messages against a mock socket | GUI / websocket gotchas |

One known failure is **not** in that document yet: scsynth enumerating your
audio devices correctly and then never starting one. §2.9 says what to do.

## Appendix E — This release

**Release this manual is pinned to: Patchwerk v2.2 "Polyphony", 2026-07-28.**

The full notes are `docs/RELEASE_POLYPHONY.md`. The release is named for what
it adds first:

- **Allocation.** Mono, poly and drone are three policies on one framework,
  differing on a single axis: how many notes may sound at once, which sounding
  note a new one displaces, and what an empty hold does. Poly voices sound up
  to sixteen notes; the drone is now an allocation with a power input rather
  than a special case (§6.3).
- **The binary plane.** Buttons, clocks, thresholds, logic gates and relays —
  none of which existed in v2.0. A patch can now switch, latch and gate itself
  while you play it, with nothing for you to press (Ch 7).
- **A third kind of module.** A **dual** generates when nothing is wired into
  it and processes when something is, deciding which from the audio graph
  rather than from a switch. `power_shaper` is the first (§14.8).
- **A fresh launch comes up stopped.** Nothing clocked runs until you press
  play, and the keys are still live — so you can plug in and play without
  starting a transport you did not want.
- **Audio boots unattended**, and a boot that fails says why instead of
  hanging.
- **Installers**: a macOS disk image and a Windows setup wizard.

Carried over from v2.0 "Wavetable", which this manual also describes: the
Blocks interface, the psine oscillator family, and visible modulation.

## Appendix F — Manual maintenance

*For maintainers, not readers — consider excluding from the printed build.*

This manual's companion is **`docs/REFERENCE-v2.2-polyphony.md`**, the frozen
snapshot of REFERENCE taken at this release. Every `REFERENCE §…` citation in
these pages resolves against that file. `docs/REFERENCE.md` is the living
document: it tracks `main`, it will diverge from the frozen copy, and it is not
what this manual was verified against.

The per-release revision loop:

1. REFERENCE.md is revised first, against its own Appendix A checklist. The
   manual never leads.
2. Freeze the revised REFERENCE.md as `docs/REFERENCE-v<release>.md` and
   re-verify every anchor in `continuity/manual-xref.md` against it. That file
   is the only place open anchors live.
3. Re-run the module coverage checklist (§11.7) against `ls modules/*.py`.
4. Redraw any figure whose card changed — `docs/manual/figspec.py` holds every
   figure, and its drawing kit reads the GUI's own colour and geometry tokens,
   so a restyle is a re-copy rather than a re-shoot.
5. Update Appendix E and the front-matter release pin.

**Standing rule:** where this manual and REFERENCE disagree, REFERENCE wins and
the manual has a bug.
