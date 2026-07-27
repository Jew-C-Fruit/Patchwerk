"""Pick scsynth device options that can actually START on this machine.

Enumerating CoreAudio devices and *starting* one are two different things,
and they fail apart. This module decides, before the engine boots, which
device configuration will come up — and says WHY when it has to change one.

## The device-start stall

A process that is TCC-disclaimed — every agent session under Claude Code is,
via `Claude.app/Contents/Helpers/disclaimer`, which calls
`responsibility_spawnattrs_setdisclaim` — can list devices freely but has no
microphone grant and no way to be prompted for one. When scsynth then opens a
device carrying an INPUT stream, coreaudiod never answers:

    SC_CoreAudioDriver::DriverStart()
      -> AudioDeviceCreateIOProcID
        -> HALC_ProxyIOContext::_TellServerAboutStreamUsage
          -> mach_msg2_trap        <-- blocks forever, no timeout, no error

scsynth prints its device list and its sample rate, then stops short of
"SuperCollider 3 server ready", and `Server().boot()` waits on a handshake
that will never come.

Two things this is NOT, both checked directly rather than assumed:

* Not the parent process. A default-args scsynth spawned from launchd — a
  completely different responsible process — hangs in exactly the same place.
  Reparenting is not a lever.
* Not `-i 0`. Disabling input BUSES does not stop scsynth opening the default
  input DEVICE, so `-i 0` alone still hangs. The fix is device selection: pin
  `-H` to a device that has no input streams at all.

The working configuration is `-H "<output-only device>" -i 0`, which is what
`input_device == output_device == <that device>` renders to. It needs no
permission, no prompt and no user interaction.

## The stall is only ONE way input fails, and usually not the one

`classify_scsynth` is the single place that names a cause, and it exists
because a boolean does not. This module's first cut answered yes/no, and a
sample-rate mismatch — a 24 kHz mic against a 48 kHz output, which EXITS
`rc=-6` in 0.3 s rather than stalling — got reported as "no microphone
permission". That sent Cole to System Settings to fix something that was not
broken. Every distinguishable failure gets its own sentence, and the
sample-rate one denies the permissions theory in as many words.

The classifier lives HERE, not in `tests/rig.py`, because the engine is what
writes `boot_note` for the user and so needs the cause too — and `tests/` can
import `synthbase/` while the reverse would be backwards. `tests/rig.py`
imports it, so there is one diagnosis path rather than two that drift.

## Why this does not simply force output-only on everyone

Cole's Terminal HAS a microphone grant, and `modules/audio_in.py` and the
master input meter are real features. So we PROBE — spawn a bare scsynth with
the requested devices and a short deadline — and fall back only when input is
proven unstartable here, carrying the reason into `boot_note`. The verdict is
cached per (device set, host app), so Terminal and a disclaimed session each
get their own answer and the probe is paid once.
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
SCSYNTH_DEVICES = "Number of Devices"

#: How long a probe waits for the ready line. A device that CAN start prints
#: it in well under a second, and the failures that EXIT do so just as fast;
#: this only bounds the stall, which never ends on its own.
PROBE_TIMEOUT = 6.0

#: Grace period after killing a STALLED probe, before the next one. See the
#: note in `probe` — coreaudiod needs it, and skipping it turns a working
#: fallback into a spurious "no device can start".
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


# -- naming the cause ---------------------------------------------------------

#: Short tags, for callers that need to BRANCH on the cause rather than print
#: it. `classify_scsynth` renders the long sentence from the same decision, so
#: the two can never disagree about what happened.
READY = "ready"
SAMPLE_RATE = "sample-rate"
DEVICE_BUSY = "device-busy"
EXITED = "exited"
STALL = "stall"
NO_DEVICES = "no-devices"


def cause_of(ready: bool, devices: bool, rc, text: str) -> str:
    """The one decision. Everything that names a cause goes through here."""
    low = (text or "").lower()
    if ready:
        return READY
    if rc is not None:
        if "sample rate" in low:
            return SAMPLE_RATE
        if "could not open" in low or "failed to open" in low:
            return DEVICE_BUSY
        return EXITED
    return STALL if devices else NO_DEVICES


def classify_scsynth(ready: bool, devices: bool, rc, text: str) -> str:
    """Turn a scsynth run into a NAMED cause. Never just "it didn't work".

    The audio session spent hours on `rc=-6, Setting sample rate failed` —
    a 24 kHz default input against a 48 kHz output — because the probe
    answered with a bool and somebody guessed "microphone permission". A
    boolean that collapses four causes into one costs far more time than it
    saves, so every distinguishable failure gets its own sentence here.
    """
    cause = cause_of(ready, devices, rc, text)
    if cause == READY:
        return "ready"
    if cause == SAMPLE_RATE:
        return (f"scsynth exited rc={rc}: SETTING SAMPLE RATE FAILED — the "
                "input and output devices disagree (a 24 kHz mic against "
                "a 48 kHz output does it). This is NOT a permissions "
                "problem. Boot with input off (`-i 0` / `--in-device`), or "
                "match the rates in Audio MIDI Setup.")
    if cause == DEVICE_BUSY:
        return (f"scsynth exited rc={rc}: could not open the audio device "
                "— it may be held exclusively, or gone (bluetooth).")
    if cause == EXITED:
        if rc == -6:
            return (f"scsynth aborted (rc={rc}) during startup — read the tail; "
                    "an abort here is a device negotiation failure, not a crash.")
        return f"scsynth exited rc={rc} before becoming ready"
    if cause == STALL:
        # Deliberately does NOT end with "audio cannot start in this session".
        # That was the earlier conclusion and it is false: the stall is an
        # INPUT-stream authorization that an output-only device sidesteps
        # entirely. `resolve()` does exactly that, automatically.
        return ("scsynth enumerated CoreAudio devices and never started one — "
                "the device-start stall. It did not exit and did not become "
                "ready. That is an INPUT stream waiting on a microphone "
                "authorization this session cannot be prompted for, so it is "
                "not a device disagreement and not fatal: an output-only "
                "device still starts, which is what the engine falls back to.")
    return "scsynth produced no device list at all — check the SuperCollider install"


#: What `boot_note` says, per cause — one clause, because it lands in the GUI
#: next to a missing input meter and has to read as an explanation, not a log.
_SHORT = {
    STALL: "this session cannot start an input device (no microphone grant, "
           "and no way to request one)",
    SAMPLE_RATE: "the input and output devices disagree on sample rate — not "
                 "a permissions problem (see Audio MIDI Setup)",
    DEVICE_BUSY: "the input device would not open (held exclusively, or gone)",
    EXITED: "scsynth would not start with input enabled",
    NO_DEVICES: "no CoreAudio devices were listed",
}


# -- device classification ----------------------------------------------------


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


# -- probing ------------------------------------------------------------------


def probe(
    device: str | None = None,
    input_channels: int = 2,
    timeout: float = PROBE_TIMEOUT,
) -> dict:
    """Can a bare scsynth START this configuration, and if not WHY not?

    No supriya and no Patchwerk in the loop, so a failure here is never a
    Patchwerk regression. The process is killed either way — this only ever
    answers the question, it never leaves a server behind.

    Returns {"ready", "devices", "rc", "cause", "why"}.
    """
    sc = find_scsynth()
    if sc is None:
        return {"ready": False, "devices": False, "rc": None,
                "cause": NO_DEVICES,
                "why": "scsynth not found — is SuperCollider installed?"}
    argv = [sc, "-u", str(_free_udp_port()), "-i", str(input_channels), "-o", "2"]
    if device:
        argv += ["-H", device]
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as exc:
        return {"ready": False, "devices": False, "rc": None,
                "cause": NO_DEVICES, "why": f"could not run scsynth: {exc}"}
    ready = devices = False
    rc = None
    buf = ""
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = proc.stdout.read()
            except BlockingIOError:
                chunk = None
            if chunk:
                buf += chunk.decode("utf-8", "replace")
                devices = devices or SCSYNTH_DEVICES in buf
                if SCSYNTH_READY in buf:
                    ready = True
                    break
            elif (rc := proc.poll()) is not None:
                try:                       # drain whatever it said on the way out
                    rest = proc.stdout.read()
                    if rest:
                        buf += rest.decode("utf-8", "replace")
                except (BlockingIOError, ValueError):
                    pass
                devices = devices or SCSYNTH_DEVICES in buf
                break                      # it EXITED — a different failure
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
    return {"ready": ready, "devices": devices, "rc": rc,
            "cause": cause_of(ready, devices, rc, buf),
            "why": classify_scsynth(ready, devices, rc, buf)}


def _free_udp_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- cached verdict -----------------------------------------------------------


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


def input_probe(force: bool = False) -> dict:
    """Can an input-bearing device start in THIS context, and why not?

    Cached per context: probing costs a second when it works and
    `PROBE_TIMEOUT` when it stalls, so the verdict is remembered rather than
    re-derived on every engine boot (device switches reboot the engine). The
    CAUSE is cached alongside it — a cached "no" that has forgotten why is
    the boolean problem again, one layer down.
    """
    key = _context_key()
    if not force:
        hit = _MEM.get(key) or _load_cache().get(key)
        if hit and time.time() - hit.get("t", 0) < CACHE_TTL:
            return hit
    res = probe(device=None, input_channels=2)
    entry = {"ok": res["ready"], "cause": res["cause"], "why": res["why"],
             "t": time.time()}
    _MEM[key] = entry
    cache = _load_cache()
    cache[key] = entry
    _save_cache(cache)
    return entry


def input_can_start(force: bool = False) -> bool:
    """The bare verdict, for callers that genuinely only need the boolean."""
    return bool(input_probe(force)["ok"])


def clear_cache() -> None:
    _MEM.clear()
    try:
        CACHE_PATH.unlink()
    except OSError:
        pass


# -- the decision -------------------------------------------------------------


class NoStartableDevice(RuntimeError):
    """No device on this machine can start here, and we know why."""


def resolve(
    input_device: str | None,
    output_device: str | None,
    input_channels: int,
) -> tuple[str | None, str | None, int, str | None]:
    """Return (input_device, output_device, input_channels, note).

    `note` is None when nothing was overridden, and a human sentence naming
    the CAUSE when we had to drop audio input to get a server at all — the
    GUI surfaces it as `boot_note`, so a missing input meter is explained
    rather than mysterious, and explained CORRECTLY: a sample-rate mismatch
    must not read as a permissions problem.
    """
    wants_input = input_channels > 0 or input_device is not None
    verdict = input_probe() if wants_input else None
    if verdict and verdict["ok"]:
        return input_device, output_device, input_channels, None

    # Output-only. `-i 0` is NOT sufficient on its own: scsynth still opens
    # the default INPUT device unless -H pins it to one that has no input.
    candidates = output_only_devices()
    if output_device and output_device in candidates:
        candidates = [output_device] + [c for c in candidates if c != output_device]
    last = None
    for dev in candidates:
        for _ in range(PROBE_ATTEMPTS):
            last = probe(device=dev, input_channels=0)
            if last["ready"]:
                break
        if last and last["ready"]:
            note = None
            if wants_input:
                cause = (verdict or {}).get("cause", STALL)
                note = (f"audio input disabled — "
                        f"{_SHORT.get(cause, _SHORT[EXITED])}, so the engine "
                        f"is pinned to {dev!r}, output only")
            # scsynth takes ONE -H; passing the same name for both renders it.
            return dev, dev, 0, note

    raise NoStartableDevice(
        "no CoreAudio device could be started.\n"
        f"  with input: {(verdict or {}).get('why', 'not tried')}\n"
        f"  output-only: {(last or {}).get('why', 'no output-only device exists')}\n"
        "  Every output device on this machine may also carry an input "
        "stream. Fix: in Audio MIDI Setup create an output-only device (or "
        "make the built-in speakers the default output) and try again."
    )
