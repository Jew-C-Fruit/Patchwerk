# 4. Signal planes — the logic system

Every wire you can draw belongs to one of four planes, and a wire's plane
decides what it carries and what it is allowed to touch. This chapter is the
map of all four; Chapters 5–8 take one plane each.

---

## 4.1 Four kinds of wire, one canvas

**Audio** carries sound. Audio is stereo everywhere between stages, and an
audio wire means one thing only: this stage's output is fed into that stage's
input. Its endpoints are module instances, the **Master Out** card, and relay
circuits. Chapter 5 owns it.

**Control** — the plane whose wires are labelled **notes** in the interface —
carries note events: who plays whom. A notes wire means the destination hears
every note-on, note-off, sustain and bend the source emits, and it is
resolved live, so a wire you draw mid-performance applies on the very next
note. Chapter 6 owns it.

**Binary** carries one hi/lo signal. Sources own a level; edges are derived
from level changes, which is why a pulse can travel through a logic gate
while the gate's other leg is held hi. A binary wire means either "this
destination's state follows that level" or "this destination fires once on a
rising edge", depending on the endpoint. Chapter 7 owns it, and the complete
list of legal destinations is REFERENCE §5.3 — it is exhaustive and
code-verified, so go there rather than trusting memory.

**Modulation** carries a continuous value into a parameter. An LFO writes one
normalised signal and fans out to as many params as you like; each
destination orbits its own slider value rather than a shared centre.
Chapter 8 owns it.

[FIG-04-01]

**Colour is the code.** Every wire on the board is drawn **solid**, in one of
four colours, and that colour is the only thing that tells the planes apart.
There is no stroke pattern anywhere in the wire layer — a notes wire and an
audio wire differ by hue and nothing else, so do not go looking for dashes or
dots to confirm what you are seeing. If you are unsure what a wire is, hover
its handle: the label names the kind and the target in words.

A wire that is currently passing signal gets one extra thing: a **moving
white overlay** running along it, on top of the wire's own colour. That is
the only animation in the wire layer, and it means *this path is live right
now*. It never changes what plane the wire belongs to, so a live notes wire
is still green under the white — read the hue, not the motion. The overlay is
also the fastest way to find a dead branch: hold a note and look for the
audio wire that is not moving.

[FIG-04-05]

[FIG-04-02]

| Plane | GUI colour | The word | Carries | Owned by |
| --- | --- | --- | --- | --- |
| Audio | `#3987e5` | `audio` | stereo sound between module stages, and out to hardware | Ch 5 |
| Control | `#1baf7a` | **`notes`** | note-on/off, sustain, bend — who plays whom | Ch 6 |
| Binary | `#e6c34a` | `binary` | one hi/lo level; edges derived from level changes | Ch 7 |
| Modulation | `#9085e9` | `mod` | a continuous value into a param's range | Ch 8 |

The four wire systems and their endpoint grammars are REFERENCE §2.3. `ctl`
is the code-facing name for the notes plane; you will meet it in file names
and error text, never on a handle.

## 4.2 The graph *is* the routing

Nothing is implicitly connected. A source with no wire out of it is not
quietly falling through to the master bus; a note source with no wire out of
it is not quietly reaching your voices. An unwired event dead-ends, silently,
with no fallback and no default destination (REFERENCE §4.1).

So the picture on the canvas is the complete truth about signal flow. There
is no second, invisible routing layer to reason about when something
misbehaves.

> **WARN.** You will meet this as "why is nothing happening" at least once,
> probably in your first hour. The answer is almost always a missing wire
> rather than a broken module. Trace backwards from the thing that is silent:
> is its input wired, is the thing feeding it wired, and does that chain
> reach all the way back to **Keys**?

## 4.3 Instance ids: `lowpass` vs `lowpass.2`

Every module and every control node can be spawned as many times as you
like. Each one gets an **instance id** — `lowpass`, `lowpass.2`,
`lowpass.3` — and that id is its identity. The **type** is the part before
the dot, so `lowpass.2` is an instance of type `lowpass`. Freed suffixes get
reused, so removing `lowpass.2` and adding another low-pass gives you
`lowpass.2` again, not `lowpass.4`.

The same scheme names every spawnable control node: `voice`, `voice.2`,
`tonic.2`, `keyshift.2`, `lfo`, `button`, `clock`, `logic`, `relay`. You will
see ids in wire labels, in endpoint names like `keyshift.2:3`, and in saved
patch files. Cards are keyed by instance id, never by type — see
REFERENCE §2.1.

## 4.4 What is global and what is wired

| | |
| --- | --- |
| **Global** — never wired, applies everywhere | transport and clock (one shared timeline), panic, the sustain pedal, pitch bend, transpose (±24 semitones), master volume, audio device configuration, saving and loading |
| **Wired** — defined entirely by the graph | all audio routing, all note routing, all binary routing, all modulation |

[FIG-04-03]

One rule predicts the split: **a global is either a physical gesture on the
instrument or a single shared timeline.** When you stamp the sustain pedal
you mean *all of it*, not the branch of the patch you happened to wire the
pedal into; the same goes for panic, bend and transpose. Master volume and
device configuration are properties of the box, not of the patch. Everything
else — who hears whom — is the graph's business (REFERENCE §2.4).

## 4.5 The closure doctrine

Every path that silences something must emit its note-offs. Not just the
obvious ones: panic, stopping or disabling the arp, stopping the deck,
leaving a record window, removing a wire, removing a node, and any rebuild
all close their notes on the way out — and so do the monitor taps riding
those wires (REFERENCE §2.5).

A note-on with no matching note-off is a stuck note downstream, and it is
*also* a stuck full-width bar on every note monitor watching that path. The
bar is not decoration; it is the same event, drawn.

[FIG-04-04]

So: **a monitor bar that will not clear is a real bug, not a display
glitch.** Hit panic to recover, then report it — with what you were doing
when it stuck.

## 4.6 Reading a patch

Reading a patch is a fixed procedure, and doing it in this order stops you
guessing. Work across `[FIG-04-01]` — the same picture as §4.1 — one plane
at a time.

1. **Audio, backwards.** Start at **Master Out** and walk against the arrows
   along the blue wires. Each one is one stage feeding the next; when you run
   out of wires you are standing on whatever generates. Anything you never
   reached is not being heard.
2. **Notes, forwards.** Start at **Keys**, because that is where every
   controller enters, and follow the green wires. Each one ends at something
   that consumes notes — a voice, the arp, the deck, a deriver, a key shifter
   lane. A voice is where the notes plane hands over to the audio plane: it
   drives one source's pitch and gate.
3. **Binary.** Find the yellow sources — buttons, clocks, thresholds, logic
   outs — and read each wire as either "follows this level" or "fires on the
   rising edge", per its destination.
4. **Modulation.** Last, because it changes values rather than routing. Each
   violet wire runs from an LFO to one param handle, and tells you which knob
   is no longer sitting still.

Name each wire out loud as you go: plane, source, target. "Notes, keys to
arp. Notes, arp to voice. Audio, wobble saw to low-pass. Audio, low-pass to
master. Mod, LFO to low-pass cutoff. Binary, button to low-pass power." When
you can do that for a patch you did not build, you can read any of them.

> **TRY THIS.** Do it on someone else's patch — one of the shipped ones —
> before you do it on your own. On your own patch you remember the intent;
> on a stranger's you only have the wires, which is the skill this section
> is teaching.

---
