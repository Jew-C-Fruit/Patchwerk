"""Mac-only live probe: the Instrument card's in-place synth swap (item 2).

Spawns an fm_bell, wires it to master, plays it through the default mono
voice, swaps it to pluck MID-SESSION with swap_synth, and checks: the
instance id survives, the type/params change, the wire stays, the swapped
voice still sounds (meter tap — no mic), and shared param values carry.
Cleans up completely (instance removed, voice target + volume restored).

    .venv/bin/python tests/probe_swap_ws.py
"""
import asyncio
import json
import os
import time

import aiohttp

PORT = int(os.environ.get("SS_PORT", "8765"))
URL = f"ws://127.0.0.1:{PORT}/ws"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    (PASS if cond else FAIL).append(name)


async def main() -> None:
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(URL)
    state = {}
    meters = []

    async def pump(timeout: float) -> None:
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            try:
                msg = await ws.receive(timeout=left)
            except asyncio.TimeoutError:
                return
            if msg.type != aiohttp.WSMsgType.TEXT:
                return
            m = json.loads(msg.data)
            if m.get("type") == "meters":
                meters.append(m.get("out") or [0, 0])
            elif m.get("type") == "state":
                state.clear()
                state.update(m)

    async def send(obj: dict) -> None:
        await ws.send_str(json.dumps(obj))

    def entry(key):
        return next((c for c in state.get("chain", []) if c["key"] == key),
                    None)

    await pump(1.5)
    prev_volume = state.get("volume", 0.8)
    prev_target = state.get("voice_target")
    prev_keys = {c["key"] for c in state.get("chain", [])}
    await send({"type": "set_volume", "volume": 0.8})

    # -- spawn + wire + play the bell -----------------------------------------
    await send({"type": "spawn_module", "key": "fm_bell"})
    await pump(1.0)
    iid = next((k for k in ({c["key"] for c in state["chain"]} - prev_keys)
                if k.split(".")[0] == "fm_bell"), None)
    check("fm_bell spawned", iid is not None, str(state.get("chain")))
    if iid is None:
        await ws.close(); await session.close(); return
    await send({"type": "graph_wire", "action": "add",
                "from": iid, "to": "master"})
    await send({"type": "set_voice_target", "key": iid, "voice": "voice"})
    await pump(0.6)
    meters.clear()
    await send({"type": "note_on", "note": 60, "velocity": 110})
    await pump(1.2)
    await send({"type": "note_off", "note": 60})
    bell_peak = max((max(f) for f in meters), default=0.0)
    check("bell audible through the meter tap", bell_peak > 0.01,
          f"peak={bell_peak:.4f}")

    # -- THE SWAP -------------------------------------------------------------
    await send({"type": "swap_synth", "id": iid, "key": "pluck"})
    await pump(1.0)
    e = entry(iid)
    check("swap keeps the instance id in the chain", e is not None,
          str([c['key'] for c in state.get('chain', [])]))
    check("swapped entry runs type=pluck", e and e["type"] == "pluck", str(e and e["type"]))
    check("swapped entry shows Pluck params (damp present)",
          e and "damp" in e["params"], str(e and list(e["params"])))
    # the voice played note 60 into the bell, so the instance's LIVE freq is
    # middle C — the swap must carry that current value (not the default).
    # NB state can't be read pre-swap: set_param/voice steering never
    # broadcast, so the swap's own broadcast is the first honest readout.
    check("shared param freq carried the LIVE value (note 60 = C4)",
          e and abs(e["params"]["freq"]["value"] - 261.6255653) < 0.01,
          str(e and e["params"]["freq"]["value"]))
    wires = state.get("wires", [])
    check("audio wire survived the swap",
          any(w.get("from") == iid and w.get("to") == "master"
              for w in wires), str(wires))
    check("voice still targets the id",
          state.get("voice_target") == iid, str(state.get("voice_target")))

    meters.clear()
    await send({"type": "note_on", "note": 60, "velocity": 110})
    await pump(1.2)
    await send({"type": "note_off", "note": 60})
    pluck_peak = max((max(f) for f in meters), default=0.0)
    check("swapped pluck audible (makeup gain live)", pluck_peak > 0.01,
          f"peak={pluck_peak:.4f}")

    # -- swap back, then clean up ---------------------------------------------
    await send({"type": "swap_synth", "id": iid, "key": "fm_bell"})
    await pump(0.8)
    e = entry(iid)
    check("swap back to fm_bell", e and e["type"] == "fm_bell",
          str(e and e["type"]))
    await send({"type": "edit_chain", "action": "remove", "key": iid})
    await pump(0.8)
    check("cleanup: instance removed", entry(iid) is None, "")
    if prev_target:
        await send({"type": "set_voice_target", "key": prev_target,
                    "voice": "voice"})
    await send({"type": "set_volume", "volume": prev_volume})
    await pump(0.5)
    leftover = {c["key"] for c in state.get("chain", [])} - prev_keys
    check("cleanup: chain back to baseline", not leftover, str(leftover))

    await ws.close()
    await session.close()
    print(f"\n{'PASS' if not FAIL else 'FAIL'} — {len(FAIL)} failures")


if __name__ == "__main__":
    asyncio.run(main())
