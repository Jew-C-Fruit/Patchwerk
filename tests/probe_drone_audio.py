#!/usr/bin/env python3
"""Live AUDIO proof for item 29's drone allocation.

    .venv/bin/python -u tests/probe_drone_audio.py

Mac only, real audio required. Amplitude comes from the MASTER METER, the
same post-volume `Amplitude.kr` `tests/audio_proof.py` reads; PITCH comes
from the target instance's broadcast `freq` param, which is exact and needs
no ear.

Five things a headless test cannot settle:

1. **A drone is silent until POWER _and_ the transport.** The effective gate
   is `power AND transport.running` (item 32's invariant in allocation
   terms). Structurally that is one boolean; audibly it is the difference
   between a patch that sounds on launch and one that does not.
2. **A drone HOLDS.** Note-off falls back, and an empty held set keeps the
   last root sounding. In amplitude: the meter must NOT drop on note-off,
   which is precisely the opposite of every other allocation.
3. **POWER must not bypass the target.** A drone and a poly voice lease
   different slots on ONE source; if power went through `set_enabled` it
   would silence the poly sharing it. Check 8 holds poly notes down while
   power falls, and requires them to keep sounding.
4. **The transpose change.** The drone now follows global transpose — it
   used to sit exactly `transpose` semitones away from the rest of the
   patch. Checks 10-12 read `freq` off both the drone's target and a mono
   voice's target and require them EQUAL under transpose, which is the
   literal statement of the bug that was fixed.
5. **Bend still does not move it, on purpose.** Check 13 pins that, so a
   later "consistency" fix has something that fails.

Effects are bypassed for the same reason as in `probe_poly_audio.py`:
autopan modulates amplitude and the tails smear every edge being measured.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import rig as R  # noqa: E402
from rig import Rig  # noqa: E402
from synthbase.allocation import midi_to_freq  # noqa: E402

SOUNDING = 0.02
SILENT = 0.006
BYPASS = ("drive", "lowpass", "echo", "reverb", "autopan")

#: Window for the RATIO measurement in check 8. The rest of the probe
#: compares against SOUNDING/SILENT, where a short window is fine; a ratio
#: divides two measurements and inherits both their spreads, so the summed
#: case wants long enough to reach its constructive peak. Measured floor of
#: `both`/`drone_only` by window, with pwm and detune pinned off:
#:
#:     0.8 s -> 2.67x        2.5 s -> 2.84x
#:
#: Most of the work is done by the two pinned params, not by the window —
#: this is margin, cheaply bought.
RATIO_WINDOW = 2.5

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(("ok    " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


async def void_if_dead(rig: Rig) -> bool:
    """VOID the run if the ENGINE, not the drone, is what went quiet.

    Two failure modes on this Mac, only the first of which announces itself:
    scsynth exits mid-run (supriya says `Server offline!`), or the pinned
    output-only device simply STOPS while scsynth stays alive metering
    zeros. The second is caught with a POSITIVE CONTROL — one note through
    the patch's own mono voice. `audio_proof.py`, which contains no
    allocation code, fails the same way; this is environmental, not item 29.
    """
    if any("offline" in str(e).lower() for e in rig.errors):
        print("\n!! ENGINE DIED MID-RUN — scsynth reported offline. The "
              "results above are VOID,\n!! not evidence about the drone. "
              "Re-run.")
        print(f"!! rig.errors: {rig.errors[:3]}")
        return True
    await rig.wire("keys", "voice")
    await rig.poke()
    rig.note_on(60, 100)
    await asyncio.sleep(0.4)
    control = await listen(rig, 1.0)
    rig.note_off(60)
    await asyncio.sleep(0.4)
    await rig.unwire("keys", "voice")
    if control < SOUNDING:
        print(f"\n!! NO AUDIO FROM THE ENGINE — the positive control (one "
              f"note through the patch's\n!! own mono voice) measured "
              f"{control}. The device stopped producing. Results\n!! above "
              f"are VOID. Re-run.")
        return True
    return False


async def listen(rig: Rig, seconds: float) -> float:
    since = len(rig.records)
    await asyncio.sleep(seconds)
    best = 0.0
    for r in rig.records[since:]:
        if r.get("rec") != "recv" or r["msg"].get("type") != "meters":
            continue
        chans = r["msg"].get("out") or [0.0, 0.0]
        best = max(best, max(float(x) for x in chans))
    return round(best, 5)


def to_unit(p: dict, value: float) -> float:
    lo, hi, curve = float(p["min"]), float(p["max"]), p.get("curve", "lin")
    if curve == "exp" and lo > 0 and hi > 0:
        return math.log(value / lo) / math.log(hi / lo)
    return 0.0 if hi == lo else (value - lo) / (hi - lo)


def entry(rig: Rig, key: str) -> dict:
    return next(c for c in rig.state["chain"] if c["key"] == key)


async def set_abs(rig: Rig, key: str, name: str, value: float) -> None:
    await rig.send({"type": "set_param", "key": key, "name": name,
                    "unit": to_unit(entry(rig, key)["params"][name], value)})


async def freq_of(rig: Rig, key: str) -> float:
    """The target instance's live `freq`, straight off a fresh state."""
    await rig.poke()
    return float(entry(rig, key)["params"]["freq"]["value"])


def levels(rig: Rig, ep: str, since: int) -> list[bool]:
    """`{"kind": "level", "ep": ...}` taps for one endpoint."""
    return [e["on"] for e in rig.events("level", since=since) if e.get("ep") == ep]


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    print("== item 29 drone allocation, on real audio ==")
    # NOTHING machine-wide here. This probe was written on a branch that
    # predates port-scoped rigs and opened with a bare `kill_scsynth()`
    # sweep, which on this machine kills every other session's rig — the
    # exact bug that presented as flaky audio, and the same sweep that was
    # removed from audio_proof.py. `Rig` claims its port through rigreg and
    # kills only what it started, so an scsynth already running belongs to
    # somebody else and is left alone.

    async with Rig(patch="pad_space", scenario="drone-audio", keep=args.keep) as rig:
        pad = rig.state.get("voice_target") or "pulse_pad"
        await rig.midi_enable()
        for key in BYPASS:
            await rig.send({"type": "set_enabled", "key": key, "enabled": False})
        await rig.poke()
        await set_abs(rig, pad, "attack", 0.02)
        await set_abs(rig, pad, "release", 0.06)
        # PWM OFF, or every amplitude here is sampling a moving target.
        # `pulse_pad` sets `width = 0.5 + SinOsc.kr(frequency=0.3) * pwm` —
        # a 3.33-SECOND cycle. Measuring it through a sub-second window
        # samples a different phase every time: one sustained voice, traced
        # for 12 s, gave 0.020-0.091 with no plateau, i.e. a 1.30x spread on
        # a signal that is not changing. At pwm=0 the same voice measures
        # 0.06723..0.06852 — a 1.02x spread, effectively deterministic.
        # This is why check 8 was flaky, and it is NOT a settle race: the
        # trace oscillates just as much after 12 s as in its first second.
        await set_abs(rig, pad, "pwm", 0.0)
        # DETUNE OFF for the same reason, one level down. Each voice runs
        # three oscillators at +/- `detune` cents, so a voice beats with
        # ITSELF at a few Hz; three voices give several slow beat components
        # and no practical window averages them out. With pwm already off,
        # dropping detune collapses the spread of the SUMMED case from 1.40x
        # to 1.07x. Neither knob is what check 8 is about — like the
        # bypassed effects above, they are measurement conditioning, chosen
        # so amplitude means what it looks like.
        await set_abs(rig, pad, "detune", 0.0)
        await rig.poke()
        # isolate: keep the patch's own mono voice off the keys for now
        await rig.unwire("arp", "voice")
        await rig.settle()

        base = await listen(rig, 1.0)
        if not check("0. stripped chain is silent at rest", base < SILENT,
                     f"peak {base}"):
            return 1

        running = bool((rig.state.get("transport") or {}).get("running"))
        drone = await rig.spawn("spawn_drone_voice", "voices")
        await rig.wire("keys", drone)
        print(f"   drone id {drone!r} -> target {pad!r}; "
              f"transport running={running} at spawn")
        check("1. the drone's id type is 'hold', not 'drone' (module shadowing)",
              drone.split(".")[0] == "hold", f"id {drone!r}")

        # -- 2/3. POWER *and* the transport ----------------------------------
        await rig.send({"type": "set_drone_power", "id": drone, "on": True})
        await rig.poke()
        powered_stopped = await listen(rig, 1.0)
        check("2. POWER alone does NOT sound while the transport is STOPPED",
              powered_stopped < SILENT,
              f"peak {powered_stopped} (effective gate = power AND running)")

        await rig.send({"type": "set_transport", "playing": True})
        await rig.poke()
        await asyncio.sleep(0.4)
        drone_alone = await listen(rig, 1.0)
        check("3. POWER + transport RUNNING makes the drone sound",
              drone_alone > SOUNDING,
              f"peak {drone_alone} vs silence {powered_stopped}")

        # -- 4. it HOLDS: note-off must not silence it -----------------------
        rig.note_on(55, 100)
        await asyncio.sleep(0.7)
        with_note = await listen(rig, 0.7)
        rig.note_off(55)
        await asyncio.sleep(0.7)
        held = await listen(rig, 1.0)
        check("4. a drone HOLDS — note-off does not silence it",
              held > SOUNDING and held > with_note * 0.6,
              f"note held {with_note} -> after note-off {held} "
              f"(a gated voice would fall to ~0)")

        # -- 5. transport stop silences it, start brings it back -------------
        await rig.send({"type": "set_transport", "playing": False})
        await rig.poke()
        await asyncio.sleep(0.6)
        stopped = await listen(rig, 1.0)
        await rig.send({"type": "set_transport", "playing": True})
        await rig.poke()
        await asyncio.sleep(0.5)
        restarted = await listen(rig, 1.0)
        check("5. transport stop silences the drone; start brings it back",
              stopped < SILENT and restarted > SOUNDING,
              f"stopped {stopped} -> restarted {restarted}")

        # -- 6/7. POWER from a BUTTON wire, with its level tap ----------------
        await rig.send({"type": "set_drone_power", "id": drone, "on": False})
        await rig.poke()
        await asyncio.sleep(0.5)
        btn = await rig.spawn("spawn_button")
        await rig.send({"type": "set_button", "id": btn, "latch": True})
        await rig.poke()
        await rig.wire(btn, f"{drone}:pwr")

        since = rig.mark_index()
        await rig.send({"type": "fire_button", "id": btn})
        await asyncio.sleep(0.8)
        on_peak = await listen(rig, 0.8)
        taps_on = levels(rig, f"{drone}:pwr", since)
        check("6. a BUTTON wire into <drone>:pwr powers it — audibly",
              on_peak > SOUNDING, f"peak {on_peak}")
        check("7. ...and announces itself as a level tap "
              "(reactive-indicator doctrine)",
              taps_on and taps_on[-1] is True, f"taps {taps_on}")

        since = rig.mark_index()
        await rig.send({"type": "fire_button", "id": btn})
        await asyncio.sleep(0.9)
        off_peak = await listen(rig, 0.9)
        taps_off = levels(rig, f"{drone}:pwr", since)
        check("7b. the button's falling edge powers it down, tap included",
              off_peak < SILENT and taps_off and taps_off[-1] is False,
              f"peak {off_peak}, taps {taps_off}")

        # -- 8. a drone and a poly SHARING one target ------------------------
        # The landmine this guards: POWER must hold the target's GATE, never
        # call set_enabled — bypassing the source would silence the poly
        # leasing another slot on it.
        await rig.send({"type": "fire_button", "id": btn})   # power back on
        await rig.poke()
        await asyncio.sleep(0.6)
        drone_only = await listen(rig, RATIO_WINDOW)
        poly = await rig.spawn("spawn_poly", "voices", voices=2)
        await rig.wire("keys", poly)
        print(f"   sharing {pad!r}: drone {drone!r} + poly {poly!r} (2 slots)")
        rig.note_on(64, 100)
        rig.note_on(71, 100)
        await asyncio.sleep(0.7)
        both = await listen(rig, RATIO_WINDOW)
        # 1.4x is DERIVED, and it is deliberately UNCHANGED. The failure
        # being excluded — the poly contributing nothing because it never
        # got its own slots — sits at exactly 1.00x. The measured floor of
        # correct behaviour is now ~2.7x. 1.4 sits between them with ~40%
        # margin above the failure and ~48% below the floor.
        #
        # The threshold was never the problem. As shipped, PWM and detune
        # made the floor 1.21x — BELOW the 1.4 threshold — so the check
        # could fail with everything working perfectly, and 1.37x was an
        # ordinary sample of that range rather than a race or a regression.
        # Conditioning the measurement moved the floor from 1.21x to ~2.7x
        # and the number stayed exactly where it was.
        check("8. a drone and a poly share ONE source on separate slots",
              both > drone_only * 1.4,
              f"drone alone {drone_only} -> drone + 2 poly notes {both} "
              f"({both / max(drone_only, 1e-9):.2f}x; poly silent = 1.00x, "
              f"measured floor ~2.7x)")

        # power the drone DOWN with the poly notes still held
        await rig.send({"type": "fire_button", "id": btn})
        await rig.poke()
        await asyncio.sleep(0.7)
        poly_survives = await listen(rig, 0.8)
        check("9. dropping drone POWER does NOT silence the poly sharing "
              "its source (power holds a gate, it never bypasses)",
              poly_survives > SOUNDING,
              f"peak with poly notes still held {poly_survives} "
              f"(a set_enabled bypass would read ~0)")
        rig.note_off(64)
        rig.note_off(71)
        await asyncio.sleep(0.6)
        await rig.send({"type": "remove_voice", "id": poly})
        rig.wired = [w for w in rig.wired if w.get("to") != poly]
        await rig.poke()

        # -- 10-13. PITCH: transpose is followed, bend is not ----------------
        # Give the drone its OWN source so it leases slot 0 and its `freq`
        # lands in the broadcast state, where it can be read exactly. On the
        # shared pad the mono voice already holds slot 0 and the drone would
        # be a satellite — real, audible, and invisible to `state`.
        before = {c["key"] for c in rig.state["chain"]}
        await rig.send({"type": "spawn_module", "key": entry(rig, pad)["type"]})
        st = await rig.poke()
        pad2 = sorted({c["key"] for c in st["chain"]} - before)[0]
        await rig.send({"type": "graph_wire", "action": "add",
                        "from": pad2, "to": "master"})
        await rig.send({"type": "set_voice_target", "key": pad2, "voice": drone})
        await rig.poke()
        await set_abs(rig, pad2, "attack", 0.02)
        await set_abs(rig, pad2, "release", 0.06)
        await set_abs(rig, pad2, "pwm", 0.0)      # check 9b reads its level
        # the patch's mono voice back on the keys, on the ORIGINAL pad, so
        # the two can be compared under one transpose
        await rig.wire("keys", "voice")
        # Power it back up: the pitch checks below must be taken on a drone
        # that is actually SOUNDING, or they prove arithmetic rather than
        # audio. Check 9b holds them to that.
        await rig.send({"type": "set_drone_power", "id": drone, "on": True})
        await rig.poke()
        await asyncio.sleep(0.6)
        pitch_rig_level = await listen(rig, 0.8)
        check("9b. the drone is sounding on its own source for the pitch "
              "checks below", pitch_rig_level > SOUNDING,
              f"peak {pitch_rig_level} on {pad2!r}")
        print(f"   pitch rig: drone -> {pad2!r} (slot 0), mono voice -> {pad!r}")

        await rig.send({"type": "set_transpose", "semitones": 0})
        await rig.poke()
        rig.note_on(60, 100)
        await asyncio.sleep(0.5)
        d0 = await freq_of(rig, pad2)
        rig.note_off(60)
        await asyncio.sleep(0.4)
        want0 = midi_to_freq(60)
        check("10. at transpose 0 the drone tracks the played note",
              abs(d0 - want0) < 0.5,
              f"drone freq {d0:.2f} Hz, want {want0:.2f} Hz")

        await rig.send({"type": "set_transpose", "semitones": 12})
        await rig.poke()
        rig.note_on(60, 100)
        await asyncio.sleep(0.5)
        d12, v12 = await freq_of(rig, pad2), await freq_of(rig, pad)
        want12 = midi_to_freq(72)
        check("11. the drone FOLLOWS global transpose (item 29's fix)",
              abs(d12 - want12) < 0.5,
              f"transpose +12, note 60 -> drone {d12:.2f} Hz, "
              f"want {want12:.2f} Hz; ignoring transpose would give "
              f"{want0:.2f} Hz")
        check("12. drone and melody are in UNISON under transpose — no "
              "`transpose`-semitone offset any more",
              abs(d12 - v12) < 0.5,
              f"drone {d12:.2f} Hz vs mono voice {v12:.2f} Hz "
              f"(the old bug put them {12} semitones apart)")

        # -- 13. bend moves the melody and deliberately NOT the drone --------
        rig.bend(2.0)
        await asyncio.sleep(0.5)
        d_bend, v_bend = await freq_of(rig, pad2), await freq_of(rig, pad)
        rig.bend(0.0)
        rig.note_off(60)
        await asyncio.sleep(0.5)
        check("13. bend moves the melody and leaves the drone alone "
              "(deliberate — do not 'fix' this)",
              abs(d_bend - d12) < 0.5 and v_bend > v12 * 1.05,
              f"drone {d12:.2f} -> {d_bend:.2f} Hz (unmoved); "
              f"mono voice {v12:.2f} -> {v_bend:.2f} Hz (bent)")

        await rig.send({"type": "set_transpose", "semitones": 0})
        await rig.poke()

        # -- 14. removing the card stops what it was holding open ------------
        pre_remove = await listen(rig, 0.8)
        await rig.send({"type": "remove_voice", "id": drone})
        rig.wired = [w for w in rig.wired
                     if w.get("to") not in (drone, f"{drone}:pwr")]
        await rig.poke()
        await asyncio.sleep(0.9)
        post_remove = await listen(rig, 1.0)
        check("14. removing a POWERED drone releases the gate it was holding",
              pre_remove > SOUNDING and post_remove < SILENT,
              f"sounding {pre_remove} -> after remove_voice {post_remove}")

        await rig.send({"type": "set_transport", "playing": False})
        await rig.unwire("keys", "voice")
        await rig.wire("arp", "voice")
        rig.wired = [w for w in rig.wired if w.get("from") != "arp"]
        if await void_if_dead(rig):
            return 2
        check("15. the rig reported no server-side errors", not rig.errors,
              "; ".join(rig.errors[:3]))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — "
          f"{len(FAILURES)} failure(s)"
          + (f": {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
