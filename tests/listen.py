#!/usr/bin/env python3
"""Hear what Patchwerk is actually doing, as numbers. No microphone.

    # play a note and describe the master output
    python3 tests/listen.py --note 69

    # feed a file into the audio-in path and describe what came out
    python3 tests/listen.py --in-file /tmp/sweep.wav --seconds 3

    # record a specific module's own bus instead of the master
    python3 tests/listen.py --note 60 --target pulse_pad

    # generate test signal and use it in one go
    python3 tests/listen.py --in-tone 440 --seconds 2

Injection writes the AUDIO-IN BUS, so `--in-file`/`--in-tone` only reach the
master output if the patch actually contains an Audio In module — otherwise
the signal is sitting on the input bus with nothing reading it, exactly as a
microphone would be. Capture that module directly (`--target audio_in`) or
wire it into the chain first. `tests/audio_io_proof.py` does both.

Prints a JSON feature block from `tests/analysis.py` and keeps the WAV, so
a follow-up question ("what about the 3rd harmonic") is one more call
against the same file rather than another rig boot.

This is the assembled-rig counterpart to the meter in
`tests/audio_proof.py`. The meter proves sound is happening; this says
WHAT the sound is. Mac-only and rig-bound: it boots a real engine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis  # noqa: E402
from rig import Rig  # noqa: E402
from synthbase.wavio import read_wav, write_wav  # noqa: E402


async def _await_reply(rig: Rig, kind: str, timeout: float = 30.0) -> dict:
    """Wait for the next server message of `type` == kind.

    The rig records everything the server said (that is Phase 1's whole
    point), so "wait for the reply" is a poll over the tail of the
    transcript rather than a second socket.
    """
    since = len(rig.records)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for r in rig.records[since:]:
            if r.get("rec") != "recv":
                continue
            got = r["msg"].get("type")
            if got == kind:
                return r["msg"]
            # `_handle` turns every exception into {"type": "error"}, so a
            # server-side failure otherwise reads as a 30-second timeout
            # with no clue in it. Fail immediately, with the reason.
            if got == "error":
                raise RuntimeError(f"server error while waiting for {kind!r}: "
                                   f"{r['msg'].get('message')}")
        await asyncio.sleep(0.05)
    raise TimeoutError(f"no {kind!r} from the server within {timeout}s")


def make_tone(path: Path, freq: float, seconds: float,
              sample_rate: int = 48000, amplitude: float = 0.5) -> Path:
    """A plain sine WAV, for when the point is a KNOWN input signal.

    Deliberately not a sweep or noise: the whole value of injected test
    signal is that every measurement downstream has an arithmetic
    expectation, and a sine's is one line long.
    """
    n = int(sample_rate * seconds)
    w = 2.0 * math.pi * freq / sample_rate
    # A whole number of periods where possible, so looping does not click.
    samples = [amplitude * math.sin(w * i) for i in range(n)]
    fade = min(256, n // 4)
    for i in range(fade):                       # and neither do the ends
        k = i / fade
        samples[i] *= k
        samples[n - 1 - i] *= k
    return write_wav(path, [samples], sample_rate)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--patch", default="pad_space")
    ap.add_argument("--target", default="master",
                    help="'master' or a module instance id")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--note", type=int, default=None,
                    help="play this MIDI note for the capture window")
    ap.add_argument("--velocity", type=int, default=100)
    ap.add_argument("--in-file", default=None,
                    help="WAV to inject on the audio-in bus while recording")
    ap.add_argument("--in-tone", type=float, default=None,
                    help="synthesise a sine of this Hz and inject it")
    ap.add_argument("--in-gain", type=float, default=1.0)
    ap.add_argument("--fundamental", type=float, default=None,
                    help="expected f0 for the harmonic measurements")
    ap.add_argument("--out", default=None, help="where to keep the WAV")
    ap.add_argument("--keep", action="store_true", help="leave the rig up")
    ap.add_argument("--port", type=int, default=None,
                    help="rig port (default $SS_PORT, else 8765). Use a "
                         "distinct one when another session holds 8765 — "
                         "connecting to THEIR rig is how you end up waiting "
                         "for a reply from a server that has no capture tap.")
    args = ap.parse_args(argv)

    in_file = args.in_file
    if args.in_tone:
        in_file = str(make_tone(Path("/tmp/patchwerk-captures/tone.wav"),
                                args.in_tone, max(args.seconds + 0.5, 1.0)))

    f0 = args.fundamental
    if f0 is None and args.note is not None:
        f0 = analysis.note_hz(args.note)
    if f0 is None and args.in_tone:
        f0 = args.in_tone

    # Deliberately NOT kill_scsynth(): several agent sessions share this
    # machine and killing "the" server kills whoever else is live. Pick a
    # free port instead.
    async with Rig(patch=args.patch, keep=args.keep, scenario="listen",
                   p=args.port) as rig:
        if rig.silent:
            print(json.dumps({"error": "rig came up SILENT — no engine, so "
                                       "there is nothing to listen to"}))
            return 1
        await rig.settle()

        if in_file:
            await rig.send({"type": "inject", "action": "play",
                            "path": in_file, "gain": args.in_gain,
                            "loop": True})
            st = await _await_reply(rig, "inject_state")
            if not st.get("playing"):
                print(json.dumps({"error": "injection did not start",
                                  "reply": st}))
                return 1
            print(f"# injecting {Path(in_file).name} "
                  f"({st['channels']}ch @ {st['sample_rate']}) "
                  f"on audio-in bus {st['input_bus']}", file=sys.stderr)

        # MIDI comes up BEFORE the capture is armed. Enabling it after
        # arming spends the first part of the window on port setup and the
        # first note_on can land before the virtual port is really live —
        # which reads as a perfectly healthy capture full of silence.
        if args.note is not None:
            await rig.midi_enable()
            await asyncio.sleep(0.3)

        await rig.send({"type": "capture", "action": "arm",
                        "target": args.target, "seconds": args.seconds + 0.5,
                        "path": args.out})
        await asyncio.sleep(0.1)

        if args.note is not None:
            rig.note_on(args.note, args.velocity)
        await asyncio.sleep(args.seconds)
        if args.note is not None:
            rig.note_off(args.note)
            await asyncio.sleep(0.4)      # let the release into the capture

        await rig.send({"type": "capture", "action": "stop"})
        done = await _await_reply(rig, "capture_done")
        if in_file:
            await rig.send({"type": "inject", "action": "stop"})

    wav = read_wav(done["path"])
    features = analysis.analyze(wav.mono(), wav.sample_rate, fundamental=f0)
    out = {
        "wav": done["path"],
        "target": done["target"],
        "bus": done["bus"],
        "channels": wav.channel_count,
        "injected": in_file,
        "note": args.note,
        **features,
    }
    print(json.dumps(out, indent=2))
    return 0 if not features["silent"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
