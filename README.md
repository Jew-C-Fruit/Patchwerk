# SuperSynth

A synthesizer base designed for **vibecoding**: modules are small Python
files, hot-reloaded into a running audio engine; patches (which modules, in
which order) are plain data; MIDI/sensors/GUI all land in the same control
layer.

Engine: [SuperCollider](https://supercollider.github.io/)'s `scsynth` server
(a separate, crash-isolated process). Control plane: Python via
[supriya](https://github.com/supriya-project/supriya).

## Setup (macOS)

```bash
brew install --cask supercollider
/opt/homebrew/bin/python3.14 -m venv .venv     # any Python 3.10+
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick start

```bash
python -m synthbase test                   # boots engine, plays 2s sine
python -m synthbase devices                # lists MIDI inputs
python -m synthbase play patches/demo.py   # saw -> filter -> echo, MIDI-playable
python -m synthbase play patches/mic_fx.py # mic through effects (headphones!)
```

While `play` runs, edit any file in `modules/` and save — the running sound
updates without a restart. A broken edit prints an error and keeps the old
version playing.

## Layout

| Path | What it does |
| --- | --- |
| `modules/` | DSP modules — one small Python file each. **The vibecoding surface.** |
| `patches/` | Plain-data chain + MIDI binding definitions |
| `synthbase/module.py` | Module contract (`@module` + `@synthdef`) and loader |
| `synthbase/engine.py` | Boots/quits scsynth, registers synthdefs |
| `synthbase/rack.py` | Instantiates the chain in order, wires buses, live param control |
| `synthbase/midi.py` | MIDI notes (mono voice) and CC bindings → rack params |
| `synthbase/watcher.py` | Hot reload of module files into the running rack |
| `synthbase/cli.py` | `devices` / `test` / `play` commands |
| `CLAUDE.md` | House rules + module template for LLM-driven development |

## Roadmap

Mono chain now. Next: sensor input over serial (same binding layer as MIDI),
polyphony as a voice-allocation wrapper, then a browser GUI that edits the
patch layer over WebSocket.
