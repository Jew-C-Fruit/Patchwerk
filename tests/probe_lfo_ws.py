"""Live ws probe for the ROUTABLE LFO rework (item 7; Mac only, server up).

    .venv/bin/python tests/probe_lfo_ws.py

Drives the real server over the websocket and verifies the item-7
semantics end to end against a LIVE scsynth: the LFO as a standalone
node, fan-out to two params with DIFFERENT curves (exp cutoff + lin res),
center steering from the mapped param's slider, audible modulation (an
LFO on the drone's amp makes the meters breathe), and SYNTH-LEAK
ACCOUNTING via scsynth /status (every spawn/wire/unwire/remove returns
the server's synth count to its baseline). Restores what it spawned.
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
    """Ask the running scsynth how many synth nodes it has."""
    msg = b"/status\x00,\x00\x00\x00"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(msg, ("127.0.0.1", SC_PORT))
        data, _ = sock.recvfrom(8192)
    finally:
        sock.close()
    # /status.reply\0.. ,iiiiiffdd\0.. then payload ints/floats
    i = data.index(b",")
    tags = data[i:data.index(b"\x00", i)].decode()[1:]
    # skip the typetag string + its null padding (4-byte aligned)
    off = i + ((len(tags) + 1 + 4) & ~3)
    vals = []
    for t in tags:
        if t == "i":
            vals.append(struct.unpack(">i", data[off:off + 4])[0]); off += 4
        elif t == "f":
            vals.append(struct.unpack(">f", data[off:off + 4])[0]); off += 4
        elif t == "d":
            vals.append(struct.unpack(">d", data[off:off + 8])[0]); off += 8
    # (unused, ugens, synths, groups, synthdefs, avg_cpu, peak_cpu, sr, sr)
    return vals[2]


async def stable_count(samples=5, gap=0.12):
    """Min synth count over a short window — transient synths (metronome
    clicks, releasing voices) die between samples, so min = the resident
    population we're accounting."""
    vals = []
    for _ in range(samples):
        vals.append(sc_synth_count())
        await asyncio.sleep(gap)
    return min(vals)


async def drain_state(ws, timeout=6):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
        if m["type"] == "state":
            return m
    raise TimeoutError("no state broadcast")


async def poke_state(ws, st):
    await ws.send_json({"type": "set_transport",
                        "bpm": st["transport"]["bpm"]})
    return await drain_state(ws)


def lfo_by_id(st, lid):
    return next((l for l in st["lfos"] if l["id"] == lid), None)


def chain_param(st, key, pname):
    c = next((c for c in st["chain"] if c["key"] == key), None)
    return c["params"][pname] if c else None


async def meter_minmax(ws, seconds=2.0):
    vals = []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            m = json.loads((await asyncio.wait_for(ws.receive(), 3)).data)
        except asyncio.TimeoutError:
            break
        if m["type"] == "meters":
            vals.append(max(m["out"][0], m["out"][1]))
    return (min(vals), max(vals)) if vals else (0.0, 0.0)


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            base_lfos = {l["id"] for l in st["lfos"]}
            n0 = await stable_count()
            check("scsynth /status reachable", n0 >= 0, str(n0))

            # -- a fresh lowpass gives us exp (cutoff) + lin (res) params --
            keys0 = {c["key"] for c in st["chain"]}
            await ws.send_json({"type": "spawn_module", "key": "lowpass"})
            st = await drain_state(ws)
            lp = next((c["key"] for c in st["chain"]
                       if c["key"] not in keys0), None)
            check("fresh lowpass spawned", lp is not None,
                  str([c["key"] for c in st["chain"]]))
            if lp is None:
                return 1
            cut0 = chain_param(st, lp, "cutoff")["value"]
            n_mod = await stable_count()

            # -- standalone spawn ------------------------------------------
            await ws.send_json({"type": "spawn_lfo"})
            lid = None
            for _ in range(6):
                st = await drain_state(ws)
                new = [l["id"] for l in st["lfos"] if l["id"] not in base_lfos]
                if new:
                    lid = new[0]
                    break
            check("spawned LFO appears in state (unwired)",
                  lid is not None and lfo_by_id(st, lid)["dests"] == [],
                  str(st["lfos"]))
            if lid is None:
                return 1
            await asyncio.sleep(0.3)
            n1 = await stable_count()
            check("spawn adds exactly ONE synth (the norm osc)",
                  n1 == n_mod + 1, f"{n_mod} -> {n1}")

            # -- fan-out to two params with DIFFERENT curves ---------------
            await ws.send_json({"type": "lfo_wire", "action": "add",
                                "id": lid, "key": lp, "name": "cutoff"})
            await ws.send_json({"type": "lfo_wire", "action": "add",
                                "id": lid, "key": lp, "name": "res"})
            st = await drain_state(ws)
            st = await drain_state(ws)
            l = lfo_by_id(st, lid)
            check("both destinations in state",
                  {(d["key"], d["param"]) for d in l["dests"]}
                  == {(lp, "cutoff"), (lp, "res")}, str(l))
            check("curves differ across the fan-out (exp + lin)",
                  chain_param(st, lp, "cutoff")["curve"] == "exp"
                  and chain_param(st, lp, "res")["curve"] == "lin")
            check("both params flagged mapped in chain state",
                  chain_param(st, lp, "cutoff")["lfo"]
                  and chain_param(st, lp, "res")["lfo"])
            await asyncio.sleep(0.3)
            n2 = await stable_count()
            check("each destination adds exactly ONE scale synth",
                  n2 == n1 + 2, f"{n1} -> {n2}")

            # -- slider on a mapped param steers ITS center ----------------
            await ws.send_json({"type": "set_param", "key": lp,
                                "name": "cutoff", "unit": 0.8})
            await asyncio.sleep(0.2)
            st = await poke_state(ws, st)
            l = lfo_by_id(st, lid)
            c = next(d["center"] for d in l["dests"] if d["param"] == "cutoff")
            r = next(d["center"] for d in l["dests"] if d["param"] == "res")
            check("mapped slider steered the cutoff dest center",
                  abs(c - 0.8) < 1e-6, str(l["dests"]))
            check("the OTHER dest's center untouched",
                  abs(r - 0.8) > 1e-6, str(l["dests"]))

            # -- unwire one dest: targeted teardown + param restore --------
            await ws.send_json({"type": "lfo_wire", "action": "remove",
                                "id": lid, "key": lp, "name": "res"})
            st = await drain_state(ws)
            l = lfo_by_id(st, lid)
            check("one dest survives a targeted unwire",
                  [d["param"] for d in l["dests"]] == ["cutoff"], str(l))
            check("unwired param unflagged",
                  not chain_param(st, lp, "res")["lfo"])
            await asyncio.sleep(0.3)
            n3 = await stable_count()
            check("unwire frees exactly its scale synth",
                  n3 == n2 - 1, f"{n2} -> {n3}")

            # -- audible: LFO on the drone's amp makes the meters breathe --
            spawned_drone = None
            dk = next((c["key"] for c in st["chain"]
                       if (c.get("type") or c["key"].split(".")[0]) == "drone"),
                      None)
            if dk is None:
                await ws.send_json({"type": "spawn_module", "key": "drone"})
                st = await drain_state(ws)
                dk = next(c["key"] for c in st["chain"]
                          if (c.get("type") or c["key"].split(".")[0]) == "drone")
                spawned_drone = dk
            await ws.send_json({"type": "graph_wire", "action": "add",
                                "from": dk, "to": "master"})
            st = await drain_state(ws)
            await ws.send_json({"type": "set_param", "key": dk,
                                "name": "amp", "unit": 0.5})
            await ws.send_json({"type": "lfo_wire", "action": "add",
                                "id": lid, "key": dk, "name": "amp"})
            st = await drain_state(ws)
            await ws.send_json({"type": "lfo_set", "id": lid,
                                "rate": 3.0, "depth": 1.0})
            await asyncio.sleep(0.5)
            lo, hi = await meter_minmax(ws)
            check("meters breathe under amp modulation",
                  hi > 0.003 and hi > lo * 2.5, f"min {lo:.4f} max {hi:.4f}")

            # -- full teardown: back to baseline (leak accounting) ---------
            await ws.send_json({"type": "remove_lfo", "id": lid})
            st = await drain_state(ws)
            check("LFO gone from state", lfo_by_id(st, lid) is None)
            p = chain_param(st, lp, "cutoff")
            expect = p["min"] * (p["max"] / p["min"]) ** 0.8  # steered unit 0.8
            check("teardown restores cutoff to its steered slider value",
                  not p["lfo"] and abs(p["value"] - expect) < expect * 0.02,
                  f"value {p['value']} expect ~{expect:.1f} (was {cut0})")
            await ws.send_json({"type": "edit_chain", "action": "remove",
                                "key": lp})
            if spawned_drone:
                await ws.send_json({"type": "edit_chain", "action": "remove",
                                    "key": spawned_drone})
            st = await drain_state(ws)
            await asyncio.sleep(0.5)
            n4 = await stable_count()
            check("synth count back to baseline (no leaks)",
                  n4 == n0, f"start {n0} end {n4}")

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
