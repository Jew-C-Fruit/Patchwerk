"""Command-line entry points.

    python -m synthbase devices            # list MIDI inputs / audio device help
    python -m synthbase test               # boot engine, play a 2s test tone
    python -m synthbase play patches/demo.py [--no-midi] [--no-reload]
"""

from __future__ import annotations

import argparse
import importlib.util
import threading
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
        hardware_buffer_size=getattr(args, "hw_buffer", None) or 256,
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


def cmd_gui(args) -> None:
    import asyncio
    import webbrowser

    from .app import SynthApp
    from .server import GuiServer

    app = SynthApp(
        input_device=getattr(args, "in_device", None),
        output_device=getattr(args, "out_device", None),
        use_midi=not args.no_midi,
        use_reload=not args.no_reload,
        hardware_buffer_size=getattr(args, "hw_buffer", None) or 256,
    )
    app.start(args.patch)
    resumed = False
    try:  # a ⟳-restart leaves a resume file: restore modules/wiring/settings
        from .presets import apply_resume
        resumed = apply_resume(app)
        if resumed:
            print("[resume] restored pre-restart modules, wiring and settings")
    except Exception as exc:  # noqa: BLE001
        print("[resume] restore failed:", exc)
    server = GuiServer(app, port=args.port)
    # A RESUMED boot is a ⟳ restart — the user already has a tab open;
    # auto-opening again would launch the system DEFAULT browser (Safari
    # on a Chrome rig: two UIs). Only fresh boots open a browser.
    if not args.no_browser and not resumed:
        threading.Timer(0.8, webbrowser.open, [f"http://127.0.0.1:{args.port}"]).start()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        app.stop()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="synthbase")
    sub = parser.add_subparsers(dest="command", required=True)

    p_devices = sub.add_parser("devices", help="list MIDI inputs")
    p_devices.set_defaults(func=cmd_devices)

    p_test = sub.add_parser("test", help="boot the engine and play a test tone")
    p_test.add_argument("--in-device", dest="in_device")
    p_test.add_argument("--hw-buffer", dest="hw_buffer", type=int,
                       help="hardware buffer in frames (default 256; lower = less latency)")
    p_test.add_argument("--out-device", dest="out_device")
    p_test.set_defaults(func=cmd_test)

    p_play = sub.add_parser("play", help="run a patch file")
    p_play.add_argument("patch", help="path to a patch file, e.g. patches/demo.py")
    p_play.add_argument("--in-device", dest="in_device")
    p_play.add_argument("--hw-buffer", dest="hw_buffer", type=int,
                       help="hardware buffer in frames (default 256; lower = less latency)")
    p_play.add_argument("--out-device", dest="out_device")
    p_play.add_argument("--no-midi", action="store_true")
    p_play.add_argument("--no-reload", action="store_true")
    p_play.set_defaults(func=cmd_play)

    p_gui = sub.add_parser("gui", help="run a patch with the browser GUI")
    p_gui.add_argument("patch", nargs="?", default="demo", help="patch name (default: demo)")
    p_gui.add_argument("--port", type=int, default=8765)
    p_gui.add_argument("--in-device", dest="in_device")
    p_gui.add_argument("--hw-buffer", dest="hw_buffer", type=int,
                       help="hardware buffer in frames (default 256; lower = less latency)")
    p_gui.add_argument("--out-device", dest="out_device")
    p_gui.add_argument("--no-midi", action="store_true")
    p_gui.add_argument("--no-reload", action="store_true")
    p_gui.add_argument("--no-browser", action="store_true")
    p_gui.set_defaults(func=cmd_gui)

    args = parser.parse_args(argv)
    args.func(args)
