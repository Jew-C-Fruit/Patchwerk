"""A rig with no engine: the real server, the real control plane, no audio.

    .venv/bin/python tests/silent_rig.py --port 8799

Item 37 Phase 1. `SynthApp.__init__` touches no engine — `app.start(patch)`
is what boots scsynth — and a bare `SynthApp` already answers `state()` with
the full 37-key snapshot. So this serves the REAL `GuiServer` over a REAL
`SynthApp` and simply never calls `start()`: no scsynth, no audio device, no
sound, and every message that lives above the rack still works.

What that buys, and it is not a consolation prize:

* the whole binary/control plane is exercisable with no audio hardware —
  buttons, clocks, logics, relays, keyshifts, tonics, wires, the transport,
  and the `{"kind":"level"}` reactive taps that item 37 exists to assert;
* a scenario can be RECORDED into a transcript on a machine with no audio,
  which is what Phase 2 replays;
* it boots in well under a second instead of ~12, so a driver check is not
  hostage to a cold scsynth.

What it CANNOT do, and no wishful thinking about it: anything that needs the
rack. No modules, no audio wires, no meters (`levels()` answers zeros), and
**no MIDI** — `_restart_midi` returns early without a rack, so the virtual
port has nothing to talk to. Use the real rig for those, and
`tests/probe_virtual_midi.py` for the MIDI path on its own.

It is deliberately NOT a fake: nothing here stubs, patches or monkeypatches
Patchwerk. The only difference from a real rig is a `start()` that was never
called, and every message is handled by the same `_handle` chain the browser
talks to.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.server import GuiServer  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args(argv)
    app = SynthApp(use_midi=False, use_reload=False)   # NO app.start()
    server = GuiServer(app, port=args.port)
    print(f"[silent-rig] no engine, no audio — http://127.0.0.1:{args.port}",
          flush=True)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
