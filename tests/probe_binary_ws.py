"""Live ws probe for the BINARY plane (binary rework; Mac only, server up).

    python tests/probe_binary_ws.py

Drives the real server over the websocket: verifies state carries logics
+ relays (and NO legacy switches key), spawns buttons + a logic gate + a
relay + a tonic deriver, checks latch/momentary button levels, the named
single-input wire grammar (:a/:b, steal-on-drop, bare-id refusal), AND
level propagation in state + the {"kind":"gate"} event, a rising edge
committing a wired deriver (a note is HELD via note_on first so Layer 2
has a held set, then released), the relay binary circuit (closed passes
the level, open blocks; relay:ctl takes a binary wire), and a real chain
module's :pwr hold-to-enable (reads the current enabled FIRST and
RESTORES it). Rig safety: the deck is never touched (not even deck:stop
— nothing here needs it); everything spawned is removed; cleanup POLLS
past stale broadcasts (bounded — set_* broadcasts exclude the sender, so
state is re-poked via set_transport at the current bpm).
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
    """set_button/set_logic/set_relay broadcasts EXCLUDE the sender (and
    fire/button_down/up broadcast nothing) — poke a fresh state out."""
    await ws.send_json({"type": "set_transport",
                        "bpm": st["transport"]["bpm"]})
    return await drain_state(ws)


def by_id(lst, i):
    return next((x for x in lst or [] if x.get("id") == i), None)


def logic_out(st, lid):
    return (by_id(st.get("logics"), lid) or {}).get("out")


async def expect_refused(ws, st, wire):
    """A ctl_wire add the server must refuse: an error frame to the
    sender, or (belt & braces) the wire absent from a poked state."""
    await ws.send_json({"type": "ctl_wire", "action": "add", **wire})
    try:
        await recv_type(ws, "error", 3)
        return True, st
    except TimeoutError:
        st = await poke_state(ws, st)
        return ({"from": wire["from"], "to": wire["to"]}
                not in st["ctl_wires"]), st


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
            check("state carries logics + relays keys",
                  "logics" in st and "relays" in st, str(sorted(st)))
            check("state has NO legacy switches key", "switches" not in st)

            base = {sec: {x["id"] for x in st.get(sec, [])}
                    for sec in ("buttons", "logics", "relays", "tonics")}
            base_wires = [dict(w) for w in st["ctl_wires"]]

            # -- spawn button + logic --------------------------------------
            b1, st = await spawn_and_find(ws, st, "spawn_button", "buttons",
                                          base["buttons"])
            lid, st = await spawn_and_find(ws, st, "spawn_logic", "logics",
                                           base["logics"])
            check("spawned button + logic appear", bool(b1 and lid),
                  str((b1, lid)))
            if not (b1 and lid):
                return 1

            # -- latch + fire toggles the level, gate event rides "midi" ---
            await ws.send_json({"type": "set_button", "id": b1,
                                "latch": True})
            st = await poke_state(ws, st)
            check("latch mode set", (by_id(st["buttons"], b1) or {})
                  .get("latch") is True, str(by_id(st["buttons"], b1)))
            await ws.send_json({"type": "fire_button", "id": b1})
            ev = await recv_type(
                ws, "midi", 4,
                lambda m: (m.get("event") or {}).get("kind") == "gate"
                and m["event"].get("id") == b1)
            check("fire on a latch button broadcasts its gate event hi",
                  ev["event"].get("on") is True, str(ev))
            st = await poke_state(ws, st)     # fire_button never broadcasts
            check("latched level visible in settings.on",
                  (by_id(st["buttons"], b1) or {}).get("on") is True,
                  str(by_id(st["buttons"], b1)))

            # -- named ins: hi through AND with :b unwired stays lo --------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b1, "to": f"{lid}:a"})
            st = await drain_state(ws)
            check("binary wire accepted (button→logic:a)",
                  {"from": b1, "to": f"{lid}:a"} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            st = await poke_state(ws, st)
            check("latched-hi through AND with :b unwired stays lo",
                  logic_out(st, lid) is False, str(by_id(st["logics"], lid)))

            # -- second latched button on :b drives the AND hi -------------
            b2, st = await spawn_and_find(ws, st, "spawn_button", "buttons",
                                          base["buttons"] | {b1})
            check("second button appears", bool(b2), str(b2))
            await ws.send_json({"type": "set_button", "id": b2,
                                "latch": True})
            await ws.send_json({"type": "fire_button", "id": b2})  # b2 hi
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b2, "to": f"{lid}:b"})
            st = await drain_state(ws)
            st = await poke_state(ws, st)
            check("AND out hi with both named ins hi",
                  logic_out(st, lid) is True, str(by_id(st["logics"], lid)))

            # -- steal-on-drop: a second source on :a replaces the wire ----
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b2, "to": f"{lid}:a"})
            st = await drain_state(ws)
            check("steal-on-drop replaced the :a wire in ctl_wires",
                  {"from": b1, "to": f"{lid}:a"} not in st["ctl_wires"]
                  and {"from": b2, "to": f"{lid}:a"} in st["ctl_wires"],
                  str(st["ctl_wires"]))

            # -- bare-id logic dst refused ----------------------------------
            ok, st = await expect_refused(ws, st, {"from": b1, "to": lid})
            check("bare-id logic dst refused", ok)

            # -- momentary: button_down/up round-trip in settings.on -------
            # (b1 is unwired since the steal — mode flip is side-effect-free)
            await ws.send_json({"type": "set_button", "id": b1,
                                "latch": False})
            st = await poke_state(ws, st)
            check("leaving latch drops the level",
                  (by_id(st["buttons"], b1) or {}).get("on") is False,
                  str(by_id(st["buttons"], b1)))
            await ws.send_json({"type": "button_down", "id": b1})
            st = await poke_state(ws, st)     # hot path: no broadcast
            check("button_down: momentary level hi in settings.on",
                  (by_id(st["buttons"], b1) or {}).get("on") is True,
                  str(by_id(st["buttons"], b1)))
            await ws.send_json({"type": "button_up", "id": b1})
            st = await poke_state(ws, st)
            check("button_up: momentary level back lo",
                  (by_id(st["buttons"], b1) or {}).get("on") is False,
                  str(by_id(st["buttons"], b1)))

            # -- real module :pwr hold-to-enable (read-and-RESTORE) --------
            mod = next((c for c in st["chain"]
                        if c.get("key") not in (None, "master")
                        and "enabled" in c), None)
            if mod is not None:
                key, was = mod["key"], bool(mod["enabled"])
                await ws.send_json({"type": "ctl_wire", "action": "add",
                                    "from": b1, "to": f"{key}:pwr"})
                st = await drain_state(ws)
                # attach applies the level (b1 lo → disabled), then hold
                await ws.send_json({"type": "button_down", "id": b1})
                st = await poke_state(ws, st)
                en = next(c["enabled"] for c in st["chain"]
                          if c["key"] == key)
                check(":pwr follows the held button (hi → enabled)",
                      en is True, str(en))
                await ws.send_json({"type": "button_up", "id": b1})
                st = await poke_state(ws, st)
                en = next(c["enabled"] for c in st["chain"]
                          if c["key"] == key)
                check(":pwr follows the release (lo → disabled)",
                      en is False, str(en))
                await ws.send_json({"type": "ctl_wire", "action": "remove",
                                    "from": b1, "to": f"{key}:pwr"})
                st = await drain_state(ws)
                if was is not False:          # restore the pre-probe enable
                    await ws.send_json({"type": "set_enabled", "key": key,
                                        "enabled": was})
                    st = await drain_state(ws)
            else:
                print("skip  module :pwr follow (no chain module on rig)")

            # -- deriver trig: the AND's rising edge commits a tonic -------
            tid, st = await spawn_and_find(ws, st, "spawn_tonic", "tonics",
                                           base["tonics"])
            check("tonic deriver appears", bool(tid), str(tid))
            # AND is hi (b2 on both ins) — take it lo BEFORE attaching so
            # the flip below is a genuine lo→hi edge (attach is no edge)
            await ws.send_json({"type": "fire_button", "id": b2})   # b2 lo
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": lid, "to": tid})
            st = await drain_state(ws)
            check("logic→deriver trig wire accepted",
                  {"from": lid, "to": tid} in st["ctl_wires"],
                  str(st["ctl_wires"]))
            # evidence path: keys→tonic, then HOLD a note so Layer 2 has a
            # held set at commit time
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": "keys", "to": tid})
            st = await drain_state(ws)
            await ws.send_json({"type": "note_on", "note": 60})
            await asyncio.sleep(0.2)          # let the evidence register
            await ws.send_json({"type": "fire_button", "id": b2})   # lo→hi
            root = None
            for _ in range(10):               # bounded poll for the commit
                st = await poke_state(ws, st)
                root = (by_id(st["tonics"], tid) or {}).get("root")
                if root is not None:
                    break
            check("AND rising edge commits the tonic (root non-null)",
                  root is not None, str(by_id(st["tonics"], tid)))
            # release + silence, then detach the deriver's wires
            await ws.send_json({"type": "note_off", "note": 60})
            await ws.send_json({"type": "all_notes_off"})
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": lid, "to": tid})
            st = await drain_state(ws)
            await ws.send_json({"type": "ctl_wire", "action": "remove",
                                "from": "keys", "to": tid})
            st = await drain_state(ws)

            # -- relay: binary circuit closed passes / open blocks ---------
            rid, st = await spawn_and_find(ws, st, "spawn_relay", "relays",
                                           base["relays"])
            check("relay appears (open by default)",
                  bool(rid) and (by_id(st["relays"], rid) or {})
                  .get("closed") is False, str(by_id(st["relays"], rid)))
            # b2 is HI here (the deriver flip left the latch on) — it is
            # the level the relay circuit will carry
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b2, "to": f"{rid}:1"})
            st = await drain_state(ws)
            check("button→relay:1 accepted (binary claim)",
                  {"from": b2, "to": f"{rid}:1"} in st["ctl_wires"]
                  and ((by_id(st["relays"], rid) or {}).get("circuits") or {})
                  .get("1", {}).get("kind") == "binary",
                  str(by_id(st["relays"], rid)))
            # relay:1 → logic:a (steals :a from b2); :b stays b2 (hi)
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": f"{rid}:1", "to": f"{lid}:a"})
            st = await drain_state(ws)
            st = await poke_state(ws, st)
            check("open relay blocks the level (AND :a lo → out lo)",
                  logic_out(st, lid) is False, str(by_id(st["logics"], lid)))
            await ws.send_json({"type": "set_relay", "id": rid,
                                "closed": True})
            st = await poke_state(ws, st)
            check("closed relay passes the level (out hi)",
                  logic_out(st, lid) is True, str(by_id(st["logics"], lid)))
            await ws.send_json({"type": "set_relay", "id": rid,
                                "closed": False})
            st = await poke_state(ws, st)
            check("re-opening blocks it again (out lo)",
                  logic_out(st, lid) is False, str(by_id(st["logics"], lid)))

            # -- relay:ctl accepts a binary wire ----------------------------
            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": b2, "to": f"{rid}:ctl"})
            st = await drain_state(ws)
            check("binary wire into relay:ctl accepted",
                  {"from": b2, "to": f"{rid}:ctl"} in st["ctl_wires"],
                  str(st["ctl_wires"]))

            # -- cleanup: remove everything, poll back to baseline ----------
            await ws.send_json({"type": "remove_relay", "id": rid})
            await ws.send_json({"type": "remove_tonic", "id": tid})
            await ws.send_json({"type": "remove_logic", "id": lid})
            await ws.send_json({"type": "remove_button", "id": b1})
            await ws.send_json({"type": "remove_button", "id": b2})
            # broadcasts from earlier messages may still be queued — poll
            # past stale states (bounded), don't trust the first one
            ok = False
            for _ in range(10):
                st = await poke_state(ws, st)
                ok = all({x["id"] for x in st.get(sec, [])} == base[sec]
                         for sec in ("buttons", "logics", "relays", "tonics"))
                ok = ok and [dict(w) for w in st["ctl_wires"]] == base_wires
                if ok:
                    break
            check("cleanup: nodes + wires back to baseline", ok,
                  str((st.get("buttons"), st.get("logics"),
                       st.get("relays"), st.get("tonics"),
                       st.get("ctl_wires"))))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
