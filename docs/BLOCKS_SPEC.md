# Patchwerk "Blocks" UI — canonical spec (compiled from design session 2026-07-17/18)

The prototype (`patchwerk_blocks_proto.html`, v12) is the reference implementation.
This doc is the feature checklist for porting it into the main GUI.

## 1. Grid & footprints
- Unit grid; parameterized constants: `BLK=10`, `GUT=2`, `PITCH=12` (units), `U=16px/unit`,
  `SH=4.5`, `MIDG=1`.
- Footprints: **S = 10x4.5**, **M = 10x10**, **L = 22x10** units.
  Identities: 2 stacked S + the narrow mid gutter (4.5+1+4.5) = M; 2 M side-by-side
  (10+2+10=22) = L (L spans two blocks **plus the gutter between**).
  (History: S was 10x4 with a 2u mid gutter; expanded 2026-07-18 so 3-param
  cards fit without crushing — the mid-block gutter narrowed to 1u to pay for it.)
- Placement grid: blocks of 10x10 with 2-unit gutters; fixed 2u spacing between all
  modules except the 1u mid-block gap between two stacked smalls.
- An S placed in an empty block snaps to the **top half**; the narrow mid-block gutter
  appears and the **bottom half is reserved** for a second S (ghost shows dashed band).
- An L occupies two horizontally adjacent blocks + the vertical gutter between (blocked for wires).

## 2. Placement / shove interaction
- Drag-hover over an occupied block shoves its tenant by **quadrant**: hover top half →
  shove down, left → shove right, bottom → up, right → left (triangle quadrants).
- Shoves chain iOS-springboard style with **live preview** (modules animate to their
  would-be spots); a chain that would run off the board shows a **red ghost** and reverts.
- M/L displace an S-pair **together**; **only an S can displace an individual S**
  (S-vs-S vertical shoves move in half-block steps).
- Palette drag-in uses the same ghost/shove/preview; invalid drop cancels the spawn.

## 3. Sizes
- Every module maps to S, M, or L: the **smallest size that fits ALL content with
  comfortable slack** — **no scrollbars and no overflow, ever**; a card that can't
  fit its params sizes up (L bodies flow params into two columns). Card top bars
  are slim to maximize body room.
- **Visual-feedback modules (monitors/scopes) are size-adjustable S/M/L** via header
  chips; resizing goes through the same shove planner (red-flash if it can't fit).

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
- **Draw layering**: straight runs in a bottom layer; all corner curves + the first/
  last (I/O) spans in a top layer, so curves visibly ride across straight bundles.

## 5. Ports & handles
- **Primary I/O** (the through-signal kinds: audio, notes/ctl, tonic) is **moveable**:
  each primary port auto-picks the edge facing the average direction of its wired
  neighbors — horizontal flow → L/R **hugging the upper corners**; vertical flow →
  snap to Top (inputs) / Bottom (outputs). Auto-repositions as wiring changes.
- **Modifier I/O (param controls, kind=mod)** stays pinned to **L (in) / R (out)**,
  placed in the parameter band, **in line with the parameters they modify**.
- **Primary and modifier groups must never overlap on a shared side**: guaranteed
  gap between the groups; if the two groups can't fit as separated bands within the
  edge (most S modules), the **primaries relocate to Top/Bottom** and mods keep the side.
- **Grouped handles** (same port fanned out) pack "a hair" apart (~12.5px pitch for
  11px handles); wire stubs stay square via exact cross-coordinate snapping of the
  first/last routed points (no lattice-rounding kinks/tilts).
- Every wire has **its own handle at both ends**.
- **Fan-out (+)**: a port that starts with a single handle grows a small **+ handle**
  next to it once occupied; dragging from + adds another occupied handle beside it.
- **NEW RULE (this port): parameter-control inputs are strictly single-source.**
  - Param/mod **inputs**: exactly one wire; **no + handle**; handle drawn **lightly**
    (smaller/dimmer, like flex's `.port.subtle`). Dropping a wire on an occupied
    param input **replaces** the existing connection (single source preserved).
  - Outputs (all kinds, incl. mod outs like LFO) may branch → + allowed.
  - Primary inputs may combine (fan-in) → + allowed.
  - (No known exceptions; if one appears it gets whitelisted explicitly.)
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

## 7. Carry-over requirements (real UI port)
- **All extant modules** must exist and work: keys, arp, key shifter, tonic deriver,
  LFO, mono voice(s), drums, all effects, drone, looper/deck, scope/wave/note
  monitors, main out — with their real params, viz, and ws control plumbing.
- **All existing top-bar controls** carry over unchanged (transport/BPM/beat, meters,
  MIDI indicator, presets, panic, key/octave, click, etc. — whatever the current
  header has).
- Wire kinds/colors: audio (cools), ctl/notes (green→yellow), mod (warm/violet),
  tonic (amber) — the existing subway LINES shade tables.
- Existing ws protocol untouched; server-side unchanged.

## 8. Efficiency requirements
- No full reroute on meter/viz frames — routing recomputes only on topology or
  layout changes (module move/resize/spawn/remove, wire add/remove/repatch).
- Card DOM is persistent (params/viz update in place); layout changes move cards,
  never rebuild them (canvases and slider state must survive).
- Route context (blocked lattice) built once per relayout, not per wire.
- Drag previews throttled (recompute on hover-cell change, not per pointermove).

## 9. Validation gate
- Cole validates manually before any git push. Deliverable lands in the working
  tree only (new file alongside flex.html; swap is a one-line copy after sign-off).
