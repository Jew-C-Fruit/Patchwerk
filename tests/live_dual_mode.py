#!/usr/bin/env python3
"""RIG CHECK — a dual module switches mode LIVE, in both directions.

CI_EXEMPT = "boots a real engine with audio; Mac-and-rig only"

Three suites already cover the pieces headlessly: `test_playable.py` proves a
dual is reachable by a voice, `test_graph.py` proves a wire into a dual lands
on its `in_bus`, and `check_blocks.py` proves the card's mode chip follows a
`<id>:mode` tap. None of them can prove the thing Cole actually asked about —
that the module MAKES THE RIGHT SOUND on each side of the switch, and changes
sound when the wire moves.

    python3 tests/live_dual_mode.py

Measured, per phase, off the master bus with a note held:

  1. GEN   — nothing wired in. A 440 Hz sine at p=2 (identity), THD ~0.
  2. FX    — a generator wired in, p=64. The psine law squares the input off:
             THD climbs an order of magnitude and odd harmonics appear at
             roughly 1/3, 1/5, 1/7 of the fundamental.
  3. GEN again — the wire cut. Back to the sine.

and the `{"kind":"level","ep":"<id>:mode"}` taps are read out of the
transcript, so the indicator's drive signal is asserted on the same run
rather than inferred from the mock.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis  # noqa: E402
from rig import Rig  # noqa: E402
from synthbase.wavio import read_wav  # noqa: E402

FAILURES: list[str] = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILURES.append(name)


async def _reply(rig: Rig, kind: str, timeout: float = 30.0) -> dict:
    since = len(rig.records)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for r in rig.records[since:]:
            if r.get("rec") == "recv" and r["msg"].get("type") == kind:
                return r["msg"]
        await asyncio.sleep(0.05)
    raise TimeoutError(kind)


def midi_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


async def capture(rig: Rig, note: int, seconds: float = 1.6) -> dict:
    await rig.send({"type": "capture", "action": "arm",
                    "target": "master", "seconds": seconds + 0.5})
    await asyncio.sleep(0.1)
    rig.note_on(note, 100)
    await asyncio.sleep(seconds)
    rig.note_off(note)
    await asyncio.sleep(0.35)
    await rig.send({"type": "capture", "action": "stop"})
    done = await _reply(rig, "capture_done")
    wav = read_wav(done["path"])
    # harmonic measurements are meaningless without the fundamental, and
    # analyze() omits them rather than guessing — so pass the note's Hz
    return analysis.analyze(wav.mono(), wav.sample_rate,
                            fundamental=midi_hz(note))


def mode_taps(rig: Rig, key: str) -> list[bool]:
    out = []
    for r in rig.records:
        if r.get("rec") != "recv":
            continue
        ev = (r["msg"].get("event") or {})
        if ev.get("kind") == "level" and ev.get("ep") == f"{key}:mode":
            out.append(bool(ev.get("on")))
    return out


async def main() -> int:
    async with Rig(patch="_live_gen", scenario="live_dual_mode") as rig:
        if rig.silent:
            print("FAIL  rig came up SILENT — no engine to listen to")
            return 1
        await rig.settle()
        await rig.midi_enable()
        await asyncio.sleep(0.3)

        # ---- 1. GEN: nothing wired in --------------------------------------
        gen = await capture(rig, 69)
        check("GEN: the dual makes sound with nothing wired in",
              not gen["silent"] and gen["rms"] > 0.01,
              f"rms={gen['rms']:.6f}")
        check("GEN: it is a 440 Hz tone",
              abs(gen["fundamental"] - 440.0) < 1.0,
              f"f0={gen['fundamental']}")
        check("GEN: p=2 is the identity curve, so ~no distortion",
              gen["thd"] < 0.05, f"thd={gen['thd']:.4f}")

        # ---- 2. FX: wire a generator in ------------------------------------
        await rig.send({"type": "spawn_module", "key": "power_sine_shaper"})
        await asyncio.sleep(0.5)
        await rig.send({"type": "graph_wire", "action": "add",
                        "from": "power_sine_shaper", "to": "power_shaper"})
        # set_param speaks NORMALISED units, not values: p is
        # param(1, 64, exp), so value == 64 ** unit. Sending "value" is
        # silently ineffective, which left p at 2.0 == the IDENTITY curve —
        # a first draft of this check "passed" while proving nothing.
        await rig.send({"type": "set_param", "key": "power_shaper",
                        "name": "p", "unit": 1.0})
        await asyncio.sleep(0.5)
        # the voice follows the FIRST playable module, which is still the dual;
        # aim it at the feeding generator so the dual is processing, not playing
        await rig.send({"type": "set_voice_target", "key": "power_sine_shaper",
                        "voice": "voice"})
        await asyncio.sleep(0.4)
        fx = await capture(rig, 69)
        check("FX: audio wired in still produces sound",
              not fx["silent"] and fx["rms"] > 0.01, f"rms={fx['rms']:.6f}")
        check("FX: the input is being SHAPED, not passed through",
              fx["thd"] > 5 * max(gen["thd"], 0.01),
              f"thd {gen['thd']:.4f} -> {fx['thd']:.4f}")
        h = fx["harmonics"]
        check("FX: odd harmonics appear (a square-off, per the psine law)",
              h["3"] > h["5"] > h["7"] > 0,
              f"3rd={h['3']:.4f} 5th={h['5']:.4f} 7th={h['7']:.4f}")

        # ---- 3. back to GEN: cut the wire ----------------------------------
        await rig.send({"type": "graph_wire", "action": "remove",
                        "from": "power_sine_shaper"})
        await rig.send({"type": "set_param", "key": "power_shaper",
                        "name": "p", "unit": 0.16666667})   # back to p=2
        await asyncio.sleep(0.5)
        await rig.send({"type": "set_voice_target", "key": "power_shaper",
                        "voice": "voice"})
        await asyncio.sleep(0.4)
        back = await capture(rig, 69)
        check("GEN again: cutting the wire returns it to generating",
              not back["silent"] and back["rms"] > 0.01,
              f"rms={back['rms']:.6f}")
        check("GEN again: a clean 440 Hz tone once more",
              abs(back["fundamental"] - 440.0) < 1.0
              and back["thd"] < 0.05,
              f"f0={back['fundamental']} thd={back['thd']:.4f}")

        # ---- the indicator's drive signal, on the same run -----------------
        taps = mode_taps(rig, "power_shaper")
        check("the wire edits emitted <id>:mode taps at all", bool(taps),
              str(taps))
        check("…and they go TRUE then FALSE, matching the two edits",
              True in taps and False in taps
              and taps.index(True) < len(taps) - 1 - taps[::-1].index(False),
              str(taps))

        print(json.dumps({"gen": {k: gen[k] for k in ("rms", "fundamental", "thd")},
                          "fx": {k: fx[k] for k in ("rms", "fundamental", "thd")},
                          "gen_again": {k: back[k] for k in ("rms", "fundamental", "thd")},
                          "mode_taps": taps}, indent=2))
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
