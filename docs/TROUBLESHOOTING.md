# Troubleshooting

A symptom-indexed companion to [`HISTORY.md`](HISTORY.md), which tells the
full story behind most of these. If something seems broken, check here
before assuming you've found a new bug — and before re-deriving a fix that's
already been through one round of pain.

## scsynth / DSP gotchas (for module authors)

These are known to cause crashes or bad audio in scsynth-facing code.
They're empirical — hit once, worked around, not necessarily fully explained
by SuperCollider's own docs:

- **Avoid `.clip()`, a scaled `RecordBuf` source, and `EnvGen`-driven
  `record_level`** — each has caused scsynth crashes. If you need clipping,
  reach for a different UGen approach (e.g. `(sig).softclip()` or manual
  `min`/`max`) and test it with `python -m synthbase test` before trusting it.
- **Don't read `In.ar(bus=0)` directly.** Bus 0 can carry leftover signal
  from the root node's tail, not clean input. Use the bus your rack assigns
  you (the `in_bus` param for effects).
- **New source voices should spawn with `gate=0`**, not `1` — the envelope
  should open the gate, not the spawn itself.
- **Multiple sources writing to the same bus SUM**, they don't replace each
  other. This is standard SuperCollider bus semantics, but it surprises
  people coming from a DAW mental model where adding a second source might
  imply switching to it.
- **Sort looper events by beat with a stable sort.** An unstable sort can
  reorder same-beat events on replay in a way that's inaudible sometimes and
  glitchy other times — stable sort removes the ambiguity entirely.

## GUI / websocket gotchas (for GUI contributors)

- **The page's websocket watchdog closes a socket it thinks has gone silent
  after ~3 seconds.** If you're writing a GUI test with a mock socket, give
  it a no-op `close()` — otherwise the watchdog's reconnect-gap can drop
  sends mid-test. The real server streams meter data at ~20 Hz, so this
  watchdog never fires against a live connection; it only bites mocks.

## Runtime / hardware gotchas (for anyone running it)

- **"Setting sample rate failed" / no audio on boot.** macOS won't open
  input and output devices at different sample rates, and some Bluetooth
  headset mics are locked to 16 kHz (HFP mode). The engine tries to
  auto-select an input that matches the output's rate, preferring the
  built-in mic, and falls back to output-only with a visible note if it
  can't. If you're still stuck, try a wired mic or the built-in one.
- **Bluetooth headphones feel laggy.** A2DP Bluetooth audio adds roughly
  100–250 ms of latency on top of everything else. There's no software fix
  for this — use wired output or speakers for anything where timing matters.
- **A MIDI controller's panel knobs don't transmit MIDI, but the
  levers/pedals do.** Observed on a Roland CP88, plausibly true of other
  stage pianos — check `python -m synthbase devices` and a MIDI monitor
  before assuming a control is bound to the wrong CC when it might not be
  transmitting at all.
- **Audio stays silent (or the engine won't start) after a rough restart.**
  If two `scsynth` processes end up racing during a restart, CoreAudio can
  wedge. `run.sh` guards against this with a pidfile-based clean shutdown,
  but if you're restarting some other way: kill any stray `scsynth`
  processes, wait a moment, then retry. A full reboot clears it if nothing
  else does.
- **A slider or button swallows keyboard note input.** Only text/select
  form elements should hold keyboard focus in the GUI; if a control you
  added is eating key presses, make sure it blurs after use.

## If it's still not in this list

Check `HISTORY.md`'s "Bug story" call-outs — several hard-won gotchas are
told there in more detail than a one-line troubleshooting entry allows. If
you've genuinely found something new, file an issue with the bug report
template — include what you checked already so we don't retread the same
ground.
