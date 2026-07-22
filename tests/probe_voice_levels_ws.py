"""Mac-only live probe: measure the four Instrument voices' output levels.

Spawns fm_bell / pluck / wind / wobble_saw one at a time, wires each to
master, gates it through the default mono voice (note_on 60), and samples
the {"type": "meters"} stream for the peak master amplitude. No microphone
involved — meters are internal DSP taps (Amplitude.kr on the master bus).

Cleans up after itself: removes every spawned instance, restores the
default voice's previous target, master volume, and transport state.
Prints one line per voice plus a summary; exits 0 always (it's a
measurement, not a gate).

    .venv/bin/python tests/probe_voice_levels_ws.py          # default port 8765
    SS_PORT=8765 .venv/bin/python tests/probe_voice_levels_ws.py
"""
import asyncio
import json
import os
import sys
import time

import aiohttp

PORT = int(os.environ.get("SS_PORT", "8765"))
URL = f"ws://127.0.0.1:{PORT}/ws"
VOICES = ["fm_bell", "pluck", "wind", "wobble_saw"]
NOTE = 60
GATE_SECS = 2.0        # sample window while the note is held
TAIL_SECS = 1.0        # let releases die before the next voice


async def main() -> None:
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(URL)
    state = {}
    meters = []          # rolling [l, r] frames

    async def pump(timeout: float) -> None:
        """Drain messages for `timeout` wall-clock seconds (bounded)."""
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

    def chain_keys() -> set[str]:
        return {c["key"] for c in state.get("chain", [])}

    # -- capture what we must restore -----------------------------------------
    await pump(1.5)   # server pushes full state on connect
    prev_volume = state.get("volume", 0.8)
    prev_target = state.get("voice_target")
    prev_keys = chain_keys()
    tr = state.get("transport", {}) or {}
    prev_click = tr.get("click")

    await send({"type": "set_volume", "volume": 0.8})
    if prev_click:
        await send({"type": "set_transport", "click": False})  # keep clicks out of the meter

    results = {}
    for vkey in VOICES:
        await send({"type": "spawn_module", "key": vkey})
        await pump(1.0)
        new = sorted(chain_keys() - prev_keys - set(results.get("_ids", [])))
        cand = [k for k in new if k.split(".")[0] == vkey]
        if not cand:
            print(f"  {vkey}: SPAWN FAILED (no new instance in state)")
            continue
        iid = cand[0]
        results.setdefault("_ids", []).append(iid)
        await send({"type": "graph_wire", "action": "add",
                    "from": iid, "to": "master"})
        await send({"type": "set_voice_target", "key": iid, "voice": "voice"})
        await pump(0.5)
        meters.clear()
        await send({"type": "note_on", "note": NOTE, "velocity": 110})
        await pump(GATE_SECS)
        await send({"type": "note_off", "note": NOTE})
        peak = max((max(f) for f in meters), default=0.0)
        # mean of the loudest half of frames — steadier than a single peak
        tops = sorted((max(f) for f in meters), reverse=True)
        body = sum(tops[: max(1, len(tops) // 2)]) / max(1, len(tops) // 2)
        results[vkey] = {"id": iid, "peak": peak, "body": body,
                         "frames": len(meters)}
        print(f"  {vkey:12s} peak={peak:.4f} body={body:.4f} "
              f"({len(meters)} meter frames)")
        await pump(TAIL_SECS)

    # -- cleanup ---------------------------------------------------------------
    for iid in results.get("_ids", []):
        await send({"type": "edit_chain", "action": "remove", "key": iid})
        await pump(0.3)
    if prev_target:
        await send({"type": "set_voice_target", "key": prev_target,
                    "voice": "voice"})
    await send({"type": "set_volume", "volume": prev_volume})
    if prev_click:
        await send({"type": "set_transport", "click": True})
    await pump(1.0)
    leftover = chain_keys() - prev_keys
    print(f"cleanup: leftover spawned instances = {sorted(leftover) or 'none'}")

    ref = max((v["body"] for k, v in results.items() if k != "_ids"),
              default=0.0)
    print("\nsummary (body level, gain-to-match-loudest):")
    for vkey in VOICES:
        r = results.get(vkey)
        if not r:
            continue
        gain = (ref / r["body"]) if r["body"] > 1e-6 else float("inf")
        print(f"  {vkey:12s} body={r['body']:.4f}  x{gain:.2f} to match")

    await ws.close()
    await session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"PROBE ERROR: {exc!r}")
        sys.exit(0)
