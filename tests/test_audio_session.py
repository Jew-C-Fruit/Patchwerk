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
           input_can_start=lambda force=False: True,
           probe_start=lambda **kw: True):
    got = A.resolve(None, None, 2)
    check("4. input that CAN start is left completely alone",
          got == (None, None, 2, None), str(got))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           input_can_start=lambda force=False: False,
           probe_start=lambda **kw: True):
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
           input_can_start=lambda force=False: False,
           probe_start=lambda **kw: True):
    _, _, _, note = A.resolve(None, None, 0)
    check("7. a caller that never wanted input gets no scary note",
          note is None, repr(note))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers", "HDMI"]),
           input_can_start=lambda force=False: False,
           probe_start=lambda device=None, **kw: device == "HDMI"):
    in_dev, _, _, _ = A.resolve(None, None, 2)
    check("8. a candidate that fails to start is skipped for one that works",
          in_dev == "HDMI", repr(in_dev))

with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers", "HDMI"]),
           input_can_start=lambda force=False: False,
           probe_start=lambda device=None, **kw: True):
    in_dev, _, _, _ = A.resolve(None, "HDMI", 2)
    check("9. an explicitly requested output is tried first",
          in_dev == "HDMI", repr(in_dev))

with Patch(list_audio_devices=fake_devices(["Scarlett"], ["Scarlett"]),
           input_can_start=lambda force=False: False,
           probe_start=lambda **kw: False):
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
    return False


with Patch(list_audio_devices=fake_devices(["Mic"], ["Speakers"]),
           probe_start=counting_probe,
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

print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, {len(FAIL)} failed"
      + (f": {', '.join(FAIL)}" if FAIL else ""))
raise SystemExit(1 if FAIL else 0)
