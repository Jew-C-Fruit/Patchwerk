# 16. IO routing

The boundary between Patchwerk and the outside world: what comes in, what goes
out, and where everything lands in between. The figures carry this chapter —
the prose only names what they show.

---

## 16.1 The bus map

Four kinds of place a signal can be, and everything else in this chapter is a
detail of one of them (REFERENCE §3.8).

[FIG-16-01]

- **Hardware output** — one stereo bus, `0`. Everything you hear arrives here,
  and the master section works on it in place.
- **Hardware input** — the first input bus after the outputs. The `audio_in`
  module reads channel 1 and spreads it to stereo.
- **Private stereo buses** between stages — one group per hop, and summing
  junctions, which is why fan-in is free.
- **The null bus** — the rack's single persistent nowhere. Every disconnected
  output parks here, silently, still running (§5.4).

## 16.2 Output

[FIG-16-02]

Sound leaves through the hardware output bus, and the **master section** sits
on that bus rather than in your chain: lagged volume so the fader does not
click, a **limiter at 0.95**, and the stereo peak meters you see in the
interface (REFERENCE §3.5). It is infrastructure — never a stage you can wire
around, and it cannot be removed.

The **Master Out** card is the player-facing half of it: the volume fader, the
meters, and the output device picker.

## 16.3 Input

[FIG-16-03]

Live audio enters through the **Audio In** module, which reads hardware input
channel 1 and spreads it to stereo. From there it is an ordinary source: wire
it into effects, into **Master Out**, wherever you like. The shipped `vox` and
`mic_fx` patches are built on it (REFERENCE §12.1).

**There is no input meter in this interface.** The pair of meters on **Master
Out** is the only level you can see, and they show what is leaving, not what is
arriving. So wire **Audio In** through to **Master Out** first, then set its
`gain` against those meters and your ears — there is nothing to watch before
the signal is routed.

> **WARN — "my microphone does nothing" is usually not a wiring fault.** Input
> can be switched off entirely by the engine's boot fallback: macOS refuses to
> open input and output at mismatched sample rates, so Patchwerk tries a
> rate-matched input, prefers the built-in one, and boots **output-only** if it
> cannot find one. The reason is recorded in the boot note (REFERENCE §3.6). A
> Bluetooth headset mic locked to 16 kHz is the usual culprit; check that
> before you check your patch.

## 16.4 Choosing devices

The pickers live on the cards, not in a settings panel. **Master Out** picks
the output device, **Audio In** picks the input device, and the **Keys/MIDI**
card picks the MIDI port. Each appears as a dropdown only when there is more
than one to choose from. A corner warning appears when the active input and
output are running at different sample rates, which only matters when both
hardware ends are actually in play.

> **WARN.** Changing an audio device **reboots the engine** — scsynth is
> stopped and a new one started, and it is audibly silent for a few seconds.
> Your patch, its settings and master volume all survive; anything mid-flight
> does not. Do not do it during a take (REFERENCE §3.6).

The other button worth knowing is **⟳**, which restarts the backend
deliberately. It snapshots the whole rack — modules, params, audio and notes
wiring, voice targets, drums routing — re-execs the server, and restores all of
it on boot (REFERENCE §12.3). Card layout survives because it never left your
browser. Unlike a device change, it gives you your wiring back.

## 16.5 Handle overflow on Master Out

Everything eventually lands on **Master Out**, so it is the one card allowed to
put input handles on two edges. It fills its top edge first and only spills onto
its side edge when the top runs out of room, so a small patch looks exactly as
it always did and a busy one keeps every handle reachable.

[FIG-16-04]

Nothing about the spill changes routing. A wire landing on the second edge is
the same wire, summing into the same bus.

## 16.6 Where drums land

The drum machine's audio has exactly three destinations, chosen on its card:
**master** (straight to the output bus, at the tail), **a specific module** (an
effect's input, or a source's output bus — so the hits ride whatever is
downstream of it), or **nowhere**, which is a real setting and means the hits
are not spawned at all. A target that no longer exists falls back to master
rather than going quiet (REFERENCE §3.8, §8.3).

§9.4.1 owns the reasons to pick each one; this chapter only shows where they
sit on the bus map.

## 16.7 A complete route

One signal, end to end, as a final check that you can read a path.

[FIG-16-05]

A microphone reaches the hardware input bus. **Audio In** reads channel 1 and
writes stereo onto its own private bus. Each audio wire carries that into the
next stage's input, which processes it and writes to the next private bus. The
last wire lands on **Master Out**, where volume, the limiter and the meters
apply in place. Then your speakers.

Every hop in that sentence is a wire you drew, and any hop you did not draw is
a signal parked on the null bus. That is the whole routing model: what you can
see is what is happening.

---
