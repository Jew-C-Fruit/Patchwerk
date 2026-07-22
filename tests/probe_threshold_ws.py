"""Live ws probe for the THRESHOLD module (item 8a; Mac only, server up).

    .venv/bin/python tests/probe_threshold_ws.py

Drives the real server over the websocket and verifies item 8 end to end
against a LIVE scsynth: LFO → threshold CV wire spawns exactly one watch
synth; crossings arrive as PING events at the LFO's rate (edge-notify via
/tr — no polling); mode=both doubles the fire rate; a level outside the
CV's swing never fires; teardown returns the synth count to baseline.

Pass SC_PORT (scsynth rides a RANDOM UDP port — find via `ps ax | grep
scsynth`, the -u flag). Fire counting rides the ws midi stream, so the
probe needs no MIDI hardware and makes no sound (the LFO drives nothing
audible).
"""

import asyncio
import json
import os
import socket
import struct

import aiohttp

WS = "http://127.0.0.1:8765/ws"
SC_PORT = int(os.environ.get("SC_PORT", "57110"))
FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# -- scsynth /status over raw OSC (no client library, no boot) ---------------

def sc_synth_count(timeout=2.0):
    msg = b"/status\x00,\x00\x00\x00"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, ("127.0.0.1", SC_PORT))
        data, _ = sock.recvfrom(8192)
    finally:
        sock.close()
    i = data.index(b",")
    tags = data[i:data.index(b"\x00", i)].decode()[1:]
    off = i + ((len(tags) + 1 + 4) & ~3)
    vals = []
    for t in tags:
        if t == "i":
            vals.append(struct.unpack(">i", data[off:off + 4])[0]); off += 4
        elif t == "f":
            vals.append(struct.unpack(">f", data[off:off + 4])[0]); off += 4
        elif t == "d":
            vals.append(struct.unpack(">d", data[off:off + 8])[0]); off += 8
    return vals[2]


async def stable_count(samples=5, gap=0.12):
    vals = []
    for _ in range(samples):
        vals.append(sc_synth_count())
        await asyncio.sleep(gap)
    return min(vals)


async def drain_state(ws, timeout=6):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
        if m["type"] == "error":
            print("      [server error] " + m.get("message", ""))
        if m["type"] == "state":
            return m
    raise TimeoutError("no state broadcast")


async def count_pings(ws, src, seconds):
    """WALL-CLOCK bounded (meters stream ~15 Hz — never drain-until-quiet)."""
    n = 0
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        left = end - asyncio.get_event_loop().time()
        try:
            m = json.loads(
                (await asyncio.wait_for(ws.receive(), max(0.05, left))).data)
        except asyncio.TimeoutError:
            break
        if (m.get("type") == "midi"
                and (m.get("event") or {}).get("kind") == "ping"
                and m["event"].get("src") == src):
            n += 1
    return n


def thr_by_id(st, tid):
    return next((t for t in st.get("thresholds", []) if t["id"] == tid), None)


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            base_lfos = {l["id"] for l in st["lfos"]}
            base_thrs = {t["id"] for t in st.get("thresholds", [])}
            n0 = await stable_count()
            check("scsynth /status reachable", n0 >= 0, str(n0))

            # -- spawn the pair --------------------------------------------
            await ws.send_json({"type": "spawn_lfo"})
            st = await drain_state(ws)
            lid = next(l["id"] for l in st["lfos"]
                       if l["id"] not in base_lfos)
            await ws.send_json({"type": "spawn_threshold"})
            st = await drain_state(ws)
            tid = next(t["id"] for t in st["thresholds"]
                       if t["id"] not in base_thrs)
            check("threshold appears in state (unwired)",
                  thr_by_id(st, tid)["source"] is None, str(st["thresholds"]))
            await asyncio.sleep(0.3)
            n1 = await stable_count()
            check("spawn pair adds exactly ONE synth (the LFO norm; "
                  "threshold none until wired)", n1 == n0 + 1, f"{n0} -> {n1}")

            # -- CV wire: one watch synth ----------------------------------
            await ws.send_json({"type": "lfo_set", "id": lid,
                                "rate": 2.0, "depth": 1.0})
            await ws.send_json({"type": "set_threshold", "id": tid,
                                "level": 0.0, "hysteresis": 0.05,
                                "mode": "rising"})
            await ws.send_json({"type": "threshold_wire", "action": "add",
                                "id": tid, "lfo": lid})
            st = await drain_state(ws)
            check("state carries the CV source",
                  thr_by_id(st, tid)["source"] == lid, str(st["thresholds"]))
            await asyncio.sleep(0.3)
            n2 = await stable_count()
            check("the CV wire adds exactly ONE watch synth",
                  n2 == n1 + 1, f"{n1} -> {n2}")

            # -- crossings arrive as pings at the LFO's rate ---------------
            await asyncio.sleep(0.3)   # clear the arm window
            got = await count_pings(ws, tid, 2.2)
            check("rising crossings fire ~rate pings (2 Hz → ~4 in 2.2 s)",
                  3 <= got <= 6, f"{got} pings")

            # -- both edges ≈ double rate ----------------------------------
            await ws.send_json({"type": "set_threshold", "id": tid,
                                "mode": "both"})
            await asyncio.sleep(0.3)
            got2 = await count_pings(ws, tid, 2.2)
            check("both mode ≈ doubles the fire rate (~8 in 2.2 s)",
                  6 <= got2 <= 11, f"{got2} pings")

            # -- a level outside the swing never fires ---------------------
            await ws.send_json({"type": "lfo_set", "id": lid, "depth": 0.4})
            await ws.send_json({"type": "set_threshold", "id": tid,
                                "level": 0.8, "mode": "rising"})
            await asyncio.sleep(0.4)
            got3 = await count_pings(ws, tid, 1.5)
            check("a level beyond the CV swing is silent", got3 == 0,
                  f"{got3} pings")

            # -- teardown: back to baseline (leak accounting) --------------
            await ws.send_json({"type": "remove_threshold", "id": tid})
            st = await drain_state(ws)
            check("threshold gone from state", thr_by_id(st, tid) is None)
            await ws.send_json({"type": "remove_lfo", "id": lid})
            st = await drain_state(ws)
            await asyncio.sleep(0.5)
            n3 = await stable_count()
            check("synth count back to baseline (no leaks)",
                  n3 == n0, f"start {n0} end {n3}")

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
