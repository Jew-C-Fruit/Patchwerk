# 2. Machine setup

Everything between a bare Mac and a playable instrument: SuperCollider, the
Python side, audio, MIDI, and the command you will type every day.

---

## 2.1 Requirements

| | What | Notes |
| --- | --- | --- |
| **Operating system** | macOS | v2.2 is developed and verified on 15.5. Patchwerk runs on Windows too — see the repo README — but this manual is written for the Mac. |
| **Python** | 3.10 or newer | The reference machine runs 3.14. |
| **SuperCollider** | 3.14.1 | Only the `scsynth` server is used. `sclang` and the SuperCollider IDE are **not** — you never open the application. |
| **Audio output** | any CoreAudio device | The system default is used unless you choose otherwise. Wired, please. |
| **Audio input** | optional | Only needed for the **Audio In** module. The engine runs output-only without one. |
| **MIDI controller** | optional | Without one, the computer keyboard plays (§2.6). |

## 2.2 Installing SuperCollider

```bash
brew install --cask supercollider
```

Prove `scsynth` works before Python is anywhere near it — half of all setup
failures live in this one binary.

> **WARN — `scsynth` is not on your `PATH`, and never will be.** It lives
> inside the application bundle, at
> `/Applications/SuperCollider.app/Contents/Resources/scsynth`. `which
> scsynth` finds nothing. This is normal, and it is the first thing people
> trip over.

Verify it by full path:

```bash
/Applications/SuperCollider.app/Contents/Resources/scsynth -v
```

That should print something like `scsynth 3.14.1 (Built from tag
'Version-3.14.1' [426edf6])`.

**Patchwerk finds a standard Homebrew cask install on its own.** You only
need the `PATH` export below if you want to type `scsynth` yourself — add it
to `~/.zshrc` and open a new terminal:

```bash
export PATH="/Applications/SuperCollider.app/Contents/Resources:$PATH"
```

If SuperCollider lives somewhere else — a different volume, a renamed bundle
— point the engine at it explicitly:

```bash
export SUPRIYA_SERVER_EXECUTABLE="/path/to/scsynth"
```

Without that, a non-standard install fails at boot with `Failed to locate
executable` before a single sound is possible.

## 2.3 Setting up the Python side

Clone the repo, make a virtual environment, install the dependencies:

```bash
git clone https://github.com/Jew-C-Fruit/Patchwerk.git
cd Patchwerk
/opt/homebrew/bin/python3.14 -m venv .venv     # any Python 3.10+
source .venv/bin/activate
pip install -r requirements.txt
```

**Every command in the rest of this manual assumes that venv is active.** If
a command reports a missing module, the first thing to check is that
`source .venv/bin/activate` has been run in this terminal.

## 2.4 Proving the engine works

One command, and it is the load-bearing checkpoint of this chapter:

```bash
python -m synthbase test
```

It boots the engine, plays a 440 Hz sine for two seconds and prints
`OK — engine boots and makes sound.`

The first time you run it, macOS asks to let the terminal use audio input.
**Grant it.** Deny it and Patchwerk still plays — the engine falls back to
output-only — but the **Audio In** module will have nothing to listen to. If
the prompt never appears and the boot simply hangs, that is the same
permission being refused silently rather than asked for.

If this command does not end in that `OK`, stop. Nothing later in this manual
will work, and the fix is in §2.9 — not further down the page.

[FIG-02-01]

## 2.5 Audio devices

[FIG-02-02]

With no flags, the engine uses the system default input and output. To choose
at launch, pass `--in-device` and `--out-device` with CoreAudio device names.
To choose while running, use the device picker on the **Master Out** card;
Chapter 16 covers the routing around it.

> **WARN.** Changing devices rebuilds the engine. The old scsynth is stopped
> and a new one started, which takes seconds and is audibly silent while it
> happens. Your patch and master volume survive it; anything mid-flight does
> not. Do not do it eight bars into a take. See REFERENCE §3.6.

### 2.5.1 Sample rate and buffer size

The engine boots with a block size of 64 and a hardware buffer of 256 frames
— about 5 ms at 48 kHz. Override the buffer with `--hw-buffer` if you need
lower latency and your interface can take it (REFERENCE §3.6).

macOS refuses to open input and output at different sample rates. When that
happens the engine auto-selects a rate-matched input, preferring the built-in
microphone, and falls back to output-only with a visible note if it cannot
find one. **The Bluetooth trap** is the usual cause: a Bluetooth headset's
microphone locks to 16 kHz in HFP mode and can never match your output, so
the boot fails with *"Setting sample rate failed"*. The same headphones cost
you 100–250 ms of latency even when they do work. Both are written up
symptom-first in `docs/TROUBLESHOOTING.md`.

## 2.6 MIDI devices

List what the machine can see:

```bash
python -m synthbase devices
```

Patchwerk opens exactly one port: the one you name, or failing that the first
non-IAC hardware port, or failing that the first port at all. You can switch
ports from the GUI while running, or start with `--no-midi` to leave MIDI off
entirely. Notes land on the `keys` node, the pitch wheel bends every voice by
±2 semitones, CC 64 is sustain, and everything else follows your patch's `cc`
bindings (REFERENCE §8.4).

**With no controller attached, the computer keyboard is your keyboard.** Tick
**keys** in the top bar — it is on by default — and the home row plays:
`a w s e d f t g y h u j k o l p ;` covers an octave and a bit from C, `z`
and `x` shift octave down and up, and Caps Lock is a sustain pedal.

> **WARN.** If a slider or button on a card has keyboard focus, it eats your
> key presses and the keyboard goes dead. Click empty canvas and try again.
> This is the single most common "the keyboard stopped working" report.

[FIG-02-03]

## 2.7 Run modes

| Command | Use it when |
| --- | --- |
| `python -m synthbase test` | proving audio works — a diagnostic, nothing more |
| `python -m synthbase play patches/demo.py` | you want the sound and none of the interface: a fixed patch with its MIDI bindings and hot reload, but no GUI process at all |
| `python -m synthbase gui <patch>` / `./run.sh` | the normal case — the full instrument, browser UI included |
| `python -m synthbase devices` | listing MIDI hardware and exiting |

REFERENCE §1.1 has the flags.

[FIG-02-04]
[FIG-02-05]

## 2.8 Launching the GUI

```bash
./run.sh                    # the pad_space patch
./run.sh demo               # a named patch
./run.sh demo --no-browser  # extra flags pass straight through
```

It prints `Patchwerk running (pid …) — http://127.0.0.1:8765` when the page
is actually answering, logs to `/tmp/synth_gui.log`, and records the process
id in `/tmp/patchwerk.pid`.

Use it rather than `python -m synthbase gui`. `run.sh` kills any surviving
instance, clears stray `scsynth` processes, refuses to launch into a port
that is still held, and waits until the page returns HTTP 200 instead of
guessing at a fixed wait. Launching by hand skips all of that, and a
surviving instance on port 8765 is the most common self-inflicted failure
there is.

> **WARN.** `./run.sh` with no argument loads `pad_space`; `python -m
> synthbase gui` with no argument loads `demo`. Name the patch if it matters.

## 2.9 When it does not work

`docs/TROUBLESHOOTING.md` is indexed by symptom and owns this material. This
is only a signpost.

| Symptom | Likely cause | Go to |
| --- | --- | --- |
| `Failed to locate executable` on boot | scsynth not found — non-standard SuperCollider install | §2.2 |
| *"Setting sample rate failed"*, or silence on boot | input and output at different rates; usually a Bluetooth headset mic at 16 kHz | TROUBLESHOOTING, "Runtime / hardware gotchas" |
| Silence, or the engine will not start, after a rough restart | two scsynth processes raced and wedged CoreAudio | TROUBLESHOOTING, same section |
| `STARTUP PROBLEM — port 8765 is still held` | an earlier instance outlived its pidfile; `run.sh` prints the pids to kill | §2.8 |
| Everything feels laggy | A2DP Bluetooth output, 100–250 ms. No software fix | TROUBLESHOOTING, same section |
| The keyboard stops playing notes, or a controller knob sends nothing | a GUI control has keyboard focus; or that knob does not transmit MIDI at all | §2.6 |

> **WARN — one known failure has no fix yet.** scsynth can enumerate your
> audio devices correctly and then never start one, leaving the engine
> waiting on a server that has booted but is not running audio. It is not
> caused by anything you did, and it is not the sample-rate fallback above.
> Kill every stray `scsynth`, wait a moment, and relaunch with `./run.sh`; a
> full reboot clears it when nothing else does.

---
