# 13. Non-playable sources

The three sources that make sound without being played. None exposes the
`freq`/`gate` pair, so no voice allocation aims at them: spawn one, wire its
output, and it is already running.

Same entry template as §11.5. **Parameter ranges, defaults and curves are in
REFERENCE §10.2** — cited once, here.

---

## 13.1 Audio In — `audio_in`

[FIG-13-01]

`input` · not playable

**What it is.** Hardware input channel 1, brought into the rack and spread to
stereo. `gain` is its only control.

**Use it for.** Putting something that is not Patchwerk through Patchwerk — a
microphone, a guitar, another instrument — and then treating it like any other
source: wire it into a chain, sum it with a voice, send it to the Loop Deck.

**Handling.** It is **channel 1 only, and it is mono**: the stereo you get is
the same signal on both sides, so a stereo effect placed immediately after it
has nothing to widen. Choosing which device that channel comes from is Ch 16.
If you declined the microphone prompt at first launch, the engine runs
output-only and this module is silent — Ch 2 covers the fix.

> **WARN.** Monitoring on speakers with `gain` up and an echo or reverb
> anywhere downstream is a feedback loop, and the master limiter will keep it
> merely loud rather than stopping it. Use headphones, or bring `gain` up from
> the bottom rather than down from the top.

## 13.2 Wind — `wind`

[FIG-13-02]

`voice` family · **not** playable · M card with M/L chips

**What it is.** Pink noise through a pair of slowly drifting stereo band-pass
filters, with a slow "weather" swell over the top. `center` places the band,
`resonance` narrows it, `gust` sets how much the weather moves. It sits among
the real voices in the palette and wears their card face, but nothing can play
it — the family label groups by appearance, not capability (§11.3).

**Use it for.** Beds, room tone, weather. And the practical one: **testing an
effect chain without playing anything.** Wire wind into the front of a chain
and every filter, delay, folder and modulator downstream has continuous
broadband material to show you what it is doing, hands free, for as long as you
want to sit and turn knobs.

**Handling.** High `resonance` with low `gust` narrows the band until it nearly
reads as a pitch; open `gust` up and it breathes instead. Its large internal
makeup gain means the default `amp` is already a working level rather than a
whisper — start there and go down, not up.

## 13.3 Drone — `drone`

[FIG-13-03]

`service` · not playable · has `freq`, has no `gate`

**The drone has no gate. It sounds for as long as its node exists, which makes
bypass its off switch.** There is no note to release, so note-offs will not stop
it and neither will panic.

**What it is.** A sustained pedal tone: a saw-to-pulse blend (`shape`) plus a
sub-octave sine (`sub`), through a low-pass (`cutoff`), with slow pulse-width
motion and a stereo drift on top. Its `freq` range runs 16–500 Hz and defaults
to 55 — this is built for the bottom of the register and nowhere else.

**Note-driven, but not playable — the near miss.** The drone *does* accept
notes: every instance carries a mono, last-note-priority note-sink on the
control plane. A note-on re-aims `freq` through the glide path; a note-off
falls back to the newest note you are still holding; and when you release
everything, it **holds the last root** rather than stopping. Driven by notes,
never gated by them.

[FIG-13-04]

**Handling.** `porta` is a toggle and it is **on** by default, with `glide`
setting how long a pitch move takes; switch it off and moves snap, though a
20 ms slew underneath keeps them from clicking. Treat the card as furniture
rather than a chain stage — the engine pins drone instances at the head of the
execution order, so you feed it, wire its output where you want it, and leave
it there.

Two more behaviours that will each surprise you once. Its note input is
**single-input**: dropping a new notes wire on it replaces the existing one and
discards whatever root was being held. And **transport stop pauses every
enabled drone**, with play resuming them — occasionally the answer to "why did
the pedal tone vanish". It follows global transpose and deliberately ignores
pitch bend (§6.7, REFERENCE §4.7 and §4.3.2).

> **WARN.** `drone` the **module** and **Drone Voice** the **allocation** are
> two different things, they both appear on the notes plane, and the
> allocation's id type is `hold`, not `drone`. The module is what makes the
> sound. The allocation is a note-routing policy that can be aimed at any
> playable source. Ch 6 has the id table; mixing them up costs an evening.

> **TRY THIS.** Wire a tonic deriver into a drone (§6.5). The pedal tone now
> re-roots itself to whatever key the estimator thinks you are playing in, and
> glides there rather than jumping. It is the cheapest interesting patch in the
> instrument.

## 13.4 Using non-playable sources with playable ones

Layering works because of one rule from §5.3: **an audio wire into a plain
source sums into that source's own output bus** rather than being processed by
it. Drag a drone's or wind's output onto a **Wobble Saw** card and both signals
leave the Wobble Saw together, down whatever chain you already built behind it.
That is the cheap way to put a drone under a played part and end up with one
filter, one reverb and one level to manage instead of two parallel chains.

Watch the sum, though. Two sources into one bus is twice the material arriving
at everything downstream, and a compressor or a drive will hear the drone just
as loudly as the notes — a pedal tone ducking under every chord is exactly the
sort of thing that gets blamed on the compressor. Set the drone's `amp` first
(its default is deliberately low, for precisely this reason), then play against
it and adjust.

---
