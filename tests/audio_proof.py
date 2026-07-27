#!/usr/bin/env python3
"""Prove the rig runs with REAL AUDIO, unattended, with nobody at the keyboard.

    python3 tests/audio_proof.py            # boot, prove, tear down
    python3 tests/audio_proof.py --keep     # leave the rig up afterwards

Every check here reads the MASTER METER — `Amplitude.kr` on the post-volume
hardware output bus, polled off the running scsynth at ~15 Hz. That number
cannot be faked by a mock: it exists only if synthdefs compiled, the node
graph is executing in order, and a real CoreAudio device is running. It is
exactly the half that `tests/silent_rig.py` cannot reach.

What each check is FOR (i.e. what a headless run could not have told us):

1. unattended boot        — no human started anything, and the engine chose a
                            device that can actually start (audio_session).
2. silence is silent      — a fresh STOPPED rig is not humming; the meter
                            baseline is real, so checks 3-6 mean something.
3. notes sound WHILE THE
   TRANSPORT IS STOPPED   — item 32's "playable path intact" claim, which the
                            control plane can only assert structurally. Here
                            it is amplitude on the output bus.
4. note-off releases      — the envelope actually completes; no stuck voice
                            droning after the gate closes.
5. master volume is in
   the signal path        — `_master`'s ReplaceOut is really rewriting bus 0,
                            not just holding a number in Python.
6. transport start/stop   — starting and stopping the clock leaves the
                            playable path audible either way.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rig as rigmod  # noqa: E402
from rig import Rig  # noqa: E402

#: Meter is an amplitude in 0..1. A pad at default volume sits well above
#: this; a silent bus sits three orders of magnitude below it.
SOUNDING = 0.02
SILENT = 0.005

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def meter_peak(rig: Rig, since: int) -> float:
    """Loudest post-master frame the rig broadcast since `since`."""
    peak = 0.0
    for r in rig.records[since:]:
        if r.get("rec") != "recv" or r["msg"].get("type") != "meters":
            continue
        out = r["msg"].get("out") or [0.0, 0.0]
        peak = max(peak, max(float(x) for x in out))
    return round(peak, 5)


async def listen(rig: Rig, seconds: float) -> tuple[int, float]:
    """Collect meter frames for `seconds`; return (frames seen, peak)."""
    since = len(rig.records)
    await asyncio.sleep(seconds)
    frames = sum(
        1 for r in rig.records[since:]
        if r.get("rec") == "recv" and r["msg"].get("type") == "meters"
    )
    return frames, meter_peak(rig, since)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patch", default="pad_space")
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--keep", action="store_true", help="leave the rig running")
    ap.add_argument("-o", "--out", default=None, help="transcript .jsonl")
    args = ap.parse_args(argv)

    print(f"== booting {args.patch} with real audio (nobody is at the keyboard) ==")
    rigmod.kill_scsynth()

    async with Rig(patch=args.patch, transcript=args.out, keep=args.keep,
                   scenario="audio-proof") as rig:
        st = rig.state
        booted = rig.booted or {}
        check("1. rig booted unattended, with an engine",
              bool(booted) and not rig.silent,
              f"{booted.get('how', 'already up')} in {booted.get('seconds', '?')}s")
        print(f"      output device: {st.get('current_output')!r}   "
              f"input_enabled={st.get('input_enabled')}")
        if st.get("boot_note"):
            print(f"      boot_note: {st['boot_note']}")

        # -- 2. the meter exists, and silence reads as silence ---------------
        await rig.settle()
        frames, base = await listen(rig, 1.5)
        check("2. meter is live and a fresh rig is SILENT", frames > 5 and base < SILENT,
              f"{frames} frames, peak {base}")
        if frames == 0:
            print("\nno meter frames at all — the rig is not metering; "
                  "nothing below can be trusted.")
            return 1

        running = bool((st.get("transport") or {}).get("running"))
        print(f"      transport running={running} (fresh launch comes up STOPPED)")

        # -- 3. a note SOUNDS, transport stopped -----------------------------
        await rig.midi_enable()
        rig.note_on(args.note, 100)
        frames, loud = await listen(rig, 1.2)
        check("3. note_on makes actual sound with the transport STOPPED",
              loud > SOUNDING and loud > base * 5,
              f"peak {loud} vs silence {base}")

        # -- 5. master volume is really in the path (while it sounds) --------
        await rig.send({"type": "set_volume", "volume": 0.0})
        await asyncio.sleep(0.4)          # _master lags vol by 50 ms
        _, ducked = await listen(rig, 0.8)
        await rig.send({"type": "set_volume", "volume": st.get("volume", 0.8)})
        await asyncio.sleep(0.4)
        _, restored = await listen(rig, 0.8)
        check("5. master volume moves the OUTPUT BUS, not just a number",
              ducked < loud * 0.25 and restored > ducked * 4,
              f"loud {loud} -> vol0 {ducked} -> restored {restored}")

        # -- 4. note_off releases -------------------------------------------
        rig.note_off(args.note)
        await asyncio.sleep(1.5)          # let the release + echo/reverb tail die
        _, tail = await listen(rig, 1.5)
        check("4. note_off releases the voice — no stuck drone",
              tail < max(SILENT, loud * 0.1), f"tail peak {tail}")

        # -- 6. transport start/stop, with audio -----------------------------
        await rig.send({"type": "set_transport", "playing": True})
        await rig.poke()
        await asyncio.sleep(0.3)
        rig.note_on(args.note + 7, 100)
        _, playing = await listen(rig, 1.2)
        rig.note_off(args.note + 7)
        await asyncio.sleep(1.2)
        await rig.send({"type": "set_transport", "playing": False})
        await rig.poke()
        await asyncio.sleep(0.3)
        rig.note_on(args.note + 7, 100)
        _, stopped = await listen(rig, 1.2)
        rig.note_off(args.note + 7)
        await asyncio.sleep(1.0)
        check("6. the playable path is audible with the transport RUNNING "
              "and STOPPED",
              playing > SOUNDING and stopped > SOUNDING,
              f"running {playing}, stopped {stopped}")

        if rig.errors:
            check("no server-side errors", False, "; ".join(rig.errors[:3]))
        else:
            check("no server-side errors", True)

    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, {len(FAIL)} failed"
          + (f": {', '.join(FAIL)}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
