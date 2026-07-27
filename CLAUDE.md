# synthbase — house rules for vibecoding

This repo is a synth base on the SuperCollider server (scsynth), controlled
entirely from Python via **supriya**. The audio engine is a separate process:
broken Python can never glitch running audio.

Patchwerk's engine package is called `synthbase` — a name from before the
project's own rename to Patchwerk. It stuck; renaming a working import path
across every module for cosmetic reasons isn't worth the risk (see Don'ts).
This is the canonical AI-guidance file; `AGENTS.md` at the repo root just
points here so tools that look for that name find it too.

## Architecture in one breath

`modules/*.py` (DSP recipes) → loaded by `synthbase/module.py` → instantiated
by `synthbase/rack.py` as a rewireable AUDIO GRAPH on scsynth (stereo buses
between stages) → note flow defined by a wire-based CONTROL PLANE in
`synthbase/app.py` → performed from the flex GUI (`synthbase/server.py`,
browser at `/`) and MIDI (`synthbase/midi.py`) → hot-swapped on file save by
`synthbase/watcher.py`.

## The graph world (v5+): ids, audio wires, control wires

**Instance ids.** Every module is spawnable MULTIPLE times. An instance id
is `"lowpass"`, `"lowpass.2"`, ... (`alloc_id` reuses freed suffixes); the
TYPE is `type_of(id)` = the part before the dot = the registry/synthdef key.
ALL protocol messages are keyed by instance id; a bare type key resolves to
the FIRST instance (legacy clients). Never treat an id as a type — derive it.

**Audio graph.** `app.graph_wires` overlays the linear chain: one outgoing
wire per source (rewiring = point its `out` at the destination's in-bus),
fan-in is free (buses SUM — extra sources sum into the running bus, never a
fresh orphaned one), disconnected outputs park on a persistent silent null
bus, and `reorder_for_wires` topo-sorts nodes so every wire's src executes
before its dst. Wires survive rebuilds; removal splice-heals A→X→B to A→B.

**Control plane.** `app.ctl_wires` is the note router — the graph IS the
routing. Node vocabulary: `keys` (all controllers enter here; never a
destination), `arp`, `deck` (the MIDI looper: keys→deck records raw,
arp→deck records voiced, deck→X replays), mono voices (`voice`,
`voice.2`, ...; each drives one target source), POLY voices (`poly`,
`poly.2`, ...; N notes at once on ONE target, oldest stolen when full —
on `main` since 2026-07-26, live-verified on the rig; see the satellite
landmine below), DRONE voices (`hold`, `hold.2`, ... —
note the id type is `hold`, NOT `drone`; holds the last root, with a binary
POWER level-in at `"<id>:pwr"` — on `main` since 2026-07-26), tonic derivers (`tonic.N`:
notes in → ctl THRU out + amber TONIC out; tonic outs land only on drone
instances' tonic-ins), and key shifters (`keyshift.N` with four isolated
LANES — endpoint grammar `"keyshift.2:3"` = lane 3; lane k in → shift →
lane k out only). Unwired events dead-end silently — honest patching.
EVERY silencing path must emit its note-offs (taps included): an unpaired
on is a stuck note and a stuck monitor bar.

**Global-vs-wired doctrine.** Transport/clock, panic + sustain, master
volume + IO config, pitch reference (transpose/bend), and persistence stay
GLOBAL. Everything else — who hears whom — is wire-defined.

⚠ **Global does not mean uniform: transpose and bend part company on the
drone.** Item 29 (shipped 2026-07-26) makes the drone
follow global TRANSPOSE — it previously sat exactly `transpose` semitones
away from the rest of the patch, which was a bug — while still IGNORING
bend, on purpose. Transpose is a standing key change, so it redefines the
reference too; bend is a momentary gesture on what you're PLAYING, and a
drone is what you play against, so bending it with the melody would leave
every interval unchanged and silently cancel the wheel. The two look
inconsistent side by side and are not. Don't "fix" the bend half.

## GUIs

**`gui/blocks.html` IS the UI.** It is the only page served — at `/` and
at `/blocks` (an alias kept for bookmarks). Cards + gutter-routed subway
wires derived from every `state` message; positions persisted per patch
in localStorage, so the server never sees card geometry. There is ONE
page and TWO GEOMETRY MODES inside it: **blocks** (the snapping grid) and
**flex** (free px positions, auto height), swapped by the toggle in the
top bar. New geometry behavior goes through the `uiMode` dispatch in
`place()` / `nodeUnitRect()` — never parallel per-mode card code — and
the blocks paths must behave identically when `uiMode === "blocks"`
(`check_blocks` sections 1–10 are the regression net).

The older pages (`flex.html`, `index.html`, the graph view) are ARCHIVED
under `gui/legacy/`, **unserved and unmaintained**; there is no
`/legacy` route. Do not "check both pages" — the archived flex page
predates the rework and speaks a dead protocol. Note `tests/gui_check8.py`
still loads `gui/legacy/{flex,index}.html` by path: if those files move
again, CI breaks there first.

The full websocket protocol is documented in `synthbase/server.py`'s
docstring, and `docs/REFERENCE.md` is the code-verified reference for
every module, signal plane and IO route.

**Monitors: local vs global.** Note/Waveform monitors and the scope are
LOCAL when wired/riding a wire (they show that path's traffic) and GLOBAL
when unwired (master feed / all taps). Source-fires emit ONE tagged tap
(`{"kind": "tap", "src": <node id>}`) per fire, not per edge.

**Blocks-geometry nomenclature (Cole, 2026-07-22 — use these words in
specs and asks).** A **unit** (= grid square) is the fine 16px grid cell
(`U = 16` in blocks.html). A **block** is the 10u×10u snappable area
(`BLK = 10`), separated by 2u gutters. Card sizes in those terms:
XS = 4.5×4.5u (a block quadrant, opt-in via `cfg.allowXS`),
S = 10×4.5u (half a block), M = 10×10u (one block), L = 22×10u (two
blocks spanning their gutter). So "3 units high" means 3 grid squares
(48px) — never 3 blocks.

**Cards are designed BLOCKS-FIRST (Cole, standing).** A card that
conforms to blocks — fits its size class, measured, no overflow —
renders correctly in flex, where height is automatic. The reverse is NOT
true. Never design a card flex-first.

**Card sizing: measured by default, DECLARED when a graphic is at stake
(Cole, 2026-07-24).** `sizeFor` normally picks the smallest class that
fits every row, which is right for a card that is only params. It is
wrong for a card whose graphic is the point: the params win the
measurement, the card lands at L, and the graphic takes whatever height
is left over — PW Pulse Pad's waveform measured FOURTEEN pixels. Those
cards set `n.defaultSize` and `addSizeChips(n, [...])` instead, and
define an explicit face per size in CSS; `n.ownFaces` then turns off the
generic 2-column fallback. At the bigger size the extra room goes to the
GRAPHIC, never to more param columns. Where a body genuinely needs two
columns, split it with `splitColumns(n)`: the LEFT column keeps every row
that owns a handle and the RIGHT column is 100% handle-free params, so
the 1:1 row↔handle line is structural rather than lucky.

**Card chrome (Cole, 2026-07-24).** The card's COLOUR BAR is the power
indicator — outline = off, filled = on, in the category colour; there is
no round head LED any more, and `bindNode` distinguishes a stationary
press (toggle) from a drag (move). Banner cards (the binary family) have
a coloured head instead and therefore no family stripe to fill. Input
handles wear a PASTEL of their data colour and hover-declare
`"data type > target name"` — handles never name their own card, wire
labels do; one `portTarget()` helper feeds both, so they cannot drift
apart. Small-print SUBTITLES are gone from every card except the four
that carry live state duplicated nowhere: Loop Deck (transport state),
the monitors (local/global scope), Threshold (its source) and LFO (its
destinations). Input handles normally fill one edge; `dualEdge` is an
opt-in OVERFLOW-only flag (once the primary edge is full, the rest spill
onto the other in-edge) so a light patch renders exactly as before —
Master Out is the only card that sets it today.

**QUIET param handles stay on their row.** The flex dead-zone nudge
exempts them, and `QUIET_SEP` fires only on a genuine SAME-ROW
collision. Never reintroduce a proximity-based separation: on a packed
card rows sit ~0.69u apart and it silently walks every handle off its
row. Where a handle truly cannot sit on its row it grows an on-card link
wire into that row instead.

**REACTIVE-INDICATOR DOCTRINE (Cole, 2026-07-24 — standing).** Every
indicator must react to LOGIC input, not just to clicks. State applied
inside the gate settle pass does NOT broadcast, so the backend emits its
own `{"kind": "level", "ep", "on"}` tap and the GUI routes it (the power
stripe, the Play/Stop card + top bar, and the click/accent LEDs were the
first three). Any new indicator is wired the same way — and a headless
mock can only prove it reacts to a message we invented, so verify it on
the rig with `tests/probe_live_gui.py`.

**Where a control has BOTH a card and a top-bar element, they are ONE
state** and a level tap must drive both. `run` has always done this
(`syncTopBarPlay`); `click` did not — a logic-driven change lit the card
LED and left the top bar reading the opposite, which was not merely
cosmetic, since `sendTransport()` READS the checkbox and pushed the stale
value back on the next BPM nudge. Fixed on `feat/p11-dual-mode` via
`syncTopBarClick`, routing both call sites through one function so they
cannot drift apart again.

**`accent` is the deliberate exception (Cole, 2026-07-26).** The transport
has THREE binary level-ins — `run`, `click`, `accent` — but accent has **no
top-bar element at all**, so there is nothing to sync and no top-bar call
in its branch of the handler. **This is an intentional asymmetry, not a
missing feature: do not "fix" it** by adding a top-bar accent control. If
accent ever gains one, it joins the rule above and needs its own
`syncTopBar…` and a falling-edge test.

The doctrine itself is untouched by that decision: **accent's CARD
indicator must still react to logic input** exactly like the others. Only
the top-bar mirror is absent, because there is no top-bar element to
mirror to.

## Writing a new module (the main vibecoding activity)

> ⚠ **The `dual` kind is LIVE on `main`** (2026-07-27):
> `KINDS = ("source", "effect", "dual")`, the
> `generates()`/`takes_audio_in()` predicates are load-bearing —
> `rack.py` branches on them in nine places — and `power_shaper` declares
> `kind="dual"`. **The three-kind table in rule 3 is what the code does
> today**; do not revert it to the old binary rule.
>
> **Gen mode is under active repair, so treat the details as moving.** It
> shipped SILENT (see the playability landmine below) and was briefly
> reverted to `kind="effect"` on a branch; Cole's call was to fix it
> properly instead, so that revert is not on `main` and is not the
> direction. Until the item 11 session reports, **read the code for ground
> truth, not this banner**: `KINDS` in `synthbase/module.py`, and
> `grep -l 'kind="dual"' modules/`. `power-shaper-dual-v1` tags the
> pre-revert module and `continuity/item11-gen-mode-failure.md` holds the
> post-mortem.
>
> The kind existing and a module using it are separate questions — check
> both.

Copy an existing file in `modules/` and change the body. The contract:

```python
from supriya import synthdef
from supriya.ugens import In, Out, ...   # 400+ UGens available

from synthbase import module, param

@module(
    name="Display Name",
    kind="source",              # "source" generates | "effect" processes | "dual" both
    params={                    # every knob a human/MIDI/GUI may turn
        "cutoff": param(60, 12000, 1200, curve="exp"),  # min, max, default
    },
)
@synthdef()
def my_module(cutoff=1200, out=0):            # function name = stable key
    ...
    Out.ar(bus=out, source=[sig_l, sig_r])    # ALWAYS stereo out
```

Rules:

1. **Function name is the module's identity.** Patches and hot reload key on
   it. Renaming the function = a new module.
2. **Stereo everywhere.** `Out.ar` gets a 2-channel source. Mono signals:
   `[sig, sig]`.
3. **`kind` decides whether you take `in_bus`.** There are THREE kinds, not
   two (`KINDS` in `module.py`), and the two helpers there are what the
   engine actually branches on — `generates(kind)` and `takes_audio_in(kind)`:

   | kind | generates? | takes `in_bus`? |
   | --- | --- | --- |
   | `source` | yes | **no** |
   | `effect` | no | yes — read `In.ar(bus=in_bus, channel_count=2)` |
   | `dual` | yes | yes |

   Everyone takes `out`. A **dual** module is both at once: it generates on
   its own, AND processes whatever is wired into it. It therefore ALWAYS
   owns an `in_bus`, even while generating.

   ⚠ **A dual changes what an incoming audio wire means.** A wire into a
   plain source SUMS into the running bus (see the landmine below); a wire
   into a dual lands on its `in_bus` instead (`Rack._dst_bus`). Don't
   generalise the fan-in rule across kinds.
4. **Every human-facing knob goes in `params`** with a sensible range and
   `curve="exp"` for frequencies/times. Defaults in the function signature
   should match the param defaults.
5. **MIDI-playable sources** additionally take `freq` and `gate` and wrap the
   signal in `EnvGen.kr(envelope=Envelope.adsr(...), gate=gate)`. Keep
   `done_action` unset (0) so the node survives release — mono voices are
   persistent nodes.
6. **Keep one module per file** unless variants truly belong together.
7. Smoothing: wrap params that will be twiddled in `Lag.kr(source=p,
   lag_time=0.02)` to avoid zipper noise.
8. Param types: `curve="toggle"` renders a checkbox; `param(..., options=("a","b"))`
   renders a dropdown (value = option index; use `Select.ar/kr` in the DSP).
9. **Pitch offsets are always in semitones or cents**, never raw frequency
   ratios — convert inside the DSP with `.semitones_to_ratio()` (e.g.
   `(cents / 100).semitones_to_ratio()`). Voice-level pitch bend already
   follows this convention (±2 semitones).
10. **A dual module's `mode` is DERIVED — never a param.** It is a plain
    synthdef arg (`mode=0`), and `App._sync_dual_modes` pushes it from the
    AUDIO GRAPH: a stored wire whose destination is this instance means FX
    (`mode=1`), no wire means GENERATE (`mode=0`). Putting it in `params`
    would give the user a knob that the next rewire silently overrides.
    Three consequences worth building for:
    - **Compute both chains and crossfade through a LAGGED `mode`**, so a
      wire landing or being cut is click-free instead of a hard switch.
    - **A rebuild spawns every dual at the synthdef default (`mode=0`)**,
      so the sync is re-pushed with `force=True` after one — same class of
      bug as playable sources having to spawn `gate=0`.
    - **Mode is invisible state, so it must announce itself**: the backend
      emits `{"kind": "level", "ep": "<id>:mode", "on": …}` for the card,
      exactly as `:pwr` does. That is the reactive-indicator doctrine, and
      a shaper that silently stops generating is precisely what it exists
      to prevent.

UGen naming: supriya mirrors SuperCollider UGens with snake_case keyword args
(`SinOsc.ar(frequency=...)`, `RLPF.ar(source=..., frequency=...,
reciprocal_of_q=...)`). When unsure of an argument name, check
`python3 -c "import inspect; from supriya.ugens import X; print(inspect.signature(X.ar))"`.

## Patches

`patches/*.py` define `PATCH = {"chain": [...], "bindings": {...}}` — plain
data. Chain order = execution order = signal flow. First entry must be a
source. See `patches/demo.py`.

## Running

```bash
source .venv/bin/activate         # created by setup
python -m synthbase devices       # list MIDI inputs
python -m synthbase test          # 2s sine — verifies engine + audio out
python -m synthbase play patches/demo.py
```

Hot reload is on by default under `play`: edit any file in `modules/`, save,
hear the change without stopping.

## Running the GUI

```bash
./run.sh                          # relaunch cleanly (pidfile-managed)
python -m synthbase gui pad_space # or directly; GUI at http://127.0.0.1:8765
```

## Testing changes

There's no audio in CI/cloud contexts. There are **THREE** headless
categories, and they fail in different ways — a change is not proven until
the relevant one is green.

**1. Python suites — applied STATE.** They construct an app and assert what
it ended up doing.
`smoke.py` (every module loads, synthdefs compile, patches parse, keyshift
math sane) · `test_graph.py` (audio-wire derivation, graph/ctl bookkeeping,
instance ids, multi-voice, tonic→drone, keyshift lanes, tap-closure and
snip-heal) · `test_looper.py` (deck timing, take pairing) · `test_gate`,
`test_ping`, `test_deriver`, `test_lfo`, `test_threshold`, `test_transport`,
`test_allocation`, `test_drone_alloc`, `test_reactive`, `test_audio_session`,
`test_rig`, `test_power_sine`.

**2. Playwright suites — the RENDER**, driven against mock state/events.
`check_blocks.py` (blocks geometry — sections 1–10 are the regression net) ·
`gui_check8.py` (cards, wires, monitors, splices, keyshift, closure
regressions; `gui_check{,6,7}.py` are earlier snapshots kept for reference,
not upkeep) · `check_real.py` (replays a captured real-rig `state` into
`blocks.html`; headless despite the name — no server, no audio).

**3. `check_replay.py` — the ENGINE'S OUTPUT joined to the GUI's render.**
The third category, and the one the other two cannot cover. Records
scenarios through `tests/silent_rig.py` (the real `GuiServer` over an
engine-less `SynthApp`, no audio and no scsynth), then asserts against those
**recordings rather than invented messages**:

    python3 tests/check_replay.py --emission   # backend, no browser needed
    python3 tests/check_replay.py --dom        # GUI, no engine needed
    python3 tests/check_replay.py              # both

Why it exists, concretely: **replacing all four `_emit_level` call sites
with `pass` leaves every Python suite green**, because those suites observe
applied state, not emissions. And the three Playwright suites contain the
string `synthbase` zero times — every `{"kind": "level"}` they assert is a
shape they wrote themselves, so a backend that stopped emitting, or emitted
a different shape, would not fail them. `check_replay` is the check that
goes red. CI runs it in BOTH jobs — `--emission` headless, `--dom` with the
browser.

Write NEW checks failing-first against the broken behavior.

`test_mixed_sources.py`, `diag_*.py`, `hear_check.py`, and `probe_ws.py`
talk to a **live** server over websocket instead of running headless — they
need `python -m synthbase gui` actually running with real audio, so treat
them as Mac-only manual checks, not something CI or a cloud session can run.

On the Mac, `python -m synthbase test` is the real proof.

## Parallel sessions and the shared `.git`

Several agent sessions work this repo at the same time, from different
sandboxes, against ONE `.git`. That is normal here, and it has its own rules.

- **NEVER run `git worktree prune` in this repo.** Each session registers its
  worktree under its own sandbox mount path, and that path does not exist from
  any other session's view — so **every worktree looks prunable to everyone
  else**. On 2026-07-26 one prune destroyed four worktrees; the working files
  survived, but three of them lost their branch ref and came back detached.
  There is no safe time to run it while anyone else may be live.
- **Lock a worktree the moment you create it** — a locked worktree is never
  pruned, by you or by anyone else:

      git worktree add .worktrees/<name> -b <branch>
      git worktree lock .worktrees/<name> --reason 'session worktree — do not prune'

- If a worktree is pruned anyway: rebuild `.git/worktrees/<name>/` by hand
  (`commondir`, `gitdir`, `HEAD`), write a `locked` file, then re-checkout the
  tree. Recover the branch ref from a reflog or a sibling clone if you can —
  otherwise the commits are still reachable by hash, so look before you rebuild
  the work.
- **`git status` does not show another session's work.** Unpushed branches are
  invisible to it. On first contact with the repo run
  `git log --branches --not --remotes` and `git for-each-ref` — this has caught
  a commit sitting unpushed for a day, and later two entire branches.
- **Re-fetch and rebase before every branch and every merge.** `main` moves
  under you mid-session, sometimes several times an hour.
- **Don't check out in the shared working tree** while another session may be
  using it, and don't leave it on a branch. Probes load the page over HTTP and
  need no checkout — stage them to `/tmp` and run them from there.

## `continuity/` has a safety net now — use it

`continuity/` is the on-disk mirror of the living docs in the claude.ai
"Patchwerk" project (the project is canonical; that folder is the copy).
It is **gitignored**, so ordinary git cannot restore it — and
`git clean -xdf` deletes ignored files outright. It was lost once, on
07-25, and rebuilt from memory.

**`continuity/bin/continuity-guard.sh` fixes that property.** Two
restore-only nets, both refreshed automatically by a `post-commit` hook:

1. `refs/continuity/snapshots` — an orphan history in the shared `.git`.
   Survives `git clean -xdf`, `worktree prune`, worktree deletion, and
   `rm -rf continuity/`. It is not on `refs/heads/*`, so the default push
   refspec never publishes it — **the notes stay private.**
2. `~/.patchwerk-continuity/` — outside the repo, so it survives `.git`
   being destroyed or the checkout being deleted. Holds the current tree,
   a bundle of the full history, and a copy of the tool itself.

```bash
continuity/bin/continuity-guard.sh status    # both nets + drift
continuity/bin/continuity-guard.sh snapshot  # also runs post-commit
continuity/bin/continuity-guard.sh restore   # bring it back
continuity/bin/continuity-guard.sh verify    # prove the nets restore
```

**If `continuity/` is gone, the tool went with it** — bootstrap from the
mirror's copy: `bash ~/.patchwerk-continuity/bin/continuity-guard.sh restore`.
The hook is machine-local (`.git/hooks/`); on a fresh clone, reinstall it
with `continuity-guard.sh install-hook`.

Two things NOT to do. **Don't edit the mirror** — it is the disaster copy,
and a third editable lineage is exactly what caused the 07-24/25 doc
divergence. **Don't defeat the shrink guard with `--force` to make a
warning go away**: a snapshot that faithfully replicates a deletion is not
a backup, so a tree that has lost most of its files is refused and BOTH
nets keep their last healthy state. Restore first, then diff.

## Landmines (learned the hard way)

- scsynth crashers: `.clip()`, scaled `RecordBuf` sources, EnvGen-driven
  `record_level`; `In.ar(0)` at the root's tail reads junk.
- Playable sources must spawn `gate=0` (the synthdef default of 1 leaves
  idle voices droning after every rebuild).
- Extra sources SUM into the running bus — a fresh bus orphans everything
  upstream ("generators go dead"). **This is KIND-SPECIFIC**: a wire into a
  `dual` module goes to its `in_bus` instead of summing. Never infer the
  destination's behaviour from the fact that it is a destination —
  `Rack._dst_bus` decides, and it is the only thing that does.
- **NOTHING ASSERTS THAT A PLAYABLE MODULE IS REACHABLE BY A VOICE.** Not
  one of the 18 Python or 3 Playwright suites. That is how item 11's
  Power Shaper — a module that **could never make a sound** — shipped
  green on 2026-07-27: `rack.py` was widened to the `generates()` /
  `takes_audio_in()` predicates, but `app.py`'s FOUR playability tests
  still compared `kind == "source"` by strict equality (`:471`, `:924`,
  `:1153`, `:1579`), so no voice would ever aim at it, and a playable
  source spawns `gate=0` — the envelope never opened. Every suite passed
  because each one tested a layer: the module compiles, the rack accepts
  it, the card renders. **Nothing tested the JOIN** — that a thing
  declaring itself playable is actually reachable from the note plane.
  Until such a check exists, **adding a kind or a playability rule means
  grepping every predicate over `kind` yourself** (`grep -rn 'kind ==' \
  synthbase/`) — a new kind is done when every predicate over it has been
  re-derived, not when it compiles. Post-mortem:
  `continuity/item11-gen-mode-failure.md`.
- **A TEST DOUBLE THAT ENUMERATES A PRODUCTION TYPE'S FIELDS breaks
  silently when that type gains one.** A double that lists what it expects
  — rather than mirroring the real shape — keeps passing while quietly
  covering less, because the new field is simply absent from the double and
  no assertion names it. **Fix the DOUBLE; do not make production bend
  around it** with `getattr(x, "new_field", default)` — a defensive
  accessor added to satisfy a stale mock buys a green suite by making the
  production type permanently harder to reason about, and hides the same
  gap next time. This is the same family as the `free(force=…)` mock
  landmine above: a double is a claim about the real thing, and a claim
  that is kinder or narrower than reality is where coverage silently goes.
- Sort looper events by beat with a STABLE key-only sort — tuple sort puts
  offs before ons at equal beats and scrambles pairing.
- Every all-off/silencing path must close its open notes AND their viz taps
  (panic, arp stop, deck stop, rebuilds, record-window exits).
- `system_profiler` (device lists) takes seconds — cache it; never call it
  per state snapshot.
- GUI sends during a websocket reconnect gap must queue, not drop (note-offs
  especially); macOS swallows letter keyups while ⌘ is held.
- A canvas with zero `clientHeight` must NOT draw: it skips the height sync
  and then paints against stale geometry. No box, no paint.
- Assert the COMPUTED property, never a substring of it — a translucency
  check for `"rgba"` passes on plenty of opaque colours, and Chromium
  computes `color-mix()` to `color(srgb … / 0.88)`. Parse and compare.
- **No top margin above the card grid.** Item 20 added one (`TOPM`) and it
  was REVERTED in PR #40: it desynchronised the card grid from the wire
  router. Reintroducing it means re-deriving the router geometry with it,
  not just threading a constant.
- Engine rebuilds invalidate server-side registration: `set_devices` makes a
  NEW scsynth, so anything holding "already sent to the server" state must
  track the server OBJECT, not a boolean (the LFO, threshold and relay-audio
  managers do; any future manager that sends synthdefs or registers OSC
  callbacks must too).
- **SATELLITE VOICES ARE NOT `Instance`s** (item 10, on `main`). A poly
  voice leases SLOTS from
  its target's `VoicePool`: slot 0 is the target instance's own node, but
  **slots ≥ 1 are satellites — real scsynth synths with no `Instance`, no
  card and no state entry**, cloned from the target's settings onto the same
  out bus. Every loop that walks `rack.instances` is therefore blind to
  them, and that is the whole hazard: **anything that moves, pauses,
  rewires, re-params or frees a target node must reach its pool too**, or
  satellites are left behind as audible garbage nothing on screen can stop.
  The paths that already do, and the failure each one prevents:
  - `reorder_for_wires` → `pool.reposition()` — else a satellite writes its
    bus AFTER the effect that reads it, and only the extra voices sound dry.
  - `set_enabled` → `pool.set_paused()` — else bypass silences the card
    while the satellites keep playing.
  - `set_param`/`set_params` → `pool.mirror()` — else a knob moves one voice
    of N. `PER_VOICE_PARAMS = ("freq", "gate")` are deliberately NOT
    mirrored: mirroring those collapses every satellite onto slot 0's pitch
    and gate.
  - `audio_rewire` and the null-bus park → mirror `out` — else rewiring
    moves the card and strands the satellites on the old bus.
  - `remove_instance` and `teardown` → `pool.dispose()` — else the
    satellites outlive the card and drone on, with no card left to stop them.

  Satellites are DERIVED state: re-clone them whenever the target's node
  identity changes (bypass, hot reload, rack rebuild) or the server object is
  replaced — the same rule as the entry above. **When you add a path that
  touches a target node, assume you owe the pool a call.**
- **`node.free()` does NOT free a gated synth — pass `force=True`.**
  supriya emits `/n_set gate 0` for any synth that HAS a gate (a release)
  and `/n_free` only for one without. Playable sources all have a gate
  (module rule 5), and that rule also mandates `done_action=0` so the node
  SURVIVES release — voices are persistent nodes. The two compose into a
  leak: an unforced free silences the instance and leaves it running
  forever while the rack forgets it exists. Measured live: removing a gated
  instance took the node count 10→11→12→13, a gateless reverb 14→13. Cost
  of forcing: removing a module mid-note cuts its release tail (see
  `_free_node_note` in `rack.py`).
- **A NODE MOCK MUST MIRROR `free(force=...)` EXACTLY** — this is how the
  leak above stayed invisible, and it will catch the next person. The old
  `FakeNode.free()` took no `force` and set `freed = True`
  UNCONDITIONALLY, so the tests cheerfully reported "freed" for satellites
  that were still running on scsynth. Two failures had to line up, and
  both are easy to reproduce: a mock whose signature is kinder than the
  real API can never fail the way production does, and production's
  `except Exception: pass` around node teardown would have swallowed a
  signature mismatch too — so neither side could report it. **A mock is a
  claim about the real API; if it is wrong, every test built on it is
  testing the mock.** When you mock a supriya call, copy the real
  signature and its DEFAULT (`force=False`), and record what was asked for
  (`free_calls`) rather than only that it was asked.
- **A ctl node id must never collide with a MODULE type** (item 29). The
  drone allocation's ids are `hold`,
  `hold.2`, … and NOT `drone`, for one reason: `drone` is already a module
  type, and a ctl node sharing an id with a rack instance **shadows that
  instance in `_ctl_sinks`** — the note router would resolve the wrong
  thing. The card is still titled "Drone Voice"; the id is plumbing, the
  name is the product. Any future ctl node needs the same check against the
  module registry before its id type is chosen.
- **A drone card's POWER must never call `set_enabled`** (item 29). POWER holds the TARGET's envelope open (`set_gate_open`), because
  bypassing the target node would **silence any poly voice sharing that same
  source** — allocations lease slots from one pool, so bypass is not a
  private off switch. Two related traps on that path: the effective gate is
  `power AND transport.running` (item 32's invariant in allocation terms),
  and on a GATELESS target there is nothing to open, so it is SKIPPED rather
  than faked — writing a `gate` the synthdef doesn't have puts a phantom
  param into `inst.settings` and into the broadcast state.
- A relay circuit's stored wires are the truth on EVERY plane. Item 25 made
  the audio plane match the mod plane: a claimed audio circuit is a permanent
  lagged-gate synth, `state.wires` broadcasts the STORED graph (endpoints
  verbatim), and open/close moves a gate param — never the wiring. Anything
  that resolves relay hops away before broadcasting them re-breaks the
  second-client render this fixed.

`docs/TROUBLESHOOTING.md` has the complementary, symptom-indexed list —
runtime/hardware gotchas (sample rate, Bluetooth, MIDI controllers) that
aren't code-facing enough to belong here.

## Don'ts

- **Don't run `git worktree prune`** — see the parallel-sessions rules above.
  It looks like housekeeping and it deletes other people's worktrees.
- Don't use sclang or .scd files — Python only, we talk straight to scsynth.
- Don't add heavyweight wrapper abstractions; modules use supriya UGens
  directly. The base stays thin.
- Don't block in MIDI callbacks (they run on the port's thread).
- Don't rename the `synthbase` package or its `python -m synthbase` entry
  point as a side effect of an unrelated change — it's intentionally stable
  even though the product name around it is now Patchwerk.
