"""Live ws probe for the GATE SUITE (item 8; Mac only, server up).

    python tests/probe_gate_ws.py

Drives the real server over the websocket: spawns a switch + logic gate,
verifies level propagation (switch → AND) in state + the {"kind":"gate"}
event, drives a REAL chain module's :pwr toggle (reads the current
enabled first and RESTORES it), checks the ping-alternator grammar and
the SR-latch endpoint swap. Deck buttons are deliberately limited to a
harmless "deck:stop" press. Restores everything it spawned.
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


async def recv_type(ws, want, timeout=6, match=None):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
        if m["type"] == want and (match is None or match(m)):
            return m
    raise TimeoutError(f"no {want} broadcast")


async def drain_state(ws, timeout=6):
    return await recv_type(ws, "state", timeout)


async def poke_state(ws, st):
    """set_switch/set_logic broadcasts EXCLUDE the sender — poke."""
    await ws.send_json({"type": "set_transport",
                        "bpm": st["transport"]["bpm"]})
    return await drain_state(ws)


def by_id(lst, i):
    return next((x for x in lst or [] if x.get("id") == i), None)


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            check("state carries switches + logics keys",
                  "switches" in st and "logics" in st, str(sorted(st)))
            sw_before = {x["id"] for x in st.get("switches", [])}
            lg_before = {x["id"] for x in st.get("logics", [])}

            await ws.send_json({"type": "spawn_switch"})
            await ws.send_json({"type": "spawn_logic"})
            sid = lid = None
            for _ in range(10):
                st = await drain_state(ws)
                sid = sid or next((x["id"] for x in st["switches"]
                                   if x["id"] not in sw_before), None)
                lid = lid or next((x["id"] for x in st["logics"]
                                   if x["id"] not in lg_before), None)
                if sid and lid:
                    break
            check("spawned switch + logic appear", bool(sid and lid),
                  str((sid, lid)))
            if not (sid and lid):
                return 1

            # -- propagation: switch → AND, with the live gate event -------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": sid, "to": lid})
            st = await drain_state(ws)
            check("gate wire accepted (switch→logic)",
                  {"from": sid, "to": lid} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            await ws.send_json({"type": "set_switch", "id": sid, "on": True})
            # kind-tagged events ride {"type":"midi","event":{...}}
            ev = await recv_type(
                ws, "midi", 4,
                lambda m: (m.get("event") or {}).get("kind") == "gate"
                and m["event"].get("id") == lid)
            check("logic out went hi + gate event broadcast",
                  ev["event"].get("on") is True, str(ev))
            st = await poke_state(ws, st)
            check("state shows logic out hi",
                  (by_id(st["logics"], lid) or {}).get("out") is True,
                  str(by_id(st["logics"], lid)))

            # -- SR latch endpoint swap ------------------------------------
            await ws.send_json({"type": "set_logic", "id": lid,
                                "op": "SR latch"})
            st = await poke_state(ws, st)
            check("op swap to SR latch dropped the bare-in wire",
                  {"from": sid, "to": lid} not in st["ctl_wires"],
                  str(st["ctl_wires"]))
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": sid, "to": f"{lid}:set"})
            st = await drain_state(ws)
            # switch is still ON → set is hi → latch must be hi
            st = await poke_state(ws, st)
            check("SR latch set by the hi switch",
                  (by_id(st["logics"], lid) or {}).get("out") is True,
                  str(by_id(st["logics"], lid)))
            await ws.send_json({"type": "set_switch", "id": sid, "on": False})
            st = await poke_state(ws, st)
            check("SR latch HOLDS after set drops",
                  (by_id(st["logics"], lid) or {}).get("out") is True,
                  str(by_id(st["logics"], lid)))

            # -- real module :pwr follow (read-and-restore) ----------------
            mod = next((c for c in st["chain"]
                        if c.get("key") not in (None, "master")
                        and "enabled" in c), None)
            if mod is not None:
                key, was = mod["key"], bool(mod["enabled"])
                await ws.send_json({"type": "ctl_wire", "action": "add",
                                    "from": sid, "to": f"{key}:pwr"})
                st = await drain_state(ws)
                # switch is lo → level-follow disables; then hi re-enables
                st = await poke_state(ws, st)
                en = next(c["enabled"] for c in st["chain"]
                          if c["key"] == key)
                check("module :pwr follows the lo switch", en is False,
                      str(en))
                await ws.send_json({"type": "set_switch", "id": sid,
                                    "on": True})
                st = await poke_state(ws, st)
                en = next(c["enabled"] for c in st["chain"]
                          if c["key"] == key)
                check("module :pwr follows the hi switch", en is True,
                      str(en))
                await ws.send_json({"type": "ctl_wire", "action": "remove",
                                    "from": sid, "to": f"{key}:pwr"})
                st = await drain_state(ws)
                if was is not True:      # restore the pre-probe enable
                    await ws.send_json({"type": "set_enabled", "key": key,
                                        "enabled": was})
                    st = await drain_state(ws)
            else:
                print("skip  module :pwr follow (no chain module on rig)")

            # -- harmless deck press + grammar refusal ---------------------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": sid, "to": "deck:stop"})
            st = await drain_state(ws)
            check("gate wire to deck:stop accepted",
                  {"from": sid, "to": "deck:stop"} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": sid, "to": "deck:stop"})
            st = await drain_state(ws)
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": sid, "to": "arp"})
            got_err = False
            try:
                m = await recv_type(ws, "error", 3)
                got_err = True
            except TimeoutError:
                st = await poke_state(ws, st)
                got_err = {"from": sid, "to": "arp"} not in st["ctl_wires"]
            check("gate → note sink refused", got_err)

            # -- cleanup ---------------------------------------------------
            await ws.send_json({"type": "remove_logic", "id": lid})
            await ws.send_json({"type": "remove_switch", "id": sid})
            st = await poke_state(ws, st)
            check("cleanup: nodes removed",
                  {x["id"] for x in st["switches"]} == sw_before and
                  {x["id"] for x in st["logics"]} == lg_before,
                  str((st["switches"], st["logics"])))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
