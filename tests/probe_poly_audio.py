#!/usr/bin/env python3
"""Live AUDIO proof for item 10's poly voice — satellites, stealing, freeing.

    .venv/bin/python -u tests/probe_poly_audio.py

Mac only, real audio required. Everything here reads the MASTER METER
(`Amplitude.kr` on the post-volume hardware bus, ~20 Hz), exactly as
`tests/audio_proof.py` does, because the questions item 10 leaves open are
questions a headless test structurally cannot answer:

**Satellites are not `Instance`s.** Slots >= 1 are bare scsynth synths cloned
onto the target's out bus with no `Instance`, no card and no state entry.
`tests/test_allocation.py` asserts the BOOKKEEPING around them against a mock
rack — which slot holds which note, which order steals. It cannot assert that
a satellite makes a sound, that four of them SUM on the shared bus instead of
orphaning it, or that freeing one actually stops it. That is what this file
does, in amplitude.

**Stealing must give the incoming note its own ATTACK.** `Poly._restrike`
closes the stolen slot's gate and reopens it `STEAL_GAP` later on the server
clock. A headless test sees two `set` calls; it cannot distinguish that from a
sustained note being silently re-pitched, which sounds wrong and is the actual
risk. Here the pad is given a long attack and a near-instant release, so a
REAL restrike is a visible amplitude crater and a legato re-pitch is a flat
line. Check 4 measures the floor of that crater.

The chain is stripped to `pulse_pad` alone (drive/lowpass/echo/reverb/autopan
bypassed): echo and reverb tails fill the crater the steal test is looking
for, and autopan modulates amplitude at 0.25 Hz, which is noise in every
number below.
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

#: Amplitudes on the master bus. A single stripped pad sits near 0.1; a fresh
#: rig sits three orders of magnitude below SILENT.
SOUNDING = 0.02
SILENT = 0.006

#: Modules bypassed so the meter reads the pad and nothing else.
BYPASS = ("drive", "lowpass", "echo", "reverb", "autopan")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(("ok    " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def engine_died(rig: Rig) -> bool:
    """Did scsynth go away underneath us?

    It does, intermittently, on this Mac: the server exits mid-run (SIGABRT
    on the way out, visible in ~/Library/Logs/DiagnosticReports) and supriya
    reports `Server offline!`. EVERY meter then reads 0.0 — so an amplitude
    probe scores a pile of feature FAILURES when what actually happened is
    that the engine died. Check this before believing any silence.
    """
    return any("offline" in str(e).lower() for e in rig.errors)


async def void_if_dead(rig: Rig, target: str) -> bool:
    """VOID the run if the ENGINE, not the feature, is what went quiet.

    Two distinct failure modes seen on this Mac, and only the first announces
    itself:

    * scsynth exits mid-run — supriya says `Server offline!`.
    * the pinned output-only device just STOPS. scsynth stays alive and keeps
      metering, every frame reads 0.0, and `rig.errors` stays empty.

    Neither is about poly voices, and both score as a pile of feature
    failures. So the second is caught with a POSITIVE CONTROL: put the
    patch's own mono voice back on the keys and play one note. If even that
    is silent, nothing measured above was evidence. `audio_proof.py` — which
    contains no allocation code at all — fails the same way, which is what
    established this as environmental.
    """
    if any("offline" in str(e).lower() for e in rig.errors):
        print("\n!! ENGINE DIED MID-RUN — scsynth reported offline. Every "
              "meter reads 0.0 from here,\n!! so the results above are VOID, "
              "not evidence about the feature. Re-run.")
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
              f"note through the\n!! patch's own mono voice) measured "
              f"{control}. The device stopped producing;\n!! scsynth is "
              f"alive and metering zeros. Results above are VOID. Re-run.")
        return True
    return False


# -- meter reading ------------------------------------------------------------


def frames(rig: Rig, since: int) -> list[tuple[float, float]]:
    """(timestamp, peak) for every meter frame recorded since index `since`."""
    out = []
    for r in rig.records[since:]:
        if r.get("rec") != "recv" or r["msg"].get("type") != "meters":
            continue
        chans = r["msg"].get("out") or [0.0, 0.0]
        out.append((r["t"], max(float(x) for x in chans)))
    return out


def peak(rig: Rig, since: int) -> float:
    fr = frames(rig, since)
    return round(max((v for _, v in fr), default=0.0), 5)


async def listen(rig: Rig, seconds: float) -> float:
    """Loudest master frame over the next `seconds`."""
    since = len(rig.records)
    await asyncio.sleep(seconds)
    return peak(rig, since)


# -- absolute params over a unit-only protocol --------------------------------


def to_unit(p: dict, value: float) -> float:
    """Invert `Param.from_unit` so a probe can ask for real units.

    `set_param` speaks 0..1 only. Asking for "attack = 0.6 s" and getting it
    is the difference between a measurement and a guess.
    """
    lo, hi, curve = float(p["min"]), float(p["max"]), p.get("curve", "lin")
    if curve == "exp" and lo > 0 and hi > 0:
        return math.log(value / lo) / math.log(hi / lo)
    if hi == lo:
        return 0.0
    return (value - lo) / (hi - lo)


async def set_abs(rig: Rig, key: str, name: str, value: float) -> None:
    st = rig.state
    entry = next(c for c in st["chain"] if c["key"] == key)
    await rig.send({"type": "set_param", "key": key, "name": name,
                    "unit": to_unit(entry["params"][name], value)})


def param_value(rig: Rig, key: str, name: str) -> float:
    entry = next(c for c in rig.state["chain"] if c["key"] == key)
    return float(entry["params"][name]["value"])


# -- the probe ----------------------------------------------------------------


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    print("== item 10 poly voice, on real audio ==")
    # NOTHING machine-wide here. This probe was written on a branch that
    # predates port-scoped rigs and opened with a bare `kill_scsynth()`
    # sweep, which on this machine kills every other session's rig — the
    # exact bug that presented as flaky audio, and the same sweep that was
    # removed from audio_proof.py. `Rig` claims its port through rigreg and
    # kills only what it started, so an scsynth already running belongs to
    # somebody else and is left alone.

    async with Rig(patch="pad_space", scenario="poly-audio", keep=args.keep) as rig:
        target = rig.state.get("voice_target") or "pulse_pad"
        # Notes enter through a REAL virtual MIDI port, so every check below
        # also crosses the rtmidi callback thread and the router.
        await rig.midi_enable()
        for key in BYPASS:
            await rig.send({"type": "set_enabled", "key": key, "enabled": False})
        await rig.poke()
        # crisp envelope: attack fast enough that a chord reaches full level
        # inside a listen window, release fast enough that note-off is sharp.
        await set_abs(rig, target, "attack", 0.02)
        await set_abs(rig, target, "release", 0.06)
        await rig.poke()
        await rig.settle()

        base = await listen(rig, 1.0)
        if not check("0. stripped chain is silent at rest", base < SILENT,
                     f"peak {base}"):
            print("   baseline is not silent — nothing below can be trusted.")
            return 1

        # The default patch routes keys -> arp -> voice (mono). Take the mono
        # voice off the keys so the meter reads the POLY and nothing else.
        await rig.unwire("arp", "voice")
        poly = await rig.spawn("spawn_poly", "voices", voices=4)
        await rig.wire("keys", poly)
        print(f"   poly id {poly!r} -> target {target!r}, 4 slots, "
              f"attack={param_value(rig, target, 'attack'):.3f}s "
              f"release={param_value(rig, target, 'release'):.3f}s")

        # -- 1. one note sounds (slot 0 is the target's own node) ------------
        rig.note_on(60, 100)
        one = await listen(rig, 0.9)
        rig.note_off(60)
        await asyncio.sleep(0.6)
        check("1. a poly voice sounds at all (slot 0)", one > SOUNDING,
              f"peak {one} vs silence {base}")

        # -- 2. THE satellite check: 4 notes SUM, they do not orphan ---------
        for n in (60, 64, 67, 71):
            rig.note_on(n, 100)
            await asyncio.sleep(0.02)
        four = await listen(rig, 0.9)
        check("2. four notes SUM on the shared bus — satellites really sound",
              four > one * 1.6,
              f"1 note {one} -> 4 notes {four}  (ratio {four / max(one, 1e-9):.2f}x)")

        # -- 3. a shared param moves EVERY voice (pool.mirror) ---------------
        loud_amp = param_value(rig, target, "amp")
        await set_abs(rig, target, "amp", loud_amp * 0.25)
        await asyncio.sleep(0.35)
        ducked = await listen(rig, 0.6)
        await set_abs(rig, target, "amp", loud_amp)
        await asyncio.sleep(0.35)
        back = await listen(rig, 0.6)
        # If only slot 0 followed the knob, three of four voices keep their
        # level and the sum barely moves (~0.8x). A real mirror lands near
        # the 0.25x the knob asked for.
        check("3. a target param moves ALL four voices (pool.mirror), not one",
              ducked < four * 0.45 and back > ducked * 1.8,
              f"{four} -> amp*0.25 {ducked} -> restored {back} "
              f"(ratio {ducked / max(four, 1e-9):.2f}x, one-voice-only would be ~0.8x)")

        # -- 4. dispose while sounding: no satellite outlives the card -------
        # Chord still held, all four gates open. Removing the card must take
        # the satellites with it — the failure this guards is "the satellites
        # outlive the card and drone on, with no card left to stop them".
        await rig.send({"type": "remove_voice", "id": poly})
        await rig.poke()
        await asyncio.sleep(0.8)
        orphan = await listen(rig, 1.2)
        check("4. remove_voice while a chord SOUNDS frees every satellite",
              orphan < SILENT, f"peak after removal {orphan}")
        for n in (60, 64, 67, 71):
            rig.note_off(n)
        await asyncio.sleep(0.4)
        rig.wired = [w for w in rig.wired if w.get("to") != poly]

        # -- 5. STEALING gives the new note its own attack -------------------
        # One slot, long attack, instant release. A real restrike craters the
        # amplitude and rebuilds it; a legato re-pitch is a flat line.
        await set_abs(rig, target, "attack", 0.60)
        await set_abs(rig, target, "release", 0.05)
        await rig.poke()
        solo = await rig.spawn("spawn_poly", "voices", voices=1)
        await rig.wire("keys", solo)
        print(f"   steal test: {solo!r} at 1 slot, "
              f"attack={param_value(rig, target, 'attack'):.3f}s "
              f"release={param_value(rig, target, 'release'):.3f}s")

        rig.note_on(60, 100)
        await asyncio.sleep(1.6)            # well past the 0.6 s attack
        sustain = await listen(rig, 0.5)

        mark = len(rig.records)
        rig.note_on(67, 100)                # steals slot 0 from note 60
        await asyncio.sleep(0.30)
        crater = frames(rig, mark)
        floor = round(min((v for _, v in crater), default=1.0), 5)
        await asyncio.sleep(1.3)            # let the new attack complete
        recovered = await listen(rig, 0.5)
        rig.note_off(67)
        rig.note_off(60)
        await asyncio.sleep(0.8)

        # The CRATER is the evidence: a legato re-pitch holds the sustain
        # level flat, so a floor well under it can only be a closed gate.
        # Recovery only has to show the voice came back audibly — pinning it
        # to a tight fraction of `sustain` measures peak-metering variance
        # between two pitches, not the restrike (it read 0.60x on one run and
        # failed a 0.6 threshold while the crater was a clean 0.34x).
        check("5. a stolen voice RE-ATTACKS: amplitude craters, then rebuilds",
              floor < sustain * 0.5 and recovered > SOUNDING,
              f"sustain {sustain} -> floor {floor} "
              f"({floor / max(sustain, 1e-9):.2f}x) -> recovered {recovered}; "
              f"{len(crater)} frames in the 300 ms after the steal")
        print("      crater trace: "
              + " ".join(f"{v:.3f}" for _, v in crater[:8]))

        after = await listen(rig, 0.8)
        check("6. the stolen note is gone — one slot, one voice sounding",
              after < SILENT, f"peak {after}")

        await rig.send({"type": "remove_voice", "id": solo})
        rig.wired = [w for w in rig.wired if w.get("to") != solo]
        await rig.poke()

        # -- 7. two MONO voices on one target no longer stomp ----------------
        # The spawn_voice fix: a second mono voice leases its OWN slot, so two
        # of them sound as two voices instead of fighting over one node.
        await set_abs(rig, target, "attack", 0.02)
        await set_abs(rig, target, "release", 0.06)
        await rig.poke()
        # The two voices must sound DIFFERENT pitches, or this measures
        # nothing: both are last-note priority, so wiring both straight to
        # `keys` lands them on the same note — and two identical pulse waves
        # at one frequency sum by PHASE, which is a coin toss, not evidence.
        # A key shifter on one leg gives voice=+5 while voice.2 plays open,
        # so the sum is incoherent and amplitude means what it looks like.
        # Two voices must sound DIFFERENT pitches or this measures nothing:
        # both are last-note priority, so wiring both straight to `keys`
        # lands them on one note, and two identical waves sum by PHASE. A
        # key shifter on one leg fixes that.
        #
        # The waveform matters too. On the default PULSE the ratio wandered
        # 1.16-2.04 across runs: the peak of two summed harmonic-rich voices
        # depends on how their harmonics happen to line up, and it is not
        # the master Limiter compressing them (that sits at 0.95 and these
        # peaks are ~0.1). A SINE has one partial per oscillator, so two
        # voices a tritone apart beat cleanly and the peak of the sum
        # reliably approaches A+B. Three intervals, and the MEDIAN carries
        # it; `one_v` is re-measured inside the loop with voice.2 unwired,
        # so 1.0x is exactly the stomping baseline being ruled out.
        await set_abs(rig, target, "wave", 3.0)          # 0=pulse .. 3=sine
        await rig.poke()
        v2 = await rig.spawn("spawn_voice")
        ks = await rig.spawn("spawn_keyshift")
        await rig.wire("keys", f"{ks}:1")
        await rig.wire(f"{ks}:1", "voice")
        ratios = []
        for shift in (6, 4, 3):
            await rig.send({"type": "set_keyshift", "id": ks, "key": shift})
            await rig.poke()
            await rig.unwire("keys", v2)
            rig.note_on(60, 100)
            await asyncio.sleep(0.4)
            one_v = await listen(rig, 1.2)
            rig.note_off(60)
            await asyncio.sleep(0.5)
            await rig.wire("keys", v2)
            rig.note_on(60, 100)      # voice -> 60+shift, voice.2 -> 60
            await asyncio.sleep(0.4)
            two_v = await listen(rig, 1.2)
            rig.note_off(60)
            await asyncio.sleep(0.5)
            ratios.append(round(two_v / max(one_v, 1e-9), 2))
        median = sorted(ratios)[len(ratios) // 2]
        check("7. two mono voices on ONE target sound as two (spawn_voice fix)",
              median > 1.35,
              f"ratios at +6/+4/+3 semitones: {ratios}, median {median}x "
              f"(stomping one node would leave every one at ~1.0x)")

        quiet = await listen(rig, 0.8)
        check("8. everything released — nothing left droning", quiet < SILENT,
              f"peak {quiet}")

        # put the patch's own routing back before restore checks drift.
        # arp->voice is a BASELINE wire, so drop it from the driver's
        # added-wire list or restore would helpfully cut it again.
        await rig.unwire(f"{ks}:1", "voice")
        await rig.wire("arp", "voice")
        rig.wired = [w for w in rig.wired if w.get("from") != "arp"]

        if await void_if_dead(rig, target):
            return 2
        check("9. the rig reported no server-side errors", not rig.errors,
              "; ".join(rig.errors[:3]))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — "
          f"{len(FAILURES)} failure(s)"
          + (f": {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
