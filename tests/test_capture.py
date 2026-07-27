#!/usr/bin/env python3
"""Headless checks for the capture tap and the input injector.

    python3 tests/test_capture.py

No scsynth, no audio device, no supriya server — a fake stands in for the
server so this runs in CI. What it proves is the BOOKKEEPING, which is
where these two managers can go wrong silently:

* registration tracks the server OBJECT. `set_devices` builds a NEW
  scsynth, and a manager that remembers "already sent" as a boolean never
  re-sends its synthdefs to the replacement. That failure is invisible
  until someone switches devices and the feature is quietly dead — the
  exact landmine `CLAUDE.md` records against the LFO and threshold
  managers, and the reason both of them do it this way.
* every allocation is freed on the paths that free it. A capture buffer is
  megabytes of scsynth's real-time pool and an injector node writes the
  audio-in bus forever; leaking either is not a tidiness problem.
* one at a time, refused loudly. Two `RecordBuf`s on one buffer, or two
  `ReplaceOut`s on one bus, is not a state anyone can reason about.

`tests/audio_io_proof.py` is the other half and needs real audio.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supriya import AddAction  # noqa: E402

from synthbase.capture import CaptureManager  # noqa: E402
from synthbase.inject import InputInjector  # noqa: E402
from synthbase.wavio import write_wav  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


# -- the fakes --------------------------------------------------------------


class FakeEntity:
    """A buffer, bus or synth. Remembers whether — and HOW — it was freed.

    Buffers and buses reject `force`, exactly as supriya's do, so the
    manager's two-arm free is exercised rather than assumed.
    """

    def __init__(self, index: int, kind: str) -> None:
        self.index, self.kind, self.freed = index, kind, False
        self.forced = False
        self.written = None

    def __int__(self) -> int:
        return self.index

    def free(self, force: bool = False) -> None:
        if force and self.kind != "synth":
            raise TypeError("free() got an unexpected keyword argument 'force'")
        self.freed = True
        self.forced = self.forced or force

    def get(self) -> float:
        return 1e9          # a phase past the buffer end: "it filled"

    def set(self, **kw) -> None:
        self.settings = kw

    def write(self, path, **kw) -> None:
        self.written = (Path(path), kw)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"")


class FakeStatus:
    actual_sample_rate = 48000.0


class FakeServer:
    def __init__(self) -> None:
        self.status = FakeStatus()
        self.synthdef_sends = 0
        self.entities: list[FakeEntity] = []
        self.synths: list[tuple] = []
        self._next = 100

    def _make(self, kind: str) -> FakeEntity:
        self._next += 1
        e = FakeEntity(self._next, kind)
        self.entities.append(e)
        return e

    def add_synthdefs(self, *defs) -> None:
        self.synthdef_sends += 1

    def sync(self) -> None:
        pass

    def add_buffer(self, **kw) -> FakeEntity:
        return self._make("buffer")

    def add_bus(self, **kw) -> FakeEntity:
        return self._make("bus")

    def add_synth(self, sdef, **kw) -> FakeEntity:
        self.synths.append((sdef, kw))
        return self._make("synth")


class FakeInstance:
    def __init__(self, out: int) -> None:
        self.settings = {"out": out}


class FakeRack:
    def __init__(self, instances: dict) -> None:
        self.instances = instances

    def find(self, key):
        return self.instances.get(key)


class FakeEngine:
    def __init__(self, server, out_channels: int = 2) -> None:
        self.server = server
        self.root_group = FakeEntity(1, "group")

        class O:
            output_bus_channel_count = out_channels
        self.options = O()


class FakeApp:
    def __init__(self, out_channels: int = 2) -> None:
        self.server = FakeServer()
        self.engine = FakeEngine(self.server, out_channels)
        self.rack = FakeRack({"pulse_pad": FakeInstance(16),
                              "reverb": FakeInstance(0)})
        self.master = None


# -- capture ----------------------------------------------------------------


def test_capture_bus_resolution() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    st = cap.arm("master", 1.0)
    check("1. 'master' captures the hardware output bus", st["bus"] == 0,
          str(st["bus"]))
    cap.stop()

    st = cap.arm("pulse_pad", 1.0)
    check("2. an instance captures ITS OWN out bus", st["bus"] == 16,
          str(st["bus"]))
    cap.stop()

    try:
        cap.arm("no_such_module", 1.0)
        check("3. an unknown target raises rather than recording silence",
              False)
    except KeyError:
        check("3. an unknown target raises rather than recording silence",
              True)


def test_capture_one_at_a_time() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    cap.arm("master", 1.0)
    try:
        cap.arm("master", 1.0)
        check("4. a second arm is REFUSED while one is running", False)
    except RuntimeError:
        check("4. a second arm is REFUSED while one is running", True)
    cap.stop()
    try:
        cap.stop()
        check("5. stopping when nothing is armed raises", False)
    except RuntimeError:
        check("5. stopping when nothing is armed raises", True)


def test_capture_frees_everything() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "c.wav"
        cap.arm("master", 1.0, path=str(out))
        res = cap.stop()
        check("6. stop() writes to the requested path",
              res["path"] == str(out) and out.exists(), res["path"])
        leaked = [e for e in app.server.entities
                  if e.kind in ("buffer", "bus", "synth") and not e.freed]
        check("7. stop() frees the buffer, the k-bus and the synth",
              not leaked, f"{[e.kind for e in leaked]}")
        buf = next(e for e in app.server.entities if e.written)
        check("8. the WAV is written as float, not clipped int24",
              buf.written[1].get("sample_format") == "float"
              and buf.written[1].get("header_format") == "wav",
              str(buf.written[1]))
        check("9. the frame count is clamped to the buffer, not the phase",
              res["frames"] == 48000, str(res["frames"]))
        # `Node.free()` releases a GATED synth instead of freeing it
        # (rack.py:_free_node_note). Neither synthdef here has a gate today;
        # forcing means adding one later cannot turn "capture over" into
        # "capture synth runs forever".
        synths = [e for e in app.server.entities if e.kind == "synth"]
        check("9b. the capture NODE is freed with force=True",
              synths and all(e.forced for e in synths),
              str([(e.kind, e.forced) for e in synths]))


def test_capture_reset_frees_and_forgets() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    cap.arm("master", 1.0)
    cap.reset()
    leaked = [e for e in app.server.entities if not e.freed
              and e.kind in ("buffer", "bus", "synth")]
    check("10. reset() frees an in-flight capture", not leaked)
    check("11. reset() forgets the registered server",
          cap._registered_server is None)
    check("12. and reset() leaves nothing armed",
          cap.status() == {"armed": False}, str(cap.status()))


def test_capture_tracks_the_server_object() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    cap.arm("master", 1.0)
    cap.stop()
    cap.arm("master", 1.0)
    cap.stop()
    check("13. synthdefs are sent ONCE per server",
          app.server.synthdef_sends == 1, str(app.server.synthdef_sends))

    # set_devices builds a NEW scsynth. A boolean flag would skip this.
    app.server = FakeServer()
    app.engine.server = app.server
    cap.arm("master", 1.0)
    cap.stop()
    check("14. a REPLACED server gets the synthdefs again",
          app.server.synthdef_sends == 1, str(app.server.synthdef_sends))


def test_capture_seconds_are_bounded() -> None:
    app = FakeApp()
    cap = CaptureManager(app)
    st = cap.arm("master", 9999.0)
    check("15. an absurd duration is capped, not allocated",
          st["seconds"] <= 120.0, str(st["seconds"]))
    cap.stop()


def test_capture_sits_after_master() -> None:
    """Placement is what makes the tap immune to `reorder_for_wires`."""
    app = FakeApp()
    cap = CaptureManager(app)
    cap.arm("master", 1.0)
    _, kw = app.server.synths[-1]
    check("16. with no master section the anchor is the root group",
          int(kw["target_node"]) == int(app.engine.root_group),
          str(int(kw["target_node"])))
    cap.stop()

    class FakeMaster:
        _master_node = FakeEntity(77, "synth")
    app.master = FakeMaster()
    cap.arm("master", 1.0)
    _, kw = app.server.synths[-1]
    check("17. with a master section the tap sits AFTER it — post-volume",
          int(kw["target_node"]) == 77
          and kw["add_action"] is AddAction.ADD_AFTER,
          f"node {int(kw['target_node'])} {kw['add_action']!r}")
    cap.stop()


# -- inject -----------------------------------------------------------------


def _tone_file(dirpath: Path, channels: int = 1, sr: int = 44100,
               seconds: float = 0.5) -> Path:
    import math
    n = int(sr * seconds)
    ch = [[0.4 * math.sin(2 * math.pi * 440 * i / sr) for i in range(n)]
          for _ in range(channels)]
    return write_wav(dirpath / f"t{channels}.wav", ch, sr)


def test_inject_basics() -> None:
    app = FakeApp()
    inj = InputInjector(app)
    check("18. nothing is playing before play()",
          inj.status()["playing"] is False)
    check("19. the injector reports the bus audio_in actually reads",
          inj.input_bus() == 2, str(inj.input_bus()))

    with tempfile.TemporaryDirectory() as tmp:
        mono = _tone_file(Path(tmp), 1)
        st = inj.play(str(mono))
        check("20. play() reports the file's real shape, not the server's",
              st["playing"] and st["channels"] == 1
              and st["sample_rate"] == 44100, str(st))
        sdef, kw = app.server.synths[-1]
        check("21. a mono file uses the 1-channel injector synthdef",
              sdef.name.endswith("1"), sdef.name)

        stereo = _tone_file(Path(tmp), 2)
        inj.play(str(stereo))
        sdef, _ = app.server.synths[-1]
        check("22. a stereo file uses the 2-channel one", sdef.name.endswith("2"),
              sdef.name)
        check("23. a second play() replaces the first — one writer per bus",
              sum(1 for e in app.server.entities
                  if e.kind == "synth" and not e.freed) == 1)

        inj.stop()
        leaked = [e for e in app.server.entities
                  if e.kind in ("buffer", "synth") and not e.freed]
        check("24. stop() frees the node AND the file buffer", not leaked,
              str([e.kind for e in leaked]))
        synths = [e for e in app.server.entities if e.kind == "synth"]
        check("24b. the injector NODE is freed with force=True — an unforced "
              "free would leave it writing the audio-in bus",
              synths and all(e.forced for e in synths),
              str([(e.kind, e.forced) for e in synths]))
        check("25. and status() says so", inj.status()["playing"] is False)

    try:
        inj.play("/definitely/not/here.wav")
        check("26. a missing file raises, rather than playing silence", False)
    except FileNotFoundError:
        check("26. a missing file raises, rather than playing silence", True)


def test_inject_tracks_the_server_object() -> None:
    app = FakeApp()
    inj = InputInjector(app)
    with tempfile.TemporaryDirectory() as tmp:
        f = _tone_file(Path(tmp), 1)
        inj.play(str(f))
        inj.play(str(f))
        check("27. injector synthdefs are sent once per server",
              app.server.synthdef_sends == 1, str(app.server.synthdef_sends))
        inj.stop()

        app.server = FakeServer()
        app.engine.server = app.server
        inj.play(str(f))
        check("28. a REPLACED server gets them again",
              app.server.synthdef_sends == 1, str(app.server.synthdef_sends))
        inj.reset()
        check("29. reset() forgets the registered server",
              inj._registered_server is None)


def test_inject_runs_before_the_rack() -> None:
    """Placement: a sibling BEFORE the root group, outside the topo-sort."""
    app = FakeApp()
    inj = InputInjector(app)
    with tempfile.TemporaryDirectory() as tmp:
        inj.play(str(_tone_file(Path(tmp), 1)))
        _, kw = app.server.synths[-1]
        check("30. the injector is placed relative to the ROOT GROUP",
              int(kw["target_node"]) == int(app.engine.root_group),
              str(int(kw["target_node"])))
        check("31. and BEFORE it, so it writes the bus first",
              kw["add_action"] is AddAction.ADD_BEFORE, repr(kw["add_action"]))


def main() -> int:
    for fn in (test_capture_bus_resolution, test_capture_one_at_a_time,
               test_capture_frees_everything, test_capture_reset_frees_and_forgets,
               test_capture_tracks_the_server_object,
               test_capture_seconds_are_bounded, test_capture_sits_after_master,
               test_inject_basics, test_inject_tracks_the_server_object,
               test_inject_runs_before_the_rack):
        print(f"\n-- {fn.__name__} --")
        fn()
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(PASS)} ok, "
          f"{len(FAIL)} failed" + (f": {', '.join(FAIL)}" if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
