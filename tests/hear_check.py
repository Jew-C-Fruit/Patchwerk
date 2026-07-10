"""Audible self-check: does the machine hear itself?

Run against the live server (mic input must be enabled). Measures the input
meter during silence, then with the drum machine + click running, and
asserts the room got louder. Restores previous state afterward.
"""

import asyncio, json, statistics, sys
import aiohttp

async def sample_input(ws, seconds):
    vals = []
    loop = asyncio.get_event_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
        if m["type"] == "meters" and m.get("in") is not None:
            vals.append(m["in"])
    return vals

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8765/ws") as ws:
            st = json.loads((await ws.receive()).data)
            if not st.get("input_enabled"):
                print("SKIP: audio input disabled — cannot listen")
                return 0
            was_drums = st["drums"]["enabled"]; was_click = st["transport"]["click"]
            vol = st["volume"]
            # true silence first: everything off
            await ws.send_json({"type": "set_drums", "enabled": False})
            await ws.send_json({"type": "set_drone", "enabled": False})
            await ws.send_json({"type": "set_transport", "click": False})
            await ws.send_json({"type": "all_notes_off"})
            await asyncio.sleep(1.5)
            silence = await sample_input(ws, 2.0)
            await ws.send_json({"type": "set_volume", "volume": 0.4})
            await ws.send_json({"type": "set_drums", "enabled": True})
            await ws.send_json({"type": "set_transport", "click": True})
            await asyncio.sleep(0.5)
            loud = await sample_input(ws, 3.0)
            await ws.send_json({"type": "set_drums", "enabled": was_drums})
            await ws.send_json({"type": "set_transport", "click": was_click})
            await ws.send_json({"type": "set_volume", "volume": vol})
            # drums are transients: compare PEAKS, not means
            base = max(silence) if silence else 0
            heard = max(loud) if loud else 0
            print(f"input peak: silence={base:.5f}  playing={heard:.5f}  "
                  f"ratio={heard / max(base, 1e-6):.1f}x")
            if heard > max(base * 2.5, 0.02):
                print("HEARD ITSELF: PASS")
                return 0
            print("could not confirm audibly (quiet output or mic gain?)")
            return 1

sys.exit(asyncio.run(main()))
