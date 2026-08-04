# 12. Playable voices

The seven sources that respond to notes. Each exposes a `freq` and a `gate`,
so each needs a voice allocation aimed at it before it makes a sound (Ch 6).

Every entry follows the template in §11.5. **Parameter ranges, defaults and
curves are in REFERENCE §10.1** — cited once, here.

---

## 12.1 Wobble Saw — `wobble_saw`

[FIG-12-01]

`voice` · note-playable · M card with M/L chips

**What it is.** A sawtooth with a sine tremolo over it: `wobble` sets the rate,
`depth` how far the level dips each cycle.

**Use it for.** The canonical source — the one every other voice is
level-matched against, and the one to reach for while you are learning anything
else in the instrument. Basslines, sirens, and anything that wants movement
without spending an LFO on it.

**Handling.** Take `depth` to 0 and you have a plain saw, which is the most
honest test tone you can send down an effect chain. At the top of `wobble`'s
range the tremolo climbs into audio rate and stops reading as tremolo — it
becomes roughness, which is a usable place to be rather than a mistake.

## 12.2 PW Pulse Pad — `pulse_pad`

[FIG-12-02]

`voice` · note-playable · M card with M/L chips

**What it is.** Three oscillators — one at the note, one `detune` cents sharp,
one `detune` cents flat — with a selectable waveform, slow pulse-width motion,
optional portamento, and its own attack and release.

[FIG-12-09]

**Use it for.** The fat keyboard voice. Where Wobble Saw is one oscillator with
motion applied, this is three oscillators beating against each other, which is
a much wider kind of thickness. Chords, pads, anything that needs to fill space
rather than cut through it.

**Handling.** Two controls will waste your time if you do not know about them.
`porta` is a toggle and it is **off** by default, so `glide` does nothing at
all until you switch it on. And `detune` at 0 collapses all three oscillators
onto one pitch — thinner, occasionally what you want; the 12-cent default is
the fat setting. Unlike the other voices, `attack` and `release` here are real
envelope controls rather than a single derived `decay`.

At M — the default — the card hides its waveform preview and shows params
only. Click the **L** chip to bring the preview up; the extra room goes to the
picture, never to more parameter columns (§3.3.3).

## 12.3 FM Bell — `fm_bell`

[FIG-12-03]

`voice` · note-playable · M card with M/L chips

**What it is.** Two-operator FM: one sine modulating another, with `ratio`
setting the modulator's pitch relative to the note and `index` setting how hard
it pushes.

**Use it for.** Bells, electric pianos, and the whole family of struck metal in
between. Which one you get is almost entirely down to `ratio`.

**Handling.** **`ratio` decides whether it sings or clangs.** Whole numbers —
1, 2, 3 — put the modulator in tune with the note: bright but tonal, which is
where the electric pianos live. Ratios between whole numbers produce inharmonic
partials, which is what your ear reads as *metal*. The 3.51 default is
deliberately not a whole number.

`index` decides how much of it you hear: at 0, a plain sine; climbing adds
partials, brightness and eventually clangour. `decay` drives the amplitude
envelope *and* a separate envelope on the index, so a long note gets duller as
it fades — which is what struck things actually do.

> **TRY THIS.** `ratio` 2.0, `index` 3, `decay` 1.2 gets you an electric piano.
> Now put `ratio` back to 3.51 and `index` to 8 without touching anything else,
> and hear how far one knob moved you.

## 12.4 Pluck — `pluck`

[FIG-12-04]

`voice` · note-playable · M card with M/L chips

**What it is.** A Karplus–Strong plucked string: a burst of noise fed into a
tuned delay that rings and decays. `decay` is how long it rings, `damp` is how
dead it sounds.

**Use it for.** Anything physical and short — guitars, harps, koto, muted
stabs. The most percussive voice in the palette, and the one that most rewards
being played rather than held.

**Handling.** It **retriggers** rather than sustains: every gate throws a fresh
burst of noise at the string, so holding a key does not hold the note open the
way it does on the pad. Its pitch range also stops at 1600 Hz where the other
voices reach 2000, so an allocation transposed a long way up will run out of
string. `damp` at 0 is bright and metallic; past about 0.6 it is a thud.

## 12.5 The psine family — three routes to one idea

Three modules, one law, three ways of computing it. They take **identical**
`freq`, `p` and `amp` ranges, which is not a coincidence — it makes them a
clean A/B/C set, where the only variable is the method.

**The law.** All of them shape a sine by

```
T_p(A) = sgn(A) · |A|^(2/p)
```

where `A` is the instantaneous value of the sine. The `sgn` term keeps the
sign, so the curve is odd-symmetric and the waveform stays balanced around
zero; the exponent `2/p` is the only thing the `p` knob touches. Three
landmarks tell you the whole knob:

- **`p` = 2** — the exponent is 1, so `T(A) = A` and the law is an
  **identity**. All three modules put out a pure sine, and they put out the
  *same* pure sine: measured total harmonic distortion is around **−130 dB**,
  far below anything
  you, the converter, or the room will find. This is the family's calibration
  point.
- **`p` above 2** — the exponent drops below 1, which pushes every sample
  toward ±1. Tops flatten, edges steepen, odd harmonics pile in. At `p` = 64
  the exponent is 1/32 and everything except the zero crossing is pinned at
  full scale: a square.
- **`p` below 2** — the exponent rises above 1, which pulls every sample toward
  zero. The waveform pinches into a spike at each peak — quieter, thinner, and
  buzzy in a completely different way from the square end.

`p` runs 1 to 64 on an exponential curve, so the interesting middle of the
morph gets most of the travel rather than being crushed against the low stop.

**Why three modules.** The law says *what* spectrum you want and says nothing
about *how* to produce it, and the how is audible:

[FIG-12-08]

| Module | How it computes the law | Aliasing | Below `p` = 2 | Pick it for |
| --- | --- | --- | --- | --- |
| Psine Waveshaper | evaluates the law per sample on a real sine | folds back, deliberately | pinches to a spike | grit that shifts as you play up the keyboard |
| Psine Harmonic Bank | renders the law's exact odd-harmonic series | none — partials above Nyquist are gated off | pinches to a spike | a clean morph, especially high on the keyboard |
| Psine Crossfade | crossfades a sine against an ideal square | none — both endpoints are exact | **nothing happens** | a morph that repeats identically under an LFO |

The two band-limited members share one mechanism — a Nyquist-gated,
RMS-normalised bank of 24 odd partials — while each keeps its own coefficient
law, because the law *is* the module's identity (REFERENCE §10.4). All three
share the same ADSR.

Their cards carry a computed **waveform preview** with a live-oscilloscope
toggle, and the preview morphs in real time when `p` is being driven by an LFO
— which makes a modulated `p` one of the few things in Patchwerk you can watch
as directly as you hear it.

> **TRY THIS.** Spawn all three, park them all at `p` = 2, and A/B them. They
> are indistinguishable, and they are meant to be. Now walk `p` up to 20 on
> each in turn, at a low note and then at a high one. Everything that separates
> these three modules is in that comparison.

**And then do it to something else.** `power_shaper` (§14.8) is the family's
fourth member and points the same law outward: whatever audio you wire in gets
the morph, with the same identity at `p` = 2 and the same fold-back character.
Once the morph makes sense as a generator, that is the interesting place to
take it — but read §14.8 first, because on this release the card only works as
an effect.

### 12.5.1 Psine Waveshaper — `power_sine_shaper`

[FIG-12-05]

`psine` · note-playable

**What it is.** The literal version: a sine oscillator with
`sgn(sin)·|sin|^(2/p)` evaluated on every sample.

**Use it for.** The dirty one. Because the law is applied per sample and
nothing is band-limited, the harmonics it generates run straight past Nyquist
and fold back down as **inharmonic** partials — grit that does not track the
note, and that shifts as you play up the keyboard.

**Handling.** **That grit is the module's character, not a defect.** A LeakDC
guard removes the DC offset the curve leaves behind; nothing else is cleaned
up, on purpose. Expect the fold-back to be loudest high on the keyboard and at
high `p` — the same `p` played two octaves apart genuinely sounds like two
instruments. If that is not what you want, §12.5.2 is the same morph without
it.

### 12.5.2 Psine Harmonic Bank — `power_sine_additive`

[FIG-12-06]

`psine` · note-playable

**What it is.** The same target spectrum built the other way round: the exact
odd-harmonic series of that curve, summed directly as partials.

**Use it for.** The clean sibling. Every partial above Nyquist is gated out
before it can exist, so there is nothing to fold and nothing to alias. Use it
high on the keyboard, at high `p`, or anywhere the signal is about to hit a
pitch shifter or a heavy resonant filter that would happily amplify grit into a
problem. It is RMS-normalised, so the level holds steady as you sweep `p`
rather than swelling with the harmonic count.

**Handling.** The bank is 24 partials and the Nyquist gate does real work. Play
high enough and most of those partials are switched off, so a top-octave note
at `p` = 64 lands much closer to a sine than the same setting two octaves down.
**The morph does less the higher you play.** That is the price of alias-free,
and it is the mirror image of the Waveshaper's behaviour in the same register.

### 12.5.3 Psine Crossfade — `power_sine_blend`

[FIG-12-07]

`psine` · note-playable

**What it is.** The wavetable route: two fixed frames — a sine and an ideal
band-limited square — crossfaded by `u = clip(1 − 2/p, 0, 1)`.

**Use it for.** Predictability. The fade is monotonic in `p`, both endpoints
are exact, and it is alias-free like the Harmonic Bank. If you are sweeping `p`
with an LFO and want a morph you can rely on repeating identically, this is the
member to pick.

**Handling — this is where the three genuinely diverge.** Read the `u`
expression: `1 − 2/p` is zero at `p` = 2 and negative below it, and the clip
pins it at 0. **So below `p` = 2 this module does nothing at all** — it is a
pure sine across the entire bottom half of the knob, where the other two are
busy pinching into spikes. Park an LFO's range across `p` = 2 here and half its
travel is wasted motion.

The middle diverges too, more subtly: at intermediate `p` you are hearing a
sine plus a scaled square, not the power law's own spectrum. The three modules
agree exactly at `p` = 2 and broadly agree at the top of the range; everywhere
in between they are three different sounds, which is the entire reason all
three ship.

## 12.6 What all voices share

**They are persistent nodes, not one-shots.** The synth stays alive between
notes and through the release, which is what lets a parameter change take
effect on a note already sounding, and what lets a poly allocation lease extra
slots on the same instance. The cost: removing a module **while it is
sounding** cuts its release tail dead rather than letting it fade (§5.6).
Release the note first if you want the tail.

**Every voice has an ADSR opened by the gate, but they expose it
differently.** PW Pulse Pad gives you `attack` and `release` directly. FM Bell
and Pluck derive their whole shape from a single `decay`, because a struck
sound has no meaningful sustain to control. Wobble Saw and the three psine
voices carry fixed envelopes and give you none.

[FIG-12-10]

**They are level-matched.** Each voice carries an internal makeup gain chosen
so the seven sit at comparable loudness at their default `amp`. So cycling a
voice card's `target` chip through the sources to audition them will not blow
your ears out halfway down the list.

---
