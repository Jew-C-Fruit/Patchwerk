import asyncio, json, sys
import aiohttp

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8765/ws") as ws:
            st = json.loads((await ws.receive()).data)
            print("chain:", [c["key"] for c in st["chain"]])
            print("drone:", st.get("drone"))
            await ws.send_json({"type": "set_drone", "enabled": True, "every": "1 bar", "octave": 2})
            for _ in range(60):
                m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
                if m["type"] == "state":
                    d = next((c for c in m["chain"] if c["key"] == "drone"), None)
                    print("after enable, drone card:", list(d["params"]) if d else "NO CARD")
                    return

asyncio.run(main())
