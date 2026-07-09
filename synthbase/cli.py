"""Command-line entry points.

    python -m synthbase devices            # list MIDI inputs / audio device help
    python -m synthbase test               # boot engine, play a 2s test tone
    python -m synthbase play patches/demo.py [--no-midi] [--no-reload]
"""

from __future__ import annotations

import argparse
import importlib.util
import time
from pathlib import Path

from supriya import synthdef
from supriya.ugens import Out, SinOsc

from .engine import Engine
from .midi import MidiRouter, list_inputs
from .module import load_all_modules
from .rack import Rack
from .watcher import Reloader

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = REPO_ROOT / "modules"


@synthdef()
def _test_sine(freq=440, amp=0.1, out=0):
    sig = SinOsc.ar(frequency=freq) * amp
    Out.ar(bus=out, source=[sig, sig])


def _engine_from_args(args) -> Engine:
    return Engine(
        input_device=getattr(args, "in_device", None),
        output_device=getattr(args, "out_device", None),
    )


def cmd_devices(args) -> None:
    names = list_inputs()
    print("MIDI inputs:")
    if names:
        for name in names:
            print(f"  - {name}")
    else:
        print("  (none found — is the keyboard plugged in and powered?)")
    print(
        "\nAudio devices: pass CoreAudio device names via --in-device/--out-device"
        " on `play`/`test`. With no flags, the system default input/output are used."
    )


def cmd_test(args) -> None:
    engine = _engine_from_args(args).boot()
    try:
        engine.server.add_synthdefs(_test_sine)
        engine.server.sync()
        print("Playing 440 Hz test tone for 2 seconds...")
        node = engine.server.add_synth(_test_sine)
        time.sleep(2)
        node.free()
        time.sleep(0.2)
        print("OK — engine boots and makes sound.")
    finally:
        engine.quit()


def _load_patch(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("synthpatch", path)
    py = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(py)
    patch = getattr(py, "PATCH", None)
    if not isinstance(patch, dict) or "chain" not in patch:
        raise SystemExit(f"{path} must define PATCH = {{'chain': [...], ...}}")
    return patch


def cmd_play(args) -> None:
    patch = _load_patch(Path(args.patch))
    registry, errors = load_all_modules(MODULES_DIR)
    for fname, exc in errors.items():
        print(f"[modules] SKIPPED {fname}: {exc!r}")
    print(f"[modules] loaded: {', '.join(sorted(registry)) or '(none)'}")

    engine = _engine_from_args(args).boot()
    rack = Rack(engine, registry)
    router = None
    reloader = None
    try:
        rack.build(patch["chain"])
        chain_str = " -> ".join(inst.display for inst in rack.instances)
        print(f"[rack] {chain_str} -> hardware out")

        bindings = patch.get("bindings", {})
        if not args.no_midi and (bindings.get("cc") or bindings.get("notes_to")):
            router = MidiRouter(
                rack,
                cc_bindings=bindings.get("cc"),
                notes_to=bindings.get("notes_to"),
                port_name=bindings.get("midi_in"),
            )
            router.start()

        if not args.no_reload:
            reloader = Reloader(engine, rack, MODULES_DIR)
            reloader.start()

        print("Running — Ctrl-C to stop.")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if reloader:
            reloader.stop()
        if router:
            router.stop()
        rack.teardown()
        engine.quit()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="synthbase")
    sub = parser.add_subparsers(dest="command", required=True)

    p_devices = sub.add_parser("devices", help="list MIDI inputs")
    p_devices.set_defaults(func=cmd_devices)

    p_test = sub.add_parser("test", help="boot the engine and play a test tone")
    p_test.add_argument("--in-device", dest="in_device")
    p_test.add_argument("--out-device", dest="out_device")
    p_test.set_defaults(func=cmd_test)

    p_play = sub.add_parser("play", help="run a patch file")
    p_play.add_argument("patch", help="path to a patch file, e.g. patches/demo.py")
    p_play.add_argument("--in-device", dest="in_device")
    p_play.add_argument("--out-device", dest="out_device")
    p_play.add_argument("--no-midi", action="store_true")
    p_play.add_argument("--no-reload", action="store_true")
    p_play.set_defaults(func=cmd_play)

    args = parser.parse_args(argv)
    args.func(args)
