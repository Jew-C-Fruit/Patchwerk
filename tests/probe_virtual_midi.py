"""Virtual-MIDI probe: can an agent play this machine at all? (item 37 Phase 1)

    .venv/bin/python -u tests/probe_virtual_midi.py

Mac-only (it needs CoreMIDI) but it needs NO rig, NO scsynth and NO audio
device — deliberately, because it answers the one question that has to be
answered before any of the rest is worth booting: is there a MIDI input for
an agent to play, and does the engine's own router accept it?

`synthbase.midi.list_inputs()` on this Mac returns `[]` — no hardware is
attached — and `MidiRouter.start()` returns immediately on an empty list, so
today an agent has no MIDI at all. This creates an rtmidi VIRTUAL port and
drives the REAL `MidiRouter` against a stub rack: the port shows up in
`list_inputs()`, the router's own selection rule accepts it, and note / CC /
sustain / bend arrive through the real rtmidi callback thread with the real
±2-semitone bend maths. The rack is stubbed and only the rack is stubbed.

The port name carries this process's PID, and the router is opened with that
name EXPLICITLY — which is what `Rig.midi_enable()` does. Asserting on
`start()`'s no-name auto-pick instead would be asserting `hardware[0]`, and
that is a coin flip the moment another session on this Mac has a virtual
port of its own open (observed 2026-07-26, and it turned this probe red).

`tests/probe_rig_ws.py` is the same path end to end through a live rig;
this is the part of it that survives a machine whose audio is broken.
"""

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import mido  # noqa: E402

from synthbase.midi import BEND_RANGE_SEMITONES, MidiRouter, list_inputs  # noqa: E402

sys.path.insert(0, str(REPO / "tests"))
import rig as R  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


class StubRack:
    """Just enough rack for the router: record what it was told to set."""

    def __init__(self):
        self.calls = []

    def set_params(self, key, **kw):
        self.calls.append((key, kw))

    def set_param(self, key, name, value):
        self.calls.append((key, {name: value}))

    def find(self, key):
        raise KeyError(key)


def main() -> int:
    name = f"{R.VIRTUAL_PORT_BASE} Probe {os.getpid()}"
    before = list_inputs(force=True)
    print(f"inputs before: {before}")
    check("no hardware MIDI is masking the result (informational)", True,
          str(before))

    out = mido.open_output(name, virtual=True)
    try:
        time.sleep(0.4)
        names = list_inputs(force=True)
        check("the virtual port is visible to synthbase.list_inputs()",
              any(name in n for n in names), str(names))

        # the router's own filter (midi.py:155) — ours must survive it, but
        # NOT necessarily be first: another session's port may also be open
        hardware = [n for n in names if "iac" not in n.lower()]
        check("the router's hardware filter keeps it (it is not an IAC bus)",
              any(name in n for n in hardware), str(hardware))
        if len(hardware) > 1:
            print(f"  (note: {len(hardware)} virtual/hardware ports present — "
                  "auto-pick would be ambiguous, which is why the driver "
                  "always names its port explicitly)")

        rack = StubRack()
        events = []
        router = MidiRouter(rack, notes_to="target", port_name=name,
                            verbose=False, on_event=events.append)
        router.start()
        check("router.start() opened OUR port when named explicitly",
              router.active_port is not None and name in router.active_port,
              str(router.active_port))
        if router.active_port is None:
            return 1

        out.send(mido.Message("note_on", note=60, velocity=96))
        time.sleep(0.25)
        freqs = [kw.get("freq") for _, kw in rack.calls if "freq" in kw]
        check("a note reaches the voice as a frequency",
              freqs and abs(freqs[-1] - 261.63) < 0.1, str(rack.calls[-1:]))
        check("the note opens the gate",
              any(kw.get("gate") == 1 for _, kw in rack.calls), str(rack.calls))

        n_before = len(rack.calls)
        out.send(mido.Message("pitchwheel", pitch=4096))
        time.sleep(0.25)
        bends = [e for e in events if e.get("kind") == "bend"]
        want = (4096 / 8192.0) * BEND_RANGE_SEMITONES
        check("pitch bend arrives at the ±2-semitone scale",
              bends and abs(bends[-1]["semitones"] - want) < 0.001, str(bends))
        bent = [kw.get("freq") for _, kw in rack.calls[n_before:] if "freq" in kw]
        check("the bend retunes the sounding note",
              bent and abs(bent[-1] - 261.63 * 2 ** (want / 12)) < 0.1, str(bent))
        out.send(mido.Message("pitchwheel", pitch=0))
        time.sleep(0.15)

        out.send(mido.Message("control_change", control=64, value=127))
        time.sleep(0.2)
        sus = [e for e in events if e.get("kind") == "sustain"]
        check("CC 64 arrives as sustain", sus and sus[-1]["on"] is True, str(sus))

        n_before = len(rack.calls)
        out.send(mido.Message("note_off", note=60))
        time.sleep(0.25)
        check("the pedal holds the note (no gate 0 while sustained)",
              not any(kw.get("gate") == 0 for _, kw in rack.calls[n_before:]),
              str(rack.calls[n_before:]))
        out.send(mido.Message("control_change", control=64, value=0))
        time.sleep(0.25)
        check("releasing the pedal closes the gate",
              any(kw.get("gate") == 0 for _, kw in rack.calls[n_before:]),
              str(rack.calls[n_before:]))

        out.send(mido.Message("control_change", control=74, value=100))
        time.sleep(0.25)
        ccs = [e for e in events if e.get("kind") == "cc" and e.get("cc") == 74]
        check("an unbound CC surfaces with its unit value",
              ccs and abs(ccs[-1]["unit"] - 100 / 127) < 0.01, str(ccs))

        router.stop()
    finally:
        out.close()
        time.sleep(0.3)

    check("the port disappears again on close",
          not any(name in n for n in list_inputs(force=True)),
          str(list_inputs(force=True)))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
