"""Live ws probe for the ESTIMATOR REDESIGN (item 7; Mac only, server up).

    python tests/probe_estimator_ws.py

Drives the real server over the websocket and verifies the two-layer
model end to end: duration-weighted evidence (a held note out-weighs a
grace note in the live analysis broadcast), the scale readout appearing
in state + the {type:"deriver"} broadcast, and the INSTANT Layer-2
commit (a held A-minor chord lands root A on the next grid tick — no
settling, no hysteresis). Also round-trips the new knobs (deck_feed,
every="deck"). Restores what it spawned.
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
    """Next broadcast of a given type (optionally filtered)."""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)
        if m["type"] == want and (match is None or match(m)):
            return m
    raise TimeoutError(f"no {want} broadcast")


async def drain_state(ws, timeout=6):
    return await recv_type(ws, "state", timeout)


async def poke_state(ws, st):
    """Force a fresh state broadcast (note events alone don't broadcast)."""
    await ws.send_json({"type": "set_transport",
                        "bpm": st["transport"]["bpm"]})
    return await drain_state(ws)


def tonic_entry(st, tid):
    return next((t for t in st["tonics"] if t["id"] == tid), None)


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(WS) as ws:
            st = await drain_state(ws)
            before = {t["id"] for t in st["tonics"]}

            await ws.send_json({"type": "spawn_tonic"})
            tid = None
            for _ in range(10):
                st = await drain_state(ws)
                new = [t["id"] for t in st["tonics"] if t["id"] not in before]
                if new:
                    tid = new[0]
                    break
            check("spawned deriver appears in state", tid is not None,
                  str(st["tonics"]))
            if tid is None:
                return 1
            t = tonic_entry(st, tid)

            # -- new knob surface ------------------------------------------
            check("settings carry NO stickiness", "stickiness" not in t,
                  str(t))
            check("settings carry deck_feed + scale keys",
                  "deck_feed" in t and "scale" in t, str(t))
            check("everies include 'deck'", "deck" in t.get("everies", []),
                  str(t.get("everies")))

            await ws.send_json({"type": "ctl_wire", "action": "add",
                                "from": "keys", "to": tid})
            await ws.send_json({"type": "set_tonic", "id": tid,
                                "every": "1 beat", "octave": 2})
            st = await drain_state(ws)

            async def note(n, on=True, dwell=0.12):
                await ws.send_json({"type": "note_on" if on else "note_off",
                                    "note": n})
                await asyncio.sleep(dwell)

            # -- duration weighting, live ----------------------------------
            # hold G3 while grace-tapping C#4: the held note must dominate
            # the analysis histogram
            await note(55)                       # G3 down (stays down)
            await note(61); await note(61, False, 0.05)   # C#4 grace
            await asyncio.sleep(1.5)             # G keeps accumulating
            d = await recv_type(ws, "deriver", 6, lambda m: m["id"] == tid)
            w = d.get("weights") or [0.0] * 12
            check("held note out-weighs the grace note (G >> C#)",
                  w[7] > max(w[1] * 2.0, 0.05), str((w[7], w[1])))
            check("deriver broadcast carries a scale field", "scale" in d,
                  str(sorted(d.keys())))
            await note(55, False)
            await ws.send_json({"type": "all_notes_off"})
            await asyncio.sleep(0.4)

            # -- Layer 1 + Layer 2: instant commit of a held chord ---------
            for n in (45, 47, 48, 50, 52, 53, 55):   # A-minor scale walk
                await note(n); await note(n, False, 0.05)
            await note(45); await note(48); await note(52)  # HOLD Am chord
            root = None
            for _ in range(10):                  # grid ticks at 1 beat
                await asyncio.sleep(0.7)
                st = await poke_state(ws, st)
                root = (tonic_entry(st, tid) or {}).get("root")
                if root == "A":
                    break
            check("held Am chord commits root A instantly", root == "A",
                  str(root))
            t = tonic_entry(st, tid)
            check("scale readout populated", bool(t.get("scale")),
                  str(t.get("scale")))
            for n in (45, 48, 52):
                await note(n, False, 0.05)

            # -- root HOLDS with nothing held (no flip-flop) ---------------
            await asyncio.sleep(1.5)             # several empty grid ticks
            st = await poke_state(ws, st)
            check("empty held-set holds the committed root",
                  (tonic_entry(st, tid) or {}).get("root") == "A",
                  str(tonic_entry(st, tid)))

            # -- deck knob round-trips -------------------------------------
            await ws.send_json({"type": "set_tonic", "id": tid,
                                "deck_feed": True})
            st = await drain_state(ws)
            check("deck_feed toggles on",
                  (tonic_entry(st, tid) or {}).get("deck_feed") is True,
                  str(tonic_entry(st, tid)))
            await ws.send_json({"type": "set_tonic", "id": tid,
                                "every": "deck"})
            st = await drain_state(ws)
            check("every='deck' accepted (idles safely without a deck wire)",
                  (tonic_entry(st, tid) or {}).get("every") == "deck",
                  str(tonic_entry(st, tid)))
            await asyncio.sleep(0.8)             # deck timer must not spin/crash
            st = await poke_state(ws, st)
            check("server alive after deck-idle ticks",
                  tonic_entry(st, tid) is not None)

            # -- cleanup ---------------------------------------------------
            await ws.send_json({"type": "all_notes_off"})
            await ws.send_json({"type": "remove_tonic", "id": tid})
            st = await drain_state(ws)
            check("cleanup: deriver removed",
                  {x["id"] for x in st["tonics"]} == before,
                  str(st["tonics"]))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
