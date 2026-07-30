# 10. The Loop Deck

Capture a performance and let it play while you play over it. The deck is a
note looper, and where you wire it decides both what it captures and what it
comes back as.

---

## 10.1 What the deck records

**The deck records notes, not audio.** Nothing you loop is a recording of
sound; it is a list of what you played and when, replayed into the note
plane.

The consequence is bigger than it sounds. A take is not bound to the voice
you made it on — rewire the deck's output afterwards and the same phrase
plays through something else entirely, at whatever the target's settings are
*now*. Change the synth, change the filter, change the octave, and the loop
follows. Nothing is baked.

The deck moves through a small set of states — empty, armed, recording,
playing, overdubbing, stopped — and the card's subtitle always tells you
which one it is in. That subtitle is the only place live deck state appears.
REFERENCE §9.

## 10.2 The controls

Four actions: `record`, `play`, `stop`, `clear`. Each is a button on the
card and also a binary trigger input, so a button node, a clock or a logic
gate can drive the deck as easily as your mouse can (Chapter 7).

**Spacebar is loop-record** — worth learning, because arming by hand while
you are already playing is exactly the moment you do not have a spare hand.
The trap from §9.5.1 applies: if a card control has keyboard focus it eats
the key, so click empty canvas first.

Three settings:

- `bars` (1–8) — the length of the loop window. Changeable only while the
  deck is **empty or stopped**, which stops you resizing a window mid-take.
- `level` (0–1) — scales replay velocity, so the loop can sit under your
  live playing rather than competing with it.
- `overdub` — the layering toggle (§10.6).

[FIG-10-01]

## 10.3 Recording

[FIG-10-02]

Hit record and the deck **arms** rather than recording immediately. The
window start quantises to the next bar top, so your take begins on a bar
line no matter when you pressed the button.

While armed, you can play *into* the downbeat. A note struck up to 0.35
beats early clamps to beat 0 of the take — and if you release it before the
window actually opens, its note-off clamps too, so a grace note lands as a
grace note rather than as a note that never ends. In practice you can count
yourself in and play naturally across the bar line.

The window closes on its own after `bars` worth of beats. Every way out of a
record window — running to the end, stopping, switching overdub off —
synthesises note-offs for anything still held, so a take is always paired
and can never capture a note that has no ending.

Only wired sources reach the recording taps. A deck with nothing wired into
it arms, runs its window and captures nothing — which brings us to the next
section.

## 10.4 Raw vs voiced: where you wire the deck matters

The deck has two inputs, and choosing between them is the most useful
decision in this chapter.

**`keys → deck` records raw.** You get exactly what your fingers did: every
note you held, for as long as you held it. Chords stay chords, so replaying
the take through an arp arpeggiates it live, to whatever the arp's settings
are at that moment.

**`arp → deck` records voiced.** You get the arpeggiator's *output* — the
pattern it made from what you held, captured as individual notes. Once
recorded, that phrase is fixed: change the arp's pattern, division or
octaves afterwards and the take does not change, because the take is the
notes, not the recipe. That is usually what you want, because it lets you
keep arpeggiating live over a locked-in arp phrase.

Same card, same buttons, materially different material. Wire both if you
like — each feeds its own tap.

> **TRY THIS.** Hold one chord and record it twice, once from `keys` and
> once from `arp`. Play both takes back, then change the arp's `pattern`.
> The raw take changes with it; the voiced take does not.

There is a third thing a take is good for: **the tonic estimator can read
the deck.** A recorded phrase is clustered by onset into chord groups, and
those groups feed the estimator as context — so a deriver that has heard
your loop knows the progression, not just the current bar. Set the
estimator's `every` to `deck` and it commits at each chord-group boundary
instead of on a fixed grid, in step with the loop rather than chasing it. A
voiced take gives it a clean chord sequence; a raw take gives it exactly
what you played. REFERENCE §4.5 and §9, and §6.5 for the derivers.

[FIG-10-03]

## 10.5 Replaying into something else

Replay follows `deck → X` wires, resolved live — so you can rewire mid-loop
and the next note goes to the new destination.

- **`deck → arp`** feeds the replay into the arp's pool, so the loop gets
  arpeggiated as it plays. Recording back from the arp cannot re-record the
  deck's own replay, so `deck → arp → deck` is safe rather than a feedback
  trap.
- **`deck → voice`** plays a **private second node** of the primary voice's
  target module. It never steals your live voice — you keep playing the same
  synth over the top of the loop.
- **`deck → voice.2`** drives that voice instead, when you want the loop on
  its own signal path.
- **`deck → tonic.2`** lets a deriver hear the replay and follow its harmony.
- **`deck → keyshift.2:3`** rides shifter lane 3, so the loop can be
  transposed independently of what you are playing live.

**No outgoing wire means the loop spins silently.** It is still running,
still in time, still there when you wire it up — and it accounts for most
"why can't I hear my loop" moments. Check for the wire first.

## 10.6 Overdubbing and takes

Switch `overdub` on and a further pass layers on top of what is already
there rather than replacing it. Build a bass line, then a counter-line, then
a top part, each on its own pass, all inside the same window.

Switching overdub off exits the record window, which — as in §10.3 — closes
any notes still held.

`clear` empties the deck, which is also when you can change `bars` again.

[FIG-10-04]

## 10.7 Stopping cleanly

Stopping the deck closes its open notes and their monitor taps, so nothing
is left hanging on the audio side or on the display. Same closure rule as
§4.5 and §9.3: **a monitor bar that will not clear after you stop the deck
is a real bug, not a display glitch.** Hit panic to recover, and report it
with what you were doing at the time.

Stopping the **transport** rather than the deck has the same effect on
anything sounding — replay stops firing and releases what it was holding
(§9.3). The take itself is untouched; press play and it picks up on the
grid.

---
