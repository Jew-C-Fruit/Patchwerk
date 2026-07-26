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

Boot / teardown. `Rig` ATTACHES to a rig that is already up and never tears
that one down; it only boots — and only then tears down — when nothing
answers. On the default port it boots through `./run.sh`, which already owns
the orphan hardening, the pidfile and a readiness poll (note run.sh eats $1 as
the patch name: it must be `./run.sh pad_space --no-browser`). On any other
port it launches `-m synthbase gui --port N` directly, since run.sh hardcodes
8765. SuperCollider is DISCOVERED, never assumed to be on PATH — supriya's own
finder knows the /Applications location, and `find_scsynth()` prepends its
directory to the child's PATH so subprocess paths work from any shell.

`silent=True` boots `tests/silent_rig.py` instead: the real `GuiServer` over a
real but ENGINE-LESS `SynthApp`. No scsynth, no audio device, 0.8 s to ready
instead of ~12 — and the whole control plane, including the `{"kind":"level"}`
taps, still works. Use it to record control-plane scenarios anywhere, and when
scsynth cannot boot. It cannot do MIDI (no rack, so no router) or audio.

CLI:

    .venv/bin/python tests/rig.py status                      # what is up
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

from transcript import TranscriptWriter  # noqa: E402

DEFAULT_PORT = 8765
PIDFILE = Path("/tmp/patchwerk.pid")
BOOT_TIMEOUT = 40.0          # cold scsynth + device fallback; run.sh allows 20
VIRTUAL_PORT_NAME = "Patchwerk Agent"

#: spawn message -> (state section, remove message). Modules are NOT here:
#: they are added with `spawn_module` and removed with `edit_chain`.
SPAWNABLE = {
    "spawn_button": ("buttons", "remove_button"),
    "spawn_clock": ("clocks", "remove_clock"),
    "spawn_voice": ("voices", "remove_voice"),
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


def kill_scsynth(timeout: float = 4.0) -> int:
    """Clear stale scsynth servers. Returns how many were alive.

    ⚠ **`-x`, never `-f`.** supriya spawns
    `/Applications/SuperCollider.app/Contents/Resources/scsynth`, so a
    `pkill -f scsynth` pattern matches that — and ALSO matches this driver's
    own shell command line, and any editor, log tail or grep that happens to
    contain the word. A `-f` kill here is a driver that kills itself.
    `run.sh` gets this right (`pkill -x scsynth`); so does this.
    """
    pids = scsynth_alive()
    if not pids:
        return 0
    subprocess.run(["pkill", "-x", "scsynth"], capture_output=True)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not scsynth_alive():
            return len(pids)
        time.sleep(0.2)
    subprocess.run(["pkill", "-9", "-x", "scsynth"], capture_output=True)
    time.sleep(0.3)
    return len(pids)


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


def scsynth_check(timeout: float = 15.0, kill_first: bool = True) -> dict:
    """Can scsynth start an audio device on this machine RIGHT NOW?

    Spawns a bare scsynth — no supriya, no Patchwerk — on a fresh UDP port
    and reports BOTH readiness signals separately, because they fail apart:
    a machine where audio cannot start still enumerates devices happily.

    Returns {"ready", "devices", "udp", "stale", "seconds", "tail"}.
    `ready=False, devices=True` is the CoreAudio device-start stall.
    """
    sc = find_scsynth()
    if sc is None:
        return {"ready": False, "devices": False, "udp": False, "stale": 0,
                "seconds": 0.0, "tail": "scsynth not found"}
    stale = kill_scsynth() if kill_first else len(scsynth_alive())
    p = free_udp_port()
    t0 = time.monotonic()
    proc = subprocess.Popen([str(sc), "-u", str(p), "-i", "0", "-o", "2"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    lines: list[str] = []
    ready = devices = False
    try:
        import selectors
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        while time.monotonic() - t0 < timeout and not ready:
            if not sel.select(0.3):
                continue
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            if SCSYNTH_DEVICES in line:
                devices = True
            if SCSYNTH_READY in line:
                ready = True
        sel.close()
    finally:
        udp = udp_held(p) if ready else False
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        kill_scsynth()
    return {"ready": ready, "devices": devices, "udp": udp, "stale": stale,
            "seconds": round(time.monotonic() - t0, 1),
            "tail": "\n".join(lines[-6:])}


def _diagnose(p: int, log: str) -> str:
    """Why the boot failed, in the two signals that actually distinguish it.

    `Server().boot()` blocks with NO timeout on the CoreAudio stall, so
    without this every environment fault presents as an indistinguishable
    hang. The driver times out on its own and then says which signal was
    missing.
    """
    chk = scsynth_check()
    out = [""]
    if chk["stale"]:
        out.append(f"  stale scsynth cleared before this check: {chk['stale']}")
    out.append(f"  bare scsynth: devices={'yes' if chk['devices'] else 'NO'} "
               f"ready={'yes' if chk['ready'] else 'NO'} "
               f"udp={'yes' if chk['udp'] else 'NO'}  ({chk['seconds']}s)")
    if chk["devices"] and not chk["ready"]:
        out.append("  -> scsynth enumerates CoreAudio devices and never starts "
                   "one. This is NOT a stale process and NOT a Patchwerk "
                   "regression: audio cannot start in this session at all. "
                   "Everything above the rack still works — boot the driver "
                   "with silent=True (tests/silent_rig.py).")
    elif not chk["devices"]:
        out.append("  -> bare scsynth produced no device list either; check "
                   "the SuperCollider install.")
    else:
        out.append("  -> bare scsynth IS healthy, so the fault is above it: "
                   f"read {log}.")
    if chk["tail"]:
        out.append("  scsynth said:\n    " + chk["tail"].replace("\n", "\n    "))
    return "\n".join(out)


# -- process ------------------------------------------------------------------

async def is_up(p: int | None = None, timeout: float = 2.0) -> bool:
    import aiohttp
    p = p or rig_port()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{p}/",
                             timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return r.status == 200
    except Exception:  # noqa: BLE001
        return False


async def boot(patch: str = "pad_space", p: int | None = None,
               log: str = "/tmp/synth_gui.log", args: list | None = None,
               silent: bool = False) -> dict:
    """Bring a rig up. Returns {"how", "pid", "log"} — raises on failure.

    Callers should check `is_up()` first: this always starts a NEW rig, and
    on the default port run.sh reaps any existing one first.

    Boot is IDEMPOTENT FROM A DIRTY STATE: any stale scsynth is cleared by
    exact name first (`kill_scsynth`), the wait is bounded by this driver's
    own timeout rather than `Server().boot()`'s absent one, and a failure is
    reported as the two readiness signals in `_diagnose` — so an environment
    fault never presents as an indistinguishable hang.

    `args` are extra `synthbase gui` flags — `["--hw-buffer", "512"]`,
    `["--out-device", "MacBook Pro Speakers"]`, `["--no-midi"]`.
    """
    p = p or rig_port()
    args = list(args or [])
    t0 = time.monotonic()
    if not silent:
        # A server left holding the audio device (or its UDP port) makes the
        # next boot fail in a way that reads as environmental. Clear it, and
        # SAY that we did — a silent kill hides how dirty the machine was.
        stale = kill_scsynth()
        if stale:
            print(f"[rig] cleared {stale} stale scsynth process(es) before boot")
    if silent:
        # tests/silent_rig.py: the real server over an engine-less SynthApp.
        # No scsynth, no audio device — the control plane only.
        fh = open(log, "wb")
        proc = subprocess.Popen(
            [str(REPO / ".venv/bin/python"), "-u", str(REPO / "tests/silent_rig.py"),
             "--port", str(p)],
            cwd=str(REPO), env=child_env(), stdout=fh, stderr=subprocess.STDOUT)
        pid, how = proc.pid, "silent"
    elif p == DEFAULT_PORT and (REPO / "run.sh").exists():
        # run.sh: orphan hardening + pidfile + its own 20 s readiness poll.
        # $1 is always eaten as the patch name — flags come after it.
        r = await asyncio.to_thread(
            subprocess.run, ["./run.sh", patch, "--no-browser", *args],
            cwd=str(REPO), env=child_env(), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"run.sh failed:\n{r.stdout}\n{r.stderr}"
                               + _boot_hint(log) + _diagnose(p, log))
        pid = int(PIDFILE.read_text().strip()) if PIDFILE.exists() else None
        how, proc = "run.sh", None
    else:
        fh = open(log, "wb")
        proc = subprocess.Popen(
            [str(REPO / ".venv/bin/python"), "-u", "-m", "synthbase", "gui",
             patch, "--port", str(p), "--no-browser", *args],
            cwd=str(REPO), env=child_env(), stdout=fh, stderr=subprocess.STDOUT)
        pid, how = proc.pid, "direct"
    while time.monotonic() - t0 < BOOT_TIMEOUT:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"rig exited {proc.returncode} — tail {log}:\n"
                               + _tail(log) + _boot_hint(log)
                               + ("" if silent else _diagnose(p, log)))
        if await is_up(p):
            return {"how": how, "pid": pid, "log": log, "args": args,
                    "seconds": round(time.monotonic() - t0, 1), "proc": proc}
        await asyncio.sleep(0.4)
    # OUR timeout, not Server().boot()'s (it has none on this failure mode).
    if proc is not None:
        _reap(proc.pid)
    if not silent:
        kill_scsynth()          # do not leave the mess for the next boot
    raise RuntimeError(f"no 200 from port {p} in {BOOT_TIMEOUT:.0f}s — "
                       f"tail {log}:\n" + _tail(log) + _boot_hint(log)
                       + ("" if silent else _diagnose(p, log)))


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

        Teardown has to be good enough that the NEXT boot does not inherit
        the mess: the rig process goes first, and if its scsynth outlives it
        (a killed parent does not always take the server with it) that gets
        cleared too, by exact name.
        """
        if not self.booted:
            return
        silent = self.booted.get("how") == "silent"
        proc = self.booted.get("proc")
        if proc is not None:
            _reap(proc.pid)
        elif self.booted.get("pid"):
            _reap(int(self.booted["pid"]))
            if PIDFILE.exists():
                PIDFILE.unlink(missing_ok=True)
        if not silent:
            await asyncio.sleep(0.4)      # give it the chance to go quietly
            if kill_scsynth():
                print("[rig] scsynth outlived its rig — cleared it")
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
    print(f"stale cleared:  {chk['stale']}")
    print(f"device list:    {'yes' if chk['devices'] else 'NO'}")
    print(f"server ready:   {'yes' if chk['ready'] else 'NO'}   "
          f"({chk['seconds']}s)")
    print(f"udp bound:      {'yes' if chk['udp'] else 'NO'}")
    if chk["tail"]:
        print("  " + chk["tail"].replace("\n", "\n  "))
    if chk["ready"]:
        print("\nAudio can start. A rig failure is above scsynth.")
        return 0
    print("\nAudio CANNOT start in this session — scsynth enumerates devices "
          "and never starts one.\nUse silent=True / --silent: the control "
          "plane, the level taps and the transcript all still work.")
    return 1


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
    sub.add_parser("midi")
    p_play = sub.add_parser("play")
    p_play.add_argument("scenario")
    args = ap.parse_args(argv)
    fn = {"status": _cmd_status, "doctor": _cmd_doctor,
          "midi": _cmd_midi, "play": _cmd_play}[args.cmd]
    return asyncio.run(fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
