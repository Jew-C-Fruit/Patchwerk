"""Live ws probe for TRANSPORT CARDS (item 9; Mac only, server up).

    python tests/probe_transport_ws.py

Drives the real server over the websocket: verifies state carries
transport_cards + transport.downbeat, spawns the tempo card (appears in
state, spawn is idempotent), wires a LATCHED button into transport:run
(hi = playing, lo = stopped — the level applies on attach too), taps
tempo through transport:tap with 4 timed fire_button pulses ~0.5 s
apart (bpm lands ≈120±8), round-trips a downbeat move via
set_transport, and removes the card (the transport endpoints are
GLOBAL — card removal never unwires them; this probe removes its own
wires explicitly).

Rig safety: the CURRENT transport state (bpm/click/accent/playing/
downbeat) is read FIRST and RESTORED at the end; everything spawned is
removed; polls are bounded. set_button broadcasts exclude the sender
and fire_button broadcasts nothing, so state is re-poked via a no-op
set_transport (no fields — set_transport broadcasts to ALL)."""

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


async def poke_state(ws):
    """A no-op set_transport (no fields) broadcasts a fresh state to ALL
    clients including the sender — and changes nothing on the rig."""
    await ws.send_json({"type": "set_transport"})
    return await drain_state(ws)


def by_id(lst, i):
    return next((x for x in lst or [] if x.get("id") == i), None)


async def spawn_and_find(ws, st, msg, section, before):
    """Spawn via msg, then poll (bounded) for the new id in state[section]."""
    await ws.send_json({"type": msg})
    for _ in range(10):
        st = await drain_state(ws)
        nid = next((x["id"] for x in st.get(section, [])
                    if x["id"] not in before), None)
        if nid:
            return nid, st
    return None, st


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            check("state carries transport_cards", "transport_cards" in st,
                  str(sorted(st)))
            check("state.transport carries downbeat",
                  "downbeat" in st.get("transport", {}),
                  str(st.get("transport")))

            # -- read-and-RESTORE baseline: everything this probe touches --
            t0 = dict(st["transport"])
            base_cards = list(st.get("transport_cards", []))
            base_buttons = {x["id"] for x in st.get("buttons", [])}
            base_wires = [dict(w) for w in st["ctl_wires"]]

            # -- spawn the tempo card (idempotent, appears in state) --------
            await ws.send_json({"type": "spawn_transport_card",
                                "which": "tempo"})
            st = await drain_state(ws)
            check("tempo card appears in state",
                  "tempo" in st["transport_cards"],
                  str(st["transport_cards"]))
            await ws.send_json({"type": "spawn_transport_card",
                                "which": "tempo"})
            st = await drain_state(ws)
            check("spawn is idempotent (still one tempo card)",
                  st["transport_cards"].count("tempo") == 1,
                  str(st["transport_cards"]))

            # -- latched button → transport:run: hi = playing, lo = stopped -
            b1, st = await spawn_and_find(ws, st, "spawn_button", "buttons",
                                          base_buttons)
            check("probe button appears", bool(b1), str(b1))
            if not b1:
                return 1
            await ws.send_json({"type": "set_button", "id": b1,
                                "latch": True})
            st = await poke_state(ws)
            check("latch mode set", (by_id(st["buttons"], b1) or {})
                  .get("latch") is True, str(by_id(st["buttons"], b1)))
            # attach applies the level: button lo → transport stops
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b1, "to": "transport:run"})
            st = await drain_state(ws)
            check("binary wire accepted (button→transport:run)",
                  {"from": b1, "to": "transport:run"} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            st = await poke_state(ws)
            check("attach applies the lo level (transport stopped)",
                  st["transport"]["running"] is False, str(st["transport"]))
            await ws.send_json({"type": "fire_button", "id": b1})   # hi
            st = await poke_state(ws)
            check("transport:run hi → playing",
                  st["transport"]["running"] is True, str(st["transport"]))
            await ws.send_json({"type": "fire_button", "id": b1})   # lo
            st = await poke_state(ws)
            check("transport:run lo → stopped",
                  st["transport"]["running"] is False, str(st["transport"]))
            # unwire + restore the play state before the tap timing test
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": b1, "to": "transport:run"})
            st = await drain_state(ws)
            await ws.send_json({"type": "set_transport",
                                "playing": bool(t0["running"])})
            st = await drain_state(ws)
            check("play state restored", st["transport"]["running"]
                  == t0["running"], str(st["transport"]))

            # -- tap tempo: 4 timed pulses ~0.5 s apart → bpm ≈ 120 ---------
            await ws.send_json({"type": "set_button", "id": b1,
                                "latch": False})   # momentary = clean pulses
            st = await poke_state(ws)
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b1, "to": "transport:tap"})
            st = await drain_state(ws)
            check("binary wire accepted (button→transport:tap)",
                  {"from": b1, "to": "transport:tap"} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            t_start = asyncio.get_event_loop().time()
            for i in range(4):
                await ws.send_json({"type": "fire_button", "id": b1})
                if i < 3:                  # absolute deadlines beat drift
                    dt = t_start + 0.5 * (i + 1) \
                        - asyncio.get_event_loop().time()
                    await asyncio.sleep(max(0.0, dt))
            st = await poke_state(ws)
            bpm = st["transport"]["bpm"]
            check("4 taps at ~0.5 s land bpm ≈ 120 (±8)",
                  abs(bpm - 120.0) <= 8.0, f"bpm={bpm}")
            # restore the rig's tempo
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": b1, "to": "transport:tap"})
            st = await drain_state(ws)
            await ws.send_json({"type": "set_transport", "bpm": t0["bpm"]})
            st = await drain_state(ws)
            check("bpm restored", abs(st["transport"]["bpm"] - t0["bpm"])
                  < 0.01, str(st["transport"]))

            # -- downbeat round-trip ----------------------------------------
            bpb = int(t0.get("beats_per_bar", 4))
            want = (int(t0.get("downbeat", 0)) + 1) % max(1, bpb)
            await ws.send_json({"type": "set_transport", "downbeat": want})
            st = await drain_state(ws)
            check("downbeat set round-trips in state",
                  st["transport"]["downbeat"] == want, str(st["transport"]))
            await ws.send_json({"type": "set_transport",
                                "downbeat": t0["downbeat"]})
            st = await drain_state(ws)
            check("downbeat restored",
                  st["transport"]["downbeat"] == t0["downbeat"],
                  str(st["transport"]))

            # -- remove the card (endpoints are GLOBAL — nothing to unwire;
            #    this probe already removed its own wires) -------------------
            await ws.send_json({"type": "remove_transport_card",
                                "which": "tempo"})
            st = await drain_state(ws)
            check("tempo card removed",
                  ("tempo" in base_cards) == ("tempo" in
                                              st["transport_cards"]),
                  str(st["transport_cards"]))

            # -- cleanup: button gone, full restore, baseline ---------------
            await ws.send_json({"type": "remove_button", "id": b1})
            await ws.send_json({
                "type": "set_transport", "bpm": t0["bpm"],
                "beats_per_bar": t0["beats_per_bar"], "click": t0["click"],
                "accent": t0["accent"], "playing": t0["running"],
                "downbeat": t0["downbeat"]})
            ok = False
            for _ in range(10):            # poll past stale broadcasts
                st = await poke_state(ws)
                tr = st["transport"]
                ok = ({x["id"] for x in st.get("buttons", [])} == base_buttons
                      and st["transport_cards"] == base_cards
                      and [dict(w) for w in st["ctl_wires"]] == base_wires
                      and abs(tr["bpm"] - t0["bpm"]) < 0.01
                      and tr["click"] == t0["click"]
                      and tr["accent"] == t0["accent"]
                      and tr["running"] == t0["running"]
                      and tr["downbeat"] == t0["downbeat"])
                if ok:
                    break
            check("cleanup: nodes + wires + transport back to baseline", ok,
                  str((st.get("buttons"), st.get("transport_cards"),
                       st.get("ctl_wires"), st.get("transport"))))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
