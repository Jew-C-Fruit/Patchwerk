# 7. Binary and logic

The on/off plane — what fires, what latches, what gates what. This is the
plane that turns a patch into a machine that runs itself.

---

## 7.1 The model

There is **one** binary signal here, and it is a level: hi or lo. Sources own
that level; everything else is derived from it changing. A "ping" is not a
separate kind of signal — it is a level that goes hi and immediately lo
again, and it travels the graph like any other change.

That is why the two kinds of destination behave differently. A **level-in**
follows the level in both directions and applies the moment the wire lands,
even if nothing has changed since. A **trig-in** fires on the rising edge
only — so dropping a wire from a source that is *already* hi does nothing,
because no edge happened. Whenever a binary wire seems inert, this is
usually why.

Feedback loops between nodes are allowed and freeze at a settled state
rather than spinning forever; only a direct self-wire is refused.
REFERENCE §5.1.

[FIG-07-01]

## 7.2 Binary sources

Five things emit binary.

- **Button** — momentary by default (hi while held), or latching (a press
  toggles, the release is ignored). Arm it to bind a MIDI CC or an
  unassigned computer key; arming one button disarms the others. Switching
  mode drops the level.
- **Clock** — the pulse-only source: its level is always lo and it fires a
  pulse on each transport tick. Its division ladder is the global one plus
  whole-note multiples (`8/1`, `4/1`, `2/1`) for slow, meter-independent
  cycles. A stopped transport parks it; it never fires.
- **Threshold** — an LFO crossing a level, turned into binary (§7.6).
- **Logic out** — a gate's computed output (§7.4).
- **Relay circuit** — a binary circuit's output (§7.5).

Every level change lights the LEDs, and a wire carrying a rising edge wears
the moving white overlay (§4.1), so you can see the plane running without a
monitor. REFERENCE §5.2.

## 7.3 Binary destinations

**REFERENCE §5.3 is the complete list of binary destinations, verified
against the code.** Keep it open next to the rig rather than memorising it.

Reading it takes two questions. **What is the endpoint's kind?** A level-in
follows the source in both directions, including the first moment the wire
exists; a trig-in fires once per rising edge and ignores everything else.
Get this wrong and you will wire a latching button into something that
wanted an edge, or a clock into something that wanted a level.

**Is the endpoint single-input?** A logic gate's legs, a relay's control-in
and every relay circuit in take exactly one wire, and a new wire **steals**
the slot rather than being refused.

The destinations group into families you can hold in your head: module and
machine power (`<id>:pwr`), logic gate legs, relay control and circuits, the
transport's controls, deriver commits, and the Loop Deck's buttons. Anything
with a colon is a sub-endpoint of the node named before it.

## 7.4 Logic gates

A logic gate is one card with an op dropdown: `AND`, `OR`, `NOR`, `XOR`,
`SR latch`, `T latch`. Every op exposes the same two single-input endpoints,
`:a` and `:b`, so the card's shape never changes and **swapping ops never
drops your wires**. An unwired input reads lo.

The four combinational ops answer "when should this be on?":

- **`AND`** — on while *both* legs are hi. Two conditions that must hold
  together: the echo runs only while the transport is running *and* you are
  holding the button.
- **`OR`** — on while *either* is hi. Two ways to switch the same thing on.
- **`NOR`** — on while *neither* is hi. Wire one leg only and you have a NOT
  gate: the filter is in whenever the drone is out.
- **`XOR`** — on while exactly one is hi. Two clocks at different rates give
  you a pattern that is on only when they disagree.

The two latches remember instead of computing. **SR latch**: `:a` sets, `:b`
resets, reset wins, and the state starts lo both when you enter the op and
when you leave it. **T latch** is the toggle and the one op that is
edge-triggered: a rising edge at `:a` flips the output, `:b` is a reset that
wins exactly as SR's does, and an edge arriving while reset is held is
eaten, not queued. Because it samples the level it last saw at `:a`, a
steady hi is not an edge — and unwiring `:a` forgets that sample, so the
next source to land there starts fresh. A T latch reloads lo after a
restart; its edge memory is not saved.

Chain T latches and each stage divides by two. One latch off a `1/4` clock
flips every beat; add a second and it flips every two. Slow patterns come
out of two cards and no sequencer.

> **TRY THIS.** Spawn a clock at `1/1` and a logic gate set to `T latch`.
> Wire the clock to `logic:a` and the gate's output to `lowpass:pwr`. Start
> the transport: the filter is now in for one whole note and out for the
> next, with nothing for you to play.

REFERENCE §5.4.

[FIG-07-02]

## 7.5 Relays

[FIG-07-03]

A relay is a bank of switch contacts — the physical kind, where a coil pulls
a row of separate switches at once. One relay node holds up to **nine
independent circuits** plus one control-in that opens and closes all of them
together.

Circuit *k* lives at `"<id>:<k>"`. The wire *into* it is that circuit's in,
the wire *out* of it is its out, and the circuit's **kind is inferred from
whichever wire lands first**, then enforced — a circuit that starts carrying
audio will refuse a note wire. Remove both wires and it forgets, ready for a
different kind. One relay can carry audio, notes, binary and modulation on
different circuits at the same time.

**Contacts are 1:1.** Both sides of a circuit take exactly one wire; a
second wire steals rather than sums. The only `+` on the card is the
expansion that reveals contacts five to nine.

Closed defaults to open. Click the card to switch it by hand, or wire a
binary source into `"<id>:ctl"` and the closed state follows that level —
last writer wins, so a click and a wire take turns.

What "open" means depends on the kind:

- **Notes** pass through only while closed. Note-offs always pass, and
  opening the relay closes every note downstream, so you can never leave a
  note hanging in an open circuit.
- **Binary** out is the circuit's inputs OR'd together, gated by closed.
- **Modulation** maps and unmaps the LFO's destination as the circuit closes
  and opens, so opening lets the parameter settle back onto its own knob
  value (§8.1).
- **Audio** is really switched, not muted: each claimed audio circuit owns a
  gate with a 10 ms slew, so opening is clickless and any reverb or echo
  tail downstream rings out instead of being cut off.

Each circuit picks its own colour and both halves of the hop draw in it, and
the wire label names the whole path — `Keys → Relay → Voice` — so a busy
relay stays readable. Removing a relay silences its note circuits and drops
its wires. REFERENCE §7.

[FIG-07-04]

## 7.6 Thresholds as binary sources

[FIG-07-05]

A threshold watches one LFO and emits binary when that LFO crosses a level.
**Chapter 8 owns the input side** — what it watches, and the depth trap that
kills most dead thresholds (§8.2). This section is only what comes out.

Mode decides what the output *is*. `rising` gives you a level that follows
the crossing state, `falling` gives you its inverse, and `both` gives you
pulses: in `both` mode the level is pinned lo and every crossing is
delivered as a pulse. So a threshold in `both` mode drives trig-ins fine but
will never hold a level-in open — wire one to a module's `:pwr` and the
module only twitches. REFERENCE §6.2.

## 7.7 Indicators react to logic

Standing rule across the whole instrument: **an indicator reacts to logic
input exactly as it reacts to a click.** Wire a latching button to any
module's `:pwr` and watch that card while you press: it changes without you
touching it. Nothing on screen responds only to your mouse, so what you see
is always the state the engine is in.

Where a control appears twice, both move: the transport's `run` and `click`
level-ins drive the card indicator *and* the top bar. `accent` drives its
card indicator only, because it has no top-bar control — an intentional
asymmetry, not a gap. REFERENCE §5.3, §8.1.

[FIG-07-06]

## 7.8 Worked example: a patch that plays itself

Build this on top of the default wiring.

1. **Give it something to say.** Record a couple of bars into the Loop Deck
   and set it playing. The default `keys→arp→deck` and `deck→voice` wiring
   means you can play the phrase in and it comes straight back out.
2. **Make a slow alternation.** Spawn a clock at `2/1` and a logic gate set
   to `T latch`. Wire clock → `logic:a`. The latch flips once every two whole
   notes, so it holds each state for two — four to a full cycle.
3. **Switch the phrase with it.** Spawn a relay. Wire `logic` → `relay:ctl`,
   then `deck` → `relay:1` and `relay:1` → a **Poly Voice** aimed at a second
   source. The loop plays through the poly voice for two whole notes and
   drops away for two — and because opening a note circuit closes its notes
   downstream, nothing is left ringing when it drops.
4. **Let it change key on its own.** Add a `tonic` deriver fed from `deck`,
   and wire the same clock into the deriver's bare id. It stops using its own
   timer and re-reads the root on each clock pulse, so the drone it feeds
   moves in step with everything else.
5. **Add a slow breath.** Put a threshold on a slow LFO and wire it to
   `echo:pwr`. The echo now comes and goes on the LFO's cycle.

Press play. Everything here hangs off the transport, so stopping parks every
clock and the arp grid: the machine freezes and restarts in phase rather
than drifting. Panic still clears any note it left sounding.

Nothing in that patch is a sequencer. It is levels, edges and one switched
junction — which is the point of this plane.

---
