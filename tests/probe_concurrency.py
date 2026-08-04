"""Two sessions, one machine: prove nobody kills anybody. (item 37 Phase 1)

    .venv/bin/python -u tests/probe_concurrency.py

The regression this exists for: the driver used to clear stale servers with
`pkill -x scsynth` and boot through `run.sh`, which reaps every
`-m synthbase` process on the machine. On a Mac running several agent
sessions at once that means **every boot killed every other session's rig**,
and theirs killed ours mid-probe. It surfaces as `Server offline!` halfway
through a run and is indistinguishable from flaky audio unless you look at
the crash: SIGABRT inside `exit()` is a SIGTERM, not a fault.

So this asserts the four properties that had to become true:

1. two rigs on DIFFERENT ports coexist, and starting the second does not
   disturb the first — checked on real rigs, scsynth and all, whenever this
   machine's audio can start;
2. a second session wanting the SAME port is REFUSED, by name, never killed;
3. a session that dies BADLY (SIGKILL, no teardown) leaves a lock the next
   session recognises as stale and takes over — no lock nobody can clear;
4. stopping our rig kills our processes and nobody else's.

The "other session" is a real separate PROCESS, because ownership is keyed
on pid and faking it in-process would prove nothing.
"""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import rig as R  # noqa: E402
import rigreg  # noqa: E402

FAILURES = []
PORT_A, PORT_B = 8788, 8789


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def other_session(port: int) -> subprocess.Popen:
    """A separate process that claims `port` and sits on it."""
    code = (f"import sys; sys.path.insert(0, {str(REPO / 'tests')!r});"
            f"import rigreg, time;"
            f"rigreg.acquire({port}, session='pretend-other');"
            f"print('held', flush=True); time.sleep(300)")
    proc = subprocess.Popen([R.pick_python(), "-u", "-c", code],
                            stdout=subprocess.PIPE, text=True)
    proc.stdout.readline()          # wait for the claim
    return proc


async def main() -> int:
    for p in (PORT_A, PORT_B):
        rigreg.clean()
    audio = await asyncio.to_thread(R.scsynth_check)
    print(f"audio can start: {'yes' if audio['ready'] else 'NO — silent rigs only'}")

    # -- 1. coexistence ---------------------------------------------------
    silent = not audio["ready"]
    a = R.Rig(p=PORT_A, silent=silent)
    await a.start()
    check("rig A is up", await R.is_up(PORT_A))
    a_scsynth = rigreg.scsynth_children(a.booted["pid"]) if not silent else []
    if not silent:
        check("rig A owns a scsynth of its own", len(a_scsynth) == 1,
              str(a_scsynth))

    b = R.Rig(p=PORT_B, silent=silent)
    await b.start()
    check("rig B came up on another port", await R.is_up(PORT_B))
    check("BOOTING B DID NOT KILL A — the whole point",
          await R.is_up(PORT_A))
    if not silent:
        check("...and A's scsynth is still alive",
              all(rigreg._alive(pid) for pid in a_scsynth), str(a_scsynth))
    check("the registry lists both", {r["port"] for r in rigreg.live()}
          >= {PORT_A, PORT_B}, str([r["port"] for r in rigreg.live()]))

    # -- 4. stopping ours touches only ours --------------------------------
    await b.stop()
    check("stopping B left A running", await R.is_up(PORT_A))
    if not silent:
        check("...and left A's scsynth alone",
              all(rigreg._alive(pid) for pid in a_scsynth), str(a_scsynth))
    check("B's lock is released", not rigreg.read(PORT_B))

    # -- 2. same port: refuse, do not kill ---------------------------------
    holder = other_session(PORT_B)
    try:
        rec = rigreg.read(PORT_B)
        check("the other session's claim is visible",
              rec and rec["owner_pid"] == holder.pid, str(rec))
        try:
            await R.boot(p=PORT_B, silent=True)
            check("a second session is REFUSED the port", False,
                  "boot succeeded — it should have refused")
        except rigreg.RigBusy as exc:
            check("a second session is REFUSED the port", True)
            check("the refusal NAMES the holder",
                  str(holder.pid) in str(exc) and "pretend-other" in str(exc),
                  str(exc))
            check("the refusal says what to do instead",
                  "SS_PORT" in str(exc) and "will NOT kill" in str(exc),
                  str(exc))
        check("the holder is still alive — refusing is not killing",
              holder.poll() is None)

        # -- 3. a session that dies badly ---------------------------------
        holder.kill()                    # SIGKILL: no teardown, lock left behind
        holder.wait(timeout=5)
        check("the dead session's lock is still on disk",
              rigreg.read(PORT_B) is not None)
        check("...and is recognised as STALE",
              rigreg.is_stale(rigreg.read(PORT_B)))
        dropped = rigreg.clean()
        check("clean() drops it", PORT_B in [d["port"] for d in dropped],
              str([d["port"] for d in dropped]))
        c = R.Rig(p=PORT_B, silent=True)
        await c.start()
        check("the next session can take the port over", await R.is_up(PORT_B))
        await c.stop()
    finally:
        if holder.poll() is None:
            holder.kill()

    check("A survived the entire run untouched", await R.is_up(PORT_A))
    await a.stop()
    check("A stops cleanly at the end", not await R.is_up(PORT_A))
    if not silent:
        for _ in range(20):
            if not any(rigreg._alive(pid) for pid in a_scsynth):
                break
            time.sleep(0.25)
        check("A's scsynth went with it (by pid, not by sweep)",
              not any(rigreg._alive(pid) for pid in a_scsynth), str(a_scsynth))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
