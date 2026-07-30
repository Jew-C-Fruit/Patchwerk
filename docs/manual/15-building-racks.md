# 15. Building racks

Everything Chapters 3–14 taught separately, used together to build one real
playable rack — and then saved, rewired and edited without stopping the sound.

---

## 15.1 From nothing to a note

Nothing ships empty, so start from `demo` — the smallest shipped rack, a
**Wobble Saw** into a **Low-pass Filter** into an **Echo** (REFERENCE §12.1) —
and add to it. Four moves get a new source from the palette to your fingers.

1. **Drag a source out of the palette.** It spawns *parked*: alive, holding its
   settings, wired to nothing, output sitting on the null bus. It makes no
   sound yet, and that is correct (REFERENCE §3.3).
2. **Wire its output to something.** Drop it on **Master Out** to hear it raw,
   or on an effect's input to run it through the chain.
3. **Aim a voice at it.** The permanent `voice` aims itself at a playable
   source as soon as one exists — including reviving a voice that went dead for
   lack of a target. If it picked the wrong card, cycle the voice's `target`
   chip or drop the voice's drive output straight on the card you want.
4. **Play.** Notes reach it along the default control plane (wires labelled
   **notes**): `keys` into `arp` into `voice`. Nothing needs the transport — a
   fresh launch comes up stopped and the rig is still hand-playable.

[FIG-15-01]

If you get silence, work §4.6's backwards trace: is the source wired out, is
the voice aimed at it, does the notes path reach `keys`.

## 15.2 Adding effects

An effect from the palette lands the same way a source does — parked, doing
nothing to anything. It is not inserted into your chain automatically, and
nothing goes quiet while you decide where it belongs.

Two ways to put it in. Wire it by hand: run the upstream module's output into
the effect's input, then the effect's output onward. Or **splice** it: drag the
mid-wire tag of an existing wire onto the new card and Patchwerk breaks the
wire and threads the effect through it (§3.5.3). Splicing is the one to reach
for on a running rack — one gesture, and the chain never opens.

Moving an effect later is the same job in reverse, because audio order is
derived from the wires and not from the chain list or from where the card sits
(§5.5). Redraw two wires and the order follows.

[FIG-15-02]

> **TRY THIS.** Instead of adding, **swap**. An Instrument card can be changed
> to a different module type in place: same instance id, same wires, same
> position. Params that share a name carry over and the rest reset, anything
> that generates comes up silent until the next note, and a swap cannot cross
> kinds — you cannot turn a source into an effect (REFERENCE §3.3).

## 15.3 Rewiring a live rack

You rewire while it plays. That is the normal way to work here, not a stunt.

Audio wires take effect as you drop them. Each source has **one** outgoing
audio wire, so dragging its output to a new input replaces the old wire rather
than adding a second — there is no disconnect step. Notes wires are resolved
live on every event, so a wire drawn mid-phrase applies on the very next note
(REFERENCE §4.2). Nothing is rebuilt and the transport does not stutter.

Cut a wire and the module it fed does not die — its output parks on the null
bus, still running, still holding its settings, so reconnecting is instant and
gapless (§5.4). Remove the module itself and the chain **splice-heals** over
the gap instead of leaving a hole (§5.6). A wire that would close a loop is
refused at the drop.

[FIG-15-03]

> **WARN.** Rewiring is safe; *removing* is the one that bites mid-take.
> Pulling out a module that is currently sounding cuts its release tail dead,
> and re-adding it does not undo the splice-heal. Release the note first.

## 15.4 Parallel paths

Buses sum, so fan-in is free (§5.3). Point two finished chains at **Master
Out** and you have a parallel rack: a pad through reverb on one path, a plucked
line through echo on the other, both arriving at the same place with no mixer
to configure.

[FIG-15-04]

The trap is aiming a source at *another source* to build the split. That wire
sums into the running bus rather than opening a second path — deliberately, so
the generators upstream stay alive (§5.3). For a genuinely parallel path, give
each chain its own route to **Master Out**. If enough of them arrive that the
card's handles run out of edge, see §16.5; the card is not broken.

## 15.5 A worked rack, end to end

One performance rack, built in order, using every plane. Follow it on the
instrument rather than reading it — each step is one gesture.

**The audio.** Start on `demo`. Add a second playable source — an **FM
Bell** — and wire it to **Master Out** on its own path, leaving the Wobble Saw
running through the Low-pass Filter and Echo. Splice a **Reverb** into the
saw's path after the Echo by dragging that wire's tag onto the Reverb card.
Two paths, one destination.

**The notes.** The default wiring already gives you `keys → arp → voice` and
`arp → deck`, with `voice` aimed at the saw. Spawn a second voice from the
palette's `allocation` section and drop its drive output on the FM Bell, so the
two sources are played by two voices. Then cut the default `deck → voice` wire
and draw `deck → voice.2` instead: whatever the deck replays plays the bell,
while your hands stay on the saw.

**The modulation.** Spawn an **LFO**, set it slow, and drag its output onto the
Low-pass Filter's `cutoff` handle. The slider starts riding the oscillation
around wherever you left it — each destination orbits its own value, so set the
cutoff where you want the centre of the sweep.

**The binary.** Spawn two **Button** cards and set both to latch. Wire one to
the Echo's power input and the other to `transport:run`. Now one press mutes
the delay and another starts and stops the clock, and both cards' own
indicators move with them because indicators react to logic exactly as they
react to clicks (§3.9).

**The transport.** Press play, arm the deck with the spacebar, and record a
phrase into it on the beat. It replays into `voice.2`, so the bell carries the
loop while you play over it.

[FIG-15-05]

Now read your own rack back using §4.6's procedure — audio backwards from
**Master Out**, notes forwards from **Keys**, then binary, then modulation.
Every wire kind in Chapter 4's legend appears in it at least once, which is why
this is the rack worth being able to narrate out loud.

## 15.6 Saving your work

Three separate things persist, they own different halves of your rack, and
conflating them is the most expensive misunderstanding in this manual.

| | Lives in | Holds | How it is written |
| --- | --- | --- | --- |
| **Patch file** | `patches/*.py` | the chain and the note bindings | you write it, by hand, in the repo |
| **Preset** | `presets/*.json` | everything performable | the top bar's **save** button |
| **Resume** | `.resume.json` | a preset *plus the graph* | automatically, by the ⟳ restart button |

A **patch file** is plain Python data: module types in order, plus the MIDI
bindings. It is version-controlled and shareable, and it is the thing you send
a friend. **A patch file cannot contain a wire** (REFERENCE §12.1). Selecting a
patch from the top-bar picker is a full rebuild and resets the notes wiring
back to default, so do it between pieces, not during one.

A **preset** snapshots everything you can perform: every instance's params and
enabled state, master volume, transport settings, and every control node on the
board (REFERENCE §12.2). What it does **not** hold is the audio graph, and it
deliberately carries no play state — so loading one mid-performance can neither
start nor stop the rig.

The **resume file** is the one that keeps your wiring. Pressing ⟳ writes a
preset snapshot *plus* the graph — audio wires, notes wires, voice targets,
drums routing — re-execs the server, and replays all of it on boot before
deleting the file (REFERENCE §12.3). Unlike a preset, it does carry play state,
applied last so the rebuilt rack is finished before it starts.

[FIG-15-06]

> **WARN — your layout is in none of the three.** Where the cards sit is
> client-side: stored per patch in your browser's local storage and never sent
> to the server (§3.7). Save a preset, open it on another machine, and you get
> the same instrument with a completely different board. Nothing is lost — the
> arrangement simply was never part of the save.

## 15.7 Editing a module while you play

[FIG-15-07]

Every module is a small Python file in `modules/`, and you can rewrite one
while it is sounding. Save the file and Patchwerk recompiles it and swaps every
running instance of that type in place — settings preserved, wires intact,
transport running, params the new version adds arriving at their defaults
(§5.9, REFERENCE §3.7). The round trip from save to audible is about a quarter
of a second.

**A file that does not compile keeps the old sound playing.** The error prints
and the running version stays exactly as it was; you fix the typo and save
again. You would expect a broken save to drop you into silence, and it does
not — that is the whole reason this loop is usable on stage.

What a module file must contain — the `@module` block, params, kinds, the
synthdef contract — is documented in `CLAUDE.md`, which changes with the code.

## 15.8 Habits that keep a rack workable

- **Name by intent when you spawn.** You cannot rename an instance id, but you
  can decide the order you add things, and `lowpass` versus `lowpass.2` is
  easier to hold in your head if the first one is the one you use most.
- **Leave a monitor wired where you are debugging.** Wired, it shows one path;
  unwired, it shows everything and tells you far less (§3.8).
- **Tidy before you save a preset**, then save. The preset does not carry the
  layout, but you will be looking at this board again in a minute.
- **Press ⟳ rather than restarting by hand.** It is the only route that brings
  your wiring back with it.
- **Panic is not a failure.** It is a control. Use it, then carry on — it does
  not stop the transport and it does not touch your patch (§3.10).

---
