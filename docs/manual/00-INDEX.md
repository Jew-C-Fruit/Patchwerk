# Patchwerk User Manual

| | |
| --- | --- |
| Status | Content complete — figures complete |
| Release | **Patchwerk v2.2 “Polyphony”** |
| Date | 2026-07-27 |
| Companion reference | [`docs/REFERENCE-v2.2-polyphony.md`](../REFERENCE-v2.2-polyphony.md) (frozen) |
| Figures | 119 — all drawn in HTML/CSS, no image files |
| Design | settled — [`DESIGN.md`](DESIGN.md), proof at [`proof/chapter-03-proof.html`](proof/chapter-03-proof.html) |

---

## What this manual is

The **release-specific, image-rich graphical user manual** for Patchwerk. It
teaches a person to use the instrument. It is revised at every release.

It sits **on top of** `docs/REFERENCE.md`, which is the living
single-source-of-truth and is code-verified. The division of labour:

| | `docs/REFERENCE.md` | this manual |
| --- | --- | --- |
| Question it answers | "what does the system do, exactly?" | "how do I do the thing I want?" |
| Audience | contributors, LLM agents, future Cole | players |
| Lifespan | living; tracks `main` | pinned to a release |
| Form | tables, exhaustive, code-cited | prose + figures, task-ordered |
| Duplication rule | owns the facts | **never restates a fact — cites the § and shows it** |

**The manual does not duplicate REFERENCE.md.** Where a complete parameter
table or endpoint table is needed, the manual points at the § and shows the
card instead. If a fact appears in both, REFERENCE.md wins and the manual has
a bug.

---

## Format decision — a directory of chapter files

**Chosen:** `docs/manual/` — one Markdown file per chapter, plus
`docs/manual/img/` for figures.
**Rejected:** a single `docs/MANUAL.md`.

**Reasons, in order of weight:**

1. **Images need a home next to the text.** 108 planned figures cannot live
   loose in `docs/`. A directory gives `img/` a natural, scoped place and
   keeps figure paths short and stable (`img/fig-03-01.png`).
2. **This repo works in parallel worktrees.** `git worktree list` currently
   shows four live checkouts. A single 100+ page file is a merge-conflict
   magnet the moment two people revise different chapters; chapter files
   conflict only when the same chapter is touched.
3. **Per-release revision is chapter-shaped.** REFERENCE.md's Appendix A
   checklist walks section by section. A per-chapter file lets the release
   checklist mark chapters done/stale individually, and lets `git log
   docs/manual/14-modules-effects.md` answer "when was the effects chapter
   last true?"
4. **The modules chapter alone is 25 modules and ~31 figures.** In one file
   it dominates and buries everything else in the outline.
5. **Reviewability.** A PR that revises one chapter shows a readable diff.

**The cost, and how it is paid:** a directory is harder to read end-to-end and
harder to search than one file. Mitigations, both deferred to the writing
phase, both tracked here so they are not forgotten:

- `00-INDEX.md` (this file) is the front door and carries the full TOC.
- **TODO (build step):** a trivial `docs/manual/build.py` that concatenates
  chapters in numeric order into a single `MANUAL-<release>.md` for printing
  and full-text search. Not written yet — no content to concatenate.

**File naming:** `NN-slug.md`, numeric prefix = reading order. Numbers are
sparse-ish on purpose (appendices start at `90-`) so a chapter can be
inserted without renumbering the world.

---

## Table of contents

### Part I — Getting the instrument running

| # | Chapter | Covers |
| --- | --- | --- |
| 1 | [Getting started](01-getting-started.md) | what Patchwerk is, the shape of the system, first sound |
| 2 | [Machine setup](02-machine-setup.md) | install, SuperCollider, venv, audio + MIDI devices, run modes, launching |

### Part II — The interface

| # | Chapter | Covers |
| --- | --- | --- |
| 3 | [The interface](03-interface.md) | the page, top bar, card anatomy, blocks vs flex, drawing wires, palette, monitors |

### Part III — The logic and signal system

| # | Chapter | Covers |
| --- | --- | --- |
| 4 | [Signal planes](04-signal-planes.md) | the four planes, wire kinds, global-vs-wired, closure doctrine |
| 5 | [The audio graph](05-audio-graph.md) | chain vs overlay, fan-in, null bus, order, bypass, master |
| 6 | [The control plane](06-control-plane.md) | keys, voices, arp, tonic derivers, key shifters |
| 7 | [Binary and logic](07-binary-logic.md) | gates, pings, logic gates, relays |
| 8 | [Modulation](08-modulation.md) | LFOs, thresholds/CV, scope |
| 9 | [Transport, timing and MIDI](09-transport-timing.md) | clock, divisions, drums, MIDI in |
| 10 | [The Loop Deck](10-loop-deck.md) | record, replay, overdub, takes |

### Part IV — The modules

| # | Chapter | Covers |
| --- | --- | --- |
| 11 | [Modules — overview](11-modules-overview.md) | how to read a module page, families, param types |
| 12 | [Playable voices](12-modules-voices.md) | 7 modules |
| 13 | [Non-playable sources](13-modules-sources.md) | 3 modules |
| 14 | [Effects](14-modules-effects.md) | 15 modules, plus the one dual in §14.8 |

**Module count: 7 + 3 + 15 + 1 = 26.** This total is a coverage contract —
see the checklist in [`11-modules-overview.md`](11-modules-overview.md).

### Part V — Doing things with it

| # | Chapter | Covers |
| --- | --- | --- |
| 15 | [Building racks](15-building-racks.md) | blank patch → performance rack, rewiring, saving, hot reload |
| 16 | [IO routing](16-io-routing.md) | hardware in/out, buses, device pickers, drum targets, monitoring |

### Back matter

| # | Chapter | Covers |
| --- | --- | --- |
| A–E | [Appendices](90-appendices.md) | glossary, keyboard/MIDI reference, wire colour key, troubleshooting pointer, release notes pointer |

### Working documents (not part of the published manual)

| File | Job |
| --- | --- |
| [`FIGURES.md`](FIGURES.md) | the figure index — generated from `figspec.py`, not hand-maintained |
| [`figspec.py`](figspec.py) | every figure, drawn in HTML and CSS. Edit a figure here. |
| [`DESIGN.md`](DESIGN.md) | the visual design spec — layout, type, colour, figures, annotation |
| [`style/manual.css`](style/manual.css) | the canonical stylesheet implementing it |
| [`proof/chapter-03-proof.html`](proof/chapter-03-proof.html) | rendered design proof — **open this in a browser** |
| [`build.py`](build.py) | concatenates the chapters into one self-contained HTML edition with contents, index and search. |
| [`img/README.md`](img/README.md) | the single image folder; capture conventions and how to obtain images |
| `continuity/manual-xref.md` | **all open anchors and open decisions** (gitignored, outside this directory) |

---

## Conventions used in the scaffold

| Marker | Meaning |
| --- | --- |
| `> **STUB.**` | a heading with no content yet; the blockquote says what goes there |
| **Cross-references** | chapters carry a POINTER only; the § numbers themselves live in `continuity/manual-xref.md` |
| `[FIG-NN-NN]` | a figure slot; see `FIGURES.md` for what it must show |
| `[NEEDS-RIG]` | this figure requires a running instance with audio to capture |
| `[AUTHORED]` | this figure is drawn, not captured — no rig needed |

---

## What remains

1. **Design decision D-2 — device-name redaction.** Still open: whether
   captured screenshots may show real CoreAudio and MIDI device names, or
   whether they are redacted to generic labels before publication.
2. **Design decision D-4 — serif webfont bundling.** Still open: whether the
   HTML edition bundles the serif face or falls back to a system stack.

**Resolved:** D-5 is answered by [`build.py`](build.py) — the single-file
HTML edition with data-URI figures. D-1 is answered by the decision to ship a
committed `.resume.json` fixture rather than a patch file.
