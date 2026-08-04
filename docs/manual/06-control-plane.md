# 6. The control plane — note routing

Who plays whom. The audio graph decides what a sound passes through; this
plane decides what makes a sound at all. Everything here is the control
plane — the wires labelled **notes**.

---

## 6.1 The node vocabulary

| Node | What it is | Source | Destination |
| --- | --- | --- | --- |
| `keys` | where every controller enters | yes | **never** |
| `arp` | the arpeggiator's note pool | yes | yes |
| `deck` | the MIDI Loop Deck | yes | yes |
| `voice`, `voice.2`, … | mono allocation — one note at a time | no (drives audio) | yes |
| `poly`, `poly.2`, … | poly allocation — N notes at once | no (drives audio) | yes |
| `hold`, `hold.2`, … | **Drone Voice** allocation — holds the last root | no (drives audio) | yes |
| `tonic.N`, `literal.N` | derivers — notes in, one derived note out | yes | yes |
| `drone`, `drone.2`, … | drone **module** instances, retargeted by note | no | yes |
| `keyshift.N:1`…`:4` | key shifters — four isolated lanes | per lane | per lane |
| `relay.N:1`…`:9` | relay circuits — a switched junction | per circuit | per circuit |

An id with a colon addresses a sub-endpoint: `keyshift.2:3` is lane 3 of the
second key shifter, `relay:5` is circuit 5. REFERENCE §4.1 is the complete
vocabulary and the rules for what may wire to what.

Two ids catch people out. A Drone Voice's id type is `hold`, not `drone` —
`drone` belongs to the drone *module*, a different node with a different job
(§6.5). And self-wires are refused, so you cannot cross-patch lane 1 of a
shifter into lane 2 of the same shifter.

A fresh patch comes up wired `keys→arp`, `arp→voice`, `arp→deck`,
`deck→voice`.

[FIG-06-01]

## 6.2 Keys: everything enters here

[FIG-06-02]

Every controller lands at `keys` — the on-screen keyboard and any hardware
MIDI the router sees. There is one `keys` node, it is a source and **never**
a destination, and nothing can wire into it. To feed something into the main
note path, wire it to whatever `keys` feeds (usually `arp`).

## 6.3 Voices

**Allocation is new in v2.2 and it is the biggest change on this plane.** A
playable source module makes the sound; an *allocation* decides which notes
get to use it. There are three policies, and they differ on **one axis
only**: how many notes may sound at once, what a new note displaces when
they run out, and what happens when you let go.

| Allocation | id | Notes at once | When they run out | Releasing the last note |
| --- | --- | --- | --- | --- |
| **Mono Voice** | `voice` | 1 | the new note takes it | closes the gate |
| **Poly Voice** | `poly` | N (1–16) | the **oldest** is stolen | closes the gate |
| **Drone Voice** | `hold` | 1 | the new note takes it | **holds the last root** |

Everything else is shared, so once you can drive one card you can drive all
three.

Mono has last-note priority in both directions: release the sounding note
and it falls back to the newest note you are still holding, pitch only, no
retrigger. Poly's steal closes the gate and reopens it about 12 ms later, so
the incoming note gets its own attack instead of inheriting the envelope of
the note it replaced. The Drone Voice never gates from the note stream at
all — its off switch is POWER, a separate binary axis (§7.3), and you hear
it only when POWER is on *and* the transport is running.

**Spawn allocations from the palette section headed `allocation`.** The
section headed `voices` holds the playable source *modules*, the things that
make the sound. Mixing the two up is the easiest mistake in this chapter.

Mono and Poly share one card: a `target` chip cycling the playable sources,
a static `±2 st` bend chip, and — poly only — a 1–16 step slider labelled
**"N notes"**. Shrinking that slider closes whatever was sounding on the
voices that go away. There is deliberately no "voices sounding" meter; do
not go looking for one. REFERENCE §13.1.

> **WARN.** Extra voices spawn aimed at the first playable source and arrive
> **unwired**. Spawn one and nothing happens, because nothing is feeding
> it — drag a wire from `keys`, `arp` or `deck` into it before you conclude
> it is broken.

An allocation leases voices from the target rather than owning a synth,
which has two visible consequences. Every parameter except pitch and gate is
mirrored across all of a poly voice's notes, so one card really is driving N
voices at once. And two mono voices aimed at the same source now sound as
two distinct voices instead of fighting over one.

The default `voice` allocation cannot be removed; every one you spawn
afterwards, of any policy, removes the same way. REFERENCE §4.3, §4.3.1
(poly) and §4.3.2 (drone).

[FIG-06-03]

## 6.4 The arpeggiator

`arp` sits in the note path — the default patch runs `keys→arp→voice`.
Disabled, notes pass straight through. Enabled, the notes you hold join a
pool and the arp steps a pattern over it, locked to the transport.

Set `division` from the shared ladder (§9.2), `gate` (0.05–1.0, the fraction
of each step that sounds, default 0.6), `octaves` (1–3) and `pattern`
(`up | down | updown | random | played`).

For `up`, `down` and `updown` the position is remembered in *pitch*, not as
an index: the next note is the nearest chord tone above or below the last
one played, judged against the pool as it stands now. Change chord
mid-stream and the line walks on from where it was rather than restarting at
the bottom.

With the arp enabled the sustain pedal becomes a **latch** — released notes
stay in the pool until you lift the pedal. Stop the transport and the arp
parks, with live notes still passing through. Emptying the pool or disabling
the arp closes its notes downstream, so nothing hangs. REFERENCE §4.4.

[FIG-06-04]

## 6.5 Tonic derivers

[FIG-06-05]

A deriver listens to a note stream and emits **one** note: a root. There are
two.

**The estimator** (`tonic`) works out what key you are actually in, weighing
what you have played recently against scale templates and snapping to the
most likely root for the notes you are holding at each commit. Controls:
`every` (how often it commits — `1 beat` through `4 bars`, plus `deck`;
default `1 bar`), `memory` (how long evidence survives, 1–30 s), `listening`
(`triadic | root+fifth | chromatic`) and `octave` (0–4). Set `every` to
`deck` and it commits on a recorded phrase's chord boundaries instead
(§10.4).

**The literal deriver** (`literal`) does no thinking and has no lag. Pick
which note to `extract` (`lowest-held | highest-held | last-played |
first-played`), then how to `place` it (`absolute` keeps the octave, `fold`
re-voices it into a chosen octave, `transpose` shifts it ±24 semitones). It
commits on every note event.

A deriver's output is an ordinary note source — wire it anywhere notes are
accepted. Feeding a Drone Voice or a drone module is the usual reason to
have one. The output is strictly mono: the old note is released before the
new one sounds, so two roots can never ring at once.

Both derivers take a **trigger override**. Wire any binary source into the
deriver's bare id and its internal timer stands down, committing on each
rising edge instead; unwire it and the timer resumes. REFERENCE §4.5.

## 6.6 Key shifters

[FIG-06-06]

A key shifter transposes a note stream into a different key. The offset is
the distance from C to the chosen key mapped to the nearest shift, so it
always moves six semitones or fewer rather than leaping an octave.

Each shifter has **four isolated lanes**, addressed `"<id>:<lane>"` with
lane 1–4. Lane 3 in goes to lane 3 out and nowhere else: one wire in, one
wire out, no interaction with the other three. So one shifter can carry a
melody, a bass line and a deriver's root through the same key change without
merging them, and a progression moves all four together from one card.

The **progression track** is a timeline of up to 32 bars. Set a `length`,
then set steps to a key or leave them empty to hold the previous one. As
soon as any step is set, the active key follows the bar count and changes on
beat 0 with the transport. Leave the track empty and the shifter sits on its
static `key`.

One thing it handles for you: a note-off is shifted by the same amount its
note-on was, even if the key changed while you were holding it. Without
that, a progression move would leave notes hanging. REFERENCE §4.6.

[FIG-06-07]

## 6.7 The globals on the note path

Transpose, bend and sustain are **global** — they act on everything
regardless of how you have wired this plane (§4.4).

- **Transpose** is ±24 semitones, applied to every allocation. It is a
  standing key change: it redefines what a note number means until you move
  it back.
- **Bend** is ±2 semitones and applies to every voice.
- **Sustain** (the pedal, or CC 64) latches the arp pool and holds voices —
  with one exception: a voice fed *only* by the enabled arp is not held,
  because the arp's own latch already carries the stream (§9.6).
- **Panic** closes every open note on the plane at once — keys, arp, voices,
  derivers, shifters.

The Drone Voice breaks the pattern twice. It **follows transpose** — new in
v2.2, and it changes existing patches: a saved patch with a non-zero
transpose used to put the drone exactly that many semitones away from
everything else, and now it does not. If an old patch's drone has moved,
that is why. It **ignores bend**, permanently and by design: a drone is the
fixed reference you play against, and one that bent with the melody would
leave the wheel doing nothing audible in every patch that has a drone in it.
REFERENCE §4.8, §4.3.2.

## 6.8 Watching notes flow

When a patch is silent, put a **Note Monitor** on the wire and look. A
monitor riding a wire shows only that path's traffic; an unwired one shows
every note node at once. That gives you a binary search.

1. Drop an unwired monitor first and play. Bars moving means notes are
   entering at `keys`; nothing moving means the problem is the controller,
   not the patch.
2. Wire the monitor onto the first hop (`keys→arp`), then walk it downstream
   one wire at a time.
3. The first hop where the bars stop is the break. Traffic arriving at a
   node but not leaving it means the node is doing something — arp disabled,
   deriver waiting for a commit, relay circuit open.
4. Traffic reaching an allocation with no sound means the trouble is past
   this plane: check the allocation's target, then the audio graph out of
   that source.

A bar that goes full width and never comes back down is a stuck note — a
note-on that never got its off. Hit panic to clear it.

[FIG-06-08]

## 6.9 Unwired events dead-end

Nothing on this plane is implied: a node with no outgoing wire fires its
events into nothing, silently, with no error and no fallback path (§4.2).

The beginner failure is a source module with no allocation aimed at it. It
looks completely healthy — the card is there, it is not bypassed, the audio
wire runs to master — and it is silent forever. **The five-second check:
find the allocation cards and read their `target` chips.** If none of them
names your source, nothing is playing it.

Removing wires has three behaviours worth expecting. Unhooking a node's
*last* input silences it, because a dropped note beats a stuck one. Removing
a deriver or a key shifter from the middle of a chain **heals the gap** —
A→X→B becomes A→B — but only when there is exactly one wire in and one out,
so the repair is unambiguous; readers expect a hole, not a repair. Removing
a voice or a relay drops every wire that touched it. REFERENCE §4.1, §4.2.

---
