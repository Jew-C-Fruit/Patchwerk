# docs/manual/img/ — the single figure folder

> **⚠️ SCAFFOLD.** 1 of 107 figures exists (`fig-03-01.png`, a pipeline proof
> captured 2026-07-26). Conventions below are downstream of
> [`../DESIGN.md`](../DESIGN.md).

**This is the only image folder.** There are no per-chapter directories; every
figure reference anywhere in the manual resolves to `docs/manual/img/`.

---

## 1. It is gitignored — what that means for you

`.gitignore` in this folder ignores **rasters** (`*.png`, `*.jpg`, `*.jpeg`,
`*.webp`, `*.gif`). A fresh clone has **no images**. That is deliberate:
screenshots are regenerated from the app, not archived.

Tracked, because they are not regenerable: `*.svg` diagram sources,
`README.md`, `.gitignore`.

> **If Cole wants the folder ignored with no exceptions,** delete the `!*.svg`
> line — but then diagram sources need a committed home elsewhere, because a
> hand-drawn SVG cannot be regenerated from anything.

### Pages degrade, they do not break

Figures are painted as CSS **background images**, never `<img>`. A missing
background simply does not paint and the placeholder underneath shows through
— where a missing `<img>` would put a broken-image icon on the page. So the
manual reads as a scaffold on a fresh clone rather than as a fault.

### How to obtain the images

| Figure kind | How |
| --- | --- |
| Screenshots (68) | regenerate from a running instance — §2 below |
| Diagram renders (39) | re-render from the committed `.svg` source in this folder |
| Everything, quickly | ask Cole for the current figure bundle; there is no published archive yet |

---

## 2. Capture — PROVEN END TO END

`../capture.py` drives a real instance with Playwright. It has been run
successfully; `fig-03-01.png` is its output.

```bash
export PATH="/Applications/SuperCollider.app/Contents/Resources:$PATH"
./run.sh pad_space --no-browser          # rig up, ~12 s
.venv/bin/python docs/manual/capture.py list          # what's on screen
.venv/bin/python docs/manual/capture.py page fig-03-01
```

**Measured:** rig boot ~12 s (once per session), then **~4 s per capture**,
fully unattended. Prerequisites already present on Cole's Mac: SuperCollider
at `/Applications/SuperCollider.app` (**not on `PATH` — export it**), the
venv, and Playwright's Chromium.

### The settled standard, enforced by the script

| Setting | Value | Follows from |
| --- | --- | --- |
| Viewport | **1440 × 900** | `--ar-page` is 16:10 |
| Scale | **2×** → 2880 × 1800 | print sharpness |
| Format | **PNG** | flat UI colour |
| Theme | the one theme | Patchwerk has no light mode |
| Geometry mode | **blocks** unless the figure is about flex | the default a reader sees |
| Browser chrome | **none** | headless has none — this is free |
| Cursor | **hidden** | headless has none — free, except in drag sequences, where it must be faked |
| Zoom | **100%** | required by the true-scale claim, DESIGN.md §9 |
| CSS transitions | **frozen** during capture | stops a mid-shove card smearing |

### 2.1 Card portraits

All 30 use the **3:2 `--ar-card`** plate with the card centred at natural size.
`capture.py card <name> <gid>` crops to the card plus even padding. Shoot them
in one sitting, one patch, one window: power on, params at module defaults, no
stray wire stubs.

**Note:** cards are keyed by **instance id** (`lowpass`, `lowpass.2`), not
type. Run `capture.py list` before batching.

---

## 3. What will NOT hold still

Honest limits found while proving the pipeline. Freezing CSS transitions does
**not** freeze these — they are canvas animation driven by live data:

| Moving thing | Affects | Mitigation |
| --- | --- | --- |
| Master + input meters | any figure showing the top bar or Master Out | capture with the transport stopped, or accept a varying meter |
| Scope / waveform monitors | FIG-08-05, FIG-14-15, FIG-03-11 | these figures *want* a live trace — accept non-determinism, or drive a fixed input |
| LFO visualisation | FIG-08-01, FIG-08-02 | phase differs per capture; harmless but never byte-identical |
| Note-monitor bars | FIG-06-08 | needs notes actually playing at the shutter — the least deterministic figure in the set |
| Beat position / clock | anything with the top bar | stop the transport first |

**Consequence: captures are not byte-reproducible**, so do not diff them in
CI. The settle delay (2.5 s) is a heuristic, not a signal — it was reliable in
every trial, but it is a sleep, not a guarantee.

### The second route: mock state, no rig

The repo's own `tests/check_blocks.py` and `tests/gui_check8.py` drive
`gui/blocks.html` from a **file:// URL with mock state — no server, no
audio**. For any figure that does not need live signal, that route is
deterministic, faster, and runnable anywhere. It is not wired into
`capture.py` yet. **Worth doing before the bulk capture run** — it would make
most of the 68 reproducible instead of merely repeatable.

---

## 4. Naming

| Kind | File |
| --- | --- |
| Screenshot | `fig-03-01.png` |
| Diagram | `fig-04-01.svg` **and** `fig-04-01.png` |
| Annotated composite | `fig-03-01.png` + `fig-03-01.src.svg` (overlay source) |

Filename = figure ID, lowercased. **IDs are never reused** — a dropped figure
retires its ID so stale references fail loudly.

Annotation (callouts, leaders, highlights) is **not baked into the capture**.
It is drawn in the page over the image, per DESIGN.md §8 — so a re-capture
does not mean re-annotating. FIG-03-01 in the proof page works this way.

---

## 5. Diagram standard

- Vector, authored as SVG, source committed here.
- **True GUI plane colours** (`--pw-*`) — diagrams sit on dark plates. Take
  them from `gui/blocks.html`, never from a screenshot.
- **Triple encoding always**: colour *and* stroke pattern *and* the word.
  Recipes in `../style/manual.css` (`.stroke-audio` …): audio solid, notes
  `10 3`, mod `2 3`, binary `9 3 2 3`.
- Stroke weight uniform 2 px; type `system-ui`; readable at half size and in
  greyscale.
- Blocks-geometry diagrams drawn **1:1** — XS 72×72, S 160×72, M 160×160,
  L 352×160. Verified in the proof page.

---

## 6. Re-capture at each release

`../FIGURES.md` carries a per-figure Status column; that is the checklist.
Any figure whose card, wire rendering or layout changed must be re-shot.

**A stale screenshot is worse than a missing one.** A missing figure is
obviously missing; a stale one teaches an interface that no longer exists.

---

## 7. Still open — needs Cole

| # | Decision | Blocks | Why it needs you |
| --- | --- | --- | --- |
| **D-1** | **The figure patch.** Whole-patch figures should all show the same patch. Recommend a purpose-built `patches/manual.py` containing every wire kind once, so FIG-04-01 and FIG-15-05 work. FIG-03-01 currently shows whatever `pad_space` had resumed — fine for a proof, not for the manual. | ~⅓ of the figure set | needs your judgement on a representative patch |
| **D-2** | **Device-name redaction.** FIG-16-02 and FIG-02-02 show real hardware — FIG-03-01 already displays "MacBook Pro Sp…" on Master Out. Show as-is or genericise? | 4 figures | privacy call; must be uniform |
| **D-4** | **Serif body face.** Charter is proposed and resolves correctly on macOS (verified in the proof). Elsewhere it falls back to Georgia. Bundle a webfont? | nothing | adds a repo dependency |

D-3 and D-5 are in `../DESIGN.md` §12; all open items are indexed in
`continuity/manual-xref.md`.
