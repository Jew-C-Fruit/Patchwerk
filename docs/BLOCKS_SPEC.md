# Patchwerk "Blocks" UI — canonical design spec

> **Status.** Originally compiled from the design session of 2026-07-17/18
> as a checklist for porting a prototype into the main GUI. **The port is
> long done: `gui/blocks.html` IS the UI** — the only page served, at `/`
> and `/blocks`. The prototype file it used to cite no longer exists, and
> the older pages (`flex.html`, `index.html`, the graph view) are archived
> under `gui/legacy/`, unserved and unmaintained.
>
> This file is now the standing DESIGN CONTRACT for the page: what the
> geometry, routing, ports and chrome are supposed to do. It is not a
> changelog — per-release behaviour lives in the release notes, and
> `docs/REFERENCE.md` is the code-verified system reference.
> **Verified against `main` @ `71bd089`, 2026-07-25.**

## 0. Two geometry modes, one page

The page has ONE card/wire pipeline and **two geometry modes**, swapped by
the toggle in the top bar (it replaced the "blocks" title):

- **blocks** — the snapping grid described by §1–§3.
- **flex** — free pixel positions, fixed card width, AUTO height
  (`data-size="F"`, so the S/M/L CSS simply does not match). First entry
  seeds positions from the current blocks layout and settles overlaps.

Mode, zoom and wire style persist per patch alongside positions in
localStorage; the server never sees card geometry.

**Cards are designed BLOCKS-FIRST (Cole, standing).** A card that conforms
to blocks — fits its size class, measured, no overflow — renders correctly
in flex, where height is automatic. The reverse is NOT true. Never design a
card flex-first.

Mode-specific behaviour is dispatched through `place()` / `nodeUnitRect()`
on `uiMode`, never as parallel per-mode card code; blocks-only machinery
(shove planner, tidy, S/M/L sizing) is guarded the same way. Blocks paths
must behave identically when `uiMode === "blocks"` — `check_blocks`
sections 1–10 are the regression net.

## 1. Grid & footprints
- Unit grid; parameterized constants: `BLK=10`, `GUT=2`, `PITCH=12` (units), `U=16px/unit`,
  `SH=4.5`, `MIDG=1`.
- Footprints: **XS = 4.5x4.5**, **S = 10x4.5**, **M = 10x10**, **L = 22x10** units.
  Identities: 2 stacked S + the narrow mid gutter (4.5+1+4.5) = M; 2 M side-by-side
  (10+2+10=22) = L (L spans two blocks **plus the gutter between**).
  (History: S was 10x4 with a 2u mid gutter; expanded 2026-07-18 so 3-param
  cards fit without crushing — the mid-block gutter narrowed to 1u to pay for it.)
- **XS is a block QUADRANT** — 2 per half-block, 4 per block, 1u internal
  gutters; occupancy is tracked in quadrant cells and persistence appends a
  5th element to the layout tuple for XS only. It is **opt-in per card**
  (`cfg.allowXS`) and used by the simple binary cards.
- Placement grid: blocks of 10x10 with 2-unit gutters; fixed 2u spacing between all
  modules except the 1u mid-block gap between two stacked smalls.
- An S placed in an empty block snaps to the **top half**; the narrow mid-block gutter
  appears and the **bottom half is reserved** for a second S (ghost shows dashed band).
- An L occupies two horizontally adjacent blocks + the vertical gutter between (blocked for wires).
- **There is NO top margin above the topmost card slots.** One was added
  (`TOPM`, item 20) and REVERTED in PR #40 because it desynchronised the
  card grid from the wire router. Reintroducing it means re-deriving the
  router geometry with it, not threading a constant through.

## 2. Placement / shove interaction
- Drag-hover over an occupied block shoves its tenant by **quadrant**: hover top half →
  shove down, left → shove right, bottom → up, right → left (triangle quadrants).
- Shoves chain iOS-springboard style with **live preview** (modules animate to their
  would-be spots); a chain that would run off the board shows a **red ghost** and reverts.
- M/L displace an S-pair **together**; **only an S can displace an individual S**
  (S-vs-S vertical shoves move in half-block steps).
- Palette drag-in uses the same ghost/shove/preview; invalid drop cancels the spawn.
- **L quantisation**: an L is two blocks wide, so quantise its OWN left edge
  (the one tracking the pointer) to the nearest column. Anything of the form
  `fx < 0.5 ? bx : bx - 1` is NOT monotonic and makes a steady drag jump two
  columns and skip one.
- **Group select + move (both modes)**: a click-drag starting in a DEAD ZONE
  draws a square lasso; every card it touches is selected; grabbing a selected
  card's head then drags the whole group; clicking anything else deselects.
  Selection survives rebuilds by gid. In blocks the group moves by a
  whole-block delta and drops only where the ENTIRE group fits (no shove);
  in flex it moves freely.
- **Tidy pours into the VISIBLE FRAME** (PR #41) — cards fill the current
  viewport rather than marching off the edge, and loners are pulled in with
  the rest.

## 3. Sizes
- Every module maps to XS, S, M, or L. The default is **measured**: the
  smallest size that fits ALL content with comfortable slack — **no
  scrollbars and no overflow, ever**; a card that can't fit its params sizes
  up. Card top bars are slim to maximize body room.
- **DECLARE the size when a graphic is at stake (Cole, 2026-07-24).**
  Measuring is right for a card that is only params. It is wrong for a card
  whose graphic is the point: the params win the measurement, the card lands
  at L, and the graphic takes the leftover — PW Pulse Pad's waveform
  measured fourteen pixels. Those cards set `n.defaultSize` and
  `addSizeChips(n, [...])`, define an explicit face per size in CSS, and set
  `n.ownFaces` to switch off the generic 2-column fallback. At the larger
  size the extra room goes to the GRAPHIC, never to more param columns.
- Where a body genuinely needs two columns, split it with `splitColumns(n)`:
  the LEFT column keeps every row that owns a handle, the RIGHT column is
  100% handle-free params, so the 1:1 row↔handle line is structural rather
  than lucky. L bodies otherwise default to ONE column.
- **Visual-feedback modules (monitors/scopes) are size-adjustable** via header
  chips; resizing goes through the same shove planner (red-flash if it can't fit).
- A collapsed expandable may keep a **mini viz strip** instead of hiding its
  view (the Loop Deck does; the strip stretches into the body's slack and
  must never CREATE slack — the measuring pin stays small so the size class
  is unchanged).

## 4. Wire routing
- Wires run **only in open gutters**, along centerlines (with a 2u gutter these fall on
  grid lines; router lattice is half-unit). Shortest path (Dijkstra + turn penalty).
- Mid-block gutters are routable only where that block is split by an S; the gutter
  under an L is blocked. No-route falls back to a dashed straight line.
- **Mid-gutter bias**: the 1u mid-block gutters are narrow — the router charges 3x
  per step on them, so wires avoid routing through centerline gutters unless
  explicitly necessary (their own handle stubs, or no reasonable alternative).
- **Bundles**: wires sharing a gutter run in visible parallel lanes (LANE_W=4px).
  - Bundle = wires leaving the same source edge; treat as a bundle until they diverge.
    **This is the primary criterion for parallel placement priority.**
  - Within a bundle, lane order follows source-handle order so the bundle never
    crosses itself; wires converging on one destination edge order by dest handle.
  - The whole bundle is **centered on the gutter centerline** (the grid line):
    center the local overlap group; a solitary wire runs dead-center.
  - **Concentric corners**: where parallel wires turn together, radii nest —
    outer lane wider radius, inner tighter (r = base + outward-projection of lane
    shift), so turns wrap around each other at a constant gap.
- **Crossing minimization**: one crossing between two wires is fine; a pair that
  crosses twice when it could cross zero times is not. After lane assignment, a
  repair pass swaps lane assignments where that reduces total crossings.
- **Fan order IS the nesting rule (PR #44).** For a long time this was only
  EMERGENT: `wiresAt()` returned a fan sorted by `w.seq` — the order the user
  happened to draw the wires, which has nothing to do with geometry — and it
  read as nested only because the cards happened to sit where seq-order agreed.
  #42 moved three cards far enough that it stopped holding, and
  `reduceCrossings` could not repair it: its only lever is swapping LANE
  OFFSETS between segments sharing a gutter, and it cannot reassign which wire
  gets which handle. So the rule is now encoded — **order each fan by the FAR
  end's position along the edge's axis** (vertical edge → the other node's
  centre Y, horizontal → its centre X, `seq` as a stable tiebreak). Two wires
  whose far ends sit in the same relative order as their near ends cannot cross
  each other twice. Applied where the edge is final and BEFORE the dual-edge
  spill split, so spill and handle placement both see the nested order. NOT
  applied to mod/quiet fans (row-locked — alignment never bends) or to fixFrac
  ports (relay circuits pair in k above out k by spec).
  **Known limitation, deliberately unfixed:** a pair can still cross twice when
  the two crossings are in DIFFERENT gutters and one is on a handle STUB — the
  lane pass never offsets first/last segments (that visibly detaches a wire
  from its handle) and the crossings share no `lines` key, so no swap or
  single-lane nudge reaches it. Guaranteeing "max 1" in that geometry needs a
  crossing-aware router. Cole's call before anyone attempts it.
- **Overlap backstop**: the lane pass only lanes INTERIOR segments, so a SHORT
  route (first + last segment only) never gets an offset and two of them in one
  gutter render identically. `separateOverlaps()` runs after lanes AND crossing
  repair and pushes genuinely collinear overlapping segments apart by one lane;
  it is a no-op when the lane pass already did its job.
- **Draw layering**: straight runs in a bottom layer; all corner curves + the first/
  last (I/O) spans in a top layer, so curves visibly ride across straight bundles.
- Flex reuses this whole pipeline through its own A* over an obstacle grid, and
  offers a ⌇/∿ toggle that swaps the orthogonal route for cubic beziers.
- **Settle pass**: `rebuildGraph` re-runs `rerouteAll` ~250 ms after building,
  because the rendered layout can differ from the freshly computed one. Checks
  assert positions POST-SETTLE. It also paints the vizzes once synchronously
  before returning — that is the card-flicker fix, not an optimisation.

## 5. Ports & handles
- **Primary I/O** (the through-signal kinds: audio, notes, binary) is **moveable**:
  each primary port auto-picks the edge facing the average direction of its wired
  neighbors — horizontal flow → L/R **hugging the upper corners**; vertical flow →
  snap to Top (inputs) / Bottom (outputs). Auto-repositions as wiring changes.
  (The `tonic` wire kind named in the original spec was RETIRED in v2.1; `tonic`
  survives only as the estimator deriver's node-id prefix.)
- **Modifier I/O (param controls, kind=mod)** stays pinned to **L (in) / R (out)**,
  placed in the parameter band, **in line with the parameters they modify**.
- **QUIET param handles stay on their row — alignment never bends.** The flex
  dead-zone nudge (below) exempts them, and `QUIET_SEP` fires only on a genuine
  SAME-ROW collision. Never reintroduce a proximity-based separation: on a packed
  card rows sit ~0.69u apart and it silently walks every handle off its row.
  Where a handle truly cannot sit on its row, it grows a faint **on-card link
  wire** out of the handle, along the card's left inset, into the row — dashed
  and dim unwired, solid and lit in the wire's colour when wired. A mod-driven
  param NAME wears its wire's colour and reverts on cut.
- **Primary and modifier groups must never overlap on a shared side**: guaranteed
  gap between the groups; if the two groups can't fit as separated bands within the
  edge (most S modules), the **primaries relocate to Top/Bottom** and mods keep the side.
- **Dual-edge overflow**: `dualEdge` is an opt-in input flag — once the primary
  edge is full (usable length / FAN_PITCH) the remaining slots spill onto the
  other in-edge (T ↔ L). OVERFLOW-ONLY by design, so a light patch renders
  exactly as before. Master Out is the only card that sets it today.
- **Grouped handles** (same port fanned out) pack "a hair" apart (~12.5px pitch for
  11px handles); wire stubs stay square via exact cross-coordinate snapping of the
  first/last routed points (no lattice-rounding kinks/tilts).
- Every wire has **its own handle at both ends**.
- **Fan-out (+)**: a port that starts with a single handle grows a small **+ handle**
  next to it once occupied; dragging from + adds another occupied handle beside it.
- **Single-source inputs.** Param/mod **inputs** take exactly one wire: no + handle,
  handle drawn lightly, and dropping on an occupied param input **replaces** the
  existing connection. Outputs (all kinds, incl. mod outs like LFO) may branch → +
  allowed. Primary inputs may combine (fan-in) → + allowed — but logic named ins,
  `relay:ctl` and **every relay circuit contact** are single-input and STEAL instead
  of growing a +. `portAllowsPlus` must test `single` FIRST, before it looks at
  direction.
- **In flex only**, primary wire handles are pulled toward the edge midpoint and
  nudged into the nearest DEAD ZONE, so a handle never lands on a slider, chip,
  kill button or another handle. Quiet param handles are exempt (above).
- Drag an end handle to **repatch** (breaks that contact, establishes new one);
  release in empty space = cut. **Double-click** a wire body or its label = delete.
- During any wire drag: compatible handles **highlight**; ONE routed **ghost wire**
  (dashed, animated) forms to the nearest compatible handle; releasing solidifies it.

## 6. Wire labels (mid-handles)
- Every wire carries a mid-wire **tag**: collapsed = small dot in the wire's shade.
- Hovering the wire OR the tag **expands** it into the flex-style pill (wire-color
  background, dark 8.5px 600 text, "Src → Dst"), contracts on leave.
- Labels **orient with their carrying line** and read along the flow: horizontal
  right-flow "Src → Dst", left-flow flips to "Dst ← Src"; vertical rotates ±90.
- Placement avoids **wire corners, wire-wire crossings** (gutter intersections and
  span midpoints), and other tags — nudged forward/back along the run to make space.
- **Short wires** whose pill would cover their own I/O handles switch to **balloon**
  mode: dot stays on the wire; pill pops out perpendicular on a stem (flips inward
  near board edges).
- Dragging the tag onto a module with compatible in+out **splices** that module in.
- Hover also brings the whole wire to the **front with a thin white outline**
  (~0.7px halo) and rings both of its end handles.
- Handles hover-declare `"data type > target name"`; handles never name their own
  card, wire LABELS do. ONE `portTarget()` helper feeds both so they cannot drift
  apart — that shared helper is what fixed labels reading `<Card>.undefined`.
- **Relay circuits read as a whole hop.** A circuit allocates one line colour from
  its family on first sight and BOTH halves draw in it (light green in, light green
  out); the label names the whole path — "Keys → Relay → Voice",
  "LFO → Relay → Echo.mix" — with an unwired end simply left off.

## 7. Card chrome
- The card's **COLOUR BAR is the power indicator** — outline = off, filled = on, in
  the category colour. There is no round head LED; `bindNode` distinguishes a
  stationary press (toggle) from a drag (move).
- **Banner cards** (the binary family) carry a coloured head that repaints with mode
  and therefore have no family stripe to fill.
- Input handles wear a **pastel of their data colour**.
- **Subtitles are gone** from every card except the four carrying live state
  duplicated nowhere: Loop Deck (transport state), the monitors (local/global
  scope), Threshold (its source), LFO (its destinations).
- **REACTIVE-INDICATOR DOCTRINE (Cole, standing).** Every indicator must react to
  LOGIC input, not only to clicks. State applied inside the gate settle pass does
  NOT broadcast, so the backend emits its own `{"kind":"level","ep","on"}` tap and
  the GUI routes it. Any new indicator is wired the same way — and a headless mock
  can only prove it reacts to a message we invented, so verify on the rig.
- **Pulse stretcher**: an edge faster than a frame is still displayed for a visible
  minimum (~140 ms), and a ping tap flashes the fed in-pin. Without it a clock tick
  through a logic gate goes hi→lo within one frame and nothing ever lights.
- A canvas with **zero `clientHeight` must NOT draw** — it skips the height sync and
  then paints against stale geometry. No box, no paint.

## 8. Viewport
- **Scroll/two-finger = PAN.** Zoom comes only from a trackpad pinch (ctrl+wheel,
  with the browser's page-zoom suppressed) or the +/- controls, and every zoom is
  SMOOTH (eased, exact landing); a pinch retargets a running animation.
- Blocks zooms only while UNLOCKED. **LOCKING snaps to the closest grid size AND
  gutter-aligns the viewport on all sides**, so only whole blocks are visible.
- The +/- controls pull from the view's 0,0 upper corner and never unlock.
- Zoom math derives scale from `world.style.zoom` and board scroll only — never
  from rect ratios (Chrome 128+ CSS-zoom geometry; the container's Chromium is
  pre-128 and CANNOT catch violations, so this one is on review, not on CI).
- The header is `flex-wrap: wrap` and changes height when its CONTENT does, not
  only on window resize — a **ResizeObserver on the header** is what keeps it from
  occluding the top gutter.
- Scrollbars are Apple-Music style: no track chrome, thin translucent thumb.

## 9. Efficiency requirements
- No full reroute on meter/viz frames — routing recomputes only on topology or
  layout changes (module move/resize/spawn/remove, wire add/remove/repatch).
- Card DOM is persistent (params/viz update in place); layout changes move cards,
  never rebuild them (canvases and slider state must survive).
- Route context (blocked lattice) built once per relayout, not per wire.
- Drag previews throttled (recompute on hover-cell change, not per pointermove).

## 10. Validation gate
- `gui/blocks.html` is edited **directly** — the old splicer pipeline is retired.
- The gates are `tests/check_blocks.py` (Playwright against mock state) and
  `tests/check_real.py` (replays a captured real-rig state fixture). Both must be
  green, plus `tests/gui_check8.py` in CI. Both print their own check TOTAL —
  quote that line rather than eyeballing a number.
- `check_real`'s fixture is INTENTIONALLY old-format — card builders must keep
  tolerating older servers. Do not "modernize" it.
- **Assert the COMPUTED property, never a substring of it.** A translucency check
  for `"rgba"` passes on plenty of opaque colours, and Chromium computes
  `color-mix()` to `color(srgb … / 0.88)`. Parse the value and compare numerically.
- Anything touching the reactive-indicator doctrine, real zoom geometry, or audio
  needs a pass on the rig (`tests/probe_live_gui.py`) — headless cannot prove it.
