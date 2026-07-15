# SuperSynth

A synthesizer base designed for **vibecoding**: modules are small Python
files, hot-reloaded into a running audio engine; the signal path is a live
**patch graph** — audio wires and note wires alike — edited in the browser;
MIDI/sensors/GUI all land in the same control layer.

Engine: [SuperCollider](https://supercollider.github.io/)'s `scsynth` server
(a separate, crash-isolated process). Control plane: Python via
[supriya](https://github.com/supriya-project/supriya).

## What it does today

- **Flex GUI** — a subway-map patch canvas at `/`: spawn any module multiple
  times ("lowpass.2"), drag wires between cards, splice by dropping a card
  (or a wire's label) onto a wire, cut with a click. Legacy panel at `/legacy`.
- **Rewireable audio graph** — fan-in sums, disconnected outputs park
  silently, execution order stays legal after any rewire.
- **Wire-defined control plane** — keys, arpeggiator, MIDI loop deck, mono
  voices, tonic derivers (root-finding) driving drone modules, and a 4-lane
  key shifter with a bar-synced key-progression track. What's wired is what
  plays.
- **Performance kit** — transport (tempo/meter/click, play/stop), 16-step
  drum machine, LFOs on any param, presets, oscilloscope/waveform/note
  monitors (local when wired, global when not).
- **Vibecoding core** — hot reload on save; a broken module prints an error
  and keeps the old sound playing.

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
./run.sh                                   # flex GUI at http://127.0.0.1:8765
python -m synthbase gui pad_space          # same, choosing a patch
python -m synthbase play patches/demo.py   # headless: saw -> filter -> echo
```

While the synth runs, edit any file in `modules/` and save — the running
sound updates without a restart. A broken edit prints an error and keeps the
old version playing.

## Layout

| Path | What it does |
| --- | --- |
| `modules/` | DSP modules — one small Python file each. **The vibecoding surface.** |
| `patches/` | Plain-data chain + MIDI binding definitions |
| `gui/flex.html` | The patch-canvas GUI (front door); `gui/index.html` = legacy panel |
| `synthbase/module.py` | Module contract (`@module` + `@synthdef`) and loader |
| `synthbase/engine.py` | Boots/quits scsynth, registers synthdefs |
| `synthbase/rack.py` | Instance ids, bus wiring, live rewiring, param control |
| `synthbase/app.py` | The whole running system: audio graph + control-plane wiring |
| `synthbase/server.py` | Web GUI server + websocket protocol (see its docstring) |
| `synthbase/midi.py` | MIDI notes (mono voices) and CC bindings → rack params |
| `synthbase/{arp,looper,drums,drone,keyshift,lfo,transport}.py` | The players: arpeggiator, loop deck, drum machine, tonic deriver, key shifter, LFOs, clock |
| `synthbase/watcher.py` | Hot reload of module files into the running rack |
| `tests/` | Engine-free suites: `smoke`, `test_graph`, `test_looper`, `gui_check*` (Playwright) |
| `CLAUDE.md` | House rules + module template for LLM-driven development |
| `docs/HISTORY.md` | The build story, version by version — bugs and all |

## Roadmap

Sensor input over serial (same binding layer as MIDI), polyphony as a
voice-allocation wrapper, more control-modifier nodes in the key-shifter
mold (chord memory, strummers, humanizers).
