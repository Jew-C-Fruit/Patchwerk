"""Who owns which rig: a port-scoped registry for a SHARED machine.

    Item 37 Phase 1, follow-up. Pure stdlib.

Several agent sessions drive this Mac at once. The first cut of the driver
did what `run.sh` does — `pkill -x scsynth` and reap every `-m synthbase`
process — which is correct on a machine with one user and catastrophic here:
**every boot killed every other session's rig, and theirs killed ours
mid-probe.** The symptom is `Server offline!` partway through a run, and it
reads exactly like flaky audio. It is not. The tell is the crash signature:
SIGABRT inside `exit()` is a process being SIGTERM'd, not one falling over.

The tab pile-up Cole reported is the same illness — a shared machine
resource with no ownership model — so both are fixed the same way: **own
what you started, never touch what you did not.**

The model, in one line each:

* **A rig is owned by its PORT.** One lock file per port, `<port>.json`,
  created O_EXCL. Holding it means you started that rig and may stop it.
* **Ownership is PROVEN, not asserted.** The lock records the owner process,
  the rig process and — the part that makes killing safe — the scsynth PID,
  which is a direct CHILD of the rig process (verified: `pgrep -P <rig>`).
  Teardown kills those pids. There is no machine-wide kill anywhere.
* **Two sessions COEXIST on different ports.** The registry does not
  serialise anything; it only stops one session from stopping another.
* **Same port: REFUSE, and name the holder.** Never kill. The message says
  which pid and which session holds it, and what to run instead.
* **A lock is never a lock nobody can clear.** Liveness is DERIVED — from
  the owner pid and from whether the port actually answers — so a session
  that dies badly leaves a lock that the next one recognises as stale and
  takes over. The file's existence proves nothing on its own.

Note the audio device is NOT the scarce thing here: CoreAudio mixes, so two
scsynths can run. The scarce things are the TCP port (genuinely exclusive)
and the user's attention. If a future rig really cannot share the device,
`acquire()` is where an exclusive mode would go — refuse globally rather
than kill, per the same rule.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

REG_DIR = Path(os.environ.get("PATCHWERK_RIGS", "/tmp/patchwerk-rigs"))


class RigBusy(RuntimeError):
    """Someone else holds this port. Refusing is the whole point."""


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    return True


def port_answers(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/",
                                    timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def scsynth_children(pid) -> list[int]:
    """scsynth processes spawned BY this rig process.

    supriya spawns the server from the rig process, so the child is exactly
    ours — the basis for killing our own without a machine-wide sweep.
    """
    if not pid:
        return []
    r = subprocess.run(["pgrep", "-P", str(pid), "-x", "scsynth"],
                       capture_output=True, text=True)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


def _path(port: int) -> Path:
    return REG_DIR / f"{port}.json"


def read(port: int) -> dict | None:
    try:
        return json.loads(_path(port).read_text())
    except (OSError, ValueError):
        return None


def is_stale(rec: dict | None) -> bool:
    """A lock nobody is behind any more.

    Both signals must be dead: the owning PROCESS and the PORT. A live owner
    with a rig still booting has no port yet; a dead owner whose rig is still
    serving is a rig worth leaving alone until someone reaps it.
    """
    if not rec:
        return True
    return not _alive(rec.get("owner_pid")) and not port_answers(rec.get("port", 0))


def live() -> list[dict]:
    """Every rig currently registered and actually behind its lock."""
    out = []
    for f in sorted(REG_DIR.glob("*.json")) if REG_DIR.exists() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not is_stale(rec):
            out.append(rec)
    return out


def clean() -> list[dict]:
    """Drop stale locks. Safe to run at any time, by anyone."""
    dropped = []
    for f in sorted(REG_DIR.glob("*.json")) if REG_DIR.exists() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            f.unlink(missing_ok=True)          # unreadable = not a claim
            continue
        if is_stale(rec):
            dropped.append(rec)
            f.unlink(missing_ok=True)
    return dropped


def describe(rec: dict) -> str:
    age = ""
    if rec.get("started"):
        age = f", up {int(time.time() - rec['started'])}s"
    return (f"port {rec.get('port')} held by session {rec.get('session', '?')} "
            f"(owner pid {rec.get('owner_pid')}, rig pid {rec.get('rig_pid')}"
            f"{age})")


def acquire(port: int, session: str = "", how: str = "") -> dict:
    """Claim a port. Raises RigBusy if someone else has it — never kills.

    A stale claim (dead owner AND silent port) is taken over. A live one is
    refused, with the holder named, because the alternative is what caused
    this file to exist.
    """
    REG_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"port": int(port), "owner_pid": os.getpid(), "rig_pid": None,
           "scsynth_pid": None, "started": time.time(),
           "session": session or os.environ.get("PATCHWERK_SESSION", ""),
           "how": how}
    path = _path(port)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        existing = read(port)
        if not is_stale(existing):
            raise RigBusy(
                f"another session already owns {describe(existing)}.\n"
                f"  Coexist on a different port: SS_PORT=<free port> ...\n"
                f"  See them all: rig.py rigs\n"
                f"  This driver will NOT kill a rig it did not start."
            ) from None
        path.unlink(missing_ok=True)           # stale: take it over
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w") as fh:
        json.dump(rec, fh)
    return rec


def update(port: int, **fields) -> dict | None:
    rec = read(port)
    if rec is None:
        return None
    rec.update(fields)
    _path(port).write_text(json.dumps(rec))
    return rec


def owned_by_us(port: int) -> bool:
    rec = read(port)
    return bool(rec and rec.get("owner_pid") == os.getpid())


def release(port: int) -> bool:
    """Drop OUR lock. Refuses to drop somebody else's."""
    if not owned_by_us(port):
        return False
    _path(port).unlink(missing_ok=True)
    return True


def stop_owned(port: int, timeout: float = 5.0) -> dict:
    """Stop the rig on this port — ONLY if we own it.

    Kills the rig process, then any scsynth it left orphaned, BY PID. There
    is deliberately no `pkill -x scsynth` here: that is the machine-wide kill
    that took down other sessions' rigs.
    """
    rec = read(port)
    if not rec or rec.get("owner_pid") != os.getpid():
        return {"stopped": False, "why": "not ours"}
    killed = []
    rig_pid = rec.get("rig_pid")
    # The recorded pid FIRST: once the parent is gone, `pgrep -P` finds
    # nothing, and a killed rig does not take its scsynth with it. That is
    # how a failed boot used to leave a server running forever.
    kids = {rec["scsynth_pid"]} if rec.get("scsynth_pid") else set()
    kids |= set(scsynth_children(rig_pid))
    for pid in ([rig_pid] if rig_pid else []) + sorted(kids):
        if _kill(pid, timeout):
            killed.append(pid)
    release(port)
    return {"stopped": True, "killed": killed}


def _kill(pid, timeout: float = 5.0) -> bool:
    if not _alive(pid):
        return False
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.15)
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return True
