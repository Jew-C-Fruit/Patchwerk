# 8. Modulation

Making parameters move by themselves: the LFOs that do the moving, the
thresholds that turn a moving value back into a decision, and the scope you
use to look at any of it.

---

## 8.1 LFOs

[FIG-08-01]

An LFO here is not a setting buried inside a module. It is a **node on the
canvas** — `lfo`, `lfo.2`, `lfo.3` — with an output handle, and you wire it
to whatever you want to move.

One LFO writes one signal, and every destination you wire scales that signal
into its own parameter's range and curve. **Fan-out is unlimited.** A single
LFO can drive a filter cutoff, a delay mix and an oscillator's shape at
once, all locked to the same phase.

There is **no centre knob**. Each destination orbits *its own slider value* —
the same value the slider would have set if nothing were wired to it. Move a
modulated parameter's slider and you are steering that destination's centre;
cut the wire and the knob stays exactly where you last put it rather than
snapping somewhere you never chose.

Parameters are **single-input**: wiring an LFO to a parameter another LFO
already owns steals it. An LFO can also reach a parameter *through* a relay
(§7.5) — closing the circuit maps the parameter, opening it unmaps and the
parameter settles back onto its own knob value. REFERENCE §6.1.

### 8.1.1 Routing an LFO to a parameter

Drag from the LFO card's output handle to the **param handle** on the row of
the parameter you want to move. Every modulatable parameter has one.

Where a handle physically cannot sit on its row — a crowded card, a row too
short to hold it — it grows a short **on-card link wire** running from the
card edge into the row instead. That stub is drawn for you, not something
you patched, and it means the same thing as a handle sitting flush: this row
is the endpoint.

> **WARN.** Cutting a mod wire does **not** delete the LFO. The node stays
> on the canvas with its rate, depth and shape intact, ready for the next
> destination. You are removing a route, not a module. To get rid of the LFO
> itself, remove the card.

[FIG-08-02]

### 8.1.2 Shapes, rate, depth

Three settings: `rate`, `depth` and `shape`.

`depth` is the swing, in normalised terms — a depth of 0.25 means the signal
moves between −0.25 and +0.25 rather than over the full range. That number
matters later, because thresholds read it (§8.2).

- `sine` — the default motion. Smooth, no corners. Vibrato, filter drift.
- `tri` — even travel up and down, which reads as more mechanical than sine
  on a slow sweep.
- `ramp` — a saw. Rises and resets. For anything that should feel like it is
  *falling* into place each cycle.
- `square` — two values and nothing between. An alternation, not a sweep:
  use it to flip a parameter between two settings in time.
- `s&h` — sample and hold. A new random value each cycle, for variation
  rather than motion.

[FIG-08-03]

### 8.1.3 Reading the LFO visualisation

**A modulated parameter shows you the modulation.** The slider
rides the actual oscillation rather than sitting still at its centre, the
numeric readout rolls with it, and the track picks up an amplitude band
showing the range being swept plus a centre marker at the value your knob is
steering. All five shapes animate — a square LFO makes the slider snap
between two positions, `s&h` makes it hop.

Use it as your confirmation that a route is live. If you drew a wire and the
slider is not moving, the route is not reaching the parameter: check depth
first, then check whether another LFO stole the destination.

The morphing extends into the cards that draw waveforms. Modulate `p` on any
psine module and that card's waveform preview morphs live as the value
sweeps — sine bending toward square and back.

## 8.2 Thresholds — turning values into decisions

A threshold takes an LFO's continuous value in and puts a binary signal out.
It is the one crossing point between the two planes. This section is what it
watches; §7.6 is what comes out.

Wire an LFO into the threshold's CV input. It is single-input, so re-wiring
replaces the source rather than adding one, and removing the source LFO
unwires it. Then set `level` — the value the LFO has to cross.

The trap is that `level` is in the LFO's **normalised terms, after depth**.
An LFO at depth 0.25 only ever reaches ±0.25, so a level of 0.0 gets crossed
twice per cycle while a level of 0.3 is never reached at all. **A threshold
that seems dead is usually watching an LFO whose depth cannot get there** —
check depth before anything else.

`hysteresis` (0–0.5, default 0.02) is the gap that stops it chattering.
Without a gap, a signal wandering right at the level flips the output
hi/lo/hi/lo on every wobble. So the threshold uses two levels, one either
side of the one you set: at the default it switches hi at `level + 0.02` and
will not switch back until the signal falls to `level − 0.02`. Raise it if a
slow crossing chatters — and lower it if a shallow LFO stops firing
altogether, because a signal that cannot travel the full gap can never come
back.

On spawn or after a settings change the threshold arms a short window that
swallows the phantom first falling edge, so one you have just placed will
not fire once for no reason. REFERENCE §6.2.

[FIG-08-04]

## 8.3 The scope

[FIG-08-05]

Two things share the word "scope".

The **oscilloscope** is a card. It draws the waveform of whatever it is
watching, and it follows the monitor doctrine from §3.8: wired, it shows
that path; unwired, it shows the master feed. The subtitle tells you which.

The **Scope Tap** is a module — a transparent inline probe. Splice it
anywhere in an audio chain and it passes signal through unchanged, but the
scope can now draw that exact point. Use it when the place you want to look
at is in the middle of a chain rather than at a module's output. Its only
parameter is `gain`, which scales what the trace shows.

Each watched bus records continuously, so a capture reads frames that are
already there rather than starting a recording, and note events kick an
immediate poll. The trace tracks your playing rather than reporting on it
afterwards. Leave a card alone for a few seconds and its buffer is reaped,
so the first capture after a pause costs a little more than the rest.
Rewiring a module rebuilds its buffer. REFERENCE §6.3.

## 8.4 Modulation and rebuilds

Changing audio devices reboots the engine. You will hear a brief silence;
your patch and your master volume come back as they were.

What to check afterwards is the modulation. LFO cards survive with their
rate, depth and shape intact, and so do your thresholds — but a rebuild
drops the **destination end** of the routes. Relay-routed modulation keeps
its circuits and loses the parameter ends the same way. So after a device
change, glance at the parameters you had moving: a slider that should be
riding and is sitting still (§8.1.3) needs its mod wire redrawn.

Unmapped parameters settle back onto their own knob values rather than
freezing wherever the LFO left them, so the redraw is safe to do at any
point. REFERENCE §3.6, §6.1.

---
