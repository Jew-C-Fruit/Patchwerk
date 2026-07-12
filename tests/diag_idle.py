"""What is sounding at idle? Silence everything, print full state, then
pause chain sources one at a time and watch the output meter."""

import asyncio, json, sys
import aiohttp

URL = "http://127.0.0.1:8765/ws"


async def peak(ws, seconds):
    p, loop = 0.0, asyncio.get_event_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
        if m["type"] == "meters":
            p = max(p, m["out"][0], m["out"][1])
    return p


async def state_of(ws):
    while True:
        m = json.loads((await asyncio.wait_for(ws.receive(), 10)).data)
        if m["type"] == "state":
            return m


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL) as ws:
            st = await state_of(ws)
            for t in ("all_notes_off",):
                await ws.send_json({"type": t})
            await ws.send_json({"type": "set_drums", "enabled": False})
            await ws.send_json({"type": "set_transport", "click": False})
            await ws.send_json({"type": "set_drone", "enabled": False})
            await ws.send_json({"type": "set_looper", "action": "stop"})
            await asyncio.sleep(2.0)
            st = await state_of_or_current(ws) if False else st
            print("patch:", st["patch"], " volume:", st["volume"])
            print("chain:", [(c["key"], c["kind"], "on" if c["enabled"] else "OFF",
                              "svc" if c.get("service") else "")
                             for c in st["chain"]])
            print("looper:", st["looper"]["state"], " drone:",
                  st["drone"]["enabled"], " arp:", st["arp"]["enabled"],
                  " transport running:", st["transport"].get("running"))
            base = await peak(ws, 2.0)
            print(f"idle peak (all silenced): {base:.5f}")
            sources = [c["key"] for c in st["chain"]
                       if c["kind"] == "source" and not c.get("service")
                       and c["enabled"]]
            for key in sources:
                await ws.send_json({"type": "set_enabled", "key": key,
                                    "enabled": False})
                await asyncio.sleep(0.8)
                p = await peak(ws, 1.5)
                print(f"  with {key} paused: {p:.5f}")
                await ws.send_json({"type": "set_enabled", "key": key,
                                    "enabled": True})
            # generator check with sources state printed
            await ws.send_json({"type": "note_on", "note": 60})
            await asyncio.sleep(0.3)
            p = await peak(ws, 1.2)
            await ws.send_json({"type": "note_off", "note": 60})
            print(f"note-on peak: {p:.5f}")
            return 0


sys.exit(asyncio.run(main()))
