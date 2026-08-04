# 1. Getting started

What Patchwerk is, how its three pieces fit together, and the shortest path
from a fresh launch to a note you can hear.

---

## 1.1 What Patchwerk is

Patchwerk is a modular synthesiser you play. You patch it in a browser —
dragging wires between cards on a grid — and you play it from a MIDI
controller or from your computer keyboard. Behind the page, a Python control
plane drives SuperCollider's `scsynth` audio server, and every module that
makes a sound is a small Python file you can rewrite while the instrument is
running.

`scsynth` is a **separate operating-system process**, so nothing Python does
can glitch audio that is already playing. Save a broken module and Patchwerk
prints the error and keeps the old sound going (REFERENCE §1, §3.7).

**v2.2 "Polyphony"** is the release this manual describes, and it is named for
what it adds: an allocation framework where mono, poly and drone are three
policies on one card, sounding up to sixteen notes at once (Chapter 6). It also
brings a whole binary plane — buttons, clocks, thresholds, logic gates and
relays, so a patch can switch and gate itself while you play it (Chapter 7) —
and a third kind of module that generates or processes depending on what you
wire into it (§14.8).

The interface it inherits is grid-native: cards snap to blocks and wires route
through the gutters like a subway map. So is the **psine** oscillator family —
one knob that morphs a pure sine into a square, built three different ways
(REFERENCE §10.4, and Chapter 12).

[FIG-01-01]

## 1.2 The shape of the system in one picture

Three layers. **scsynth** makes the sound. The **control plane** — Python —
owns the modules, the rack they load into, and the wires labelled **notes**,
where your keyboard, your MIDI controller and the players (arpeggiator, loop
deck, key shifters) all meet. The **page** is a view onto both.

The page is never the authority: everything it draws comes from the server's
state, and everything you do on it is a message back. Save a module file and
the change loops straight into the running rack without stopping the sound.
REFERENCE §1 has the layer table and §1.3 maps the layers onto files.

[FIG-01-02]

## 1.3 What you need

- **A Mac** running macOS 15.5 or thereabouts.
- **Python 3.10 or newer.**
- **SuperCollider**, installed as an application.
- **Headphones or speakers**, ideally wired. Bluetooth output adds enough
  latency to make playing feel wrong.
- **Chrome.** That is what the interface is developed and tested against.
- **Optional: a MIDI controller.** Without one you play from the computer
  keyboard, which is enough to get started.

Chapter 2 turns this list into a working machine. Do that first.

## 1.4 Your first sound

Five steps, from a set-up machine to a note.

1. In the repo, run `./run.sh`. It launches the GUI on the `pad_space`
   patch and opens a browser at `http://127.0.0.1:8765`.
2. You are looking at a chain of cards: **PW Pulse Pad** into **Drive**,
   **Low-pass Filter**, **Echo**, **Reverb** and **Auto Pan**, ending at
   **Master Out**.
3. Check that **keys** in the top bar is ticked. It is on by default.
4. Press `a`, `s`, `d`, `f`, `g` — a white-key run from C4. The black keys
   sit above them on `w`, `e`, `t`, `y`, `u`. Press `z` or `x` to drop or
   raise the octave.
5. Hold a note and drag the **Low-pass Filter** card's `cutoff` slider. That
   is the instrument responding live; nothing was rebuilt.

Your key presses reach the pad along the default control-plane wiring —
`keys` into `arp` into `voice`, and the voice drives the Pulse Pad's pitch
and gate (REFERENCE §4.1, §4.3).

> **WARN.** The spacebar is not a stop button. It arms the Loop Deck's
> record, in every input mode. Caps Lock is the sustain pedal.

[FIG-01-03]

## 1.5 Where to go next

Three routes, depending on what you came for.

- **I want to play.** Chapter 3 maps the interface, then Chapter 15 builds a
  performance rack from a blank patch.
- **I want to understand the routing.** Chapter 4 explains the four signal
  planes and why what is wired is what plays.
- **I want to know what each module does.** Chapter 11 explains how to read a
  module page; Chapters 12–14 are the catalogue.

## 1.6 How to read this manual

This manual is pinned to **v2.2 "Polyphony"** and is rewritten at every
release.

Alongside it sits `docs/REFERENCE.md`, a code-verified document that owns the
facts — every parameter range, every endpoint, every protocol message. This
manual cites it rather than restating it, like this: REFERENCE §4.6. **If the
two disagree, REFERENCE wins and this manual has a bug.**

Two typographic conventions run throughout. **Bold** names something you can
see and touch on screen — a card, a button, a control. `Monospace` names
something you type or something the code calls its own: a module key, a
command, a parameter, an instance id.

---
