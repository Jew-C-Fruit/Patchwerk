# 3. The interface

This chapter names every thing on screen — regions, card parts, handles, wires,
the grid — so the rest of the manual can point at a part instead of describing
it again. Read it once; the module chapters assume it.

---

## 3.1 One page, two geometry modes

Patchwerk is **one page**, served at `/` and at `/blocks` — the same file under
two addresses. It has two **geometry modes**, swapped from the toggle in the
top bar:

- **blocks** — cards snap to a grid and wires route through the gutters between
  them. This is the mode the manual shows, and the mode cards are designed for.
- **flex** — free pixel positions, fixed card width, automatic height. First
  entry seeds positions from your blocks layout and settles any overlaps.

Mode, zoom and wire style are remembered per patch. Stay in blocks unless you
have a reason not to: a card that behaves in blocks always behaves in flex, and
not the other way round.

[FIG-03-01]

[FIG-03-05]

## 3.2 The top bar

Control by control, left to right — each icon below is the thing you will
actually click:

- [FIG-TB-MODE] switches the board between blocks and flex geometry.
- [FIG-TB-RESTART] snapshots the whole rack, re-execs the server and restores it.
- [FIG-TB-PLAY] starts and stops the transport. A fresh launch comes up stopped.
- [FIG-TB-BPM] sets the tempo; [FIG-TB-METER] the time signature; [FIG-TB-CLICK]
  the metronome.
- [FIG-TB-VOL] is the master fader, with the output meters beside it.
- [FIG-TB-KEYS] arms the computer keyboard and shows its current octave.
- [FIG-TB-TIDY] compacts the layout; [FIG-TB-LOCK] freezes panning.
- [FIG-TB-HELP] lists every key binding live, including your own trigger cards.

None of it is patched. Transport, master volume and panic are **global** — one
shared timeline, one output level, one panic that reaches every voice — and no
wire changes that (REFERENCE §2.4; the doctrine belongs to Ch 4).

Some of these also appear on cards, and it is one state shown twice: drive
`transport:run` from the binary plane and the top-bar play button moves with the
**Play/Stop** card, not against it (REFERENCE §8.1).

## 3.3 Anatomy of a card

[FIG-03-03]

Every module, node and monitor is a **card**, keyed by its instance id. The
parts, named once, here:

- **Colour bar** — the slim head, in the card's family colour. It is the power
  switch and the drag handle (§3.3.1).
- **Body** — everything below the bar.
- **Param rows** — one control per row: slider, chip, toggle or picker.
- **Input handles** and **output handles** — the small pills wires attach to.
- **Size chips** — in the header, on the cards that offer them (§3.3.3).

Only four cards carry a subtitle: the **Loop Deck** (transport state), the
**monitors** (local or global), **Threshold** (its source) and **LFO** (its
destinations). If a card has a subtitle, read it — it is the only place that
information appears.

### 3.3.1 The colour bar is the power switch

Outline = off. Filled = on, in the family colour. Press the bar and release
without moving and the card toggles; press and drag and the card moves. Same
bar, told apart by whether the pointer travelled.

There is **no round head LED**. Older screenshots show one, and readers hunt for
it. It is gone. The colour bar took the job.

[FIG-03-12]

### 3.3.2 Handles and what they tell you

Input handles wear a **pastel of their data colour**, so you can see what a
socket takes before you drag anything at it. Hover one and it declares itself as
`"data type > target name"` — the kind of data, then the target on this card
that it feeds. Naming both ends is the **wire label**'s job (§3.5.2).

One rule to carry away: **param and modulation inputs take exactly one wire.**
They are drawn lightly, they never grow a `+`, and dropping a new wire on an
occupied one replaces what was there without warning. Outputs branch freely, and
primary inputs combine — both of those grow a `+` handle once occupied, and
dragging from the `+` adds another handle beside it.

[FIG-03-08]

### 3.3.3 Card sizes

Sizes are **measured automatically**: a card takes the smallest class that fits
all of its content, so there are no scrollbars and no overflow anywhere. XS is
opt-in and used by the simple binary cards, most param cards land at S or M, and
a body that genuinely needs two columns takes L.

Measuring is wrong when the card's **graphic** is the point — the params win the
measurement and the picture takes whatever is left. So five cards override it:
`pulse_pad`, `fm_bell`, `pluck`, `wind` and `wobble_saw` default to **M** and
carry M and L size chips. At L the extra room goes to the graphic, never to more
param columns.

> **TRY THIS.** Spawn a **PW Pulse Pad**. At M — its default — the waveform
> preview is hidden outright and you get params only. Click the **L** chip and
> the preview appears at full height. If you came for the picture, you have to
> go to L.

Monitors and scopes resize from header chips the same way. A resize that will
not fit red-flashes and reverts.

### 3.3.4 Banner cards

The binary family — logic gates and their neighbours — are **banner cards**.
Instead of a family stripe they carry a coloured **head that repaints as the
card's mode changes** (a logic gate's op, for instance). There is no
outline-versus-filled bar to read on these: the head is both the identity and
the state readout.

## 3.4 The grid: units, blocks, gutters

This is the house vocabulary, and the manual uses it everywhere from here on. A
**unit** is one 16px grid square. A **block** is 10×10 units. Blocks are
separated by **2-unit gutters**. "Three units high" means three grid squares —
never three blocks.

The four size classes, in units and in pixels: **XS** 4.5×4.5u (72×72px), **S**
10×4.5u (160×72px), **M** 10×10u (160×160px), **L** 22×10u (352×160px). Two
stacked S cards plus the gutter between them equal one M, so an S dropped into
an empty block snaps to the **top half** and the bottom half is held for a
second S. An L is two blocks *plus the gutter between them*, which is why no
wire routes under an L.

REFERENCE §2.6 owns this nomenclature.

[FIG-03-04]

## 3.5 Wires

### 3.5.1 Drawing one

Grab an **output handle**, drag, and land on an **input handle**. While you drag,
every compatible handle on the board highlights and an animated **ghost wire**
routes itself to the nearest one; release and it becomes a real wire. Release
over nothing and the drag is abandoned.

[FIG-03-06]

### 3.5.2 Reading one

Wires run **only in the open gutters**, along their centrelines, by the shortest
path — which is why a patch reads like a subway map rather than a plate of
spaghetti. A wire's **colour is its data kind**: audio, the control plane (wires
labelled **notes**), binary, modulation. Ch 4 carries the legend.

Every wire has a mid-wire **tag**, collapsed to a small dot in the wire's shade.
Hover the wire or the dot and it expands into a pill reading `Src → Dst`, and
the whole wire lifts to the front with a thin white halo and rings at both ends.
Wires leaving the same edge travel as a **bundle** in parallel lanes, ordered by
where their far ends sit, so a fan reads as nested rather than tangled.

[FIG-03-07]

### 3.5.3 Removing one, and what heals

Double-click a wire's body or its label to delete it. Or drag either end handle:
onto another handle to repatch, into empty space to cut.

Removing a **wire** leaves a hole, as you would expect. Removing a **module**
does not: Patchwerk **splice-heals** the chain, re-aiming every feeder of the
removed module at that module's own destination, and scrubbing its notes wires,
its `:pwr` wires and any LFO destinations pointing at it on the way out. §5.6
owns the full behaviour (REFERENCE §3.2).

The reverse gesture exists too: drag a wire's tag onto a card with a compatible
input and output and that module is **spliced into** the wire.

## 3.6 Adding and removing modules

[FIG-03-09]

Modules come from the **palette**, grouped by family — sources first, then the
effect families. Drag one onto the board; the drop uses the same ghost, shove and
live preview as moving a card does (§3.7), and an invalid drop cancels the spawn
rather than dumping the card somewhere arbitrary.

You can have as many instances of a module as you like. The first is `lowpass`,
the second `lowpass.2`, and so on. Cards are keyed by that **instance id**, never
by type — and the id is not cosmetic: every endpoint you will ever wire is built
on it (`lowpass.2:pwr`, `keyshift.2:3`). Ch 4 is where that grammar is taught.

> **WARN.** The palette has a section headed `allocation` and a section headed
> `voices`, and they are not the same thing. **Mono Voice**, **Poly Voice** and
> **Drone Voice** live under `allocation` — they spawn note-routing nodes on the
> control plane. `voices` holds the playable *source modules* that actually make
> sound. Reaching for the wrong section is the most common palette mistake
> (REFERENCE §13.1).

## 3.7 Moving things: placement and tidy

Drag a card by its colour bar. Drop it on an occupied block and the tenant is
**shoved** by quadrant — hover the top half and it shoves down, the left and it
shoves right — and shoves chain, springboard style. A chain that would run off
the board shows a **red ghost** and reverts on release.

A click-drag starting in empty space draws a lasso; grab a selected card's head
afterwards and the whole group moves together. **Tidy** pours the patch into the
**visible frame**, pulling in stragglers rather than marching cards off the
edge.

Card positions are **client-side only**. They are stored per patch in your
browser and never sent to the server. Open the same patch in a different
browser, or on a different machine, and you get the same instrument with a
different layout. The patch is the wiring, not the arrangement.

[FIG-03-10]

## 3.8 Monitors and the scope

Three cards show you what is happening rather than change it: the **note
monitor**, the **waveform monitor**, and the **oscilloscope**. All three follow
one doctrine.

**Wired, they are local.** Ride a monitor on a wire and it shows that path's
traffic and nothing else. **Unwired, they are global** — the master feed, or
every tap on the control plane at once. The card's subtitle tells you which mode
it is in. An unwired monitor showing everything at once is not broken; if you
want one path, wire the monitor into it.

The oscilloscope draws whatever module it is watching; splice a **Scope Tap**
anywhere in a chain to probe that point without changing the sound (REFERENCE
§6.3).

[FIG-03-11]

## 3.9 Indicators react to logic, not just to clicks

Every indicator on the page reacts to **logic-driven** state exactly as it reacts
to a click. If something in the binary plane turns a module off, that module's
colour bar empties on its own — nobody has to touch it. REFERENCE §5.3 is the
full list of what the binary plane can drive.

> **TRY THIS.** Wire any binary source to a chain module's power input and toggle
> the source. The target card's colour bar fills and empties with no pointer
> anywhere near it. Do the same into `transport:run` and watch the top-bar play
> button move too.

Edges faster than one video frame are stretched to a visible minimum, so a clock
tick through a logic gate still flashes rather than vanishing between frames.

## 3.10 Panic

**Panic is in the top bar.** Press it and every open note everywhere closes at
once — the keys, the arp, every voice, every deriver, every key shifter,
whatever the wiring says (REFERENCE §4.8). It is global, so no patching affects
it. It does not stop the transport and it does not change your patch.

Reach for it whenever a note will not stop. §4.5 explains how one gets stuck.
