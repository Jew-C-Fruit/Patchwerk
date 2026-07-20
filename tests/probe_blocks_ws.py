"""Server-side round-trip validation over the REAL websocket (no GUI needed).
Runs on the Mac: .venv/bin/python tests/probe_blocks_ws.py
1. captures live state -> /tmp/real_state.json (for container-side page checks)
2. exercises the exact messages the Blocks GUI sends: spawn_module ->
   graph_wire add -> graph_wire remove -> edit_chain remove
3. verifies the rack is EXACTLY restored, writes /tmp/blocks_ws_probe.json
"""
import asyncio
import json

import aiohttp

OUT = "/tmp/blocks_ws_probe.json"
res = {"checks": [], "failures": []}


def check(name, cond, extra=""):
    res["checks"].append([name, bool(cond), "" if cond else str(extra)])
    if not cond:
        res["failures"].append(name)
    print(("ok    " if cond else "FAIL  ") + name + ("" if cond else f"  [{extra}]"))


def rack_snap(st):
    return json.dumps({"chain": [c["key"] for c in st["chain"]],
                       "wires": st.get("wires"), "ctl": st.get("ctl_wires"),
                       "lfos": [l["id"] for l in st.get("lfos", [])]}, sort_keys=True)


async def next_state(ws, pred, timeout=8.0):
    async def _wait():
        while True:
            msg = await ws.receive()
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            d = json.loads(msg.data)
            if d.get("type") == "state" and pred(d):
                return d
    return await asyncio.wait_for(_wait(), timeout)


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("ws://127.0.0.1:8765/ws") as ws:
            st = await next_state(ws, lambda d: True, timeout=10)
            with open("/tmp/real_state.json", "w") as f:
                json.dump(st, f)
            check("live state captured", True)
            before = rack_snap(st)
            olds = [c["key"] for c in st["chain"]]

            await ws.send_json({"type": "spawn_module", "key": "echo"})
            st = await next_state(
                ws, lambda d: len(d["chain"]) > len(olds))
            fresh = [c["key"] for c in st["chain"] if c["key"] not in olds]
            check("spawn_module added one instance", len(fresh) == 1, str(fresh))
            key = fresh[0]

            await ws.send_json({"type": "graph_wire", "action": "add",
                                "from": key, "to": "master"})
            st = await next_state(
                ws, lambda d: any(w["from"] == key and w["to"] == "master"
                                  for w in d.get("wires", [])))
            check("graph_wire add landed in state.wires", True)

            await ws.send_json({"type": "graph_wire", "action": "remove", "from": key})
            st = await next_state(
                ws, lambda d: not any(w["from"] == key and w["to"] == "master"
                                      for w in d.get("wires", [])))
            check("graph_wire remove (the GUI's cut) landed", True)

            await ws.send_json({"type": "edit_chain", "action": "remove", "key": key})
            st = await next_state(
                ws, lambda d: key not in [c["key"] for c in d["chain"]])
            check("edit_chain remove dropped the instance", True)

            after = rack_snap(st)
            check("rack restored EXACTLY", before == after,
                  f"{before[:120]} != {after[:120]}")

    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print("FAILURES:" if res["failures"] else "ALL WS CHECKS PASSED",
          res["failures"] or "")


asyncio.run(main())
