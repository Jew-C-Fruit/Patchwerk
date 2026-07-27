# Item 29 — Drone Voice card: GUI spec for the item 11 session

Backend is built and green on `feat/p29-drone-allocation` (branched from
`feat/p2-poly-voice` @ `d10b907`, **not** from `main`). `gui/blocks.html` is
yours; this is the whole ask, written so nothing has to be inferred from the
Python.

Nothing in the GUI breaks without this. A drone voice simply cannot be
spawned from the palette until the button exists, and if one arrives from a
resumed patch it renders as a "Mono Voice" until the policy dispatch lands —
see §2, which is the one change that is a *bug fix* rather than a feature.

---

## 1. Palette entry

A third button beside Mono Voice (and beside Poly Voice, whose card is also
still outstanding from item 10):

```js
send({type: "spawn_drone_voice"});
```

Label **"Drone Voice"**. No arguments. The server replies with a fresh
`state` broadcast containing the new entry.

It **throws if there is no note-playable source in the rack** (same rule as
`spawn_voice`/`spawn_poly` — a voice must have something to aim at). Same
error surface as those two; no special handling needed.

## 2. Dispatch on `policy` — the one existing-behaviour fix

`state.voices` is no longer mono-only. Every entry now carries `policy`, and
`buildVoiceCard` is currently applied to all of them:

```js
const voiceNodes = voiceList.map(buildVoiceCard);   // blocks.html ~6068
```

That must become a dispatch:

| `policy` | card |
| --- | --- |
| `"mono-latest"` | `buildVoiceCard` (today's Mono Voice) |
| `"poly"` | the Poly Voice card (item 10, still outstanding) |
| `"hold"` | **the Drone Voice card (this spec)** |

Default unknown policies to `buildVoiceCard` so an older/newer server can't
blank the canvas.

## 3. State shape

`state.voices[]` entries (`app.state()`):

```json
{"id": "hold", "target": "pw_pulse_pad", "policy": "hold", "slots": 1,
 "power": false}
```

* `id` — `"hold"`, `"hold.2"`, … The type is **`hold`, not `drone`**, because
  `drone` is a MODULE type and a ctl node sharing an id with a rack instance
  would shadow that instance in the note router. Card title should still read
  **"Drone Voice"** — the id is plumbing, the name is the product.
* `target` — the playable source it steers, same vocabulary as Mono Voice's
  target chip.
* `slots` — always `1`.
* `power` — the drone card's POWER, and **`null` for the gated policies**, so
  `policy === "hold"` (not `power != null`) is the right discriminator.
  It is **user INTENT, not the audible state**: the effective gate is
  `power && transport.running` (item 32). Do not try to render the
  effective value — the card shows intent, and the transport card shows
  the other half.

## 4. Card shape

Blocks-first, family `ctl` (it is a peer of Mono Voice). Body is params
only, no graphic — so **measured sizing**: no `defaultSize`, no
`addSizeChips`, no `ownFaces`. Two rows lands it at S (10×4.5u).

Rows:

1. **target** — identical to Mono Voice's chip: cycle `playableSources()`,
   send `{type: "set_voice_target", key, voice: gid}`. The drone reuses that
   exact message; nothing new.
2. **power** — see §5.

**No subtitle.** Per the card-chrome doctrine subtitles survive only on the
four cards carrying live state duplicated nowhere; the drone's held root is
not broadcast, so there is nothing honest to put there. Please don't invent
one.

Kill button: `send({type: "remove_voice", id: gid})` — the same message as
the other allocations (there is no `remove_drone_voice`). Removal closes the
gate it was holding and drops its POWER wire, server-side.

## 5. POWER — the indicator, and the reason it exists

A drone aimed at an ordinary playable source has to hold that source's
envelope open; that is POWER, and it is the drone's only off switch (it
inherits "bypass is the only off switch" from the drone module, as LEVEL
semantics).

**Preferred rendering: the card's COLOUR BAR**, exactly as it is the power
indicator on chain cards — outline = off, filled = on, in the category
colour, with `bindNode`'s stationary-press-vs-drag split toggling it. This
card is the first `ctl` card that genuinely has a power axis, so it is the
first place that chrome applies off the chain. If claiming the stripe on a
ctl card turns out to be more surgery than it's worth, an explicit toggle
row is an acceptable fallback — but the stripe is the better read.

Click sends:

```js
send({type: "set_drone_power", id: gid, on: <bool>});
```

**The reactive half is already routed.** The backend emits

```json
{"kind": "level", "ep": "hold:pwr", "on": true}
```

from **both** routes — a card click *and* a binary wire driving the level-in
inside the gate settle pass. The existing `e.kind === "level"` handler
(~6705) already forwards `<base>:pwr` to `uiParam["<base>.pwr"]` for
bare-gid cards, so **registering `uiParam[gid + ".pwr"]` is all the wiring
the indicator needs.**

Two small additions in that handler's `sub === "pwr"` branch, so the local
state model stays in step for the next rebuild (it currently only knows
about `arp`, `drums` and chain modules):

```js
const dv = (state && state.voices || []).find(v => v.id === base);
if (dv) dv.power = on;
```

The tap is covered by `tests/test_drone_alloc.py`
(`test_power_tap_fires_from_a_direct_call`,
`test_power_tap_fires_from_a_binary_wire`) — it fires on both edges from
both routes, so the indicator is allowed to depend on it. Item 10 declined
to add an indicator for want of exactly that proof; this one has it.
Headless proof is still not rig proof: please give it a pass with
`tests/probe_live_gui.py`.

## 6. Ports

```js
ports: [["in", "ctl", "notes"],     // TONE
        ["in", "bin", "power"],     // POWER
        ["out", "ctl", "drive"]]    // to the target source, as Mono Voice
```

* **TONE in** — an ordinary ctl-wire destination at the **bare gid**
  (`"hold"`). Anything that emits notes wires in: keys, arp, deck, a tonic
  or literal deriver, a key-shifter lane. Notes steer the target's `freq`
  through its own Lag/glide path; **releasing the last note HOLDS the
  root** — that is the policy, not a bug, and there is nothing to render
  for a note-off.
* **POWER in** — a BINARY level-in at `"<gid>:pwr"`, exactly like
  `"<key>:pwr"` on a chain card. It is a fan-in level-in, not a
  single-input endpoint (no steal-on-drop). `is_toggle_dst` already accepts
  it, so wire validation needs no change.
* **out "drive"** — the visual link to `target`, same as Mono Voice's.

Handle pastels follow the data colour as usual: TONE is a ctl handle, POWER
is a binary handle.

## 7. What is deliberately NOT on the card

* **No bend readout.** Mono Voice carries a static `±2 st` chip; a drone
  ignores bend by design (it is the reference you bend *against*), so a bend
  row would be a lie. It does follow global transpose, which has no per-card
  UI anywhere.
* **No voice-count row.** `slots` is always 1.
* **No mode/root readout.** The held root is not broadcast.

## 8. Protocol summary

| message | direction | payload |
| --- | --- | --- |
| `spawn_drone_voice` | → server | `{}` |
| `set_drone_power` | → server | `{id, on}` |
| `set_voice_target` | → server | `{key, voice: id}` (existing) |
| `remove_voice` | → server | `{id}` (existing) |
| `state.voices[]` | ← server | `{id, target, policy, slots, power}` |
| `{"kind":"level","ep":"<id>:pwr","on":…}` | ← server | reactive indicator |

Full prose lives in `synthbase/server.py`'s docstring, which is updated on
the branch.
