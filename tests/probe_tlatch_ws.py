"""Live ws probe for the T LATCH (item 31). Mac only, server must be up.

    .venv/bin/python -u tests/probe_tlatch_ws.py

Proves on the REAL backend what test_gate proves headlessly: ONE pulse
source into a T latch ALTERNATES, where the same source into an SR latch
latches hi and sticks. That contrast is the whole reason the op exists —
run them side by side off one button so the difference is the only
variable. Self-cleaning: every node it spawns is removed and the
transport state it found is restored.

Landmines this probe was written around (all of them bit during the first
live run, and all are general to probe_*_ws.py):

* READ THE LATEST STATE, NOT THE FIRST. Broadcasts from earlier messages
  queue up, so the first `state` after a poke can predate what you just
  did. Reading the first one made a perfectly correct T latch look like
  it swallowed its first two pulses — the reads were simply two behind.
  `poke()` drains to the freshest state, BOUNDED (meters stream forever;
  "drain until quiet" never returns).
* PICK SPAWNED IDS BY DIFF, never `state[...][-1]` — that snapshot can be
  stale and hand you a pre-existing node. The rig's own `button` was
  already latched and wired to transport:click, so "pulses" on it were
  really latch toggles.
* A LATCHED button toggles its LEVEL per down; only a MOMENTARY one gives
  one rising edge per press. Set `latch` explicitly rather than assuming.
* `fire_button` does not drive a CLOCK — clocks fire off the transport
  grid — so a clock is the wrong pulse source for a probe like this.
* Run with `python -u`, or a hang shows up as an empty log file.
"""
import asyncio
import json
import sys

import aiohttp

WS = "http://127.0.0.1:8765/ws"
FAIL = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


async def recv_type(ws, want, timeout=6):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        msg = await asyncio.wait_for(ws.receive(), timeout)
        m = json.loads(msg.data)
        if m.get("type") == want:
            return m
    raise TimeoutError(want)


async def poke(ws):
    """set_* broadcasts EXCLUDE the sender — poke with a no-op to get state.

    Then DRAIN TO THE LATEST. Broadcasts from earlier messages queue up, so
    the first `state` back after a poke can predate the thing you just did:
    reading the first one made a perfectly correct T latch look like it was
    swallowing its first two pulses (the reads were simply two behind).
    Documented landmine; this is the fix.
    """
    await ws.send_json({"type": "set_transport"})
    st = await recv_type(ws, "state")
    for _ in range(40):               # BOUNDED: meters stream, never "drain
        try:                          # until quiet" without a hard cap
            msg = await asyncio.wait_for(ws.receive(), 0.25)
        except (asyncio.TimeoutError, TimeoutError):
            break
        try:
            m = json.loads(msg.data)
        except Exception:             # noqa: BLE001  (CLOSE/ERROR frames)
            break
        if m.get("type") == "state":
            st = m
    return st


def logic_out(st, lid):
    for lg in st.get("logics", []):
        if lg.get("id") == lid:
            return lg.get("out")
    return None


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await recv_type(ws, "state")
            base_logics = {l["id"] for l in st.get("logics", [])}
            base_buttons = {b["id"] for b in st.get("buttons", [])}
            was_playing = bool(st.get("transport", {}).get("playing"))
            check("connected to the live rig", True)
            print("      spawning nodes...", flush=True)

            # --- spawn a T latch + an SR latch + one clock -------------
            await ws.send_json({"type": "spawn_logic"})
            st = await recv_type(ws, "state")
            tid = [l["id"] for l in st["logics"] if l["id"] not in base_logics][0]
            await ws.send_json({"type": "spawn_logic"})
            st = await recv_type(ws, "state")
            sid = [l["id"] for l in st["logics"]
                   if l["id"] not in base_logics | {tid}][0]
            await ws.send_json({"type": "spawn_button"})
            st = await recv_type(ws, "state")
            new = [b["id"] for b in st["buttons"] if b["id"] not in base_buttons]
            cid = new[0]  # by DIFF, never [-1] off a possibly-stale state
            # MOMENTARY, explicitly: a latched button toggles its LEVEL on
            # every down, so down/up pairs would be alternating edges, not
            # one edge each.
            await ws.send_json({"type": "set_button", "id": cid, "latch": False})
            await asyncio.sleep(0.2)

            await ws.send_json({"type": "set_logic", "id": tid, "op": "T latch"})
            await ws.send_json({"type": "set_logic", "id": sid, "op": "SR latch"})
            st = await poke(ws)
            print("      ops read back", flush=True)
            ops = {l["id"]: l["op"] for l in st["logics"]}
            check("the live server accepts op 'T latch'",
                  ops.get(tid) == "T latch", str(ops))
            check("'T latch' is advertised in the op list",
                  "T latch" in (st["logics"][0].get("ops") or []),
                  str(st["logics"][0].get("ops")))
            check("a fresh T latch starts lo", logic_out(st, tid) is False,
                  str(logic_out(st, tid)))

            # --- ONE clock feeding BOTH latches ------------------------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": cid, "to": f"{tid}:a"})
            await asyncio.sleep(0.25)
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": cid, "to": f"{sid}:a"})
            await asyncio.sleep(0.25)
            wires, wired = [], False
            for _ in range(8):
                st = await poke(ws)
                wires = [(w.get("from"), w.get("to"))
                         for w in st.get("ctl_wires", [])]
                wired = ((cid, f"{tid}:a") in wires
                         and (cid, f"{sid}:a") in wires)
                if wired:
                    break
                await asyncio.sleep(0.25)
            check("one pulse source wired to BOTH latches' :a", wired,
                  str([w for w in wires if w[0] == cid]))

            # --- fire the clock by hand, reading out after each tick ---
            seen = []
            for _ in range(4):
                await ws.send_json({"type": "button_down", "id": cid})
                await asyncio.sleep(0.15)
                await ws.send_json({"type": "button_up", "id": cid})
                await asyncio.sleep(0.25)
                st = await poke(ws)
                seen.append((logic_out(st, tid), logic_out(st, sid)))
            print("      pulses (T, SR):", seen, flush=True)
            t_seq = [p[0] for p in seen]
            sr_seq = [p[1] for p in seen]
            check("the T latch ALTERNATES on the live rig (divide-by-2)",
                  t_seq == [True, False, True, False], str(t_seq))
            check("the SR latch on the SAME source sticks hi (what T fixes)",
                  sr_seq == [True, True, True, True], str(sr_seq))

            # --- reset leg --------------------------------------------
            await ws.send_json({"type": "spawn_button"})
            st = await recv_type(ws, "state")
            bid = [b["id"] for b in st["buttons"]
                   if b["id"] not in base_buttons | {cid}][0]
            await ws.send_json({"type": "set_button", "id": bid, "latch": True})
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": bid, "to": f"{tid}:b"})
            await ws.send_json({"type": "button_down", "id": bid})
            await asyncio.sleep(0.2)
            st = await poke(ws)
            check("a hi on :b forces the T latch lo (reset wins)",
                  logic_out(st, tid) is False, str(logic_out(st, tid)))
            await ws.send_json({"type": "button_down", "id": cid})
            await asyncio.sleep(0.15)
            await ws.send_json({"type": "button_up", "id": cid})
            await asyncio.sleep(0.25)
            st = await poke(ws)
            check("a pulse while held in reset is eaten",
                  logic_out(st, tid) is False, str(logic_out(st, tid)))

            # --- cleanup, polling past stale broadcasts ---------------
            for msg in ({"type": "remove_logic", "id": tid},
                        {"type": "remove_logic", "id": sid},
                        {"type": "remove_button", "id": cid},
                        {"type": "remove_button", "id": bid}):
                await ws.send_json(msg)
                await asyncio.sleep(0.15)
            await ws.send_json({"type": "set_transport", "playing": was_playing})
            clean = False
            for _ in range(12):
                st = await poke(ws)
                ids = {l["id"] for l in st.get("logics", [])}
                bids = {b["id"] for b in st.get("buttons", [])}
                if ids == base_logics and cid not in bids and bid not in bids:
                    clean = True
                    break
                await asyncio.sleep(0.2)
            check("rig returned to baseline (no leaked nodes)", clean)

    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(FAIL)} failures")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
