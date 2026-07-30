# Figures

**119 figures, all drawn in HTML and CSS.** No image files, no capture step.
`figspec.py` is the source; `build.py` inlines it. This file is generated from
`figspec.py` — do not hand-edit it.

## The rules

**No frames.** Figures sit on the paper, inline with the text. A card specimen
is dark because a card is dark; that is the thing itself, not a box around it.

**Sized to the job.** A screenshot of the whole board, shrunk to figure size, is
a picture of forty things none of which are legible. Most figures here are a
single icon, a coloured line, or one card.

**Wires are solid.** Colour is the only thing separating the planes — verified
against `gui/blocks.html`, where `svg#wires path` carries no dasharray. A live
wire wears a moving white overlay (`path.flow`); that marks it live, not a
different kind. Any figure implying dashes or dots on a wire is wrong.

**Near the heading.** A figure belongs beside the prose that introduces it, not
parked at the foot of the section.

## Sizes

| size | count | for |
| --- | --- | --- |
| `inline` | 13 | rides inside a sentence — an indicator, a handle, one top-bar control |
| `spot` | 43 | beside its paragraph, next to the heading — usually one card |
| `column` | 63 | text-column width — diagrams with parts to compare |

## Editing a figure

Find the `F("FIG-…")` call in `figspec.py`, change it, rebuild. The kit —
`wire()`, `stripe()`, `handle()`, `card()`, `osc()`, `flow()`, `led()`,
`stepgrid()` and the rest — is built from the GUI's own tokens, so figures match
the interface because they share its values.

**Active states are the GUI's own CSS**, transcribed from `gui/blocks.html`:
`.gled.on`, `.stripe.pwr.on`, `.mod.bypassed`, `.mod.rec`, `.dstep.on`,
`.metrostrip i.lit`, `.relaybtn.on`. If a lit state is restyled, re-copy here.

## The figures


### Chapter 1

| id | size | shows |
| --- | --- | --- |
| `FIG-01-01` | column | A voice, two effects, and the wire out. The waveform is the psine morph at p = 8. |
| `FIG-01-02` | column | Four processes. Python owns the graph, scsynth makes the sound, the browser is a view, MIDI is an input. |
| `FIG-01-03` | column | The shortest path to a note, and the keys that play it. |

### Chapter 2

| id | size | shows |
| --- | --- | --- |
| `FIG-02-01` | column | A clean `test` run. If you do not reach `ok`, stop here — §2.9 has the fix. |
| `FIG-02-02` | spot | The output picker on the Master Out card. It only appears when there is more than one device. |
| `FIG-02-03` | column | What the machine can see. Index, then name. |
| `FIG-02-04` | column | Three processes and what runs between them: OSC to the engine, a websocket to the page, audio to the interface. |
| `FIG-02-05` | column | Four run modes. Only one of them is the instrument. |

### Chapter 3

| id | size | shows |
| --- | --- | --- |
| `FIG-03-01` | column | The four regions. Everything else in this manual points back at these names. |
| `FIG-03-03` | spot | One card. The colour bar is the power switch; each param row carries its own input handle. |
| `FIG-03-04` | column | Units, blocks and gutters, with the four card footprints drawn against them. |
| `FIG-03-05` | column | The same patch in both modes. The wiring is identical; only the arrangement differs. |
| `FIG-03-06` | column | Drawing a wire. Release over nothing and the drag is abandoned — nothing is created. |
| `FIG-03-07` | column | Wires travel in the gutters between blocks, bundled and centred on the grid line. |
| `FIG-03-08` | inline | — |
| `FIG-03-09` | spot | The palette groups by family. Reaching for the wrong section is the commonest mistake. |
| `FIG-03-10` | column | Tidy compacts each connected tree into a column in signal order. It moves cards, never wires. |
| `FIG-03-11` | column | A monitor shows the path it is wired to. Unwired, it shows every source at once. |
| `FIG-03-12` | inline | — |

### Chapter 4

| id | size | shows |
| --- | --- | --- |
| `FIG-04-01` | column | The four planes on one patch. Each is a separate wiring system with its own rules. |
| `FIG-04-02` | column | The four wire colours. Colour is the whole code — wires are solid, and the word is on the handle label. |
| `FIG-04-03` | column | If it has one value for the whole instrument it is global; if it has a wire it is not. |
| `FIG-04-04` | column | Every note-on gets an off. The bar that never ends is a stuck note — hit panic. |
| `FIG-04-05` | column | A live wire wears a moving white overlay. The wire's own colour never changes. |

### Chapter 5

| id | size | shows |
| --- | --- | --- |
| `FIG-05-01` | column | Two layers, both true. Once you draw a wire, the overlay is the one that routes audio. |
| `FIG-05-02` | column | Three sources into one destination. Fan-in needs no mixer. |
| `FIG-05-03` | column | A disconnected output parks on the null bus. It is alive, not removed. |
| `FIG-05-04` | column | Sources run before their destinations because the wires say so. |
| `FIG-05-05` | column | Removing a module heals the chain rather than leaving a hole. |
| `FIG-05-06` | column | Bypass dims the body and swaps in a pass-through. The card, its wires and its settings all stay. |
| `FIG-05-07` | spot | The master card: one fader, two meters, a limiter behind them. |

### Chapter 6

| id | size | shows |
| --- | --- | --- |
| `FIG-06-01` | column | How a fresh launch is wired before you touch anything. |
| `FIG-06-02` | spot | `keys` is a source only. Wire to what it feeds, never into it. |
| `FIG-06-03` | column | Three allocations off one keyboard. They differ on one axis: how many notes may sound at once. |
| `FIG-06-04` | column | The arp sits between keys and voice. Bypass it and the route still plays. |
| `FIG-06-05` | spot | The Estimator listening. The amber bar is the committed root; the outlined one is what is currently leading. |
| `FIG-06-06` | spot | A key shifter with an eight-step progression loaded. |
| `FIG-06-07` | column | One step per bar, advancing with the transport. The outlined cell is where the clock is now. |
| `FIG-06-08` | column | Notes flowing. Colour is the source: green keys, amber arp, red deck. |

### Chapter 7

| id | size | shows |
| --- | --- | --- |
| `FIG-07-01` | column | Binary carries a level. Everything else — pings, triggers — is an edge derived from it changing. |
| `FIG-07-02` | column | Six operations, one card. The inputs stay `:a` and `:b` whichever you choose, so a swap never drops a wire. |
| `FIG-07-03` | spot | A relay. Each circuit takes the colour of the kind wired into it; the button opens and closes all of them. |
| `FIG-07-04` | column | Opening a relay does not change the picture — the wire stays drawn. Only the LED moves. |
| `FIG-07-05` | spot | A threshold turns a modulation value into a binary level. Its CV input takes exactly one LFO. |
| `FIG-07-06` | column | Indicators follow state, not your mouse. Nobody touched the arp — the logic did. |

### Chapter 8

| id | size | shows |
| --- | --- | --- |
| `FIG-08-01` | spot | An LFO card. One card can drive many destinations; the subtitle counts them. |
| `FIG-08-02` | column | Drop an LFO output on a parameter handle. The slider then shows the band it is sweeping. |
| `FIG-08-03` | column | The five LFO shapes. `s&h` holds one random value per cycle rather than moving continuously. |
| `FIG-08-04` | column | Hysteresis is the gap between the level that turns it on and the level that lets it off again. |
| `FIG-08-05` | spot | Splice a Scope Tap anywhere to see that point without changing the sound. |

### Chapter 9

| id | size | shows |
| --- | --- | --- |
| `FIG-09-01` | column | A fresh launch comes up stopped. The keys still play; only clocked things wait. |
| `FIG-09-02` | column | Divisions in beats. Change one mid-flight and the next event lands in phase, not offset. |
| `FIG-09-03` | column | Sixteen steps, four lanes. The outlined cell is the step the transport is on. |
| `FIG-09-04` | column | Drums are a source like any other — route them deliberately. |
| `FIG-09-05` | spot | MIDI arrives at `keys` and nowhere else. Splits and layers belong on the controller. |

### Chapter 10

| id | size | shows |
| --- | --- | --- |
| `FIG-10-01` | column | The deck's three working states. The subtitle is the only place it tells you which. |
| `FIG-10-02` | spot | Recording runs a fixed window. It captures what is wired into it — wire nothing and it records nothing. |
| `FIG-10-03` | column | Where you wire the deck decides what it stores: the keys you pressed, or the notes that sounded. |
| `FIG-10-04` | column | The cycle. Overdub layers onto the take; clear empties it. |

### Chapter 11

| id | size | shows |
| --- | --- | --- |
| `FIG-11-01` | column | The palette, grouped the way it is grouped on screen. |
| `FIG-11-02` | spot | Four control types on one card: exponential `freq`, a select, a toggle, and a linear `amp`. |
| `FIG-11-03` | column | The family colours. Family groups by appearance, not by what a module does to the signal. |

### Chapter 12

| id | size | shows |
| --- | --- | --- |
| `FIG-12-01` | spot | The Instrument card. |
| `FIG-12-02` | spot | The PW Pulse Pad card. |
| `FIG-12-03` | spot | The FM Bell card. |
| `FIG-12-04` | spot | The Pluck card. |
| `FIG-12-05` | spot | The literal waveshaper at p = 8. |
| `FIG-12-06` | spot | The band-limited bank at p = 8 — the same curve without the fold-back. |
| `FIG-12-07` | spot | The crossfade route at p = 8. |
| `FIG-12-08` | column | One knob, five positions. p = 2 is a pure sine; p = 64 is a square. |
| `FIG-12-09` | column | The pad's pulse wave. `pwm` moves the duty cycle slowly; `detune` stacks a second copy beside it. |
| `FIG-12-10` | column | Every gated voice shares this envelope. Note-on runs attack and decay; note-off starts release. |

### Chapter 13

| id | size | shows |
| --- | --- | --- |
| `FIG-13-01` | spot | The Audio In card. |
| `FIG-13-02` | spot | The Wind card. |
| `FIG-13-03` | spot | The Drone card. |
| `FIG-13-04` | column | The drone holds the last root instead of stopping. It is driven by notes but never gated by them. |

### Chapter 14

| id | size | shows |
| --- | --- | --- |
| `FIG-14-01` | spot | The Low-pass Filter card. |
| `FIG-14-02` | spot | The Telephone card. |
| `FIG-14-03` | spot | The Echo card. |
| `FIG-14-04` | spot | The Reverb card. |
| `FIG-14-05` | spot | The Chorus card. |
| `FIG-14-06` | spot | The Flanger card. |
| `FIG-14-07` | spot | The Phaser card. |
| `FIG-14-08` | spot | The Auto Pan card. |
| `FIG-14-09` | spot | The Drive card. |
| `FIG-14-10` | spot | The Bitcrush card. |
| `FIG-14-11` | spot | The Wavefolder card. |
| `FIG-14-12` | spot | The Compressor card. |
| `FIG-14-13` | spot | The Pitch Shift card. |
| `FIG-14-14` | spot | The Ring Mod card. |
| `FIG-14-15` | spot | The Scope Tap card. |
| `FIG-14-16` | column | Order is the effect. The first is a distorted sound in a room; the second is a distorted room. |
| `FIG-14-17` | column | Auto Pan acts on everything upstream of it. Its position is a decision, not a habit. |
| `FIG-14-18` | spot | The Power Shaper card. |

### Chapter 15

| id | size | shows |
| --- | --- | --- |
| `FIG-15-01` | column | A blank patch, one source, and the two wires that make it sound. |
| `FIG-15-02` | column | An effect added mid-wire lands where you dropped it. |
| `FIG-15-03` | column | Rewiring a live rack. Nothing stops; removal heals the gap rather than leaving a hole. |
| `FIG-15-04` | column | Parallel paths into one destination. No mixer, no cost per wire. |
| `FIG-15-05` | column | The worked rack. Four planes, one instrument — read it one plane at a time. |
| `FIG-15-06` | column | Two different saves. Neither holds your card layout — that lives in the browser. |
| `FIG-15-07` | spot | Hot reload swaps the module in place and keeps its wiring. |

### Chapter 16

| id | size | shows |
| --- | --- | --- |
| `FIG-16-01` | column | Five places a signal can be. Everything else is a detail of one. |
| `FIG-16-02` | spot | The output half: fader, meters, and the device picker when there is more than one device. |
| `FIG-16-03` | spot | Audio In is a source like any other. Set `gain` here; read the level on the master meters. |
| `FIG-16-04` | column | Handles fill the top edge first and spill onto the side only when they run out of room. |
| `FIG-16-05` | column | One signal, controller to speakers, as a final read-through. |

### Appendices

| id | size | shows |
| --- | --- | --- |
| `FIG-A-01` | column | The computer keyboard as a controller. The `?` button shows this live, plus your own trigger bindings. |
| `FIG-A-02` | column | The plane colours, repeated for reference. |

### Top bar (inline)

| id | size | shows |
| --- | --- | --- |
| `FIG-TB-BPM` | inline | — |
| `FIG-TB-CLICK` | inline | — |
| `FIG-TB-HELP` | inline | — |
| `FIG-TB-KEYS` | inline | — |
| `FIG-TB-LOCK` | inline | — |
| `FIG-TB-METER` | inline | — |
| `FIG-TB-MODE` | inline | — |
| `FIG-TB-PLAY` | inline | — |
| `FIG-TB-RESTART` | inline | — |
| `FIG-TB-TIDY` | inline | — |
| `FIG-TB-VOL` | inline | — |
