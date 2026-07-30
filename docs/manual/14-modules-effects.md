# 14. Effects

The fifteen modules that process audio instead of generating it, grouped the
way the palette groups them. One further card sits at the end: the Power Shaper
is a **dual**, a kind of its own, and it gets §14.8.

Every effect is an audio-plane stage: one stereo input, one stereo output,
spliced anywhere between a source and **Master Out** (Chapter 5). Adding one is
a splice, not a send — the wire that used to run past it now runs through it.

**Every parameter range and default in this chapter lives in REFERENCE §10.3.**
This chapter tells you what the modules are for; it does not reprint the
tables.

One thing to know before the list: most effects carry a `mix` control, so you
can dial in a taste of anything. Three do not — `lowpass`, `autopan` and
`compressor` are always fully in circuit, and the only way to soften them is to
back off their own controls.

---

## 14.1 Filters

### 14.1.1 Low-pass Filter — `lowpass`

[FIG-14-01]

The canonical effect, and the one to reach for first. `cutoff` sets where the
top of the sound stops; `resonance` lifts a peak right at the cutoff, which is
what turns a sweep from "getting darker" into "wah".

Its cutoff is internally lagged, so it takes modulation gracefully — this is
the friendliest destination on the canvas for an LFO or an envelope (Chapter
8), and a slow cutoff sweep is the single most useful modulation in the
instrument.

### 14.1.2 Telephone — `telephone`

[FIG-14-02]

A band-pass with teeth: `low` and `high` squeeze the signal into a narrow band
from both ends, and `crunch` soft-clips whatever survives. An intercom, a hold
line, a radio in the next room.

This is a character effect, not a corrective one — do not try to use it as a
tidy band-pass, because the crunch is on the wet path and does not switch off.
It arrives fully wet, which is the loudest thing about it; pulling `mix` back
to around a third gives you the same colour sitting under the original sound
rather than replacing it.

## 14.2 Time and space

### 14.2.1 Echo — `echo`

[FIG-14-03]

A feedback delay. **There is no decay control, and you should not go looking
for one** — decay is derived from `feedback`, because more feedback means both
more repeats and a longer tail. If the echoes pile up faster than you want,
lengthen `time`; if they hang around too long, lower `feedback`. Near its
ceiling `feedback` gives you a tail that outlasts the note by a long way, which
is a fine effect and an easy way to swamp a patch.

### 14.2.2 Reverb — `reverb`

[FIG-14-04]

Space: `room` is the size of it, `damp` is how quickly the bright part of the
tail dies away.

The defaults are deliberately modest — a room, not a cathedral. Push `room` up
and pull `damp` down together for the long bright wash; push both up for a big
soft space that stays out of the way. Reverb belongs at the end of a chain,
after the dirt and the filter, for the same reason it does in a real signal
path: you want to hear a distorted sound in a room, not a distorted room.

### 14.2.3 Chorus — `chorus`

[FIG-14-05]

Two delay taps around 12 ms, each modulated, with the phases and rates offset
between left and right. That offset is where the width comes from — chorus does
not just thicken a sound, it spreads it across the stereo field, and it will do
that to a mono source.

Slow and shallow is a thickener you stop noticing; fast and deep is a seasick
warble. This is the safest of the three modulated delays and the one to try
first on a thin voice.

### 14.2.4 Flanger — `flanger`

[FIG-14-06]

The same idea as chorus with a much shorter delay — roughly 1.5 to 7.5 ms —
and a feedback path around it, which puts comb notches in the middle of the
audible range and sharpens them into the metallic jet-plane whoosh.

`feedback` is the control that separates it from chorus: at zero it is a
tighter, drier chorus, and wound up it rings and gets aggressive. Deep `depth`
plus high `feedback` on an already-bright source is where flanger starts
sounding like a fault, so move one at a time.

### 14.2.5 Phaser — `phaser`

[FIG-14-07]

Four cascaded swept allpass filters. No delay line is involved, so instead of a
dense comb you get a handful of notches drifting up and down the spectrum — a
hollow, breathing sweep rather than a metallic one.

**Choosing between the three:** chorus for width and thickness, flanger for
metallic intensity, phaser for movement without either. Phaser is the gentlest
and the one that survives being left on; flanger is the most obviously an
effect. All three want harmonic content to work on — none of them does much to
a pure sine.

### 14.2.6 Auto Pan — `autopan`

[FIG-14-08]

Sweeps the stereo position with a sine.

> **WARN.** Auto Pan sums its input to mono before it pans. Any stereo image
> built upstream of it — chorus width, reverb spread, a stereo source — is
> collapsed and gone. This is the one effect whose position in the chain
> changes what the *other* effects do, so decide where it goes deliberately
> rather than dropping it on the end out of habit.

[FIG-14-17]

## 14.3 Dirt

### 14.3.1 Drive — `drive`

[FIG-14-09]

A soft clip: `gain` pushes the signal into a tanh curve, which rounds the peaks
off rather than shearing them, so it thickens before it tears.

`tone` is a **post-clip** low-pass. It runs *after* the distortion, so it is
there to tame the fizz the clipping just created, not to shape what goes in. If
a drive setting is harsh, reach for `tone` before you reach for `gain`. Drive
arrives fully wet; backing `mix` off to blend the clean signal back underneath
is the usual way to keep the low end intact.

### 14.3.2 Bitcrush — `bitcrush`

[FIG-14-10]

Two separate kinds of damage that people usually lump together, and worth
keeping apart in your head.

`srate` throws away samples. Lowering it folds high frequencies back down into
the audible range as clangy, inharmonic aliasing — this is the control that
makes things sound like cheap old samplers. `bits` throws away resolution,
quantising the waveform into steps, which adds a gritty noise floor that is
loudest on quiet passages and decaying tails. Sweeping `srate` alone with
`bits` left high is the cleanest way to hear what each one does.

### 14.3.3 Wavefolder — `wavefolder`

[FIG-14-11]

Rather than flattening peaks, folding reflects them back on themselves, so each
fold adds a new burst of harmonics up top. At low `fold` settings it is a
bright edge; at high ones the waveform stops resembling what went in.

`symmetry` is a DC offset applied **before** the fold, which is why it changes
the harmonic character rather than just shifting the balance: an offset signal
hits the fold thresholds unevenly, the even harmonics come up, and the tone
shifts from hollow to nasal. Leave it at zero for the classic symmetrical fold,
then nudge it either way. A LeakDC stage cleans the offset up afterwards, so
you will not get a DC thump out of it.

**Choosing between the three dirt modules:** drive for warmth and glue,
bitcrush for digital damage, wavefolder for new harmonics that were never in
the source. A fourth card does this family's job on the psine law —
`power_shaper`, a continuous morph toward a square — at §14.8.

## 14.4 Dynamics

### 14.4.1 Compressor — `compressor`

[FIG-14-12]

Downward compression only — it pulls loud material down, it does not lift quiet
material up. Reach for it when a patch has a couple of notes that jump out, or
when a plucked source has a spike at the front that eats your headroom.

`attack` decides whether the initial transient gets through: fast tames the
click, slow keeps the snap. `release` decides how quickly it lets go, and
setting it too short on a sustained sound is what causes audible pumping. There
is no `mix`, so if it sounds squashed, raise the threshold rather than trying
to blend it away.

## 14.5 Voice and pitch

### 14.5.1 Pitch Shift — `pitchshift`

[FIG-14-13]

Granular pitch shifting: up or down by two octaves, with no change in speed.

**The trade-off in `window` is the whole module.** It sets the grain size, and
the grain size *is* the latency. Small windows respond quickly but chop the
sound into audible grains; turning `window` up smooths those artefacts away and
makes the module measurably laggier at the same time. There is no setting that
is both, so pick the end you need: short for anything that has to feel played,
long for anything that has to sound smooth. `smear` randomises the grain
timing — at zero it is deliberately robotic and metallic, and a very small
amount softens that without losing definition.

### 14.5.2 Ring Mod — `ringmod`

[FIG-14-14]

Multiplies the input by a sine at `carrier`, replacing the original frequencies
with their sums and differences. Those are almost never in tune with the note
you played; the clangorous bell-metal quality is the point rather than a side
effect.

The carrier does not track the keyboard, so the same setting produces a
different interval on every note — good for texture, bad for anything that has
to stay musical. Down at the bottom of the carrier range you cross out of pitch
territory and it reads as tremolo instead. It arrives mostly wet; blending some
dry back in keeps the original pitch audible under the clang.

## 14.6 Utility

### 14.6.1 Scope Tap — `scope_tap`

[FIG-14-15]

**Scope Tap makes no sound of its own.** It is a monitoring tool that splices
into an audio chain like any effect, and the oscilloscope then draws whatever
passes through (§8.3). Capture is continuous rather than triggered, so the
trace is there the instant you look.

The `gain` trim is the only thing it does, and it is not free: anything other
than the default 1.0 scales the signal on its way downstream as well as the
trace you are reading. Lift it only when a quiet signal is unreadable, and put
it back.

## 14.7 Ordering effects

Effects are a chain, and the order changes the result. Three orderings cover
most of what you will want.

**The default one — dirt, filter, dynamics, space.** Distort first, filter the
fizz off, level it, then put it in a room. Use this when you do not have a
reason to do something else; it is also why `drive` has its own post-clip
`tone`.

**Filter first when the source is too bright to distort.** A hot, harsh source
fed straight into `drive` or `wavefolder` produces harshness squared. Put
`lowpass` ahead of the dirt and the distortion has something civilised to work
with.

**Modulation before space, always.** Chorus, flanger and phaser want a dry
signal to work on; run them after reverb and they smear the tail instead of
moving the sound. Reverb and echo go last.

And the exception that outranks all three: **`autopan` collapses stereo to
mono**, so anything before it that was building width is wasted. Put it last in
a mono chain, or early if you want chorus and reverb to rebuild a stereo image
after it — knowing that the space effects will soften the pan motion if you do.
You cannot have wide chorus and hard panning through the same chain; pick one.

[FIG-14-16]

## 14.8 The dual kind — Power Shaper

[FIG-14-18]

**It is note-playable while it generates.** Aim a voice allocation at it and it
behaves like the three psine generators in §12.5 — same law, same `p`, its own
`drive` on top. Wire audio into it and it stops being an instrument mid-note and
starts shaping what you sent it, with no click in between.

> **TRY THIS.** Aim a Mono Voice at a Power Shaper and play a line, then drag a
> saw voice's output onto its audio input while you are still holding a note.
> The card flips to FX, and the line you were playing becomes the thing being
> shaped.


`psine` · **dual** · shaping audio is the mode that works

**A dual is a third kind of module**, new in v2.2, and `power_shaper` is the
only one. It has an audio input like an effect but does not need one: with
nothing wired in it **generates**, and the moment a wire lands it becomes an
**effect** on whatever arrives (REFERENCE §2.2).

**The mode is not a knob.** Patchwerk reads it off the audio graph — a wire
into this card means FX, no wire means GENERATE — and the card shows which one
it is in, updating the instant you draw or cut that wire. Both chains are
computed all the time and crossfaded through a lagged mode signal, so the
switch is click-free: you can rewire it while it is sounding.

**Use it for** the psine morph (§12.5) pointed at something other than its own
oscillator. Same law, same **identity at `p` = 2**, squaring off harder as `p`
climbs toward 64 and pinching to a spike below 2 — with `drive` pushing the
signal further into the curve, which is what makes it bite on quiet material.
It is not band-limited, so fold-back aliasing is the fingerprint, the same
character `power_sine_shaper` has, and not a defect. `mix` blends it back
against the dry signal.


**The parameters split by mode.** `p` and `drive` work in both. `freq` and
`amp` are GENERATE only, `mix` is FX only, and the ones that do not apply have
nothing to do in the mode you are in. Ranges and defaults are in REFERENCE
§10.3.1.

> **TRY THIS.** Splice it after a `wobble_saw` with `p` at 2, so it is doing
> nothing audible. Now walk `p` up to 20 and back, then add `drive`. That is
> the same morph you heard in §12.5, applied to a signal that already has
> harmonics of its own — which is why it lands harder here than on a sine.

---
