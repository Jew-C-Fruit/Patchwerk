#!/usr/bin/env python3
"""Prove the internal listener HEARS and the file injector IS the audio in.

    SS_PORT=8791 python3 tests/audio_io_proof.py --port 8791

Mac-only, rig-bound, real audio — the sibling of `tests/audio_proof.py`.
That one proves sound is HAPPENING by reading the master meter; this one
proves we can say WHAT the sound is, and that a file can stand in for the
microphone.

Run it on a port of its own. Several agent sessions share this machine and
connecting to whoever holds 8765 means asking someone else's server for a
capture tap it does not have.

Two calibration decisions worth knowing about, both learned from the rig
rather than assumed:

**Pitch is asserted against the ENGINE'S OWN reported frequency, not
against `note_hz(midi_note)`.** Playing note 69 through `pad_space` does
not put 440 Hz on the master bus: the note goes `keys -> arp -> voice`, and
the voice drives `pulse_pad` at 110 Hz — two octaves down. That is the
patch working correctly. Asserting 440 would have been asserting a
convention nothing promised. Reading the sounding frequency out of `state`
and finding it in the audio is the stronger claim anyway: it ties the
recording to what the control plane says it is doing.

**Silence is proved on a QUIESCED rig, and quiescing is polled, not
slept.** `pad_space` ends in echo -> reverb, and a held note leaves a
feedback tail still at RMS 0.015 five seconds after `all_notes_off`, not
reaching the noise floor until roughly fifteen. Measured, not guessed. A
fixed sleep would either fail on honest audio or make the run pointlessly
long, so `quiesce()` captures until the peak is exactly zero. It also
means the proof does not depend on having booted the server itself —
`boot_if_down` may hand us a rig someone else left sounding.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis as A  # noqa: E402
from listen import _await_reply, make_tone  # noqa: E402
from rig import Rig  # noqa: E402
from synthbase.wavio import read_wav  # noqa: E402

TONE_HZ = 440.0
TONE_AMP = 0.5

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    return ok


def meter_peak(rig: Rig, since: int) -> float:
    pk = 0.0
    for r in rig.records[since:]:
        if r.get("rec") == "recv" and r["msg"].get("type") == "meters":
            pk = max(pk, max(float(x) for x in (r["msg"].get("out") or [0, 0])))
    return round(pk, 5)


def sounding_freqs(rig: Rig) -> list[float]:
    """Whatever the rack says its oscillators are tuned to, right now."""
    out = []
    for c in rig.state.get("chain") or []:
        f = (c.get("params") or {}).get("freq")
        if isinstance(f, dict) and f.get("value"):
            out.append(float(f["value"]))
    return out


async def grab(rig: Rig, target: str, seconds: float) -> tuple:
    """Record `target` for `seconds`; return (wav, mono samples, meter peak)."""
    await rig.send({"type": "capture", "action": "arm",
                    "target": target, "seconds": seconds + 0.4})
    since = len(rig.records)
    await asyncio.sleep(seconds)
    await rig.send({"type": "capture", "action": "stop"})
    done = await _await_reply(rig, "capture_done")
    wav = read_wav(done["path"])
    return wav, wav.mono(), meter_peak(rig, since)


async def quiesce(rig: Rig, tries: int = 15) -> list[float]:
    """Silence the rig and wait out its tail; return the last capture.

    `pad_space` ends in echo -> reverb and rings for ~15 s after a held
    note, so this polls rather than sleeping a guessed constant.
    """
    await rig.send({"type": "all_notes_off"})
    samples: list[float] = []
    for _ in range(tries):
        _, samples, _ = await grab(rig, "master", 0.8)
        if A.peak(samples) == 0.0:
            break
        await asyncio.sleep(0.7)
    return samples


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patch", default="pad_space")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--note", type=int, default=69)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out-device", default=None,
                    help="pin scsynth's output device, e.g. an output-only "
                         "one. Passed through to `synthbase gui`.")
    args = ap.parse_args(argv)
    boot_args = (["--out-device", args.out_device] if args.out_device else [])

    tone = make_tone(Path("/tmp/patchwerk-captures/proof-tone.wav"),
                     TONE_HZ, 3.0, amplitude=TONE_AMP)

    async with Rig(patch=args.patch, p=args.port, keep=args.keep,
                   boot_args=boot_args, scenario="audio-io-proof") as rig:
        if rig.silent:
            print("rig came up SILENT — no engine, nothing to listen to")
            return 1
        await rig.settle()
        print(f"      boot_note: {rig.state.get('boot_note')}")

        # -- 1: silence, on a rig that has been told to shut up -----------
        # NOT simply "capture at startup": `boot_if_down` means we may have
        # adopted a rig someone else left sounding, and a proof that only
        # holds when it booted the server itself is a proof that fails at
        # random. Quiesce first, then assert.
        quiet = await quiesce(rig)
        check("1. a quiesced rig records EXACT silence",
              A.peak(quiet) == 0.0,
              f"rms {A.rms(quiet):.8f} peak {A.peak(quiet):.8f}")

        # -- 2-4: the internal listener on a real note --------------------
        # Route notes STRAIGHT to the voice for the pitch check. In
        # `pad_space` the default control path is keys -> arp -> voice, and
        # the arp re-voices across octaves, so the sounding pitch changes
        # several times inside a 1.5 s window: the rack's reported `freq`
        # was 493.9 Hz on one run and 110.0 Hz on the next, and a
        # single-frequency assertion against a moving target is a coin
        # toss. A direct wire makes one note mean one pitch.
        arp_was = bool((rig.state.get("arp") or {}).get("enabled"))
        wires_was = [dict(w) for w in (rig.state.get("ctl_wires") or [])]
        added_wire = not any(w.get("from") == "keys" and w.get("to") == "voice"
                             for w in wires_was)
        await rig.send({"type": "set_arp", "enabled": False})
        if added_wire:
            await rig.send({"type": "ctl_wire", "action": "add",
                            "from": "keys", "to": "voice"})
        await rig.settle()
        await rig.midi_enable()
        await asyncio.sleep(0.3)
        rig.note_on(args.note, 100)
        await asyncio.sleep(0.3)
        freqs = sounding_freqs(rig)          # read it WHILE the note is held
        wav, mono, meter = await grab(rig, "master", 1.5)
        rig.note_off(args.note)

        check("2. the capture tap records real audio", not A.is_silent(mono),
              f"rms {A.rms(mono):.5f} peak {A.peak(mono):.5f}")
        # The meter is Amplitude.kr with a 0.2 s release, so it tracks the
        # peak ENVELOPE, not the peak sample: same order, never equal.
        pk = A.peak(mono)
        check("3. the capture AGREES with the master meter",
              meter > 0 and 0.4 < pk / meter < 2.5,
              f"capture peak {pk:.5f} vs meter {meter:.5f}")

        if not freqs:
            check("4. the rack reports a sounding frequency", False)
        else:
            want = freqs[0]
            # Look at the reported fundamental and its first harmonics: a
            # patch this long (drive -> lowpass -> echo -> reverb) may well
            # push more energy into a harmonic than into f0, and that is
            # not a failure of the tap.
            cands = [want * h for h in (1, 2, 3, 4)
                     if want * h < wav.sample_rate / 2]
            got_f, got_a = A.strongest(mono, wav.sample_rate,
                                       cands + [want * 1.5, want * 2.5])
            check(f"4. the audio sits on the rack's reported {want:.1f} Hz "
                  f"(or a harmonic), not between",
                  got_f in cands and got_a > 0.002,
                  f"strongest {got_f:.1f} Hz @ {got_a:.5f} of {cands}")

        # -- 5: the tail decays -------------------------------------------
        loud = A.rms(mono)
        tail = A.rms(await quiesce(rig))
        check("5. the tail DECAYS to the noise floor — no stuck voice",
              tail < loud * 0.01, f"{loud:.6f} -> {tail:.8f}")

        # -- 6-8: the file IS the audio in --------------------------------
        await rig.send({"type": "spawn_module", "key": "audio_in"})
        await rig.settle()
        await asyncio.sleep(0.4)            # spawn broadcasts after the build
        ids = [c.get("key") for c in (rig.state.get("chain") or [])]
        target = next((i for i in ids if str(i).startswith("audio_in")), None)
        if not check("6. an Audio In module exists to test against",
                     target is not None, str(ids)):
            return 1

        # Baseline the audio-in bus BEFORE injecting. When the machine has
        # a real input device this bus carries the actual microphone, so
        # "silent afterwards" is not a claim we can make — the claim we
        # actually want is that the INJECTED TONE stops, which is a
        # frequency measurement and holds either way.
        _, before, _ = await grab(rig, target, 1.0)
        print(f"      audio-in baseline rms {A.rms(before):.8f} "
              f"(input_enabled={rig.state.get('input_enabled')})")

        await rig.send({"type": "inject", "action": "play",
                        "path": str(tone), "gain": 1.0, "loop": True})
        st = await _await_reply(rig, "inject_state")
        check("7. injection reports playing on the audio-in bus",
              bool(st.get("playing")) and st.get("input_bus") is not None,
              f"bus {st.get('input_bus')} "
              f"{st.get('channels')}ch @ {st.get('sample_rate')}")
        await asyncio.sleep(0.4)

        wav2, inj, _ = await grab(rig, target, 1.5)
        check("8. the injected file reaches Audio In's OUTPUT",
              not A.is_silent(inj), f"rms {A.rms(inj):.5f}")

        # A 44.1 kHz file played on a 48 kHz server without BufRateScale is
        # 8.8% sharp — a wrong answer that still looks like a working
        # feature, so the sharp frequency is an explicit candidate.
        sharp = TONE_HZ * 48000 / 44100
        f, amp = A.strongest(inj, wav2.sample_rate,
                             [TONE_HZ / 2, TONE_HZ, sharp, TONE_HZ * 2])
        check(f"9. the injected tone is at {TONE_HZ:.0f} Hz, correctly rated",
              f == TONE_HZ and amp > TONE_AMP * 0.2,
              f"strongest {f:.1f} Hz @ {amp:.4f} "
              f"(an unrated 44.1k file would read {sharp:.0f})")

        # -- 10: stopping really stops ------------------------------------
        await rig.send({"type": "inject", "action": "stop"})
        await _await_reply(rig, "inject_state")
        await asyncio.sleep(0.3)
        _, after, _ = await grab(rig, target, 1.0)
        gone = A.tone_at(after, wav2.sample_rate, TONE_HZ)
        check("10. stopping injection removes the injected TONE",
              gone < amp * 0.02, f"{amp:.4f} -> {gone:.6f} at {TONE_HZ:.0f} Hz")
        # And the bus is back where it started — silent on a stub, room tone
        # on a real microphone, but not still carrying a player.
        check("11. ...and the bus returns to its pre-injection level",
              A.rms(after) <= max(A.rms(before) * 4, 1e-3),
              f"baseline {A.rms(before):.8f} -> after {A.rms(after):.8f}")

        # Put the control plane back. `rig.restore()` only undoes what it
        # tracked, and these went out as raw sends — so an untidied wire
        # survives the run and shows up as restore DRIFT, or worse, silently
        # rewires the patch for whoever adopts this rig next.
        if added_wire:
            await rig.send({"type": "ctl_wire", "action": "remove",
                            "from": "keys", "to": "voice"})
        await rig.send({"type": "set_arp", "enabled": arp_was})
        # spawn_module adds; edit_chain removes (rig.py's spawn/remove table
        # says so, and test_rig.py pins that the two tables agree).
        await rig.send({"type": "edit_chain", "action": "remove",
                        "key": target})

        # POLL for the result, do not `settle()` for it. `edit_chain` runs in
        # an executor against a live 7-module rack and can outrun settle's
        # 1 s cap, which returns stale state — the first cut of this check
        # failed for exactly that reason and reported the cleanup as broken
        # when it had merely not landed yet.
        def tidy(st) -> bool:
            wires = st.get("ctl_wires") or []
            chain = st.get("chain") or []
            return (not [w for w in wires if w not in wires_was]
                    and not [c for c in chain
                             if str(c.get("key", "")).startswith("audio_in")])

        try:
            await rig.until(tidy, timeout=10.0, what="the rig to be tidy again")
            check("12. the proof leaves the rig as it found it", True)
        except TimeoutError:
            wires = rig.state.get("ctl_wires") or []
            chain = rig.state.get("chain") or []
            check("12. the proof leaves the rig as it found it", False,
                  f"wires left: {[w for w in wires if w not in wires_was]}  "
                  f"modules left: {[c.get('key') for c in chain
                                    if str(c.get('key', '')).startswith('audio_in')]}")

        check("no server-side errors", not rig.errors,
              "; ".join(rig.errors[:3]) if rig.errors else "")

    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, "
          f"{len(FAIL)} failed" + (f": {', '.join(FAIL)}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
