# 9. Transport, timing and MIDI

The clock everything rhythmic hangs off — what it drives, what stopping it
does, and how to get a controller into the instrument.

---

## 9.1 The transport is global

There is one clock. It is not wired to anything, and everything that asks
for a grid asks this one (§4.4). It has play and stop, tempo (20–300 BPM)
and meter (1–12 beats per bar).

The beat timeline is absolute: beat 0 is anchored when the instrument
starts, and a tempo change re-anchors so the beat position stays continuous
rather than jumping. `downbeat` moves which beat the click accents and
nothing else — the grid everything quantises to does not move under you.

**Tap tempo** lives on the `transport:tap` binary input. Feed it a button, a
clock, anything with an edge: taps between 0.25 and 2.0 seconds apart
(roughly 240 down to 30 BPM) average over the last four, and a tap outside
that range quietly restarts the sequence. It sets tempo only — never phase,
never whether the transport is running.

The **Play/Stop** and **Tempo** cards are views of the transport, not copies
of it. Remove them and it is still running, and its binary endpoints
`transport:run`, `transport:click`, `transport:accent` and `transport:tap`
still work.

> **WARN.** A fresh launch comes up **stopped**. Drums, clocks, the arp grid
> and drones make no sound at all until you press play, which reads as a
> dead rig. The note path is untouched by this — play the keys and you hear
> the default patch immediately.

[FIG-09-01]

## 9.2 Divisions

Anything that repeats picks its rate from one shared ladder: the arp, clock
nodes, a deriver's `every`, the deck. Learn it once and it reads the same
everywhere.

A plain fraction is its literal length in a 4/4 bar, so `1/4` is one beat and
`1/8` is half of one. A trailing **`.`** is dotted — half again as long, so
`1/4.` is a beat and a half. A trailing **`T`** is a triplet — three in the
space of two, so `1/4T` is two thirds of a beat. The ladder runs from `1/1`
(a whole four-beat bar) down to `1/32`. The exact table is REFERENCE §8.1;
you only need the `.` and `T` convention.

Everything quantises onto the absolute grid rather than starting from the
moment you clicked. Change a division mid-bar and the next event lands where
the new division says it should, in phase with everything else.

[FIG-09-02]

## 9.3 What stopping actually does

**Stops:** the beat position freezes; clock nodes stop firing, so anything
downstream of one goes quiet; the drum sequencer idles; the arp thread
parks; deriver grid timers idle, so no more commits on the `every` schedule;
the Loop Deck skips replay and releases anything it was sounding; and every
enabled drone pauses. A drone you spawn *while stopped* comes up paused too,
because drones have no gate and would otherwise sound the instant they
existed.

**Does not stop:** your keyboard and controller. Notes still play, voices
still sound, effects still process, tails still ring out. The arp passes
live notes straight through while parked, so a patch wired `keys→arp→voice`
stays playable. LFOs keep oscillating and modulated parameters keep moving;
modulation is not on the clock.

Press play and all of it resumes on the same grid — you do not lose phase by
stopping. Everything in that first list closes its notes on the way out,
monitor taps included (§4.5). REFERENCE §8.2.

## 9.4 The drum machine

Four lanes — `kick`, `snare`, `hat`, `clap` — on a 16-step grid of `1/16`
notes, which is exactly one bar of 4/4, walked on the shared transport. The
sounds are synthesised one-shots, not samples; there is nothing to load.

Each lane has its own `level` (0–1) on top of a fixed per-lane trim, so the
four are already balanced against each other by ear and the level control is
for taste rather than repair. Patterns, levels and the audio target all ride
presets, so a saved patch brings its beat with it.

That is the whole machine. It is deliberately one bar and four lanes — for
anything more elaborate, record a phrase into the Loop Deck (Chapter 10) or
drive modules from clocks and logic (§7.8). REFERENCE §8.3.

[FIG-09-03]

### 9.4.1 Where drums go

Drum hits are **aimed**, and there are three answers.

- **Master** — the default. Hits land at the end of the master bus, past
  your chains, so nothing you patch colours them.
- **A module** — pick any instance and the hits go through it. An effect
  receives them at its input, so they get that effect and everything after
  it in the chain; a source receives them on its own output bus.
- **Nowhere** — a real, choosable setting. The sequencer keeps running and
  keeps emitting its step events, but no sound is made. Use it to keep a
  pattern's timing alive while silencing it, or to build a pattern before
  you commit to hearing it.

**If a module you were aiming at is removed, the target falls back to
master.** The drums do not go silent — they reappear dry at the end of the
bus, which is easy to mistake for the effect having stopped working.
REFERENCE §3.8.

[FIG-09-04]

## 9.5 MIDI in

[FIG-09-05]

Patchwerk opens **one** MIDI input port at a time. Choosing it, and running
with MIDI off, is §2.6.

What arrives from that port:

- **Notes** land at `keys`, with velocity respected.
- **Pitch wheel** becomes global bend, ±2 semitones on every voice.
- **CC 64** is the sustain pedal (§9.6).
- **Every other CC** goes to your patch's `cc` bindings, scaled through the
  target parameter's own curve. A bound CC also moves the on-screen slider,
  so the physical knob and the virtual one stay in agreement. An unbound CC
  is surfaced rather than dropped, which is how you find out what a
  controller actually sends before you bind it.

Armed buttons intercept the CC stream ahead of all of that — that is how a
button learns a control (§7.2).

There is **no channel filtering**: whatever the port sends arrives, on any
channel. If you need a keyboard split or a layer, do it on the controller
and let Patchwerk see the result. REFERENCE §8.4.

### 9.5.1 Computer keyboard

With no controller attached the computer keyboard plays. Tick **keys** in
the top bar — it is on by default — and the layout in §2.6 applies.

Two traps, both of which look like Patchwerk faults and are not:

- **A focused control eats your keys.** If a slider or button on a card has
  keyboard focus, it takes the key presses instead of the keyboard player.
  Click empty canvas and play again.
- **On macOS, letter key-ups are swallowed while ⌘ is held.** The note-on
  arrives, you hit a ⌘ shortcut, and the key-up never reaches the page — so
  the note stays on. It reads exactly like a stuck-note bug. Hit panic
  (§3.10) to clear it, and keep ⌘ out of the way while you are playing on
  the letter keys.

## 9.6 Sustain

Sustain is global — CC 64 from a pedal, or Caps Lock on the computer
keyboard. It is not wired and applies everywhere at once (§4.4).

The one wrinkle: a voice fed *only* by the enabled arpeggiator is not
sustained directly. Instead the pedal latches the arp's pool, holding
released notes in the pattern until you lift it. Latching both would defeat
the arp's own gating, so it does one or the other and never both.
REFERENCE §4.8.

---
