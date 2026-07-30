# Patchwerk — Build History

A version-by-version account of how this instrument came to be: the thinking
behind each step, what changed, how the sound and interaction changed, and —
because they're the best part of the story — the bugs and their fixes.

Every heading below is a git tag. (Three were corrected in 2026-07-30: the
chapters drafted as *recall*, *conductor* and *deck* are tagged
`v0.10-history`, `v1.0-nightshift` and `v1.1-patchable` — and `conductor`
is separately the name of `v1.2`.) **Chapters for `v1.2-conductor` through
`v2.1-rewire` were never written; the Flex Arc below covers that ground
thematically and `v2.2-polyphony` resumes the per-release form.**

To run any legacy version without touching your working copy:

```bash
git worktree add /tmp/patchwerk-v0.2 v0.2-gui
cd /tmp/patchwerk-v0.2
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m synthbase gui demo        # (older versions: `play patches/demo.py`)
# when done:  git worktree remove /tmp/patchwerk-v0.2
```

---

## v0.1-scaffold — the bet

**Thinking.** The design constraint was *vibecodability*: new sounds should be
writable by an LLM as small text files, hot-loadable, and safely wrong. That
ruled out Pure Data (patch files encode positions and wire indices — hostile
to text editing) and pointed at a split architecture: **SuperCollider's
`scsynth` server** as the audio engine (25 years of production DSP, its node
tree makes *execution order* and *buses* first-class) driven entirely from
**Python via supriya** — skipping sclang, where most SuperCollider friction
lives.

**What you get.** No GUI. A CLI (`play`, `test`, `devices`), four modules
(saw voice, filter, echo, mic input), plain-data patch files, and the two
properties everything since rests on: a broken module fails to load with a
readable error while audio keeps playing (the engine is a separate process),
and editing a module file hot-swaps its sound into the running chain without
a dropout.

**Sound.** A droning filtered saw you edit live in a text editor.

---

## v0.2-gui — the browser becomes the front panel

**Thinking.** The GUI is just another client of the control plane. A browser
page over a websocket costs nothing architecturally and gives keystroke
capture that's window-focused *by nature*.

**What changed.** aiohttp serves a single-file HTML page; websocket protocol
for params/notes/patches/devices; master volume + level meters (control buses
polled at 15 Hz); computer keyboard as a piano (awsedftgyhuj, z/x octaves).

**Bug story #1 — the silent CP88.** The MIDI router auto-opened the *first*
CoreMIDI port, which is the IAC virtual bus, not the piano. Hardware now wins
over virtual ports (fixed in v0.5 era).

---

## v0.3-toggles — bypass, and the sample-rate saga

**Thinking.** Vibecoded modules need to be auditioned: every module gets an
on/off switch. Sources pause; effects swap to a true passthrough so the chain
keeps flowing — a bypass, not a mute.

**Bug story #2 — "Setting sample rate failed."** macOS refuses to open input
and output devices at different sample rates, and a Bluetooth headset mic is
*locked* to 16 kHz (HFP). The engine learned to auto-select an input matching
the output's rate (preferring the built-in mic), fall back to output-only
with a visible note, and boot on a random free UDP port so a stale `scsynth`
can never block a restart.

**Bug story #3 — the keyboard-eating slider.** Clicking any control left it
holding keyboard focus, swallowing note keys. Only text/select elements may
swallow keys now, and controls blur after use.

---

## v0.4-more-sound — the palette grows

**What changed.** Reverb (FreeVerb), soft-clip drive with tone, a detuned
3-oscillator pulse pad (the new house voice), an autopanner, and the
`pad_space` patch: pad → drive → filter → autopan → echo → reverb. Hardware
buffer dropped to 256 frames (~6 ms).

**Sound.** From "test tone" to "instrument." Toggling stages of pad_space
on/off became the canonical demo of what each module adds.

**Interaction insight.** Keystroke→sound lag was dominated by Bluetooth
headphones (~100–250 ms of A2DP latency) — no software fix exists; wired
output or speakers solve it.

---

## v0.5-cp88 — the piano plugs in

**Thinking.** Physical controls and virtual controls should be
indistinguishable to the engine — one normalized 0..1 control path with
per-param range/curve scaling.

**What changed.** Virtual sliders *track* physical controls (a bound CC moves
the on-screen slider); pitch bend (±2 st) and sustain pedal semantics on the
mono voice; a MIDI monitor line showing exactly what the hardware sends
(which instantly identified the wah pedal as CC4). Research finding: the
CP88's panel knobs don't transmit MIDI at all — only the levers, pedals, and
FC jacks do.

---

## v0.6-arp — the first musician in the box

**What changed.** An arpeggiator as a *note-pool layer* between all
controllers and the voice — computer keys and piano feed it identically.
Patterns (up/down/updown/random/as-played), octave range, gate length,
sustain pedal = latch.

**Interaction.** Hold a chord with the pedal, tweak the filter with both
hands free. The arp retriggers the envelope per step, so the pad's attack
knob morphs it from wash to stab.

---

## v0.7-transport — the clock, and the continuity trick

**Thinking.** A sequencer needs a shared musical clock, and the arp should be
*of* the clock, not near it. Steps quantize to an **absolute** transport grid
(tempo/meter/click track): chord changes cannot move the downbeat, because
nothing about note content ever touches the timeline.

**The innovation.** Chord-transition continuity: the arp's memory is the last
*pitch that sounded*, and each step asks "what's the nearest chord tone above
that, in whatever the chord is *now*?" Play ascending Eb–Ab–C, switch to
Eb–Ab–Bb just after the Eb — the Ab plays next instead of the line
restarting. Disjoint chords, changing chord sizes, mid-beam changes: one
rule handles them all. Measured grid jitter: ~0.3 ms.

**Sound.** Lines flow through harmony changes like a player who heard the
change coming.

---

## v0.8-drone — the instrument starts listening

**Thinking.** A pedal tone that *finds the root of what you play*. The sound
is an ordinary module (waveform blend, sub-osc, portamento toggle + glide);
the brain is control-plane: a time-decaying pitch-class histogram with bass
emphasis, roots scored by harmonic support (a played fifth argues for its
root), 25% hysteresis so it doesn't waffle, and moves allowed only on
transport grid points (1 beat … 4 bars).

**Bug story #4 — the 15-second freeze.** Moving a few sliders froze
everything for up to 15 s. Not CPU: every slider tick triggered a full state
snapshot, and the snapshot ran `system_profiler` — a 1–3 s *subprocess* — on
the server's event loop. Device lists are now cached; knob streams send tiny
targeted messages. 300 rapid param messages: 0.34 s.

**Sound.** Enable the drone, play; the floor of the music quietly re-tunes
itself to your harmony, sliding at whatever glide you set.

---

## v0.9-ux — small levers, big feel

Per-param reset buttons (freq wanders by design — playing notes moves it),
pulse-pad portamento (off by default; drone's on at 1.5 s), click accent
toggle, service-module cards pinned to the top of the list.

**Bug story #5 — the invisible drone card.** Two-part: state broadcasts
after enabling the drone excluded the very client that clicked (leftover
anti-slider-fight logic), and a restart race in `run.sh` could leave a
half-updated server running. Fixed with broadcast-to-all on structural
changes and a pidfile-based restart.

---

## v0.10-history — presets, LFOs, and the drum machine

**What changed.** Presets (full settings recall as git-friendly JSON), the
LFO system — engine-native control buses mapped onto any param, the patch
cables of the eventual graph UI — nine new modules (bells, pluck, flangers,
folders, crushers), a 16-step drum machine riding the transport grid,
collapsible module cards, and a read-only signal-flow graph view: the first
picture of the instrument as a *graph* rather than a list.

---

## v1.0-nightshift — play/stop, and the page that heals itself

**Thinking.** An instrument you perform with needs a conductor's gestures:
stop the world, start the world, and never make the player debug a socket.

**What changed.** Transport play/stop that freezes the beat position (the
arp parks, drones pause, the looper holds its phrase); the drum machine's
output became routable *through the effect chain* instead of hardwired to
the master; a real oscilloscope (a one-shot probe synth records ~46 ms of
any module's out bus and the browser draws it); and the websocket watchdog —
the page notices a dead or silent socket and reconnects itself, because a
performer will never click "reconnect" mid-song.

**Bug story #6 — the drum bus that ate the band.** Early scope probes and
drum routing both taught the same lesson: on scsynth, *who reads a bus and
in what order* is the whole game. A probe reading `In.ar(0)` at the root's
tail recorded junk from other nodes; drums summed into the wrong stage
doubled voices. Everything since routes by explicit bus + node order.

---

## v1.1-patchable — the loop deck saga

**Thinking.** A looper is the fastest path from "instrument" to "band". The
first attempt recorded *audio* into scsynth buffers; supriya's buffer reads
returned allocation garbage on this setup, so the deck became a **MIDI
looper**: record note events with beat offsets, replay them through whatever
the patch currently is. Change the sound under a running loop and the loop
wears it — a feature audio recording could never give.

**What changed.** Bar-synced record/overdub/play/stop, a track visualization
(beat grid, red note bars, tracking playhead), spacebar as the record
gesture, and the loop riding the transport so tempo changes re-time the
phrase.

**Bug story #7 — the armed-window vanishing act.** Notes struck a hair
before the loop top belong at beat 0 — so the armed state clamps early
strikes there. But early *releases* were simply dropped, and months later
(v7) that resurfaced as a phantom: strike-and-release during the count-in
left an unpaired note-on that the window-close "helpfully" sealed at loop
end — a note droning the whole loop and a full-width bar on the deck. The
fix: the clamp applies to the off too.

**Bug story #8 — generators go dead.** Adding a second source mid-chain
allocated it a fresh bus — orphaning everything upstream, which kept writing
to a bus nobody read. Extra sources now SUM into the running bus (fan-in is
free on scsynth; buses add).

**Bug story #9 — the drone that wouldn't wait.** Sources spawn with the
synthdef default `gate=1`, so every rebuild left each voice *sounding* at
its default pitch — a chorus of idle drones after every edit. Playable
sources now spawn `gate=0`: silent until told otherwise.

**Bug story #10 — the phantom overdub.** Overdub passes overlap freely, and
the deck's private voice emitted raw gate events: one pass's release could
silence another pass's note and leave open bars in the roll. The deck voice
got real mono-voice semantics — a note stack, sounding-transition events —
and (v7) every exit from a record window now closes whatever the take still
holds open.

---

## The Flex Arc

At this point the instrument sounded like a band and read like a *list*.
The list was a lie — drums fan in, LFOs cross-cut, the drone listens
sideways — so the GUI had to become the thing it was describing: a patch
graph.

### flex prototypes v1–v10 — subway maps for signal

Ten throwaway prototypes built the visual language before touching the real
synth. Wires route like subway lines: an A* router on a grid treats cards
as obstacles (with a sealed-route fallback so even desperation paths
circumscribe every card); parallel runs in a corridor fan into lanes;
colors are assigned per corridor so no two lines sharing a run look alike;
labels ride the longest segment and read *with* the direction of flow
("Src → Dst" flips to "Dst ← Src" on right-to-left runs); white flow dots
trace live signal; dragging a card shoves neighbors aside (make-space) and
dropping it on a wire splices it in. The prototype's router, lane and label
algorithms survived into production nearly verbatim.

### flex v1 — the prototype meets the synth

The canvas wired to the real websocket protocol. Graph nodes and wires are
*derived from every state message* — the server stays the single source of
truth — while card positions persist per patch in localStorage. The page
became the front door (`/`), the old panel GUI retired to `/legacy`.

### flex v2 — the audio graph gets real

**What changed.** Wires stopped being a drawing and became *the routing*:
`graph_wires` overlay the linear chain, every audio wire is cuttable
(disconnected outputs park on a silent null bus), fan-in just works (buses
sum), a topological sort keeps node execution order legal after any rewire,
palette click spawns a module unconnected, palette drag splices it into the
wire you drop it on, and the drums out became an ordinary draggable audio
output.

### flex v3 — the control plane becomes wiring

**Thinking.** If audio is patchable, notes should be too. The keys→arp→voice
path was hardwired policy; v3 made it *wires* (`ctl_wires`): the graph IS
the note router. The arp only affects what's wired through it; the deck
records exactly the sources wired into it (keys = raw, arp = voiced) and
replays into exactly what it's wired to; no outgoing wire means a note
dead-ends silently — honest patching. The oscilloscope became `scope_tap`,
an ordinary spawnable effect that IS the scope card.

### flex v4 — monitors learn local vs global

Every source-fire emits one tagged tap (`src: keys|arp|deck`); a Note
Monitor dropped onto a ctl wire shows only its upstream sources, unwired it
shows everything; the Waveform monitor rides an audio wire and draws that
module's envelope. The doctrine crystallized here: **transport, panic,
sustain, master volume, pitch reference, and persistence stay global;
everything else is wire-defined.**

### flex v5 — instance ids, many voices, and the drone splits in two

**What changed.** Every module became spawnable *multiple times*: identity
split into instance id ("lowpass.2") vs type ("lowpass"), the whole protocol
re-keyed by id, and the palette stopped removing placed entries. Mono voices
became spawnable nodes, each with its own target and wiring. And the drone
split Grasshopper-style into a **Tonic Deriver** (notes in → thru out +
amber TONIC out) and a plain drone *sound* module with a tonic-in socket —
multiple derivers, multiple drones, different pairings.

### flex v6 — the key shifter, and four closure bugs

**What changed.** The Key Shifter: an experimental control-modifier node
that transposes note streams into a selected key (nearest mapping, shifts
stay within ±6 st), with **four isolated lanes** ("keyshift.2:3" = lane 3)
so parallel signals ride one shifter without merging, a 12-key pop-out
palette, and a 1–32-bar progression track that steps the active key with
the transport. The invariant that makes it safe live: an off is shifted by
the SAME offset its on used, even if the key changed mid-note.

**Bug story #11 — the bars that never left.** Monitors filled with
full-width bars that never scrolled off. Root cause: every *silencing* path
(panic, octave change, arp stop, deck stop, patch rebuild) killed the sound
but emitted no note-off events — so monitors held their bars open forever.
Every all-off path now closes its open taps. A sibling bug: the riding
waveform monitor's history grew from empty instead of rolling a fixed
window, packing ever-thinner blocks until unreadable. And the scope card
vanished for suffixed instances because type detection fell back to the raw
instance id ("scope_tap.2" ≠ "scope_tap").

### flex v7 — macOS eats a keyup, the socket eats an off

**Bug story #12 — the ⌘ mute.** Notes stuck (and monitor bars with them)
whenever a keyboard shortcut was used mid-note: macOS swallows the keyup of
letter keys while ⌘ is held, so the note-off never existed. A modifier
landing while notes are down now panics them.

**Bug story #13 — the reconnect gap.** The watchdog's own reconnect could
drop messages sent during the gap — including note-offs. Sends now queue in
an outbox and flush on open, and a fresh socket closes any taps left open
from before it (the server already silenced them at disconnect).

### flex v8 — the legacy palette's revenge

**Bug story #14 — the vanishing Signal Gen.** A freshly vibecoded source
vanished from the palette after a second instance was added, and after
deleting the duplicate it could never be added again. The hunt went through
the backend (id allocation reuse: clean), the flex palette (retention:
clean) — and ended in `/legacy`: the OLD gui still filtered its add-list by
"already in chain", written for the one-instance world. Under v5 instance
ids, one placed instance hides the type *forever*. The new architecture was
innocent; the museum piece was armed. The filter is gone.

**What else changed.** Snip-heal: deleting a node that sat A→X→B
auto-reconnects A→B (audio via graph_wires as before; ctl now too — but only
when unambiguous, one-in/one-out, per lane on the shifter; ambiguity drops
rather than inventing wires). Wire *labels* became drag handles: grab a
badge, drop it on a compatible module, and the module splices into that
wire. And the drone's tonic-in moved to the card's upper-left corner — a
fixed amber socket that can no longer hide under a param handle (it had
been overlapping one by 2.5 px).

---

## v2.2-polyphony — more than one note at a time

More than one note at a time, and one framework for deciding which. Plus an
entire signal plane that was not there before, and a third kind of module.

At **v2.0 "Wavetable"** there was no `allocation.py`. Mono voices, the arp and
the drone each carried their own copy of the same question — how many notes may
sound, and what a new one displaces — and answered it three different ways.
There was also no `gate.py`, no `ping.py`, no `relay.py` and no `threshold.py`:
a patch could make sound and be played, but it could not decide anything.

### Patch notes

#### Allocation (new)

- **Three policies on one framework.** Mono, poly and drone differ on exactly
  one axis: how many notes may sound at once, which sounding note a new note
  displaces, and what an empty hold does. Everything else about them is shared.
- **Poly voices** sound 1–16 notes, set by a slider on the card. Shrinking it
  closes whatever was sounding on the slots that go away. A parameter moved on
  a poly voice moves across every note it is holding.
- **One card renders mono and poly**, and the policy is read from the server
  rather than guessed from the id.
- **The drone is now an allocation** with its own card and a power input,
  rather than a special case wired in beside the note path. It follows
  transpose, which it did not before — a saved patch with a non-zero transpose
  will sound different.
- Voice leaks fixed in three places at once: removing a module, stealing a
  note, and disposing a voice while it was sounding.

#### The binary plane (new)

- **One hi/lo signal kind.** Buttons, clocks, thresholds, logic gates and
  relays all speak it, and every edge — a trigger, a ping — is derived from a
  level changing rather than sent separately. Wires carry it in yellow.
- **Buttons** are momentary or latching, bindable to a computer key or a MIDI
  CC. **Clocks** fire on a division of the transport. **Thresholds** turn a
  modulation value into a hi/lo level, with hysteresis so a slow sweep does not
  chatter at the crossing.
- **Logic gates** — AND, OR, NOR, XOR, SR latch, T latch — on one card whose
  banner *is* the operator selector. The inputs stay `:a` and `:b` whichever
  operation you pick, so changing the op never drops a wire.
- **Relays**: up to nine independent circuits plus one control input that opens
  and closes all of them together. A circuit takes the kind of whatever lands
  on it first, then enforces it. Audio circuits are permanent lagged-gate
  synths, so opening one is clickless and the stored wiring is what every
  client draws.
- **Power inputs.** Modules, the arp and the drums expose a quiet binary
  level-in, so a patch can switch parts of itself on and off while you play.

#### A third module kind

- **`dual`** generates when nothing is wired into its audio input and processes
  when something is — and it decides which from the audio graph, not from a
  switch you set. Both chains are computed and crossfaded through a lagged
  mode, so a wire landing mid-note is click-free.
- **`power_shaper`** is the first: the psine law applied either to its own
  oscillator or to whatever you send it. Note-playable while generating.

#### Transport and boot

- **A fresh launch comes up stopped.** Nothing clocked runs until you press
  play, and the keys are live regardless — so you can plug in and play without
  starting a transport you did not ask for. The shipped default patch keeps an
  intact `midi → voice → source → master` path.
- **Audio boots unattended**, and a boot that fails explains itself instead of
  hanging. The long-standing "enumerates devices but never starts one" failure
  is diagnosed in the engine rather than left to the operator.

#### Packaging

- **A macOS disk image and a Windows setup wizard.**

#### Testing

- **Audio is testable without hardware** — an internal listener, a file as the
  microphone.
- **Transcript replay**: real server→client traffic is recorded through a
  silent rig and replayed into the real page, so a broken emission fails a test
  instead of failing silently in front of a player.
- **A rig driver with virtual MIDI**, and a reachability test that would have
  caught the dual kind shipping unplayable.

### Carried over from v2.0 "Wavetable"

The Blocks interface, the psine oscillator family, and visible modulation. The
v2.2 manual documents these alongside the new work; `docs/RELEASE_WAVETABLE.md`
has their original notes.

### The manual

v2.2 ships a **user manual** — `docs/manual/`, built to a single self-contained
HTML file and served by the instrument itself at `/manual`, linked from the `?`
button. Every figure is drawn in HTML and CSS from the GUI's own colour and
geometry tokens; there are no screenshots to go stale.

Its companion is `docs/REFERENCE-v2.2-polyphony.md`, the frozen snapshot of
REFERENCE taken at this release. `docs/REFERENCE.md` remains the living
document and will diverge from it.

---

*Each version above corresponds to a tag or a deliverable zip; this file
gains a chapter per milestone.*
