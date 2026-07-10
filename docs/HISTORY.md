# SuperSynth — Build History

A version-by-version account of how this instrument came to be: the thinking
behind each step, what changed, how the sound and interaction changed, and —
because they're the best part of the story — the bugs and their fixes.

Every version below is a git tag. To run any legacy version without touching
your working copy:

```bash
git worktree add /tmp/supersynth-v0.2 v0.2-gui
cd /tmp/supersynth-v0.2
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m synthbase gui demo        # (older versions: `play patches/demo.py`)
# when done:  git worktree remove /tmp/supersynth-v0.2
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

## v0.10 and beyond — see the tags

Presets (full settings recall), the LFO system (engine-native control buses
mapped onto any param — the patch cables of the eventual graph UI), nine new
modules, the drum machine on the transport grid, collapsible module cards,
and the read-only signal-flow graph view. Each carries its own tag and
commit message; this file gains a chapter per milestone.
