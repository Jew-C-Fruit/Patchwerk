"""Mixed-source wiring check: generators must stay audible with audio_in
in the chain (regression: fresh bus per source orphaned everything upstream).

Runs against the live server via websocket. Adds audio_in to the chain,
plays a note on the generator, and asserts the OUTPUT meter moves. Then
removes audio_in and restores state.
"""

import asyncio, json, sys
import aiohttp

URL = "http://127.0.0.1:8765/ws"


async def sample_out(ws, seconds):
    peak, loop = 0.0, asyncio.get_event_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
        if m["type"] == "meters":
            peak = max(peak, m["out"][0], m["out"][1])
    return peak


async def state_of(ws):
    while True:
        m = json.loads((await asyncio.wait_for(ws.receive(), 10)).data)
        if m["type"] == "state":
            return m


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(URL) as ws:
            st = await state_of(ws)
            chain = [c["key"] for c in st["chain"] if not c.get("service")]
            had_audio_in = "audio_in" in chain
            gen = next((c["key"] for c in st["chain"]
                        if c["kind"] == "source" and c["key"] != "audio_in"
                        and not c.get("service")), None)
            if gen is None:
                print("SKIP: no generator source in chain")
                return 0
            vol0 = st["volume"]
            await ws.send_json({"type": "set_volume", "volume": 0.0})
            if not had_audio_in:
                await ws.send_json({"type": "edit_chain", "action": "add",
                                    "key": "audio_in"})
                await state_of(ws)
            # quiet first — pause audio_in so live mic bleed can't mask the
            # measurement (wiring is fixed at build time; pause only mutes)
            vol = vol0
            was_drone = st.get("drone", {}).get("enabled")
            was_looping = st.get("looper", {}).get("state") in (
                "playing", "overdubbing")
            await ws.send_json({"type": "set_enabled", "key": "audio_in",
                                "enabled": False})
            await ws.send_json({"type": "all_notes_off"})
            await ws.send_json({"type": "set_drums", "enabled": False})
            await ws.send_json({"type": "set_transport", "click": False})
            await ws.send_json({"type": "set_drone", "enabled": False})
            await ws.send_json({"type": "set_looper", "action": "stop"})
            await asyncio.sleep(3.5)  # let echo/reverb tails of mic bleed die
            await ws.send_json({"type": "set_volume", "volume": 0.5})
            await asyncio.sleep(0.5)
            base = await sample_out(ws, 1.5)
            # play the generator with audio_in occupying its chain slot
            await ws.send_json({"type": "note_on", "note": 60})
            await asyncio.sleep(0.3)
            loud = await sample_out(ws, 1.5)
            await ws.send_json({"type": "note_off", "note": 60})
            await ws.send_json({"type": "all_notes_off"})
            await ws.send_json({"type": "set_volume", "volume": vol})
            if was_drone:
                await ws.send_json({"type": "set_drone", "enabled": True})
            if was_looping:
                await ws.send_json({"type": "set_looper", "action": "play"})
            if not had_audio_in:
                await ws.send_json({"type": "edit_chain", "action": "remove",
                                    "key": "audio_in"})
            else:
                await ws.send_json({"type": "set_enabled", "key": "audio_in",
                                    "enabled": True})
            print(f"out peak with audio_in in chain: idle={base:.5f} "
                  f"note={loud:.5f} gen={gen}")
            if loud > max(base * 2, 0.015):
                print("GENERATOR AUDIBLE WITH AUDIO_IN: PASS")
                return 0
            print("FAIL: generator silent with audio_in in the chain")
            return 1


sys.exit(asyncio.run(main()))
