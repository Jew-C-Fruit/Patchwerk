"""Reproduce the looper scream with meter sampling + node-tree capture."""
import asyncio, json
import aiohttp

async def sample(ws, seconds, tag, out):
    loop = asyncio.get_event_loop()
    end = loop.time() + seconds
    peaks = []
    while loop.time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
        if m["type"] == "meters":
            peaks.append(round(max(m["out"]), 3))
        elif m["type"] == "midi" and m["event"].get("kind") == "looper":
            out.append(f"  state -> {m['event']['state']}")
    out.append(f"{tag}: peaks={peaks[:20]}")

async def main():
    out = []
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8765/ws") as ws:
            await ws.receive()
            await ws.send_json({"type": "set_volume", "volume": 0.2})
            await ws.send_json({"type": "set_drums", "enabled": False})
            await ws.send_json({"type": "set_drone", "enabled": False})
            await ws.send_json({"type": "set_looper", "action": "clear"})
            await asyncio.sleep(1)
            await sample(ws, 1.5, "baseline(quiet)", out)
            await ws.send_json({"type": "set_looper", "action": "record",
                                "bars": 1, "level": 0.9, "overdub": False})
            await sample(ws, 6.0, "after-rec", out)
            await ws.send_json({"type": "set_looper", "action": "clear"})
    print("\n".join(out))

asyncio.run(main())
