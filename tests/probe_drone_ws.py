"""Live ws probe for the DRONE REWORK (Mac only, server running).

    python tests/probe_drone_ws.py

Drives the real server over the websocket and verifies the item-3
semantics end to end: the drone as a MONO ctl note-sink (last-note
priority, hold-on-empty), the deriver emitting its committed root as a
note stream, immediate pitch on a fresh deriver→drone wire, and output
level while the drone sounds. Restores what it spawned.
"""

import asyncio
import json

import aiohttp

WS = "http://127.0.0.1:8765/ws"
FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


async def drain_state(ws, timeout=6):
    """Wait for the next full state broadcast."""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
        if m["type"] == "state":
            return m
    raise TimeoutError("no state broadcast")


async def poke_state(ws, st):
    """Force a fresh state broadcast (note events alone don't broadcast):
    a no-op set_transport at the current bpm re-broadcasts to everyone."""
    await ws.send_json({"type": "set_transport",
                        "bpm": st["transport"]["bpm"]})
    return await drain_state(ws)


def drone_key(st):
    return next((c["key"] for c in st["chain"] if c.get("type") == "drone"
                 or str(c["key"]).split(".")[0] == "drone"), None)


def drone_freq(st, key):
    c = next((c for c in st["chain"] if c["key"] == key), None)
    return c["params"]["freq"]["value"] if c else None


async def rms_out(ws, seconds=1.5):
    vals = []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            m = json.loads((await asyncio.wait_for(ws.receive(), 3)).data)
        except asyncio.TimeoutError:
            break
        if m["type"] == "meters":
            vals.append(max(m["out"][0], m["out"][1]))
    return max(vals) if vals else 0.0


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            spawned_drone = False
            dk = drone_key(st)
            if dk is None:
                await ws.send_json({"type": "spawn_module", "key": "drone"})
                st = await drain_state(ws)
                dk = drone_key(st)
                spawned_drone = True
            check("drone instance present", dk is not None, str(
                [c["key"] for c in st["chain"]]))
            if dk is None:
                return 1
            await ws.send_json({"type": "graph_wire", "action": "add",
                                "from": dk, "to": "master"})
            st = await drain_state(ws)

            # -- mono ctl sink: keys→drone ---------------------------------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": "keys", "to": dk})
            st = await drain_state(ws)
            check("keys→drone wire accepted",
                  {"from": "keys", "to": dk} in st["ctl_wires"],
                  str(st["ctl_wires"]))

            async def note(n, on=True):
                await ws.send_json({"type": "note_on" if on else "note_off",
                                    "note": n})
                await asyncio.sleep(0.15)

            await note(45)            # A2
            st = await poke_state(ws, st)
            f = drone_freq(st, dk)
            check("note_on retargets drone freq (A2≈110)",
                  f and abs(f - 110.0) < 1.0, str(f))
            await note(52)            # E3 takes over
            st = await poke_state(ws, st)
            f = drone_freq(st, dk)
            check("last-note priority (E3≈164.8)",
                  f and abs(f - 164.81) < 1.5, str(f))
            await note(52, on=False)  # release the root → fall back to A2
            st = await poke_state(ws, st)
            f = drone_freq(st, dk)
            check("root release falls back (A2≈110)",
                  f and abs(f - 110.0) < 1.0, str(f))
            await note(45, on=False)  # release last → HOLD
            st = await poke_state(ws, st)
            f = drone_freq(st, dk)
            check("empty held-set holds the root", f and abs(f - 110.0) < 1.0,
                  str(f))

            # -- audible: drone into master actually sounds ----------------
            level = await rms_out(ws)
            check("output level while the drone sounds", level > 0.001,
                  str(level))

            # -- deriver emits notes: keys→tonic→drone ---------------------
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": "keys", "to": dk})
            await ws.send_json({"type": "spawn_tonic"})
            st = await drain_state(ws)
            tid = st["tonics"][-1]["id"]
            await ws.send_json({"type": "set_tonic", "id": tid,
                                "every": "1 beat", "octave": 2})
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": "keys", "to": tid})
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": tid, "to": dk})
            st = await drain_state(ws)
            # C-major evidence, then wait past a 1-beat grid decision
            for n in (48, 60, 64, 67, 60, 48):
                await note(n)
                await note(n, on=False)
            await asyncio.sleep(2.5)
            await ws.send_json({"type": "all_notes_off"})
            st = await poke_state(ws, st)
            f = drone_freq(st, dk)
            check("deriver committed root drives the drone (C2≈65.4)",
                  f and abs(f - 65.41) < 1.0, str(f))
            root = next((t.get("root") for t in st["tonics"]
                         if t["id"] == tid), None)
            check("deriver root readout is C", root == "C", str(root))

            # -- cleanup: remove what we spawned ---------------------------
            await ws.send_json({"type": "remove_tonic", "id": tid})
            if spawned_drone:
                await ws.send_json({"type": "edit_chain", "action": "remove",
                                    "key": dk})
            await drain_state(ws)

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
