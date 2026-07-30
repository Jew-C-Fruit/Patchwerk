# 5. The audio graph

How sound gets from a source to the speakers, and what happens when you
rewire it while it is playing. Everything here is the audio plane — the blue
wires, the word `audio` on the label.

---

## 5.1 The chain, and the overlay on top of it

A patch has **two layers, and both are true at once.** Confusion about audio
routing is nearly always someone looking at one and reasoning about the
other.

The **chain** is the layer underneath. A patch file lists its stages in
order, and that list is built in order (REFERENCE §3.1): each stage that
isn't last gets its own private stereo bus feeding the next one, and the last
stage goes out to hardware. The head of the chain has to *generate*
something — a source or a dual. Only an effect at the head is refused.

> **A dual may lead a chain.** This changed in v2.2. A dual generates *and*
> processes, so it is legal at the head, where it is handed its own private
> input bus rather than being left reading whatever the null bus has in it.
> If you learned the old "a chain must start with a source" rule, drop it.

The **overlay** is the layer on top: the audio wires you draw
(REFERENCE §3.2). It starts out empty, and while it is empty the chain speaks
for itself. Your first structural edit adopts the wiring the chain currently
implies, and from then on the wires are the truth. They survive rebuilds —
reload a patch or a preset and every stored wire is re-imposed on the ids
that still exist.

[FIG-05-01]

Read the wires first; fall back to chain order only where no wire has been
drawn.

## 5.2 Rewiring

Each source has **one outgoing audio wire**. Rewiring is not a two-step
disconnect-then-connect: drag from the output handle to a different input and
the new wire replaces the old one.

Where a wire lands depends on what you drop it on:

- **an effect, or a dual** — its input, upstream of everything that effect
  does;
- **a plain source** — that source's own output bus, where it sums (§5.3);
- **Master Out** — straight to the hardware bus, past everything else.

Dropping a wire onto a dual's input is also what puts that dual into FX mode;
cutting it puts it back to generating (REFERENCE §2.2). A wire that would
close a loop is refused when you drop it, so you cannot accidentally build a
feedback path this way.

## 5.3 Fan-in is free — buses sum

Point as many sources as you like at the same destination. Buses sum, there
is no mixer to configure and no cost to the extra wire.

[FIG-05-02]

The part that surprises people is what happens when you aim a source at
another *source* in the middle of a chain. The new wire lands on the running
bus and sums into it — it does not get a fresh bus of its own. A fresh bus
there would orphan everything upstream of the join, and the symptom you would
report is "my generators went dead." If you want a source kept separate, give
it its own path to **Master Out** instead of landing it on another source.

## 5.4 Disconnected outputs

Cut a module's output wire and the module does not vanish, go idle, or fall
through to the speakers. Its output parks on a persistent silent **null
bus** — a real destination that goes nowhere.

[FIG-05-03]

So a disconnected module is still alive: still running, still holding its
settings, still costing CPU, and reconnecting is instantaneous with no
respawn and no gap. And because the parking bus is never the hardware bus, an
accidental disconnect can never dump an unprocessed signal into your
monitors.

## 5.5 Execution order

Nodes are ordered so that a wire's source always runs before its destination,
and that order is **derived from the wires** — not from where cards sit on
the canvas, and not from the order you added them (REFERENCE §3.2). Moving a
card is a purely visual act. Even reordering the chain list is just a list
edit, because audio order is wire-defined.

[FIG-05-04]

Draw the wires that describe the signal flow you want and the order follows.
It becomes visible in two cases: if the current order already satisfies every
wire, rewiring costs nothing on the server, so most edits are free; and if
you somehow have a cycle, reordering refuses rather than picking an arbitrary
answer.

## 5.6 Removing a module: splice-heal

Remove a module and the chain closes over the gap. If A fed X and X fed B,
removing X leaves A→B — every feeder of X is re-aimed at X's own
destination.

[FIG-05-05]

**Readers expect a hole, not a repair.** The gap-closing is the right
behaviour — pulling a filter out of a chain should leave the chain playing —
but it does mean removal is not reversible by re-adding: putting the module
back does not put it back *there*. If you might want it in place again,
bypass it instead (§5.7). Removal also cleans up after itself on the other
planes, dropping the
module's note wires, its power wires and any LFO destinations that pointed at
its params.

> **WARN.** Removing a module *while it is sounding* cuts its release tail
> dead rather than fading it. If you want the tail, release the note first,
> then remove.

## 5.7 Bypass and enable

Bypass is not removal. It leaves the module in place, in the chain, with all
its settings, and it does the sensible thing per kind (REFERENCE §3.4):

- **A source** pauses. It goes silent and keeps its state; unpausing picks up
  where it left off.
- **An effect** is replaced with a true passthrough. Signal keeps flowing
  through the chain unprocessed — the stages downstream still get audio.
- **A dual** takes the effect path, which is correct in both of its modes: in
  FX mode it passes its input through; in generate mode nothing is wired in,
  so it passes silence.

Re-enabling restores the real module and re-attaches any LFO mappings it had.

[FIG-05-06]

**Drone is the special case worth remembering:** it has no gate, so it sounds
for as long as its node exists. Bypass *is* its off switch. See §13.3.

## 5.8 The master section

[FIG-05-07]

The master section sits after everything else, operating in place on the
hardware output bus. It is never part of your chain and cannot be rewired
around (REFERENCE §3.5).

Three things happen there. Volume is applied with a short lag, so moving the
fader does not click. A **limiter at 0.95** catches everything above it —
this is why feedback, stacked looper takes and extreme LFO ranges do not
scream at you. And the stereo peak levels are measured and sent to the meters
you see in the interface.

The limiter is a safety net, not a mix tool. If it is engaging constantly,
turn something down upstream.

## 5.9 Hot reload

**Edit a module's source file, save it, and hear the change without stopping
the audio.** This is the centre of the whole workflow: you can keep playing
while you rewrite the thing you are playing.

Saving a file in `modules/` recompiles it and swaps every running instance of
that type in place. Your settings are preserved; params the new version adds
come up at their defaults. Nothing is respawned, no wires are lost, and the
transport keeps running (REFERENCE §3.7).

If the file does not compile, the error prints and **the old version keeps
running.** A typo costs you nothing but a message — you fix it and save
again. Disabled effects adopt the new version when you re-enable them;
disabled sources stay paused. A module that isn't in the current patch just
updates quietly in the palette.

> **TRY THIS.** With a patch playing, open the module file for something you
> can hear, change one constant — a filter frequency, a detune amount — and
> save. The change lands in about a quarter of a second, mid-note. That
> round-trip is the authoring loop; §15.7 walks it properly.

---
