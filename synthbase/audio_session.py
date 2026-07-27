"""Pick scsynth device options that can actually START on this machine.

Enumerating CoreAudio devices and *starting* one are two different
permissions, and they fail apart. A process that is TCC-disclaimed — every
agent session under Claude Code is, via `Claude.app/Contents/Helpers/
disclaimer`, which calls `responsibility_spawnattrs_setdisclaim` — can list
devices freely but has no microphone grant and no way to be prompted for
one. When scsynth then opens a device that carries an INPUT stream,
coreaudiod never answers:

    SC_CoreAudioDriver::DriverStart()
      -> AudioDeviceCreateIOProcID
        -> HALC_ProxyIOContext::_TellServerAboutStreamUsage
          -> mach_msg2_trap        <-- blocks forever, no timeout, no error

That is the whole bug. scsynth prints its device list and its sample rate,
then stops before "SuperCollider 3 server ready", and `Server().boot()`
waits on a handshake that will never come.

Two things this is NOT, both checked directly rather than assumed:

* Not the parent process. A default-args scsynth spawned from launchd —
  a completely different responsible process — hangs in exactly the same
  place. Reparenting is not a lever.
* Not `-i 0`. Disabling input BUSES does not stop scsynth from opening the
  default input DEVICE, so `-i 0` alone still hangs. The fix is device
  selection: pin `-H` to a device that has no input streams at all.

So the working configuration is `-H "<output-only device>" -i 0`, which is
what `input_device == output_device == <that device>` renders to. It needs
no permission, no prompt and no user interaction.

We do not force that on everyone: Cole's Terminal HAS a microphone grant,
`modules/audio_in.py` and the master input meter are real features, and a
blanket disable would silently remove them. Instead we PROBE — spawn a bare
scsynth with the requested devices and a short deadline — and only fall back
when the probe proves input cannot start here. The verdict is cached, so the
cost is paid once per machine/context rather than on every boot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .audio_devices import list_audio_devices

SCSYNTH_READY = "SuperCollider 3 server ready"

#: How long a probe waits for the ready line. A device that CAN start prints
#: it in well under a second; the stall never ends, so this only bounds the
#: failure case.
PROBE_TIMEOUT = 6.0

#: Grace period after killing a STALLED probe, before the next one. See the
#: note in `probe_start` — coreaudiod needs it, and skipping it turns a
#: working fallback into a spurious "no device can start".
SETTLE_AFTER_STALL = 1.5

#: Attempts per candidate device in `resolve`. One retry is enough for the
#: settle race above; more would just be slow.
PROBE_ATTEMPTS = 2

CACHE_PATH = Path.home() / ".patchwerk" / "audio-session.json"
CACHE_TTL = 7 * 24 * 3600.0

_MEM: dict[str, dict] = {}


def find_scsynth() -> str | None:
    """scsynth is inside SuperCollider.app on macOS and not on PATH."""
    for cand in (
        "/Applications/SuperCollider.app/Contents/Resources/scsynth",
        "/usr/local/bin/scsynth",
        "/opt/homebrew/bin/scsynth",
    ):
        if Path(cand).is_file():
            return cand
    from shutil import which

    return which("scsynth")


# -- device classification ------------------------------------------------


def output_only_devices() -> list[str]:
    """Output devices that carry NO input stream, best candidate first.

    A device is disqualified by having an input side under the same name —
    that is precisely the input stream whose usage report blocks in
    coreaudiod. The system default sorts first so we keep using whatever the
    user actually listens on.
    """
    devices = list_audio_devices(force=True)
    inputs = {d["name"] for d in devices["inputs"]}
    outs = [d for d in devices["outputs"] if d["name"] not in inputs]
    outs.sort(key=lambda d: (not d.get("default"), d["name"]))
    return [d["name"] for d in outs]


# -- probing --------------------------------------------------------------


def probe_start(
    device: str | None,
    input_channels: int,
    timeout: float = PROBE_TIMEOUT,
) -> bool:
    """Can a bare scsynth START this device configuration, right now?

    No supriya and no Patchwerk in the loop, so a False here is never a
    Patchwerk regression. The process is killed either way — this only ever
    answers the question, it never leaves a server behind.
    """
    sc = find_scsynth()
    if sc is None:
        return False
    argv = [sc, "-u", str(_free_udp_port()), "-i", str(input_channels), "-o", "2"]
    if device:
        argv += ["-H", device]
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError:
        return False
    ready = False
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        buf, deadline = "", time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = proc.stdout.read()
            except BlockingIOError:
                chunk = None
            if chunk:
                buf += chunk.decode("utf-8", "replace")
                if SCSYNTH_READY in buf:
                    ready = True
                    break
            elif proc.poll() is not None:
                break            # died (bad device name) — not a stall
            else:
                time.sleep(0.05)
    finally:
        stalled = not ready and proc.poll() is None
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        proc.stdout.close()
        if stalled:
            # A killed stall leaves coreaudiod holding the half-finished
            # stream-usage request, and the NEXT probe fails spuriously for
            # a moment. Observed directly: back-to-back `rig.py doctor` runs
            # disagreed until this settle was added. Without it the
            # output-only fallback can be wrongly declared impossible.
            time.sleep(SETTLE_AFTER_STALL)
    return ready


def _free_udp_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- cached verdict -------------------------------------------------------


def _context_key() -> str:
    """What the verdict actually depends on: the devices and who spawned us.

    The same Mac answers differently under Terminal (microphone granted) and
    under a disclaimed agent session, so the host app has to be part of the
    key or a cached verdict from one context is wrong in the other.
    """
    devices = list_audio_devices(force=False)
    names = sorted(
        d["name"] for d in devices["inputs"] + devices["outputs"]
    )
    return json.dumps({"devices": names, "host": _host_app()}, sort_keys=True)


def _host_app() -> str:
    """The topmost non-launchd ancestor — the TCC-responsible app, in effect."""
    if sys.platform != "darwin":
        return sys.platform
    pid, top = os.getpid(), ""
    for _ in range(24):
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            break
        if not out:
            break
        ppid, _, comm = out.partition(" ")
        try:
            ppid = int(ppid.strip())
        except ValueError:
            break
        top = comm.strip() or top
        if ppid <= 1:
            break
        pid = ppid
    return top


def _load_cache() -> dict:
    try:
        raw = json.loads(CACHE_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=1))
    except OSError:
        pass          # a cache we cannot write is a slow probe, not a failure


def input_can_start(force: bool = False) -> bool:
    """Can an input-bearing device start in THIS context? Cached per context.

    Probing costs a second when it works and `PROBE_TIMEOUT` when it does
    not, so the verdict is remembered rather than re-derived on every engine
    boot (device switches reboot the engine).
    """
    key = _context_key()
    if not force:
        hit = _MEM.get(key) or _load_cache().get(key)
        if hit and time.time() - hit.get("t", 0) < CACHE_TTL:
            return bool(hit["ok"])
    ok = probe_start(device=None, input_channels=2)
    entry = {"ok": ok, "t": time.time()}
    _MEM[key] = entry
    cache = _load_cache()
    cache[key] = entry
    _save_cache(cache)
    return ok


def clear_cache() -> None:
    _MEM.clear()
    try:
        CACHE_PATH.unlink()
    except OSError:
        pass


# -- the decision ---------------------------------------------------------


class NoStartableDevice(RuntimeError):
    """No device on this machine can start here, and we know why."""


def resolve(
    input_device: str | None,
    output_device: str | None,
    input_channels: int,
) -> tuple[str | None, str | None, int, str | None]:
    """Return (input_device, output_device, input_channels, note).

    `note` is None when nothing was overridden, and a human sentence when we
    had to drop audio input to get a server at all — the GUI surfaces it as
    `boot_note` so a missing input meter is explained rather than mysterious.
    """
    wants_input = input_channels > 0 or input_device is not None
    if wants_input and input_can_start():
        return input_device, output_device, input_channels, None

    # Output-only. `-i 0` is NOT sufficient on its own: scsynth still opens
    # the default INPUT device unless -H pins it to one that has no input.
    candidates = output_only_devices()
    if output_device and output_device in candidates:
        candidates = [output_device] + [c for c in candidates if c != output_device]
    for dev in candidates:
        ok = any(
            probe_start(device=dev, input_channels=0)
            for _ in range(PROBE_ATTEMPTS)
        )
        if ok:
            note = None
            if wants_input:
                note = (
                    f"audio input disabled — this session cannot start an "
                    f"input device (no microphone permission and no way to "
                    f"request one), so the engine is pinned to {dev!r}, "
                    f"output only"
                )
            # scsynth takes ONE -H; passing the same name for both renders it.
            return dev, dev, 0, note

    raise NoStartableDevice(
        "no CoreAudio device could be started.\n"
        "  Every output device on this machine also carries an input stream, "
        "and this session has no microphone permission, so opening one blocks "
        "in coreaudiod forever.\n"
        "  Fix: in Audio MIDI Setup create an output-only device (or make the "
        "built-in speakers the default output) and try again."
    )
