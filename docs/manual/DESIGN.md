# Manual visual design specification

> **⚠️ SCAFFOLD — part of the manual scaffold, not published documentation.
> See [00-INDEX.md](00-INDEX.md).**
>
> This spec governs how the manual **looks**. It contains no manual content
> and does not wait on REFERENCE.md — layout and colour can be settled while
> the section numbering is still moving.

**Implemented by:** [`style/manual.css`](style/manual.css) — canonical.
**Proved by:** [`proof/chapter-03-proof.html`](proof/chapter-03-proof.html) —
open it in a browser.

---

## 1. What this governs, and what it does not

| Document | Governs |
| --- | --- |
| `docs/BLOCKS_SPEC.md` | the **Patchwerk interface** — grid, wire routing, port discipline |
| **this file** | the **manual about** that interface — page layout, type, figures |
| `img/README.md` | how individual figures are captured and authored |

These are not peers. **`BLOCKS_SPEC.md` is upstream.** This spec derives its
entire palette from the running GUI and has no independent colour authority.
When the interface changes colour, this document is wrong until re-derived —
it is never the other way around.

---

## 2. Principles

1. **Derive, never invent.** Every colour comes out of `gui/blocks.html`. A
   manual whose blue disagrees with the interface's blue teaches a lie.
2. **Hue carries signal meaning; the page furniture is achromatic.** Links,
   rules, headings and callout markers are grey. If something on the page is
   coloured, that colour *means* a signal plane, a module family, or danger.
   The reader can trust colour, which is the whole reason to reserve it.
3. **Never colour alone.** Every plane distinction is encoded three ways —
   colour, stroke pattern, and the word. Print and colour-blind readers get
   the full meaning.
4. **The manual's paper is the interface's ink.** The GUI is `#f2f1ea` text
   on `#0d0d0d`; the manual inverts it. Same two colours, opposite roles.
5. **Screenshots sit on plates.** A dark screenshot dropped straight onto
   light paper reads as a hole burned in the page. Mounted on a dark plate
   with padding, it reads as deliberate.
6. **The manual quotes the interface in the interface's typeface.** Body
   prose is a serif; anything naming an on-screen thing is set in the GUI's
   own `system-ui`. The reader can see, typographically, when the manual is
   pointing at the screen.

---

## 3. Palette provenance

Copied verbatim from `gui/blocks.html` @ `687692b`. **Do not eyedrop these
from a screenshot — re-copy them from source at each release.**

### 3.1 Surfaces — `blocks.html:8–22`

| Token | Value | Role in the GUI |
| --- | --- | --- |
| `--pw-plane` | `#0d0d0d` | canvas |
| `--pw-surface` | `#1a1a19` | palette, chrome |
| `--pw-card` | `#202020` | card body |
| `--pw-ink-1` | `#f2f1ea` | primary text |
| `--pw-ink-2` | `#a9a89e` | secondary text |
| `--pw-hairline` | `#2c2c2a` | dividers |
| `--pw-grid` | `rgba(255,255,255,0.045)` | canvas grid, 24px |
| `--pw-danger` | `#e34948` | destructive / alarm |

### 3.2 The four signal planes — `blocks.html:11–20, 820–829`

| Plane | GUI variable | Value | The word the GUI uses |
| --- | --- | --- | --- |
| Audio | `--audio` | `#3987e5` | "audio" |
| Control | `--ctl` | `#1baf7a` | **"notes"** |
| Modulation | `--mod` | `#9085e9` | "mod" |
| Binary | `--bin` | `#e6c34a` | "binary" |
| Binary (latch) | `--binlatch` | `#ee9338` | latch / logic / relay banners |

**Use the GUI's own words.** `SIG_NAME` (`blocks.html:828`) calls the control
plane **"notes"**, not "control" or "ctl". A handle the reader hovers says
`notes > cutoff`; the manual must say the same word or the legend does not
match the thing it labels. `ctl` remains the term for the *code-facing*
plane name — REFERENCE.md §4 uses it — so the manual says "the control plane
(wires labelled **notes**)" on first use and "notes" thereafter.

### 3.3 Module family colours — `blocks.html:795–800`

`voice #3987e5` · `input #1baf7a` · `filter #eda100` · `time #199e70` ·
`dirt #eb6834` · `dyn #e87ba4` · `vox #e34948` · `service #9085e9` ·
`io #c3c2b7` · `psine #22b8d4`

These appear **only in the module chapters**, as the accent on a module's own
entry — matching the stripe on that module's card. Nowhere else.

### 3.4 Subway line palettes — `blocks.html:813–818`

Each plane has a *set* of line colours, so several wires of the same kind
stay tellable apart. Diagrams showing more than one wire of a plane draw from
the same sets, in order:

```
audio  #3987e5 #22b8d4 #1f9e9e #6db1ff #4a5fd0 #7fd4ef
notes  #1baf7a #8cc63f #e8c832 #4fd08c #c9d94d #0f8f66
mod    #e34948 #f2842c #d446b8 #9085e9 #e87ba4 #ff6b5e #7a5fd0
bin    #e6c34a #eda45c #ef7d78 #ee5da0
```

---

## 4. Colour on paper

### 4.1 The paper

| Token | Value | Derivation |
| --- | --- | --- |
| `--paper` | `#f7f6f1` | `--pw-ink-1` lifted slightly |
| `--paper-2` | `#efeee7` | inset panels, code |
| `--ink` | `#1a1a19` | `--pw-surface`, verbatim |
| `--ink-mute` | `#6b6a63` | `--pw-ink-2` darkened for light-background contrast |
| `--rule` | `#dedcd2` | |

### 4.2 Where true GUI values are used

**Inside dark plates only** — figures, diagrams, legends, the geometry
specimens. There, the reader is looking at interface-coloured things on an
interface-coloured background, and the values must be exact.

### 4.3 Where adjusted values are used

The GUI palette is tuned against `#0d0d0d`. On `#f7f6f1`, `#e6c34a` as a
thin rule or small label is close to invisible. So body-text plane tokens use
**contrast-adjusted variants — hue and saturation preserved, lightness
lowered until the colour clears WCAG AA (4.5:1) as small text**:

| Plane | True (on plate) | Paper variant | Measured on `--paper` |
| --- | --- | --- | --- |
| Audio | `#3987e5` | `--audio-ink: #2a68b4` | 5.19:1 |
| Notes | `#1baf7a` | `--ctl-ink: #127653` | 5.19:1 |
| Mod | `#9085e9` | `--mod-ink: #5f52c0` | 5.62:1 |
| Binary | `#e6c34a` | `--bin-ink: #7e6511` | 5.17:1 |
| Latch | `#ee9338` | `--latch-ink: #9e550d` | 5.16:1 |
| Danger | `#e34948` | `--danger-ink: #b8302f` | 5.54:1 |

**These are computed, not eyeballed.** Each is the *lightest* colour of the
same hue and saturation that reaches ~5.2:1 — bisecting on HLS lightness so
the tokens stay as close to the interface colour as legibility permits. The
~5.2 target rather than a bare 4.5 leaves headroom for antialiasing and for
the paper's warm tint. If a GUI colour changes, re-derive rather than
adjusting by eye:

```python
import colorsys
def lin(c): c/=255; return c/12.92 if c<=.03928 else ((c+.055)/1.055)**2.4
def L(h):
    h=h.lstrip('#'); r,g,b=(int(h[i:i+2],16) for i in (0,2,4))
    return .2126*lin(r)+.7152*lin(g)+.0722*lin(b)
def ratio(a,b):
    la,lb=L(a),L(b); return (max(la,lb)+.05)/(min(la,lb)+.05)

def solve(src, paper="#f7f6f1", target=5.15):
    r,g,b = (int(src.lstrip('#')[i:i+2],16)/255 for i in (0,2,4))
    h,l,s = colorsys.rgb_to_hls(r,g,b)
    lo,hi = 0.0,l
    for _ in range(60):
        mid=(lo+hi)/2
        c="#%02x%02x%02x"%tuple(round(v*255) for v in colorsys.hls_to_rgb(h,mid,s))
        lo,hi = (mid,hi) if ratio(c,paper)>=target else (lo,mid)
    return "#%02x%02x%02x"%tuple(round(v*255) for v in colorsys.hls_to_rgb(h,lo,s))
```

The first draft of this table was picked by eye and `--latch-ink` came out at
4.11:1 — a real accessibility failure that looked perfectly fine on screen.
Hence the solver.

This split is deliberate and must be understood before anyone "fixes" it:
**a legend printed next to a screenshot sits on a plate and is exact; a plane
token in a sentence sits on paper and is adjusted.** Both are correct in
their place.

---

## 5. Typography

### 5.1 Faces

| Role | Stack |
| --- | --- |
| Body prose | `"Charter","Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif` |
| Anything naming the interface | `system-ui,-apple-system,"Segoe UI",sans-serif` — **identical to `blocks.html:26`** |
| Keys, commands, figure IDs | `ui-monospace,SFMono-Regular,Menlo,Consolas,monospace` |

A serif body is chosen for long-form reading and, more usefully, so that the
sans-serif stands out as *quotation from the screen*.

### 5.2 Scale

| Element | Size | Face | Notes |
| --- | --- | --- | --- |
| Chapter eyebrow | 0.72rem, 0.18em tracked, caps | mono | "Chapter 3" |
| `h1` chapter title | 2.7rem / 1.06 | serif 600 | |
| Standfirst | 1.02rem / 1.55 | ui | what the chapter is for |
| `h2` section | 1.6rem | serif 600 | top rule, number in the rail |
| `h3` subsection | 1.17rem | serif 650 | number inline |
| `h4` | 0.78rem caps, 0.09em | ui 700 | |
| Body | 17px / 1.65 | serif | measure 33rem ≈ 66ch |
| Caption | 0.81rem / 1.5 | ui | |
| Figure ID | 0.7rem caps, 0.09em | mono 650 | |
| Marginalia | 0.72rem / 1.5 | ui | |

Micro-labels reuse the GUI's own idiom — 9–10px, uppercase, 0.07–0.12em
tracking, secondary ink (`blocks.html:49–50, 78–82`).

### 5.3 Layout

A **band**: an 11rem marginal rail, a 2.25rem gap, then a 33rem text column,
centred. The arrangement echoes the interface itself — a narrow rail beside a
wide working area.

The rail carries figure references and § citations, so they stay out of the
prose. Section numbers in `h2` hang in the rail, aligning with the marginalia
beneath. **Open anchors do not live in the rail** — they live in
`continuity/manual-xref.md`; the rail may point at that file but never
enumerates.

Figures span rail + gap + column, so they sit wider than the text and give the
page rhythm. Below 950px the rail folds inline above its paragraph.

### 5.4 Inline conventions

| Convention | Set as | Example |
| --- | --- | --- |
| On-screen control or card | `.ui`, sans 600 | the Loop Deck card |
| Module key, command, endpoint | `code`, mono in a tinted chip | `pulse_pad`, `keyshift.2:3` |
| REFERENCE citation | `.xref`, muted sans | REFERENCE §4.6 |
| Scaffold marker | `.todo`, amber chip | **must not survive to print** — the print stylesheet hides it, and its presence in a proof is a bug |

---

## 6. Figures

### 6.1 Anatomy

```
┌─ plate ────────────────────────────┐   dark, #0d0d0d, 12px radius, 14px pad
│  ┌─ shot ───────────────────────┐  │   artwork; carries the GUI's 24px grid
│  │                              │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
FIG-03-01   Caption sentence.  ⟨plane token⟩
            ① legend  ② legend  ③ legend
```

The caption is a two-column grid: ID in mono on the left, sentence on the
right, numbered legend beneath the sentence in two columns.

### 6.2 Aspect classes

Six, and no others. A figure that does not fit one of these is re-planned,
not given a bespoke ratio — uniformity is what makes 108 figures look like
one set.

| Class | Ratio | For |
| --- | --- | --- |
| `--ar-page` | 16:10 | full-window screenshot (1440×900) |
| `--ar-wide` | 16:9 | wide diagram |
| `--ar-diagram` | 4:3 | standard diagram |
| `--ar-pair` | 2:1 | side-by-side comparison |
| `--ar-seq` | 3:1 | three-frame sequence |
| `--ar-card` | 3:2 | module card portrait |

**All 30 card portraits use `--ar-card`,** with the card centred at its
natural pixel size inside the plate. Cards differ in size class (XS/S/M/L);
the *plate* does not. This is what stops the module chapters looking ragged.

### 6.3 Captions

One sentence, sans, describing what the reader should see — not repeating the
body text. Figures that are about one plane carry that plane's token at the
end of the caption.

---

## 7. The four planes in diagrams

Triple-encoded. Colour is never load-bearing on its own.

| Plane | Colour | Stroke | Word |
| --- | --- | --- | --- |
| Audio | `#3987e5` | solid | `audio` |
| Notes | `#1baf7a` | dashed `10 3` | `notes` |
| Mod | `#9085e9` | dotted `2 3` | `mod` |
| Binary | `#e6c34a` | dash-dot `9 3 2 3` | `binary` |

**Stroke weight is uniform at 2px** — weight is deliberately *not* a fifth
variable, so it stays free for emphasis.

The legend (`FIG-04-02`, duplicated as `FIG-A-02`) shows all three encodings
together and is the reference for every other diagram.

---

## 8. Annotating screenshots

### 8.1 Callout markers

- 22px filled circle, `#f2f1ea` fill, `#0d0d0d` numeral, sans 700, with a
  2.5px dark halo so it survives over any part of the interface.
- **Achromatic by law.** A coloured callout would be read as a plane. This is
  the single most important annotation rule.
- Numbered in reading order — top-left to bottom-right — never in order of
  importance.

### 8.2 Leader lines

- 1.5px, `#f2f1ea` at 85%, **no arrowheads**.
- **Orthogonal only** — horizontal and vertical segments. Patchwerk's own
  wires are gutter-routed and orthogonal; annotation that runs at arbitrary
  angles fights the picture it sits on.

### 8.3 Region highlights

Both idioms are lifted from the interface so they already mean something to
the reader:

| Idiom | Style | Source |
| --- | --- | --- |
| Attention | 1.5px white outline, 6px radius, soft white halo | `.splice-target`, `blocks.html:110–111` |
| Destructive | 2px `--pw-danger` outline + glow | `.replace-hi`, `blocks.html:115–117` |

A scrim (`rgba(13,13,13,0.55)`) dims everything outside the highlighted
region when one region must dominate.

### 8.4 Composites

`FIG-03-01` and `FIG-15-05` are annotated composites: a flattened `.png` for
the manual plus a committed `.src.svg` holding the overlay, so annotation can
be revised at the next release without redrawing it.

---

## 9. True-scale geometry diagrams

Blocks-geometry figures (`FIG-03-04` and anything else about units, blocks
and gutters) are drawn **1:1 with the running interface** — one CSS pixel is
one interface pixel, at `U = 16px`, `BLK = 10`, `GUT = 2`
(`blocks.html:1264–1265`), with card footprints from `SIZE_PX`
(`blocks.html:1283–1286`):

| Class | Units | Pixels |
| --- | --- | --- |
| XS | 4.5 × 4.5 | 72 × 72 |
| S | 10 × 4.5 | 160 × 72 |
| M | 10 × 10 | 160 × 160 |
| L | 22 × 10 | 352 × 160 |

Card radius (9px), border, shadow and the 4px power stripe are copied from
`.mod` (`blocks.html:129–149`) so a specimen is indistinguishable from a real
card with the text removed. On screen at 100% zoom the reader can hold the
page against the interface and see they match.

---

## 10. Prose callouts

Achromatic rules of differing weight, with a mono uppercase label:

| Kind | Rule | For |
| --- | --- | --- |
| `.callout` | 2px grey | an aside |
| `.callout.try` | 5px grey | "Try this" — a concrete starting point |
| `.callout.warn` | 2px `--danger-ink` | a real trap |

**`.warn` is the one place a prose element carries hue**, and it is
defensible: `--danger` means the same thing in the manual as it does in the
interface. Every other callout stays grey so that the exception keeps its
force.

`.stub` — the dashed panel holding a scaffold directive — is scaffold
furniture and disappears as each section is written.

---

## 11. Print

The manual is expected to be printed. `@page` margins 18/16mm; body drops to
10.5pt; the rail narrows to 8rem; figures, tables and callouts are
`break-inside: avoid`; headings are `break-after: avoid`; plate shadows are
dropped; `.todo` markers are hidden.

The four-plane triple encoding exists largely for this: a greyscale print of
a four-plane diagram must remain readable.

---

## 12. Open decisions — need Cole

Everything above is settled and implemented. These are not.

| # | Decision | Why it needs you |
| --- | --- | --- |
| **D-1** | **The figure patch.** Whole-patch figures should all show the *same* patch. Recommend a purpose-built `patches/manual.py` designed to contain every wire kind at least once, so `FIG-04-01` and `FIG-15-05` work. Alternative: reuse an existing `patches/*.py`. | Touches ~a third of the figure set; needs your judgement on what a representative patch is |
| **D-2** | **Device-name redaction.** `FIG-02-02`, `FIG-02-03`, `FIG-16-02`, `FIG-16-03` show real hardware names. Show as-is, or genericise? | Privacy call, and it must be uniform |
| **D-3** | **The `SIG_PASTEL.mod` discrepancy.** `blocks.html:826` gives mod's handle pastel as `#f0a9a8` — a pastel of the *warm* mod line, not of `--mod` violet `#9085e9`. Every other pastel matches its base. Is this intentional? | It is a GUI question, not a manual one. The manual will document whatever is true, but if it is a bug it should be fixed in the GUI first, not documented |
| **D-4** | **Serif body face.** Charter is proposed and ships with macOS. If the manual is ever built by CI or read on Linux the fallback is Georgia, which is fine but different. Bundle a webfont? | Adds a dependency; your call whether that is worth it |
| **D-5** | **Build target.** Chapters are Markdown; this stylesheet targets HTML. Something must render one to the other. Proposal: a small `build.py` (also producing the single-file print edition already noted in `00-INDEX.md`). | Choosing the toolchain is a repo decision |

---

## 13. Files

| File | Role |
| --- | --- |
| `DESIGN.md` | this spec — the reasoning |
| `style/manual.css` | the implementation — **canonical** |
| `proof/chapter-03-proof.html` | rendered proof; inlines a snapshot of the CSS so it opens standalone |
| `img/README.md` | per-figure capture and authoring conventions, downstream of this spec |
| `img/` | **the single image folder — gitignored for rasters.** Every figure reference in the manual resolves here |
| `continuity/manual-xref.md` | the one place open anchors and open decisions are tracked (gitignored; outside the manual dir) |

**If the CSS and this document disagree, this document is the intent and the
CSS is the bug** — the reverse of the REFERENCE.md rule, because here the
prose was written first and deliberately.
