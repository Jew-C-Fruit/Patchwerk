"""Rig driver: boot Patchwerk, drive it, play real MIDI into it, record it.

    Item 37 Phase 1. Mac-only — this one needs an actual rig.

    from tests.rig import Rig
    async with Rig(patch="pad_space", transcript="/tmp/t.jsonl") as rig:
        await rig.midi_enable()                 # a VIRTUAL port, no hardware
        b = await rig.spawn("spawn_button", "buttons")
        await rig.send({"type": "ctl_wire", "action": "add",
                        "from": b, "to": "transport:tap"})
        rig.note_on(60); await rig.sleep(0.3); rig.note_off(60)
        st = await rig.poke()                   # freshest state, guaranteed

Every existing `tests/probe_*_ws.py` re-implements connect / drain / poke /
spawn-by-diff / restore, and each one re-derives the same landmines. This is
that code, once, with the landmines already paid for:

* **Latest state, structurally.** A background reader task drains the socket
  the whole time, so `rig.state` is ALWAYS the freshest snapshot — there is no
  "read the first state after a poke and be two behind" (landmines #50). It
  also means the transcript is COMPLETE: nothing is dropped on the floor
  between two `recv_type` calls, which is the property Phase 2 replays.
* **The poke idiom.** `fire_button`, `note_on`, `lfo_set`, `set_volume`,
  `sustain` and `all_notes_off` broadcast NOTHING, and `set_param`/`set_tonic`/
  `set_literal` broadcast with `exclude=sender` — so a driver never sees its
  own effect. `poke()` sends a no-op `{"type":"set_transport"}` (no fields:
  changes nothing, broadcasts to ALL) and waits for the state that follows.
* **Spawn ids by DIFF** (`spawn()`), never `state[section][-1]`.
* **`SS_PORT` everywhere** — 10 of the 12 existing probes hardcode 8765.
* **Restore.** `snapshot()` at connect, `restore()` at exit: everything this
  driver spawned is removed, every wire it added is cut, transport / volume /
  MIDI settings go back. `drift()` reports what did not come back.

**Virtual MIDI is the point of the MIDI half.** `list_inputs()` on this Mac
returns `[]` — with no controller plugged in there is no MIDI at all, so an
agent that wants to exercise the REAL input path (rtmidi callback thread,
velocity, sustain CC 64, ±2-semitone bend, CC bindings, button CC capture)
has no way in. `{"type":"note_on"}` over the websocket enters at
`SynthApp.note_on` and skips every one of those layers. `midi_enable()`
creates an rtmidi virtual port, waits for the engine to SEE it, points
`set_midi` at it, and then confirms the router actually opened it.

    ⚠ There is no engine change here, and one rough edge is worked around
    rather than fixed. `set_midi` -> `_restart_midi` -> `MidiRouter.start()`
    calls `list_inputs()`, which caches for 3 s, and `start()` returns early
    on an empty list EVEN WHEN AN EXPLICIT PORT WAS GIVEN (`midi.py:151-153`)
    — so a port created moments ago is invisible and the failure is silent.
    `midi_enable()` therefore waits for the port to appear in
    `state.midi_inputs` BEFORE sending `set_midi`, and then asserts
    `state.midi_port` came back as ours instead of assuming it. The plan's
    §6.3 (force the cache, honour an explicit port name) is still the right
    fix; it is an engine change and it is not this file's to make.

Boot / teardown, and ISOLATION — several agent sessions drive this machine at
once. `Rig` ATTACHES to a rig that is already up and never tears that one
down; it only boots, and only then tears down, when nothing answers. The port
is claimed in `rigreg`: two sessions COEXIST on different ports, and a second
session wanting a port someone else holds is REFUSED by name rather than
killed. Teardown kills our rig process and the scsynth it spawned BY PID.

    ⚠ Nothing here is machine-wide any more, and that is the point. The
    first cut used `pkill -x scsynth` and booted through `./run.sh`, which
    reaps every `-m synthbase` process on the machine — so every boot one
    session made killed every other session's rig, and theirs killed ours
    mid-probe. It presents as `Server offline!` partway through a run and is
    indistinguishable from flaky audio; the tell is SIGABRT inside `exit()`,
    which is a SIGTERM, not a crash. So the driver launches
    `-m synthbase gui --port N` itself and never shells out to run.sh. The
    cost is that run.sh's orphan hardening is not inherited: a port held by
    something we do not own is REFUSED, not cleared.

SuperCollider is DISCOVERED, never assumed to be on PATH — supriya's own
finder knows the /Applications location, and `find_scsynth()` prepends its
directory to the child's PATH so subprocess paths work from any shell.

`silent=True` boots `tests/silent_rig.py` instead: the real `GuiServer` over a
real but ENGINE-LESS `SynthApp`. No scsynth, no audio device, 0.8 s to ready
instead of ~12 — and the whole control plane, including the `{"kind":"level"}`
taps, still works. Use it to record control-plane scenarios anywhere, and when
scsynth cannot boot. It cannot do MIDI (no rack, so no router) or audio.

CLI:

    .venv/bin/python tests/rig.py status                      # what is up
    .venv/bin/python tests/rig.py rigs                        # who owns what
    .venv/bin/python tests/rig.py doctor                      # can audio start?
    .venv/bin/python tests/rig.py tabs --close-stale          # sweep orphan tabs
    .venv/bin/python tests/rig.py midi                        # virtual-MIDI check
    .venv/bin/python tests/rig.py play tests/scenarios/x.json -o /tmp/x.jsonl
    SS_PORT=8799 .venv/bin/python tests/rig.py --silent \\
        -o tests/fixtures/transcript_transport_levels.jsonl \\
        play tests/scenarios/transport_levels.json
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import browser  # noqa: E402
import rigreg  # noqa: E402
from transcript import TranscriptWriter  # noqa: E402

DEFAULT_PORT = 8765
PIDFILE = Path("/tmp/patchwerk.pid")
BOOT_TIMEOUT = 40.0          # a cold scsynth plus a device fallback
#: Our virtual MIDI port carries the PID. Several sessions run this machine
#: at once, and a fixed name collides: `MidiRouter.start()` with no explicit
#: port picks `hardware[0]`, so two identically-named ports make "which one
#: did we open" a coin flip — and a leaked port from a killed session is
#: indistinguishable from a live one. With the pid it is neither.
VIRTUAL_PORT_BASE = "Patchwerk Agent"
VIRTUAL_PORT_NAME = f"{VIRTUAL_PORT_BASE} {os.getpid()}"

#: spawn message -> (state section, remove message). Modules are NOT here:
#: they are added with `spawn_module` and removed with `edit_chain`.
SPAWNABLE = {
    "spawn_button": ("buttons", "remove_button"),
    "spawn_clock": ("clocks", "remove_clock"),
    "spawn_voice": ("voices", "remove_voice"),
    # item 10. All allocation policies — mono, poly, hold — are ONE registry
    # behind one state section and one remove message, so they share
    # "voices"/remove_voice with spawn_voice rather than getting sections of
    # their own. Added by the BATCH MERGE: the rig was written against a base
    # with only mono voices, and test_rig's drift guard is BIDIRECTIONAL — an
    # entry here before server.py dispatches it fails just as loudly as a
    # dispatch with no entry. So each line lands in the merge that brings its
    # own handler, not a package earlier.
    "spawn_poly": ("voices", "remove_voice"),
    "spawn_drone_voice": ("voices", "remove_voice"),   # item 29 (see above)
    "spawn_tonic": ("tonics", "remove_tonic"),
    "spawn_literal": ("literals", "remove_literal"),
    "spawn_keyshift": ("keyshifts", "remove_keyshift"),
    "spawn_logic": ("logics", "remove_logic"),
    "spawn_relay": ("relays", "remove_relay"),
    "spawn_lfo": ("lfos", "remove_lfo"),
    "spawn_threshold": ("thresholds", "remove_threshold"),
}

WIRE_KINDS = ("ctl_wire", "graph_wire", "mod_wire", "lfo_wire", "threshold_wire")


def rig_port() -> int:
    """The rig's port. `SS_PORT` wins; two probes already honour it."""
    return int(os.environ.get("SS_PORT", DEFAULT_PORT))


def find_scsynth() -> Path | None:
    """Locate scsynth WITHOUT requiring it on PATH.

    supriya's own finder searches $SCSYNTH_PATH, PATH, and the Mac
    /Applications locations; this falls back to the same Mac paths so the
    function still answers when supriya is not importable.
    """
    try:
        from supriya.scsynth import find as _find
        return Path(_find())
    except Exception:  # noqa: BLE001
        pass
    if (found := shutil.which("scsynth")):
        return Path(found)
    for p in ("/Applications/SuperCollider.app/Contents/Resources/scsynth",
              "/Applications/SuperCollider/SuperCollider.app/Contents/Resources/scsynth"):
        if Path(p).exists():
            return Path(p)
    return None


def pick_python() -> str | None:
    """The interpreter to launch a rig with. MIRRORS `run.sh`'s pick_python.

    A git WORKTREE has no `.venv/` of its own — it is gitignored, so it
    lives only in the main checkout — and agent sessions work almost
    entirely from worktrees. Hardcoding `REPO/.venv/bin/python` therefore
    failed exactly where the driver is used most.

    The order is run.sh's, deliberately, candidate for candidate: this
    tree's venv, an already-activated venv, the MAIN worktree's venv, then
    whatever `python3` is on PATH. `sys.executable` is NOT used as a
    shortcut, tempting as it is — two resolution strategies in one repo is
    how they drift, and `test_rig.py` asserts this list still matches the
    one in run.sh.

    (run.sh gained `pick_python()` on `feat/p38-audio-session`, which is not
    yet on main. The parity check tolerates its absence and says so.)
    """
    for cand in python_candidates():
        if cand and os.access(cand, os.X_OK) and Path(cand).is_file():
            return cand
    return shutil.which("python3")


def python_candidates() -> list[str]:
    """The candidates, in order — factored out so the parity check can read
    them without running anything."""
    out = [str(REPO / ".venv/bin/python")]
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        out.append(str(Path(venv) / "bin/python"))
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=str(REPO), capture_output=True, text=True)
    root = common.stdout.strip()
    if root.endswith("/.git"):
        out.append(str(Path(root[:-len("/.git")]) / ".venv/bin/python"))
    return out


class NoPython(RuntimeError):
    pass


def child_env() -> dict:
    """Environment for a spawned rig: scsynth's directory on PATH."""
    env = dict(os.environ)
    sc = find_scsynth()
    if sc is not None:
        env["PATH"] = str(sc.parent) + os.pathsep + env.get("PATH", "")
    return env


def ids_in(state: dict, section: str) -> set:
    """Ids in a state section, whatever shape it takes."""
    val = (state or {}).get(section)
    if isinstance(val, dict):
        return set(val)
    out = set()
    for row in val or []:
        out.add(row.get("id") if isinstance(row, dict) else row)
    return {x for x in out if x is not None}


# -- scsynth hygiene ----------------------------------------------------------
#
# Boot must be idempotent from a DIRTY machine. A driver that only works on a
# clean one is not a driver — so every boot clears stale servers first, every
# teardown makes sure the next boot does not inherit ours, and a boot that
# fails says WHICH signal was missing instead of hanging.

#: scsynth's own readiness line. Device enumeration is NOT readiness — that is
#: exactly the trap that made a stalled scsynth read as "boots fine standalone"
#: (it prints its device list, then stalls inside CoreAudio device start and
#: never prints this). Two signals, both required: this line, and a bound UDP
#: socket on its port.
SCSYNTH_READY = "SuperCollider 3 server ready"
SCSYNTH_DEVICES = "Number of Devices"


def scsynth_alive() -> list[int]:
    """PIDs of running scsynth processes, by EXACT name."""
    r = subprocess.run(["pgrep", "-x", "scsynth"], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def kill_scsynth(rig_pid: int) -> list[int]:
    """Kill the scsynth processes THIS rig spawned. Nothing else. Ever.

    ⚠ This was `pkill -x scsynth` and that was a **machine-wide kill on a
    machine running several agent sessions at once**: every boot took down
    every other session's rig, and theirs took down ours mid-probe. It
    presents as `Server offline!` halfway through a run and reads exactly
    like flaky audio — the tell is SIGABRT inside `exit()`, which is a
    process being SIGTERM'd, not one crashing.

    scsynth is a direct CHILD of the rig process (verified with
    `pgrep -P`), so ownership is provable and the sweep is unnecessary.
    `-x` is still right for MATCHING the name — a `-f` pattern would match
    this driver's own command line — it is the missing `-P` that was the bug.
    """
    killed = []
    for pid in rigreg.scsynth_children(rig_pid):
        if rigreg._kill(pid):
            killed.append(pid)
    return killed


def udp_held(p: int) -> bool:
    """Is anything holding a UDP socket on this port? (readiness signal 2)"""
    r = subprocess.run(["lsof", "-nP", f"-iUDP:{p}"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def free_udp_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


#: ONE diagnosis path, not two. `classify_scsynth` lived here first; it now
#: lives in `synthbase/audio_session.py` because the ENGINE needs the cause
#: too — `boot_note` is what tells the user why the input meter is missing,
#: and it was making the exact "no microphone permission" misattribution this
#: classifier exists to prevent. `tests/` may import `synthbase/`; the reverse
#: would be backwards. Re-exported so `rig.classify_scsynth` still resolves.
from synthbase.audio_session import (  # noqa: E402
    PROBE_ATTEMPTS,
    classify_scsynth,
    output_only_devices,
)


def scsynth_check(timeout: float = 15.0, device: str | None = None) -> dict:
    """Can scsynth start an audio device on this machine RIGHT NOW?

    Spawns a bare scsynth — no supriya, no Patchwerk — on a fresh UDP port
    and reports the readiness signals SEPARATELY, because they fail apart:
    a machine where audio cannot start still enumerates devices happily,
    and a machine where the devices disagree on sample rate exits instead
    of stalling. Those are three different answers and get three different
    `why` sentences.

    `device` pins `-H`. Leaving it None reproduces the DEFAULT device choice,
    which is the configuration that stalls in a disclaimed session — and note
    that `-i 0` below does NOT save it, because disabling input buses does not
    stop scsynth opening the default input DEVICE. So a stall here means "the
    default device cannot start", never "this machine has no audio": callers
    must re-ask with an output-only device before concluding anything.

    Returns {"ready", "devices", "udp", "rc", "why", "others", "seconds",
    "tail"}. It kills ONLY the scsynth it spawned.
    """
    sc = find_scsynth()
    if sc is None:
        return {"ready": False, "devices": False, "udp": False, "rc": None,
                "why": "scsynth not found — is SuperCollider installed?",
                "others": 0, "seconds": 0.0, "tail": ""}
    others = len(scsynth_alive())      # reported, never touched
    p = free_udp_port()
    t0 = time.monotonic()
    argv = [str(sc), "-u", str(p), "-i", "0", "-o", "2"]
    if device:
        argv += ["-H", device]
    proc = subprocess.Popen(argv,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    lines: list[str] = []
    ready = devices = False
    rc = None
    try:
        import selectors
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        while time.monotonic() - t0 < timeout and not ready:
            if (rc := proc.poll()) is not None:
                lines.extend(x.rstrip() for x in proc.stdout.readlines())
                break                      # it EXITED — a different failure
            if not sel.select(0.3):
                continue
            line = proc.stdout.readline()
            if not line:
                rc = proc.poll()
                break
            lines.append(line.rstrip())
            if SCSYNTH_DEVICES in line:
                devices = True
            if SCSYNTH_READY in line:
                ready = True
        sel.close()
    finally:
        udp = udp_held(p) if ready else False
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    text = "\n".join(lines)
    return {"ready": ready, "devices": devices, "udp": udp, "rc": rc,
            "others": others, "why": classify_scsynth(ready, devices, rc, text),
            "seconds": round(time.monotonic() - t0, 1),
            "tail": "\n".join(lines[-6:])}


def _diagnose(p: int, log: str) -> str:
    """Why the boot failed, as a NAMED cause plus the signals behind it.

    `Server().boot()` blocks with no timeout on the device-start stall, so
    without this every environment fault presents as an indistinguishable
    hang. The driver times out on its own and then says which signal was
    missing and what that means.
    """
    chk = scsynth_check()
    out = [""]
    if chk["others"]:
        out.append(f"  {chk['others']} other scsynth process(es) running — "
                   "another session's, left alone")
    out.append(f"  bare scsynth: devices={'yes' if chk['devices'] else 'NO'} "
               f"ready={'yes' if chk['ready'] else 'NO'} "
               f"udp={'yes' if chk['udp'] else 'NO'} "
               f"rc={chk['rc']}  ({chk['seconds']}s)")
    out.append(f"  -> {chk['why']}")
    if chk["ready"]:
        out.append(f"     scsynth itself is healthy, so the fault is above it: "
                   f"read {log}.")
    elif chk["devices"] and chk["rc"] is None:
        out.append("     Everything above the rack still works — boot the "
                   "driver with silent=True (tests/silent_rig.py).")
    if chk["tail"]:
        out.append("  scsynth said:\n    " + chk["tail"].replace("\n", "\n    "))
    return "\n".join(out)


# -- process ------------------------------------------------------------------

async def probe_http(p: int | None = None, timeout: float = 2.0) -> dict:
    """Is a rig serving here — and if not, WHY NOT?

    A bare bool collapses four different situations into "it didn't work":
    nothing is listening, something is listening but is not a rig, the rig
    is up but still booting, or the request timed out. Those want four
    different responses from a caller, and the audio session lost hours to
    exactly this shape of answer — a boolean probe reported a sample-rate
    mismatch as "no microphone permission" and sent Cole to System Settings
    to fix something that was not broken.

    Returns {"up", "why", "status"}.
    """
    import aiohttp
    p = p or rig_port()
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"http://127.0.0.1:{p}/",
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    return {"up": True, "why": "serving", "status": 200}
                return {"up": False, "status": r.status,
                        "why": f"something is listening on {p} but answered "
                               f"HTTP {r.status} — not a Patchwerk rig"}
    except aiohttp.ClientConnectorError:
        return {"up": False, "status": None,
                "why": f"nothing is listening on {p}"}
    except asyncio.TimeoutError:
        return {"up": False, "status": None,
                "why": f"port {p} accepted the connection but did not answer "
                       f"in {timeout:.0f}s — a rig mid-boot, or a wedged one"}
    except Exception as exc:  # noqa: BLE001
        return {"up": False, "status": None,
                "why": f"probing {p} failed: {exc.__class__.__name__}: {exc}"}


async def is_up(p: int | None = None, timeout: float = 2.0) -> bool:
    """The bool form, for callers that genuinely only need yes/no."""
    return (await probe_http(p, timeout))["up"]


async def boot(patch: str = "pad_space", p: int | None = None,
               log: str = "/tmp/synth_gui.log", args: list | None = None,
               silent: bool = False) -> dict:
    """Bring a rig up. Returns {"how", "pid", "log", ...} — raises on failure.

    Callers should check `is_up()` first: this always starts a NEW rig, and
    raises `rigreg.RigBusy` if another session already owns the port.

    Boot is IDEMPOTENT FROM A DIRTY STATE and ISOLATED FROM OTHER SESSIONS:
    the port is claimed in `rigreg` (refusing, never killing, if someone else
    holds it), the wait is bounded by this driver's own timeout rather than
    `Server().boot()`'s absent one, and a failure is reported as the two
    readiness signals in `_diagnose` — so an environment fault never presents
    as an indistinguishable hang.

    ⚠ **This deliberately does NOT use `run.sh`.** run.sh reaps every
    `-m synthbase` process and every `scsynth` on the machine. That is right
    for a human relaunching their own rig and catastrophic for a driver on a
    machine running several agent sessions: it kills rigs it did not start.
    The cost is that run.sh's orphan hardening is not inherited — so when a
    port is held by something we do not own, this REFUSES and names it
    instead of clearing it.

    `args` are extra `synthbase gui` flags — `["--hw-buffer", "512"]`,
    `["--out-device", "MacBook Pro Speakers"]`, `["--no-midi"]`.
    """
    p = p or rig_port()
    args = list(args or [])
    t0 = time.monotonic()
    rigreg.clean()                    # drop locks nobody is behind any more
    rigreg.acquire(p, how="silent" if silent else "gui")   # raises RigBusy
    try:
        if not silent and udp_held(p):
            pass                      # informational only; the lock is truth
        py = pick_python()
        if not py:
            raise NoPython(
                "no python found to launch a rig with — no .venv in this "
                "tree, none activated, none in the main worktree, and no "
                "python3 on PATH. Run setup, or activate a venv.")
        cmd = ([py, "-u", str(REPO / "tests/silent_rig.py"), "--port", str(p)]
               if silent else
               [py, "-u", "-m", "synthbase", "gui", patch, "--port", str(p),
                "--no-browser", *args])
        fh = open(log, "wb")
        proc = subprocess.Popen(cmd, cwd=str(REPO), env=child_env(),
                                stdout=fh, stderr=subprocess.STDOUT)
        how = "silent" if silent else "direct"
        rigreg.update(p, rig_pid=proc.pid)
        seen_scsynth: list[int] = []
        while time.monotonic() - t0 < BOOT_TIMEOUT:
            # Record the scsynth child the MOMENT it appears, not on success.
            # A boot that fails leaves the rig process dead and its scsynth
            # ORPHANED (verified: killing the parent does not take the server
            # with it), and by then `pgrep -P <dead pid>` finds nothing — so
            # the only chance to learn the pid is while the parent lives.
            if not seen_scsynth and not silent:
                seen_scsynth = rigreg.scsynth_children(proc.pid)
                if seen_scsynth:
                    rigreg.update(p, scsynth_pid=seen_scsynth[0])
            if proc.poll() is not None:
                raise RuntimeError(f"rig exited {proc.returncode} — tail {log}:\n"
                                   + _tail(log) + _boot_hint(log)
                                   + ("" if silent else _diagnose(p, log)))
            if await is_up(p):
                kids = rigreg.scsynth_children(proc.pid) or seen_scsynth
                if kids:
                    rigreg.update(p, scsynth_pid=kids[0])
                return {"how": how, "pid": proc.pid, "log": log, "args": args,
                        "port": p, "scsynth": kids,
                        "seconds": round(time.monotonic() - t0, 1), "proc": proc}
            await asyncio.sleep(0.4)
        # OUR timeout, not Server().boot()'s (it has none on this failure mode)
        raise RuntimeError(f"no 200 from port {p} in {BOOT_TIMEOUT:.0f}s — "
                           f"tail {log}:\n" + _tail(log) + _boot_hint(log)
                           + ("" if silent else _diagnose(p, log)))
    except BaseException:
        rigreg.stop_owned(p)          # kills OUR rig + OUR scsynth, by pid
        rigreg.release(p)
        raise


def _boot_hint(log: str) -> str:
    """An EMPTY boot log is a symptom, not an absence of one.

    The rig prints nothing until scsynth answers, so a zero-byte log means
    the process is still inside `Server().boot()`, which blocks with no
    timeout of its own. `_diagnose` then says whether scsynth itself can
    start an audio device at all.
    """
    try:
        if Path(log).stat().st_size == 0:
            return ("\n  (the log is EMPTY: the rig never got past "
                    "Server().boot(), which has no timeout of its own.)")
    except OSError:
        pass
    return ""


def _tail(path: str, n: int = 20) -> str:
    try:
        return "\n".join(Path(path).read_text(errors="replace").splitlines()[-n:])
    except Exception:  # noqa: BLE001
        return "(no log)"


def _reap(pid: int, timeout: float = 4.0) -> None:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# -- the driver ---------------------------------------------------------------

class RigError(RuntimeError):
    pass


class Rig:
    """One websocket to one rig, with a reader task behind it."""

    def __init__(self, patch: str = "pad_space", *, p: int | None = None,
                 transcript: str | Path | None = None, boot_if_down: bool = True,
                 keep: bool = False, midi_name: str = VIRTUAL_PORT_NAME,
                 scenario: str | None = None, history: int = 20000,
                 boot_args: list | None = None, silent: bool = False) -> None:
        self.patch = patch
        self.boot_args = list(boot_args or [])
        self.silent = silent          # boot tests/silent_rig.py: no engine
        self.port = p or rig_port()
        self.transcript_path = Path(transcript) if transcript else None
        self.boot_if_down = boot_if_down
        self.keep = keep                  # never tear down, even if we booted
        self.midi_name = midi_name
        self.scenario = scenario

        self.state: dict = {}             # ALWAYS the latest snapshot
        self.records: list[dict] = []     # every message received, in order
        self._history = history
        self.booted: dict | None = None   # set only if WE started the rig
        self.baseline: dict = {}
        self.spawned: list[tuple[str, str]] = []   # (remove_msg, id)
        self.wired: list[dict] = []                # wire messages we added
        self.tw: TranscriptWriter | None = None
        self._ws = None
        self._session = None
        self._reader: asyncio.Task | None = None
        self._bump = None                 # asyncio.Event, replaced per notify
        self._midi_out = None
        self._midi_baseline: tuple | None = None
        self._t0 = time.monotonic()
        self._state_seq = 0               # bumped by the reader per state
        self._tabs: list = []             # browser tabs WE opened
        self.errors: list[str] = []       # {"type":"error"} the rig sent us

    # -- lifecycle ------------------------------------------------------------

    async def __aenter__(self) -> "Rig":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> "Rig":
        import aiohttp
        if not await is_up(self.port):
            if not self.boot_if_down:
                raise RigError(f"no rig on port {self.port} (boot_if_down=False)")
            self.booted = await boot(self.patch, self.port,
                                     args=self.boot_args, silent=self.silent)
        self._bump = asyncio.Event()
        self._t0 = time.monotonic()
        if self.transcript_path:
            self.tw = TranscriptWriter(
                self.transcript_path, port=self.port, patch=self.patch,
                scenario=self.scenario, silent=self.silent,
                booted=bool(self.booted), midi=self.midi_name)
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            f"http://127.0.0.1:{self.port}/ws", heartbeat=30)
        self._reader = asyncio.create_task(self._read_loop())
        await self.wait(lambda r: r["msg"].get("type") == "state", timeout=10,
                        what="the connect state")
        self.snapshot()
        return self

    async def stop(self, restore: bool = True) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        ok = True
        try:
            if restore and self._ws is not None and not self._ws.closed:
                ok = await self.restore()
            if self.tw is not None and self.state:
                self.tw.final(self.state)
        finally:
            try:
                if self._tabs:
                    print(f"[rig] closed {self.close_ui()} tab(s) we opened")
            except browser.BrowserError as exc:
                print(f"[rig] could not close our tabs: {exc}")
            self.midi_disable_port()
            if self._reader is not None:
                self._reader.cancel()
                try:
                    await self._reader
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
            if self._session is not None:
                await self._session.close()
            if self.tw is not None:
                self.tw.close()
            if self.booted and not self.keep:
                await self.shutdown()
        if not ok:
            print("[rig] WARNING — restore left drift:", self.drift())

    async def shutdown(self) -> None:
        """Stop a rig THIS driver booted. Never touches one we attached to.

        Everything goes by PID, through the registry: the rig process, then
        the scsynth it spawned (a killed parent does not always take the
        server with it). There is no machine-wide sweep — that is what took
        down other sessions' rigs.
        """
        if not self.booted:
            return
        port = self.booted.get("port", self.port)
        proc = self.booted.get("proc")
        if proc is not None and proc.poll() is None:
            kids = rigreg.scsynth_children(proc.pid)
            if kids:
                rigreg.update(port, scsynth_pid=kids[0])
        out = rigreg.stop_owned(port)
        if out.get("killed"):
            print(f"[rig] stopped pid(s) {out['killed']} on port {port}")
        rigreg.release(port)
        self.booted = None

    # -- reader ---------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            await self._read_forever()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a dead reader must be LOUD
            self.errors.append(f"reader died: {exc!r}")
            print(f"[rig] reader died: {exc!r}")

    async def _read_forever(self) -> None:
        import aiohttp
        async for raw in self._ws:
            if raw.type is not aiohttp.WSMsgType.TEXT:
                break
            try:
                msg = json.loads(raw.data)
            except ValueError:
                continue
            rec = {"rec": "recv", "t": self._now(), "msg": msg}
            self.records.append(rec)
            if len(self.records) > self._history:
                del self.records[:len(self.records) - self._history]
            if msg.get("type") == "state":
                self.state = msg
                self._state_seq += 1
            elif msg.get("type") == "error":
                self.errors.append(msg.get("message", ""))
            if self.tw is not None:
                self.tw.recv(msg)
            ev, self._bump = self._bump, asyncio.Event()
            ev.set()

    def _now(self) -> float:
        return round(time.monotonic() - self._t0, 4)

    # -- send / wait ----------------------------------------------------------

    async def send(self, msg: dict) -> None:
        if self.tw is not None:
            self.tw.send(msg)
        await self._ws.send_json(msg)

    async def wait(self, pred, timeout: float = 6.0, since: int | None = None,
                   what: str = "a matching message") -> dict:
        """Wait for a received record matching `pred(record)`.

        `since` is an index into `self.records` — pass `rig.mark_index()`
        taken BEFORE the send, and messages that arrived while you were
        awaiting something else still count. That is the whole reason the
        reader runs as a task.
        """
        i = 0 if since is None else since
        end = time.monotonic() + timeout
        while True:
            ev = self._bump                      # capture BEFORE scanning
            while i < len(self.records):
                if pred(self.records[i]):
                    return self.records[i]["msg"]
                i += 1
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"timed out after {timeout}s waiting for {what}")
            try:
                await asyncio.wait_for(ev.wait(), left)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"timed out after {timeout}s waiting for {what}") from None

    def mark_index(self) -> int:
        return len(self.records)

    async def wait_type(self, want: str, timeout: float = 6.0,
                        since: int | None = None, match=None) -> dict:
        return await self.wait(
            lambda r: r["msg"].get("type") == want and (match is None or match(r["msg"])),
            timeout, since, what=f"a {want!r} message")

    async def poke(self, timeout: float = 6.0) -> dict:
        """Force a fresh state broadcast and return the LATEST state.

        A no-op `set_transport` carries no fields, changes nothing on the
        rig, and broadcasts to ALL clients including the sender — which the
        silent hot paths (`fire_button`, `note_on`, `set_param`, ...) do not.

        It returns `self.state`, not the message it matched, and it settles
        first — landmine #50 with a twist that cost an hour to find. Waiting
        for "the first state after my send" is NOT the same as "the freshest
        state": earlier sends leave broadcasts queued, so poke N happily
        returns the broadcast that poke N-1 caused, and the driver runs one
        state behind FOREVER. A wire added a moment ago then reads as absent.
        """
        since = self.mark_index()
        await self.send({"type": "set_transport"})
        await self.wait_type("state", timeout, since)
        await self.settle()
        return self.state

    async def settle(self, quiet: float = 0.12, cap: float = 1.0) -> None:
        """Wait until no NEW state broadcast has landed for `quiet` seconds.

        Quiet on the socket itself is the wrong test: `meters` streams at
        20 Hz on a live rig, so the socket is never silent and this would
        always burn `cap`.
        """
        end = time.monotonic() + cap
        while True:
            n = self._state_seq
            await asyncio.sleep(quiet)
            if self._state_seq == n or time.monotonic() >= end:
                return

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def until(self, pred, timeout: float = 6.0, poll: float = 0.35,
                    what: str = "a state condition") -> dict:
        """Poke until `pred(state)` holds. For effects with no broadcast."""
        end = time.monotonic() + timeout
        while True:
            st = await self.poke()
            if pred(st):
                return st
            if time.monotonic() >= end:
                raise TimeoutError(f"timed out after {timeout}s waiting for {what}")
            await asyncio.sleep(poll)

    def mark(self, label: str) -> None:
        """Label a point in the transcript. Phase 2 segments on these."""
        if self.tw is not None:
            self.tw.mark(label)
        self.records.append({"rec": "mark", "t": self._now(), "label": label})

    # -- graph ----------------------------------------------------------------

    async def spawn(self, spawn_msg: str, section: str | None = None,
                    timeout: float = 6.0, **fields) -> str:
        """Spawn a node and return its id, picked by DIFF.

        `state[section][-1]` can hand back a pre-existing node from a stale
        snapshot; the difference against a freshly poked baseline cannot.
        """
        if section is None:
            if spawn_msg not in SPAWNABLE:
                raise RigError(f"no section known for {spawn_msg!r} — pass one")
            section = SPAWNABLE[spawn_msg][0]
        before = ids_in(await self.poke(), section)
        await self.send({"type": spawn_msg, **fields})
        end = time.monotonic() + timeout
        while True:
            st = await self.poke()
            new = ids_in(st, section) - before
            if new:
                nid = sorted(new)[0]
                if spawn_msg in SPAWNABLE:
                    self.spawned.append((SPAWNABLE[spawn_msg][1], nid))
                return nid
            if time.monotonic() >= end:
                raise TimeoutError(f"{spawn_msg} produced no new id in {section}")

    async def wire(self, src: str, dst: str, kind: str = "ctl_wire",
                   **fields) -> dict:
        msg = {"type": kind, "action": "add", "from": src, "to": dst, **fields}
        await self.send(msg)
        st = await self.poke()
        self.wired.append(msg)
        return st

    async def unwire(self, src: str, dst: str, kind: str = "ctl_wire",
                     **fields) -> dict:
        await self.send({"type": kind, "action": "remove", "from": src,
                         "to": dst, **fields})
        self.wired = [w for w in self.wired
                      if not (w["type"] == kind and w["from"] == src
                              and w["to"] == dst)]
        return await self.poke()

    # -- baseline / restore ---------------------------------------------------

    def snapshot(self) -> dict:
        st = self.state
        self.baseline = {
            "transport": dict(st.get("transport") or {}),
            "volume": st.get("volume"),
            "midi_port": st.get("midi_port"),
            "midi_enabled": st.get("midi_enabled"),
            "ctl_wires": copy.deepcopy(st.get("ctl_wires") or []),
            "wires": copy.deepcopy(st.get("wires") or []),
            "sections": {sec: ids_in(st, sec)
                         for sec, _ in SPAWNABLE.values()},
            "transport_cards": list(st.get("transport_cards") or []),
        }
        return self.baseline

    def drift(self) -> dict:
        """What is on the rig now that was not at connect. Reports, never acts."""
        st, base = self.state, self.baseline
        out = {}
        for sec in base.get("sections", {}):
            extra = ids_in(st, sec) - base["sections"][sec]
            if extra:
                out[sec] = sorted(extra)
        now_w = [dict(w) for w in st.get("ctl_wires") or []]
        was_w = [dict(w) for w in base.get("ctl_wires") or []]
        if now_w != was_w:
            out["ctl_wires"] = {"now": now_w, "was": was_w}
        tr, tr0 = st.get("transport") or {}, base.get("transport") or {}
        moved = {k: (tr0.get(k), tr.get(k)) for k in tr0
                 if k in tr and tr[k] != tr0[k]}
        if moved:
            out["transport"] = moved
        return out

    async def restore(self, timeout: float = 12.0) -> bool:
        """Undo everything this driver did. True if the rig came back clean."""
        if not (self.wired or self.spawned or self._midi_baseline or self.drift()):
            return True                   # touched nothing — send nothing
        for msg in reversed(self.wired):
            await self.send({**msg, "action": "remove"})
        self.wired.clear()
        for remove_msg, nid in reversed(self.spawned):
            await self.send({"type": remove_msg, "id": nid})
        self.spawned.clear()
        base = self.baseline
        if base.get("volume") is not None:
            await self.send({"type": "set_volume", "volume": base["volume"]})
        tr = base.get("transport") or {}
        if tr:
            await self.send({
                "type": "set_transport", "bpm": tr.get("bpm"),
                "beats_per_bar": tr.get("beats_per_bar"),
                "click": tr.get("click"), "accent": tr.get("accent"),
                "playing": tr.get("running"), "downbeat": tr.get("downbeat")})
        if self._midi_baseline is not None:
            await self.send({"type": "set_midi", "port": self._midi_baseline[0],
                             "enabled": bool(self._midi_baseline[1])})
            self._midi_baseline = None
        end = time.monotonic() + timeout
        while True:                       # poll PAST stale broadcasts
            d = self.drift()
            if not d:
                return True
            if time.monotonic() >= end:
                return False
            await self.poke()

    # -- the browser: reuse a tab, and close what we opened -------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def open_ui(self, tidy: bool = True) -> bool:
        """Show this rig in Chrome. REUSES the rig's tab if one is open.

        The rig itself never opens one — the driver always launches with
        `--no-browser`, because `synthbase gui` otherwise opens the default
        browser on every boot and that is where most of the stacked-up tabs
        came from. Returns True if an existing tab was reused.
        """
        tab, reused = browser.open_or_reuse(self.url, tidy=tidy)
        if not reused:
            self._tabs.append(tab)
        return reused

    def close_ui(self) -> int:
        """Close only the tabs THIS driver opened."""
        n = browser.close_tabs(self._tabs) if self._tabs else 0
        self._tabs.clear()
        return n

    # -- MIDI: a real virtual port, not the websocket note path ---------------

    def midi_open_port(self) -> str:
        """Create the rtmidi virtual port. Returns the name it took."""
        import mido
        if self._midi_out is not None:
            return self.midi_name
        self._midi_out = mido.open_output(self.midi_name, virtual=True)
        return self.midi_name

    def midi_disable_port(self) -> None:
        if self._midi_out is not None:
            try:
                self._midi_out.close()
            except Exception:  # noqa: BLE001
                pass
            self._midi_out = None

    async def midi_enable(self, timeout: float = 20.0) -> str:
        """Virtual port up, engine pointed at it, router CONFIRMED open.

        The confirmation is not ceremony: `MidiRouter.start()` returns early
        when discovery is empty even with an explicit port name, so a
        `set_midi` that silently did nothing looks exactly like success.
        """
        self._midi_baseline = (self.state.get("midi_port"),
                               self.state.get("midi_enabled"))
        name = self.midi_open_port()
        st = await self.until(
            lambda s: any(name in n for n in s.get("midi_inputs") or []),
            timeout=timeout, poll=0.6,
            what=f"{name!r} to appear in state.midi_inputs "
                 "(list_inputs() caches for 3 s)")
        full = next(n for n in st["midi_inputs"] if name in n)
        await self.send({"type": "set_midi", "port": full, "enabled": True})
        await self.until(lambda s: s.get("midi_port") == full, timeout=10.0,
                         what=f"the router to open {full!r}")
        return full

    def _midi_send(self, **msg) -> None:
        import mido
        if self._midi_out is None:
            raise RigError("no virtual MIDI port — call midi_enable() first")
        m = mido.Message(**msg)
        self._midi_out.send(m)
        if self.tw is not None:
            self.tw.midi(msg)
        self.records.append({"rec": "midi", "t": self._now(), "msg": dict(msg)})

    def note_on(self, note: int, velocity: int = 100, channel: int = 0) -> None:
        self._midi_send(type="note_on", note=int(note), velocity=int(velocity),
                        channel=channel)

    def note_off(self, note: int, velocity: int = 0, channel: int = 0) -> None:
        self._midi_send(type="note_off", note=int(note), velocity=int(velocity),
                        channel=channel)

    def cc(self, control: int, value: int, channel: int = 0) -> None:
        self._midi_send(type="control_change", control=int(control),
                        value=int(value), channel=channel)

    def sustain(self, on: bool) -> None:
        self.cc(64, 127 if on else 0)

    def bend(self, semitones: float) -> None:
        """±2 semitones full scale — the engine's `BEND_RANGE_SEMITONES`."""
        pitch = int(round(max(-1.0, min(1.0, semitones / 2.0)) * 8191))
        self._midi_send(type="pitchwheel", pitch=pitch)

    async def play(self, note: int, seconds: float = 0.25,
                   velocity: int = 100) -> None:
        self.note_on(note, velocity)
        await asyncio.sleep(seconds)
        self.note_off(note)

    # -- what came back -------------------------------------------------------

    def events(self, *kinds: str, since: int = 0) -> list[dict]:
        """Inner `{"type":"midi","event":{...}}` events — level/gate/tap/cc/..."""
        out = []
        for r in self.records[since:]:
            if r.get("rec") != "recv" or r["msg"].get("type") != "midi":
                continue
            e = r["msg"].get("event", {})
            if not kinds or e.get("kind") in kinds:
                out.append(e)
        return out

    # -- scenarios ------------------------------------------------------------

    async def run_scenario(self, scn: dict) -> dict:
        """Run a scenario — DATA, not a bespoke script. See SCENARIO_HELP."""
        vars_: dict = {}
        for i, step in enumerate(scn.get("steps") or []):
            do = step.get("do")
            s = _subst(step, vars_)
            if do == "send":
                await self.send(s["msg"])
                if s.get("poke", True):
                    await self.poke()
            elif do == "spawn":
                nid = await self.spawn(s["type"], s.get("section"))
                if s.get("as"):
                    vars_[s["as"]] = nid
            elif do == "wire":
                await self.wire(s["from"], s["to"], s.get("kind", "ctl_wire"))
            elif do == "unwire":
                await self.unwire(s["from"], s["to"], s.get("kind", "ctl_wire"))
            elif do == "midi":
                kind = s.get("kind")
                if kind == "note_on":
                    self.note_on(s["note"], s.get("velocity", 100))
                elif kind == "note_off":
                    self.note_off(s["note"])
                elif kind == "cc":
                    self.cc(s["cc"], s["value"])
                elif kind == "bend":
                    self.bend(s["semitones"])
                elif kind == "sustain":
                    self.sustain(bool(s["on"]))
                else:
                    raise RigError(f"step {i}: unknown midi kind {kind!r}")
            elif do == "midi_enable":
                await self.midi_enable()
            elif do == "wait":
                await asyncio.sleep(float(s.get("s", 0.1)))
            elif do == "poke":
                await self.poke()
            elif do == "mark":
                self.mark(s["label"])
            elif do == "restore":
                await self.restore()
            else:
                raise RigError(f"step {i}: unknown step {do!r}")
        return {"vars": vars_, "errors": list(self.errors)}


SCENARIO_HELP = """A scenario is JSON:

  {"name": "transport-tap", "patch": "pad_space",
   "steps": [
     {"do": "spawn", "type": "spawn_button", "as": "b"},
     {"do": "send",  "msg": {"type": "set_button", "id": "$b", "latch": false}},
     {"do": "wire",  "from": "$b", "to": "transport:tap"},
     {"do": "mark",  "label": "taps"},
     {"do": "send",  "msg": {"type": "fire_button", "id": "$b"}, "poke": false},
     {"do": "wait",  "s": 0.5},
     {"do": "midi_enable"},
     {"do": "midi",  "kind": "note_on", "note": 60, "velocity": 90},
     {"do": "restore"}
   ]}

"$name" anywhere in a step is replaced by a variable a previous `spawn`
bound with "as". Steps: send | spawn | wire | unwire | midi | midi_enable |
wait | poke | mark | restore."""


def _subst(obj, vars_: dict):
    if isinstance(obj, str):
        return vars_.get(obj[1:], obj) if obj.startswith("$") else obj
    if isinstance(obj, list):
        return [_subst(x, vars_) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, vars_) for k, v in obj.items()}
    return obj


def validate_scenario(scn: dict) -> list[str]:
    """Cheap structural check — CI can run this with no rig."""
    problems = []
    if not isinstance(scn, dict):
        return ["scenario is not an object"]
    if not scn.get("name"):
        problems.append("no name")
    steps = scn.get("steps")
    if not isinstance(steps, list) or not steps:
        return problems + ["no steps"]
    known = {"send", "spawn", "wire", "unwire", "midi", "midi_enable",
             "wait", "poke", "mark", "restore"}
    bound = set()
    for i, s in enumerate(steps):
        do = s.get("do")
        if do not in known:
            problems.append(f"step {i}: unknown step {do!r}")
            continue
        if do == "send" and not isinstance(s.get("msg"), dict):
            problems.append(f"step {i}: send needs a msg object")
        if do == "spawn":
            if not s.get("type"):
                problems.append(f"step {i}: spawn needs a type")
            elif s["type"] not in SPAWNABLE and not s.get("section"):
                problems.append(f"step {i}: {s['type']} needs an explicit section")
            if s.get("as"):
                bound.add(s["as"])
        if do in ("wire", "unwire"):
            for end in ("from", "to"):
                if not s.get(end):
                    problems.append(f"step {i}: {do} needs {end!r}")
            if s.get("kind", "ctl_wire") not in WIRE_KINDS:
                problems.append(f"step {i}: unknown wire kind {s.get('kind')!r}")
        if do == "midi" and s.get("kind") not in (
                "note_on", "note_off", "cc", "bend", "sustain"):
            problems.append(f"step {i}: unknown midi kind {s.get('kind')!r}")
        if do == "mark" and not s.get("label"):
            problems.append(f"step {i}: mark needs a label")
        for ref in _refs(s):
            if ref not in bound:
                problems.append(f"step {i}: ${ref} is not bound by an earlier spawn")
    return problems


def _refs(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj[1:]] if obj.startswith("$") else []
    if isinstance(obj, list):
        return [r for x in obj for r in _refs(x)]
    if isinstance(obj, dict):
        return [r for v in obj.values() for r in _refs(v)]
    return []


# -- CLI ----------------------------------------------------------------------

async def _cmd_status(args) -> int:
    p = args.port or rig_port()
    up = await is_up(p)
    sc = find_scsynth()
    print(f"port {p}: {'UP' if up else 'down'}")
    print(f"scsynth: {sc or 'NOT FOUND'}")
    if not up:
        return 1
    async with Rig(p=p, boot_if_down=False) as rig:
        st = rig.state
        print(f"patch: {st.get('patch')}   modules: "
              f"{len(st.get('modules') or [])}   ctl_wires: "
              f"{len(st.get('ctl_wires') or [])}")
        print(f"midi_inputs: {st.get('midi_inputs')}  port={st.get('midi_port')!r} "
              f"enabled={st.get('midi_enabled')}")
    return 0


async def _cmd_rigs(args) -> int:
    """Who owns what on this machine. The shared-machine situation report."""
    if args.clean:
        for rec in rigreg.clean():
            print(f"dropped stale lock: {rigreg.describe(rec)}")
    rigs = rigreg.live()
    if not rigs:
        print("no rigs registered")
    for rec in rigs:
        mine = " (OURS)" if rec.get("owner_pid") == os.getpid() else ""
        print(f"  {rigreg.describe(rec)}{mine}")
    others = scsynth_alive()
    if others:
        print(f"\nscsynth running: {others}  — NOT killed by this driver; "
              "it only ever stops the ones its own rigs spawned")
    return 0


async def _cmd_doctor(args) -> int:
    """Can this machine start audio at all? Answer before blaming the code."""
    p = args.port or rig_port()
    print(f"scsynth:        {find_scsynth() or 'NOT FOUND'}")
    alive = scsynth_alive()
    print(f"scsynth alive:  {alive or 'none'}")
    print(f"rig on {p}:     {'UP' if await is_up(p) else 'down'}")
    if await is_up(p):
        print("  (a rig is running — not probing scsynth under it)")
        return 0
    chk = await asyncio.to_thread(scsynth_check)
    print(f"other scsynth:  {chk['others']}  (left alone — another "
          "session's)")
    print(f"exit code:      {chk['rc']}")
    print(f"device list:    {'yes' if chk['devices'] else 'NO'}")
    print(f"server ready:   {'yes' if chk['ready'] else 'NO'}   "
          f"({chk['seconds']}s)")
    print(f"udp bound:      {'yes' if chk['udp'] else 'NO'}")
    if chk["tail"]:
        print("  " + chk["tail"].replace("\n", "\n  "))
    print(f"\n{chk['why']}")
    if chk["ready"]:
        return 0
    # Do not condemn the session on the DEFAULT device alone. The stall is an
    # input-stream authorization, and an output-only device sidesteps it —
    # which is the configuration the engine actually boots with here, so a
    # doctor that reports "no audio" would be contradicting a working rig.
    for dev in output_only_devices():
        for _ in range(PROBE_ATTEMPTS):
            # Retried: killing the stalled probe above leaves coreaudiod
            # briefly unable to start ANY device, and a single attempt here
            # reported "no audio at all" on a machine where audio was fine.
            alt = await asyncio.to_thread(scsynth_check, 8.0, dev)
            if alt["ready"]:
                break
        if alt["ready"]:
            print(f"\nAudio CAN start — on {dev!r} ({alt['seconds']}s), just "
                  "not on the default device.\nsynthbase.audio_session pins "
                  "the engine to an output-only device automatically, so a "
                  "rig\nboots here with real audio. Audio INPUT is "
                  "unavailable: the input meter and\nmodules/audio_in.py are "
                  "off, and state.boot_note says which cause it was.")
            return 0
    print("\nNo output-only device could start either, so this really is a "
          "mute session.\nUse silent=True / --silent meanwhile: the control "
          "plane, the level taps and the\ntranscript all still work without "
          "audio.")
    return 1


async def _cmd_tabs(args) -> int:
    """What Patchwerk tabs are open, and optionally sweep the dead ones."""
    if not browser.running():
        inst = browser.installed()
        print("no browser is running — no tabs to manage."
              + (f"  (installed: {', '.join(inst)})" if inst else
                 "  (no scriptable browser installed)"))
        return 0
    try:
        tabs = browser.patchwerk_tabs()
        stale = {t.url for t in browser.stale_tabs()}
    except browser.BrowserError as exc:
        print(exc)
        return 1
    if not tabs:
        print("no Patchwerk tabs open")
        return 0
    for t in tabs:
        print(("  STALE  " if t.url in stale else "  live   ") + str(t))
    if not args.close_stale:
        if stale:
            print(f"\n{len(stale)} stale — close them with: "
                  "rig.py tabs --close-stale")
        return 0
    closed = browser.close_stale()
    print(f"\nclosed {len(closed)} stale tab(s)")
    return 0


async def _cmd_ui(args) -> int:
    """Open the rig in Chrome — reusing its tab if one is already open."""
    p = args.port or rig_port()
    url = f"http://127.0.0.1:{p}/"
    if not await is_up(p):
        print(f"nothing is serving {url} — start a rig first "
              "(or `rig.py doctor` if it will not boot)")
        return 1
    try:
        tab, reused = browser.open_or_reuse(url)
    except browser.BrowserError as exc:
        print(exc)
        return 1
    print(f"{'reused' if reused else 'opened'} {tab}")
    print("Drive it through a DOM-aware browser tool — the claude-in-chrome "
          "MCP where Chrome exists, otherwise the in-app Claude Browser "
          "(preview_start + read_page). Never pixel-level control.\n"
          "Screenshots and headless assertions want Playwright's own "
          "Chromium instead, which cannot leave a tab behind — "
          "see tests/browser.py for the split.")
    return 0


async def _cmd_midi(args) -> int:
    """Prove the virtual port round-trips through the REAL router."""
    p = args.port or rig_port()
    async with Rig(patch=args.patch, p=p, transcript=args.out,
                   scenario="midi-check") as rig:
        full = await rig.midi_enable()
        print(f"router opened {full!r}")
        since = rig.mark_index()
        rig.mark("midi")
        rig.cc(74, 100)
        rig.bend(1.0)
        rig.sustain(True)
        rig.sustain(False)
        rig.bend(0.0)
        await rig.sleep(0.8)
        got = rig.events(since=since)
        kinds = [e.get("kind") for e in got]
        print(f"events back: {kinds}")
        ok = all(k in kinds for k in ("cc", "bend", "sustain"))
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1


async def _cmd_play(args) -> int:
    scn = json.loads(Path(args.scenario).read_text())
    problems = validate_scenario(scn)
    if problems:
        print("\n".join("scenario: " + p for p in problems))
        return 1
    async with Rig(patch=scn.get("patch", args.patch), p=args.port or rig_port(),
                   transcript=args.out, scenario=scn.get("name"),
                   silent=args.silent) as rig:
        out = await rig.run_scenario(scn)
    print(f"vars: {out['vars']}   rig errors: {out['errors'] or 'none'}")
    if args.out:
        print(f"transcript: {args.out}")
    return 1 if out["errors"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog=SCENARIO_HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--patch", default="pad_space")
    ap.add_argument("-o", "--out", default=None, help="transcript .jsonl")
    ap.add_argument("--silent", action="store_true",
                    help="boot tests/silent_rig.py (no engine, no audio, no MIDI)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("doctor")
    p_rigs = sub.add_parser("rigs")
    p_rigs.add_argument("--clean", action="store_true",
                        help="drop locks nobody is behind any more")
    p_tabs = sub.add_parser("tabs")
    p_tabs.add_argument("--close-stale", action="store_true",
                        help="close Patchwerk tabs whose rig is gone")
    sub.add_parser("ui")
    sub.add_parser("midi")
    p_play = sub.add_parser("play")
    p_play.add_argument("scenario")
    args = ap.parse_args(argv)
    fn = {"status": _cmd_status, "doctor": _cmd_doctor, "tabs": _cmd_tabs,
          "ui": _cmd_ui, "rigs": _cmd_rigs, "midi": _cmd_midi,
          "play": _cmd_play}[args.cmd]
    try:
        return asyncio.run(fn(args))
    except rigreg.RigBusy as exc:
        print(f"\nREFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
