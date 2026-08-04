"""Live probe for the RIG DRIVER itself (item 37 Phase 1; Mac only).

    .venv/bin/python -u tests/probe_rig_ws.py           # attach if a rig is up
    SS_PORT=8799 .venv/bin/python -u tests/probe_rig_ws.py   # nothing there: BOOT one
    SS_PORT=8799 .venv/bin/python -u tests/probe_rig_ws.py --silent   # no audio needed

Proves the half of `tests/rig.py` that CI cannot: that it attaches to or
boots a real rig, that VIRTUAL MIDI actually round-trips through the real
rtmidi callback thread into the engine, that the transcript on disk is a
faithful record of what came back, and that the rig is left exactly as it
was found.

The MIDI assertions are the point. `{"type":"note_on"}` over the websocket
enters at `SynthApp.note_on` and proves nothing about MIDI; these notes
enter through CoreMIDI, `MidiRouter._handle`, and the ±2-semitone bend and
CC-64 sustain paths that only hardware normally reaches.

It attaches to whatever is on `SS_PORT` (8765 by default) and boots one only
if nothing answers — so point it at a spare port to exercise boot/teardown,
and run it bare to exercise the attach path against Cole's live rig.

`--silent` boots `tests/silent_rig.py` instead — the real server over an
engine-less `SynthApp`. Everything above the rack is then exercised for real
with no audio device at all; the MIDI section is SKIPPED and says so, because
a rackless app never starts its router. That mode is the one to reach for
when scsynth cannot boot (this Mac, 2026-07-26: the CoreAudio driver
intermittently never reaches "server ready").

Rig safety: baseline captured at connect and restored at exit (the driver
does it), and teardown only ever touches a rig this probe started.
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import rig as R          # noqa: E402
import transcript as T   # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


async def _midi_section(rig) -> None:
    """Everything that only real MIDI can prove. Needs a rack — real rig only."""
    full = await rig.midi_enable()
    print(f"router opened {full!r}")
    check("state.midi_inputs sees the virtual port",
          any(rig.midi_name in n for n in rig.state.get("midi_inputs") or []),
          str(rig.state.get("midi_inputs")))
    check("the router ACTUALLY opened it (set_midi can fail silently)",
          rig.state.get("midi_port") == full, str(rig.state.get("midi_port")))

    since = rig.mark_index()
    rig.mark("midi")
    await rig.play(60, seconds=0.30, velocity=96)
    await rig.sleep(0.25)
    taps = [e for e in rig.events("tap", since=since)
            if e.get("src") == "keys"]
    check("a virtual-port note reaches the ctl plane as a keys tap",
          [t["on"] for t in taps] == [True, False]
          and {t["note"] for t in taps} == {60}, str(taps))

    since2 = rig.mark_index()
    rig.bend(1.0)
    await rig.sleep(0.3)
    bends = rig.events("bend", since=since2)
    check("pitch bend round-trips at the engine's ±2 semitone scale",
          bends and abs(bends[-1].get("semitones", 0) - 1.0) < 0.02,
          str(bends))
    rig.bend(0.0)

    since3 = rig.mark_index()
    rig.sustain(True)
    rig.sustain(False)
    await rig.sleep(0.3)
    sus = rig.events("sustain", since=since3)
    check("CC 64 arrives as sustain on then off",
          [s.get("on") for s in sus] == [True, False], str(sus))

    since4 = rig.mark_index()
    rig.cc(74, 100)
    await rig.sleep(0.3)
    ccs = [e for e in rig.events("cc", since=since4) if e.get("cc") == 74]
    check("an unbound CC surfaces with its unit value",
          ccs and abs(ccs[-1].get("unit", 0) - 100 / 127) < 0.01, str(ccs))


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", default="pad_space")
    ap.add_argument("--silent", action="store_true",
                    help="boot the engine-less silent rig (no audio, no MIDI)")
    ap.add_argument("--keep", action="store_true", help="leave a booted rig up")
    args = ap.parse_args(argv)

    p = R.rig_port()
    print(f"scsynth: {R.find_scsynth()}")
    check("scsynth is discoverable without PATH", R.find_scsynth() is not None)
    was_up = await R.is_up(p)
    print(f"port {p}: {'a rig is already up' if was_up else 'nothing listening'}")

    tdir = Path(tempfile.mkdtemp(prefix="rig-probe-"))
    tpath = tdir / "probe.jsonl"

    async with R.Rig(patch=args.patch, p=p, transcript=tpath,
                     scenario="probe_rig_ws", keep=args.keep,
                     silent=args.silent) as rig:
        check("connected and holding a state snapshot",
              bool(rig.state.get("modules") is not None
                   or rig.state.get("ctl_wires") is not None),
              str(sorted(rig.state))[:160])
        if rig.booted:
            print(f"booted via {rig.booted['how']} in {rig.booted['seconds']}s "
                  f"(pid {rig.booted['pid']})")
        check("attached rigs are never torn down",
              (rig.booted is None) == was_up)

        # -- the poke idiom: a silent hot path becomes observable ----------
        st = await rig.poke()
        check("poke() returns a fresh state", st.get("type") == "state")

        # -- spawn by diff -------------------------------------------------
        bid = await rig.spawn("spawn_button")
        check("spawn() returns the new id, by diff",
              bid in R.ids_in(rig.state, "buttons"), str(bid))

        # -- wire, then splice it out again ---------------------------------
        await rig.wire(bid, "transport:tap")
        check("wire() lands in state.ctl_wires",
              {"from": bid, "to": "transport:tap"} in rig.state["ctl_wires"],
              str(rig.state["ctl_wires"]))
        since0 = rig.mark_index()
        await rig.send({"type": "set_button", "id": bid, "latch": True})
        await rig.send({"type": "fire_button", "id": bid})
        await rig.sleep(0.4)
        check("a fired button reaches the gate plane as a level tap",
              any(e.get("ep", "").startswith("transport")
                  for e in rig.events("level", since=since0))
              or bool(rig.events("gate", since=since0)),
              str(rig.events(since=since0)[-4:]))
        await rig.unwire(bid, "transport:tap")

        # -- VIRTUAL MIDI ---------------------------------------------------
        if args.silent:
            print("--silent: SKIPPING the MIDI section — a rackless app never "
                  "starts its router. Use tests/probe_virtual_midi.py.")
        else:
            await _midi_section(rig)
        # -- no stuck notes, live ------------------------------------------
        rig.mark("stuck-check")
        check("nothing left open after the note/sustain traffic",
              T.unpaired_notes(rig.records) == [],
              str(T.unpaired_notes(rig.records)))
        check("the rig reported no errors", not rig.errors, str(rig.errors))

        booted_note = bool(rig.booted)

    # -- after teardown: baseline and transcript ---------------------------
    t = T.read_transcript(tpath)
    check("transcript parses", len(t.records) > 5, str(len(t.records)))
    check("transcript header names the run",
          t.header.get("scenario") == "probe_rig_ws"
          and t.header.get("port") == p, str(t.header))
    check("transcript recorded server state broadcasts", bool(t.recv("state")))
    kinds = sorted({e.get("kind") for e in t.events()})
    check("transcript recorded the event traffic",
          bool(t.events()) if args.silent
          else (bool(t.events("tap")) and bool(t.events("cc"))), str(kinds))
    check("transcript recorded what the driver SENT",
          any(m.get("type") in ("fire_button", "spawn_button")
              for m in t.sends()), str(t.sends()[:3]))
    if not args.silent:
        check("transcript recorded the injected MIDI",
              any(m.get("type") == "note_on" for m in t.midi()), str(t.midi()[:3]))
    check("transcript closes with a final state", t.final_state is not None)
    check("marks segment the transcript",
          bool(t.marks()) and (args.silent or "midi" in t.marks()), str(t.marks()))
    norm = T.normalize(t.records)
    check("the transcript normalises (Phase 2 replays this)",
          bool(norm) and not any(
              r.get("msg", {}).get("type") in T.DEFAULT_DROP for r in norm))
    print(f"transcript: {tpath}  ({len(t.records)} records, "
          f"types={dict(t.types())})")

    if booted_note and not args.keep:
        check("a rig we booted is gone again", not await R.is_up(p))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
