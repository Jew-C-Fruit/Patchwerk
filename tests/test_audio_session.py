#!/usr/bin/env python3
"""Headless checks for synthbase/audio_session.py — no scsynth, no audio.

The device-start stall itself can only be reproduced on a Mac with
CoreAudio, so what is testable here is the LOGIC around it: which devices
are considered startable, that the fallback renders the argv that actually
works, that a probe verdict is cached per context rather than per process,
and that a machine with no output-only device fails loudly instead of
hanging. `tests/audio_proof.py` is the other half, and it needs real audio.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from synthbase import audio_session as A  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


class Patch:
    """Swap module attributes for the duration of a block."""

    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(A, k)
            setattr(A, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(A, k, v)


def fake_devices(inputs, outputs):
    return lambda force=False: {
        "inputs": [{"name": n, "channels": 1, "sample_rate": 48000,
                    "default": i == 0} for i, n in enumerate(inputs)],
        "outputs": [{"name": n, "channels": 2, "sample_rate": 48000,
                     "default": i == 0} for i, n in enumerate(outputs)],
    }


# -- 1. classification ---------------------------------------------------------

with Patch(list_audio_devices=fake_devices(
        ["MacBook Pro Microphone"], ["MacBook Pro Speakers"])):
    check("1. an output with no same-named input is output-only",
          A.output_only_devices() == ["MacBook Pro Speakers"],
          str(A.output_only_devices()))

with Patch(list_audio_devices=fake_devices(
        ["Scarlett 2i2"], ["Scarlett 2i2"])):
    # The interface case: one device carrying BOTH directions is exactly the
    # thing whose input stream blocks in coreaudiod, so it must NOT qualify.
    check("2. a device with an input side is NOT output-only",
          A.output_only_devices() == [], str(A.output_only_devices()))

with Patch(list_audio_devices=fake_devices(
        ["Mic"], ["Headphones", "Speakers"])):
    devs = A.output_only_devices()
    check("3. the system default output sorts first",
          devs[0] == "Headphones", str(devs))


# -- 2. resolve() ---------------------------------------------------------------

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {"ok": True, "cause": A.READY, "why": "ready"},
           probe=lambda **kw: {"ready": True, "cause": A.READY, "why": "ready", "rc": None, "devices": True}):
    got = A.resolve(None, None, 2)
    check("4. input that CAN start is left completely alone",
          got == (None, None, 2, None), str(got))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {"ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda **kw: {"ready": True, "cause": A.READY, "why": "ready", "rc": None, "devices": True}):
    in_dev, out_dev, ch, note = A.resolve(None, None, 2)
    # scsynth takes ONE -H. Passing the SAME name as both input and output
    # is what makes supriya render `-H "Speakers"` rather than `-H "" Speakers`,
    # and the two-arg form re-opens the default input — the whole bug.
    check("5. fallback pins BOTH device slots to one output-only device",
          (in_dev, out_dev, ch) == ("Speakers", "Speakers", 0),
          f"{in_dev!r}/{out_dev!r} ch={ch}")
    check("6. dropping input announces itself in boot_note",
          bool(note) and "input" in note.lower(), (note or "")[:60])

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {"ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda **kw: {"ready": True, "cause": A.READY, "why": "ready", "rc": None, "devices": True}):
    _, _, _, note = A.resolve(None, None, 0)
    check("7. a caller that never wanted input gets no scary note",
          note is None, repr(note))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers", "HDMI"]),
           input_probe=lambda force=False: {"ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda device=None, **kw: {"ready": device == "HDMI", "cause": A.STALL, "why": "x", "rc": None, "devices": True}):
    in_dev, _, _, _ = A.resolve(None, None, 2)
    check("8. a candidate that fails to start is skipped for one that works",
          in_dev == "HDMI", repr(in_dev))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers", "HDMI"]),
           input_probe=lambda force=False: {"ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda device=None, **kw: {"ready": True, "cause": A.READY, "why": "ready", "rc": None, "devices": True}):
    in_dev, _, _, _ = A.resolve(None, "HDMI", 2)
    check("9. an explicitly requested output is tried first",
          in_dev == "HDMI", repr(in_dev))

with Patch(list_audio_devices=fake_devices(["Scarlett"], ["Scarlett"]),
           input_probe=lambda force=False: {"ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda **kw: {"ready": False, "cause": A.STALL, "why": "stalled", "rc": None, "devices": True}):
    try:
        A.resolve(None, None, 2)
        check("10. no startable device RAISES rather than hanging", False)
    except A.NoStartableDevice as exc:
        check("10. no startable device RAISES rather than hanging",
              "Audio MIDI Setup" in str(exc), "and says how to fix it")


# -- 3. cache -------------------------------------------------------------------

calls = []


def counting_probe(**kw):
    calls.append(kw)
    return {"ready": False, "cause": A.STALL, "why": "stalled", "rc": None,
            "devices": True}


with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           probe=counting_probe,
           CACHE_PATH=Path("/tmp/pw-audio-session-test.json")):
    A.clear_cache()
    A.input_can_start()
    A.input_can_start()
    A.input_can_start()
    check("11. the probe verdict is cached, not re-derived per boot",
          len(calls) == 1, f"{len(calls)} probes for 3 asks")
    A.clear_cache()
    A.input_can_start()
    check("12. clear_cache forces a fresh probe", len(calls) == 2,
          f"{len(calls)} probes")

key_a = None
with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           _host_app=lambda: "Terminal"):
    key_a = A._context_key()
with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           _host_app=lambda: "Claude"):
    check("13. the same Mac keys separately per HOST APP",
          A._context_key() != key_a, "Terminal has a mic grant; a disclaimed "
                                     "session does not")

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           _host_app=lambda: "Terminal"):
    key_same = A._context_key()
with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers", "HDMI"]),
           _host_app=lambda: "Terminal"):
    check("14. plugging in a device invalidates the verdict",
          A._context_key() != key_same)

Path("/tmp/pw-audio-session-test.json").unlink(missing_ok=True)


# -- 4. the cause survives all the way into boot_note ---------------------------
# This is the whole reason the probe stopped returning a bool. A sample-rate
# mismatch (24 kHz mic vs 48 kHz output) EXITS rc=-6 in 0.3 s; it is not the
# stall and it is not a permissions problem, and a boot_note that says
# "no microphone grant" sends someone to System Settings for nothing.

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.SAMPLE_RATE,
               "why": A.classify_scsynth(False, True, -6,
                                         "ERROR: Setting sample rate failed")},
           # No rate-matched input EXISTS here, which is the precondition for
           # disabling input at all — section 5 covers the case where one
           # does. Patched rather than left real: `find_rate_matched_input`
           # reads `audio_devices.list_audio_devices` directly, so it would
           # shell out to system_profiler from a headless test.
           find_rate_matched_input=lambda out: None,
           probe=lambda device=None, **kw: {
               "ready": True, "cause": A.READY, "why": "ready",
               "rc": None, "devices": True}):
    _, _, _, note = A.resolve(None, None, 2)
    # It may MENTION permissions — denying the theory is the point — but it
    # must never blame the microphone, which is what sent Cole to System
    # Settings for a device mismatch.
    check("15. a sample-rate mismatch does NOT get blamed on the microphone",
          "microphone" not in note.lower()
          and "not a permissions problem" in note.lower(), note)
    check("16. ...and names sample rate as the actual cause",
          "sample rate" in note.lower(), note)

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.STALL, "why": "stalled"},
           probe=lambda device=None, **kw: {
               "ready": True, "cause": A.READY, "why": "ready",
               "rc": None, "devices": True}):
    _, _, _, note = A.resolve(None, None, 2)
    check("17. the stall DOES name the missing microphone grant",
          "microphone" in note.lower(), note)

check("18. cause_of and classify_scsynth are one decision, not two",
      A.cause_of(False, True, -6, "Setting sample rate failed") == A.SAMPLE_RATE
      and "SAMPLE RATE" in A.classify_scsynth(False, True, -6,
                                              "Setting sample rate failed"))

# The stall sentence must not claim the session is mute — that was the false
# conclusion this branch exists to disprove, and rig.py's doctor now checks.
stall_text = A.classify_scsynth(False, True, None, "Number of Devices: 2")
check("19. the stall sentence no longer says audio cannot start here",
      "cannot start" not in stall_text and "output-only" in stall_text,
      stall_text)

# One classifier, imported — not a second copy that can drift.
import rig as R  # noqa: E402

check("20. tests/rig.py re-exports THE classifier rather than copying it",
      R.classify_scsynth is A.classify_scsynth)


# -- 5. sample-rate is the one failure with a RECOVERY --------------------------
# Naming the cause is only half the value; the other half is that the causes
# want different things done. A stall cannot be recovered from — no device
# with an input stream will start. A sample-rate mismatch can: another input
# device may already run at the output's rate, and using it keeps audio input
# working instead of silently removing `modules/audio_in.py` and the input
# meter for the rest of the session.
#
# `Engine.boot` has carried this fallback all along and has never executed it:
# it keys off an exception from `Server().boot()`, and `_preflight_devices`
# now settles the devices first, so the failing boot it would catch never
# happens.

CALLS = []


def _probe_pair(device=None, input_channels=2, output_device=None, **kw):
    CALLS.append({"device": device, "input_channels": input_channels,
                  "output_device": output_device})
    return {"ready": True, "cause": A.READY, "why": "ready",
            "rc": None, "devices": True}


with Patch(list_audio_devices=fake_devices(["Mic", "Built-in"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.SAMPLE_RATE, "why": "rates disagree"},
           find_rate_matched_input=lambda out: "Built-in",
           probe=_probe_pair):
    CALLS.clear()
    in_dev, out_dev, ch, note = A.resolve(None, None, 2)
    check("21. a SAMPLE-RATE failure keeps audio input, on a matched device",
          (in_dev, ch) == ("Built-in", 2), f"{in_dev!r} ch={ch}")
    check("22. ...and says which device it swapped to, not that input died",
          bool(note) and "Built-in" in note and "disabled" not in note,
          (note or "")[:72])
    check("23. ...and PROBES the pair it is about to boot, not one half",
          CALLS and CALLS[-1]["device"] == "Built-in"
          and CALLS[-1]["input_channels"] == 2, str(CALLS[-1:]))

with Patch(list_audio_devices=fake_devices(["Mic", "Built-in"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.SAMPLE_RATE, "why": "rates disagree"},
           find_rate_matched_input=lambda out: "Built-in",
           probe=lambda device=None, **kw: {
               # the matched device does not start either; only -i 0 does
               "ready": kw.get("input_channels", 2) == 0,
               "cause": A.READY if kw.get("input_channels", 2) == 0
               else A.SAMPLE_RATE,
               "why": "x", "rc": -6, "devices": True}):
    _, _, ch, note = A.resolve(None, None, 2)
    check("24. a matched device that STILL fails falls through to output-only",
          ch == 0, f"ch={ch}")
    check("25. ...and boot_note names sample rate, never the microphone",
          "sample rate" in (note or "").lower()
          and "microphone" not in (note or "").lower(), (note or "")[:72])

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.STALL, "why": "stalled"},
           find_rate_matched_input=lambda out: "Built-in",
           probe=lambda device=None, **kw: {
               "ready": True, "cause": A.READY, "why": "ready",
               "rc": None, "devices": True}):
    _, _, ch, _ = A.resolve(None, None, 2)
    check("26. a STALL does NOT take the rate-matched path — no grant, no "
          "device", ch == 0, f"ch={ch}")

with Patch(list_audio_devices=fake_devices(["Mic", "Built-in"], ["Speakers"]),
           input_probe=lambda force=False: {
               "ok": False, "cause": A.SAMPLE_RATE, "why": "rates disagree"},
           find_rate_matched_input=lambda out: "Built-in",
           probe=_probe_pair):
    # An EXPLICIT input device is the user's choice; silently substituting a
    # different one would be the same class of surprise as mis-blaming the mic.
    _, _, ch, _ = A.resolve("Scarlett", None, 2)
    check("27. an explicitly requested input is never silently swapped",
          ch == 0, f"ch={ch}")



class _RecordingPopen:
    """Capture scsynth's argv without running it. OSError exits probe early."""

    argv = None

    def __init__(self, argv, **kw):
        _RecordingPopen.argv = list(argv)
        raise OSError("not actually launching scsynth")


class _FakeSubprocess:
    Popen = _RecordingPopen
    PIPE = STDOUT = -1
    TimeoutExpired = Exception


# `find_scsynth` must be patched alongside `subprocess`, not just it. It
# resolves the binary off the SuperCollider.app paths and then PATH, so on a
# machine with no SuperCollider installed — which is exactly the CI runner —
# `probe()` returns its not-found verdict and NEVER reaches Popen. The three
# checks below then read an empty argv and fail, having asserted nothing about
# argv rendering. Green on a dev Mac, red in CI, for a reason that is about the
# host and not about the code under test.
with Patch(subprocess=_FakeSubprocess, find_scsynth=lambda: "/nonexistent/scsynth"):
    A.probe(device="In", input_channels=2, output_device="Out")
    # Guard the guard: if probe() ever short-circuits before Popen again, fail
    # HERE with a clear cause rather than three confusing empty-argv mismatches.
    check("27b. the argv checks below actually reached scsynth's launch",
          _RecordingPopen.argv is not None,
          "" if _RecordingPopen.argv is not None
          else "probe() returned before Popen — argv never recorded")
    argv = _RecordingPopen.argv or []
    check("28. differing devices render scsynth's TWO-ARGUMENT -H",
          argv[-3:] == ["-H", "In", "Out"], str(argv[-4:]))

    A.probe(device="Same", input_channels=0, output_device="Same")
    argv = _RecordingPopen.argv or []
    check("29. one device renders the one-argument form, as before",
          argv[-2:] == ["-H", "Same"], str(argv[-3:]))

    A.probe(device="Only", input_channels=2)
    argv = _RecordingPopen.argv or []
    check("30. omitting output_device changes nothing for existing callers",
          argv[-2:] == ["-H", "Only"], str(argv[-3:]))

print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, {len(FAIL)} failed"
      + (f": {', '.join(FAIL)}" if FAIL else ""))
raise SystemExit(1 if FAIL else 0)
