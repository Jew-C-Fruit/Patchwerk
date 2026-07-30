# 11. Modules — overview

How to read a module page, so the three chapters after this one can be short.

---

## 11.1 Sources, effects and duals

A **source** generates audio and has no audio input. An **effect** processes
audio and has exactly one. A **dual** — new in v2.2 — is both: it generates
when nothing is wired into its audio input, and becomes an effect the moment a
wire lands there. Every module, of any kind, puts out a stereo pair.

v2.2 ships **26 modules: ten sources, fifteen effects and one dual.** Seven of
the ten sources respond to notes and three do not; that is the line Ch 12 and
Ch 13 split on. The dual is `power_shaper`, and it sits at the end of Ch 14
(§14.8) because it takes an audio input.

One wrinkle worth knowing once: you *can* drop an audio wire onto a source
card, and it is **not** an input. It sums into that source's own output bus and
rides along downstream (§5.3) — a mixing gesture, not a processing one. The
dual is the exception: on that one card the wire genuinely is an input, and it
changes what the card is.

## 11.2 Playable and non-playable

A module is **note-playable** if and only if it generates and its synthdef
exposes both `freq` and `gate` (REFERENCE §2.2). Nothing else decides it, and
it tells you what to do after you drop a card:

- **Playable** — it needs a **voice allocation** aimed at it, and something
  feeding that allocation notes, or it will never make a sound. Ch 6 owns that
  wiring.
- **Non-playable** — it runs the moment it exists. Wire its output somewhere
  and you are done.

Playable sources spawn with the gate **shut**, deliberately: otherwise every
idle voice would drone after each rebuild. A freshly spawned voice card sitting
silent is correct behaviour, not a fault.

> **WARN.** The family label `voice` is not the same claim as "playable".
> `wind` is in the `voice` family, wears the instrument card face and sizes
> like the real voices — and has neither `freq` nor `gate`. It is in Ch 13.

## 11.3 Families

A module's **family** sets the card's colour and the palette section the module
lives in. It carries no DSP meaning at all — the engine never reads it
(REFERENCE §2.2).

[FIG-11-03]

| Family | Holds | Chapter |
| --- | --- | --- |
| `voice` | `wobble_saw`, `pulse_pad`, `fm_bell`, `pluck`, `wind` | 12, and `wind` in 13 |
| `psine` | `power_sine_shaper`, `power_sine_additive`, `power_sine_blend`, `power_shaper` | 12, and `power_shaper` in §14.8 |
| `input` | `audio_in` | 13 |
| `service` | `drone` | 13 |
| `filter` | `lowpass`, `telephone` | 14 |
| `time` | `echo`, `reverb`, `chorus`, `flanger`, `phaser`, `autopan` | 14 |
| `dirt` | `drive`, `bitcrush`, `wavefolder` | 14 |
| `dyn` | `compressor` | 14 |
| `vox` | `pitchshift`, `ringmod` | 14 |
| `effect` | `scope_tap` — no family of its own, so it falls back to its kind | 14 |

**`psine` arrived in v2.0, which was named "Wavetable" after it.** Its colour is
cyan `#22b8d4`, and it is the only family whose members are one idea built
several ways — three generators in §12.5 and one dual in §14.8.

Two families straddle chapters: `voice` contains a module you cannot play, and
`psine` contains a module that is only sometimes a source. Families group by
*appearance*, so read those two as warnings rather than as inconsistencies.

[FIG-11-01]

## 11.4 Parameter types

[FIG-11-02]

Every module's controls are one of four types, and the type is visible from the
card:

| Type | Looks like | Notes |
| --- | --- | --- |
| linear | slider/knob | plain range from min to max |
| exponential | slider/knob | frequencies and times; movement is finer at the low end, so the musically useful part gets most of the travel |
| toggle | checkbox | the value is the param's min or its max, nothing between |
| select | dropdown | a fixed set of named options |

Exponential is the one to internalise. A `cutoff` from 60 to 12000 Hz on a
linear slider would put every usable filter setting in the first centimetre; on
an exponential curve the octaves are evenly spaced, which is how your ear hears
them anyway.

One standing convention across the palette: **pitch offsets are always in
semitones or cents, never raw frequency ratios.** `detune` is cents,
`semitones` is semitones. A parameter that genuinely is a ratio is an operator
ratio inside a synthesis technique, not a transposition.

## 11.5 How to read the module pages

Every entry in Chapters 12, 13 and 14 has the same shape:

```
## <Display Name> — `key`
<figure: the card>
<family> · <playable or not> · <card size, where the module declares one>
**What it is.** One sentence.
**Use it for.** Two or three sentences, musical rather than technical.
**Handling.** Only where a real trap or a useful starting point exists.
```

**Parameter values are not repeated in these chapters.** The complete,
code-verified tables live in REFERENCE §10 — §10.1 for playable voices,
§10.2 for non-playable sources, §10.3 for effects and §10.3.1 for the dual —
and they are regenerated from the source at each release. Each chapter cites
its section once, at the top.

**What every module has, so no entry says it twice.** A stereo audio output. A
binary `:pwr` endpoint — wire a level into `lowpass.2:pwr` and the module's
enable follows it (REFERENCE §5.3). A modulation destination on every
parameter, one wire each (Ch 8). A bypass, which pauses a source and turns an
effect into a true passthrough (§5.7). Entries below mention these only where a
module does something unusual with one.

**What only some modules have.** An audio *input* — effects and the dual. A
`target` relationship with a voice allocation — playable sources. A notes-plane
presence of its own — `drone` only, which is why it gets the longest entry in
Ch 13.

## 11.6 What is not in the palette

Patchwerk runs a number of synthdefs that are not modules: the metronome, the
master section and its meters, the drum voices, the LFO and threshold helpers,
the scope's ring buffer, the rack's passthrough router, the Loop Deck's private
voice. You cannot spawn one and none of them appears in a patch. The only
reason to know they exist is that their names turn up in log output, and **the
leading underscore is the tell** — `_click`, `_master`, `_kick`. REFERENCE
§10.5 lists them.

## 11.7 Coverage checklist — all 26 modules

**Ch 12 — playable voices (7).** `wobble_saw`, `pulse_pad`, `fm_bell`,
`pluck`, `power_sine_shaper`, `power_sine_additive`, `power_sine_blend`.

**Ch 13 — non-playable sources (3).** `audio_in`, `wind`, `drone`.

**Ch 14 — effects (15).** `lowpass`, `telephone`, `echo`, `reverb`, `chorus`,
`flanger`, `phaser`, `autopan`, `drive`, `bitcrush`, `wavefolder`,
`compressor`, `pitchshift`, `ringmod`, `scope_tap`.

**Ch 14 §14.8 — dual (1).** `power_shaper`.

---
