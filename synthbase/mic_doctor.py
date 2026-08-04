"""Why is the microphone prompt coming back? Diagnose before changing anything.

    python3 -m synthbase mic-doctor
    python3 -m synthbase mic-doctor --json

Cole's report was "the mic permission needs to be persistent, rather than a
temporary approval". That is a symptom with at least four different causes,
and three of them are not permission at all. This prints which one is
actually happening, with the evidence, and says what to do about it.

## What macOS is doing, and why a grant here does not stick

Patchwerk never asks for the microphone itself. **scsynth does**, and
scsynth is an awkward client for TCC:

* It lives at `SuperCollider.app/Contents/Resources/scsynth` — the
  *Resources* directory, not `Contents/MacOS/`. It is signed
  (Developer ID, team HE5VJFE9E4) and carries
  `com.apple.security.device.microphone`, but `codesign` reports
  `Info.plist=not bound`: it has **no bundle identifier of its own**.
* `NSMicrophoneUsageDescription` — the sentence in the prompt — lives in
  the *app's* Info.plist, which scsynth is not launched through. supriya
  spawns the binary directly.

With no bundle identity, TCC attributes the request to the **responsible
process**. Under a normal Terminal that is Terminal.app, which has a stable
bundle id and holds a grant indefinitely — which is why this has never been
a problem when Cole runs `./run.sh` by hand.

Under Claude Code it is not. Claude Code runs TCC-*disclaimed*, which means
the process is its own responsible process, and the process is the venv's
Python. On this machine that resolves to:

    .venv/bin/python -> /opt/homebrew/Cellar/python@3.14/3.14.0_1/...

which `codesign` reports as **adhoc**, with a designated requirement of
`cdhash H"..."` and nothing else. A cdhash grant is real but it is pinned
to those exact bytes at that exact path — so **every Homebrew Python
revision bump silently invalidates it**, because both the hash and the
Cellar path change. The grant does not get revoked; it stops matching. From
the user's side that is indistinguishable from "the approval was
temporary".

## The honest conclusion

A durable grant has to be held by something that has a durable identity.
The venv Python does not, and cannot be given one without signing it.

**There IS one thing here that can hold a durable grant: the packaged
Patchwerk.app** (`packaging/`, branch `feat/packaging-installers`). The
packaging work independently hit the same wall from the other side and
found what a real prompt needs — all three together, each failing silently
alone: `NSMicrophoneUsageDescription` in the bundle's Info.plist, a
*compiled* `CFBundleExecutable` stub that forks and waits (as a shell
script, tccd blamed the interpreter — `responsible={identifier=python3.12}`
— and never read the plist), and the
`com.apple.security.device.audio-input` entitlement, without which
`--options runtime` makes TCC refuse to prompt at all. That is the durable
identity, and it is the right place for an end user's grant to live.

For everything else the answer is not to chase the grant, it is to **not
need the microphone**: `synthbase/inject.py` plays a file onto the audio-in
bus, which is what the microphone was being used for in a test anyway. Job
done with TCC entirely out of the loop.

So: an end user grants it once to Patchwerk.app; Cole grants it to Terminal
for a hand-run rig; an agent session should expect `input disabled` —
correctly, and now with the correct reason — and inject a file instead.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import audio_session as A
from .audio_devices import list_audio_devices

SC_APP = Path("/Applications/SuperCollider.app")


def _codesign(path: Path) -> dict:
    """Signature facts that decide whether a TCC grant can be durable."""
    out = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    try:
        raw = subprocess.run(["codesign", "-dv", "--", str(path)],
                             capture_output=True, text=True, timeout=15)
        text = raw.stdout + raw.stderr
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
        return out
    for line in text.splitlines():
        if line.startswith("Identifier="):
            out["identifier"] = line.split("=", 1)[1]
        elif line.startswith("TeamIdentifier="):
            out["team"] = line.split("=", 1)[1]
        elif line.startswith("Info.plist"):
            out["info_plist_bound"] = "not bound" not in line
        elif "flags=" in line and "adhoc" in line:
            out["adhoc"] = True
    out.setdefault("adhoc", False)
    try:
        req = subprocess.run(["codesign", "-d", "-r-", "--", str(path)],
                             capture_output=True, text=True, timeout=15)
        for line in (req.stdout + req.stderr).splitlines():
            if "designated =>" in line:
                out["designated"] = line.split("=>", 1)[1].strip()
    except Exception:  # noqa: BLE001
        pass
    # A grant is durable only if TCC has something stable to key it on.
    out["durable_identity"] = bool(
        out.get("team") and out.get("team") != "not set" and not out["adhoc"])
    return out


def diagnose() -> dict:
    """Everything relevant, as data. `report()` is the human rendering."""
    devices = list_audio_devices(force=True)
    default_in = next((d for d in devices["inputs"] if d.get("default")), None)
    default_out = next((d for d in devices["outputs"] if d.get("default")), None)

    # `audio_session.probe` already names the cause and renders the sentence
    # (`cause_of` / `classify_scsynth`). There is exactly one classifier in
    # this repo and this is not it — the doctor's job is the IDENTITY half,
    # which nothing else looks at.
    verdict = A.probe(input_channels=2)
    rates_agree = bool(
        default_in and default_out
        and default_in.get("sample_rate") == default_out.get("sample_rate"))

    return {
        "host_app": A._host_app(),
        "python": _codesign(Path(sys.executable).resolve()),
        "scsynth": _codesign(Path(A.find_scsynth() or "/nonexistent")),
        "sc_app_bundle_id": _bundle_id(),
        "default_input": default_in,
        "default_output": default_out,
        "rates_agree": rates_agree,
        "probe": verdict,
        "output_only_devices": A.output_only_devices(),
        "rate_matched_input": _rate_match(default_out, devices),
    }


def _bundle_id() -> str | None:
    plist = SC_APP / "Contents" / "Info.plist"
    if not plist.is_file():
        return None
    try:
        return subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier",
             str(plist)], capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _rate_match(default_out, devices) -> str | None:
    if not default_out:
        return None
    rate = default_out.get("sample_rate")
    for d in devices["inputs"]:
        if d.get("sample_rate") == rate:
            return d["name"]
    return None


# -- the human report ------------------------------------------------------


def report(d: dict | None = None) -> int:
    """Print the diagnosis. Returns a shell exit code: 0 = input works."""
    d = d or diagnose()
    p, s = d["python"], d["scsynth"]
    din, dout = d["default_input"], d["default_output"]

    print("== who is actually asking for the microphone ==")
    print(f"  responsible app : {d['host_app']}")
    print(f"  python          : {p.get('path')}")
    print(f"                    signature={'adhoc' if p.get('adhoc') else 'signed'}"
          f"  durable-identity={p.get('durable_identity')}")
    if p.get("designated"):
        print(f"                    DR: {p['designated'][:96]}")
    print(f"  scsynth         : {s.get('path')}")
    print(f"                    identifier={s.get('identifier')!r} "
          f"team={s.get('team')} bundled-Info.plist={s.get('info_plist_bound')}")
    print(f"  SuperCollider.app bundle id: {d['sc_app_bundle_id']}")
    if not p.get("durable_identity"):
        print("\n  -> This Python has NO stable code-signing identity, so any")
        print("     microphone grant TCC records for it is keyed to the exact")
        print("     binary bytes and path. A Homebrew Python update changes")
        print("     both, and the grant silently stops matching. THAT is what")
        print("     a 'temporary approval' looks like from the user's side.")

    print("\n== devices ==")
    print(f"  default in  : {din['name']!r} @ {din.get('sample_rate')} Hz"
          if din else "  default in  : none")
    print(f"  default out : {dout['name']!r} @ {dout.get('sample_rate')} Hz"
          if dout else "  default out : none")
    print(f"  rates agree : {d['rates_agree']}")
    print(f"  output-only : {d['output_only_devices']}")

    print("\n== can scsynth start with audio input, right now ==")
    pr = d["probe"]
    print(f"  ready={pr['ready']}  cause={pr['cause']}  rc={pr['rc']}")
    for line in _wrap(pr["why"], 72):
        print(f"  {line}")

    print("\n== what to do ==")
    for line in advice(d):
        print(f"  {line}")
    return 0 if pr["ready"] else 1


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def advice(d: dict) -> list[str]:
    """The recommendation, keyed off the CAUSE — never a generic list.

    Branches on `audio_session`'s short tags rather than on substrings of
    the sentence, so a reworded diagnosis cannot silently change which
    advice a user gets.
    """
    cause = d["probe"]["cause"]
    if cause == A.READY:
        return ["Nothing. Audio input starts here and the meter is live."]
    if cause == A.NO_DEVICES:
        return ["Install SuperCollider, or check the install — scsynth "
                "listed no CoreAudio devices at all."]
    if cause == A.DEVICE_BUSY:
        return ["NOT a permission problem. The input device would not open: "
                "something holds it exclusively, or it went away "
                "(bluetooth). Disconnect and retry, or pick another input."]
    if cause == A.SAMPLE_RATE:
        match = d["rate_matched_input"]
        out = []
        if match:
            out.append(f"NOT a permission problem. Use {match!r} as the input "
                       f"device — it already matches the output's rate.")
            out.append("The engine now does this automatically at boot; if you "
                       "are still seeing input disabled, clear the cached "
                       "verdict with `synthbase.audio_session.clear_cache()`.")
        else:
            out.append("NOT a permission problem. No input device matches the "
                       "output's sample rate.")
        din, dout = d["default_input"], d["default_output"]
        if din and dout:
            out.append(f"Or in Audio MIDI Setup set {din['name']!r} "
                       f"({din.get('sample_rate')} Hz) to "
                       f"{dout.get('sample_rate')} Hz to match "
                       f"{dout['name']!r}.")
        out.append("Do NOT re-approve the microphone; it is not the problem.")
        return out
    if cause == A.STALL:
        out = [
            "This IS the permission case: coreaudiod never answered, which is "
            "what a missing microphone grant looks like from a disclaimed "
            "process.",
        ]
        if not d["python"].get("durable_identity"):
            out += [
                "Approving it from here will not stick — see the identity note "
                "above. Two options that do work:",
                "  1. Run the microphone check from Terminal (`./run.sh`), "
                "whose grant is held against a real bundle id and persists — "
                "or from the packaged Patchwerk.app, which is built to hold "
                "one (see packaging/README.md).",
                "  2. Don't use the microphone: `synthbase/inject.py` plays a "
                "WAV onto the audio-in bus, which is what a test needs anyway "
                "(`python3 tests/listen.py --in-file signal.wav`).",
            ]
        return out
    return ["scsynth exited before starting audio — the sentence above is "
            "the reason, and it is not a permission failure."]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    d = diagnose()
    if "--json" in argv:
        print(json.dumps(d, indent=2))
        return 0 if d["probe"]["ready"] else 1
    return report(d)


if __name__ == "__main__":
    raise SystemExit(main())
