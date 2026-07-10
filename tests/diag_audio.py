import asyncio, json, statistics
import aiohttp

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("http://127.0.0.1:8765/ws") as ws:
            st = json.loads((await ws.receive()).data)
            print("in:", st["current_input"], "| out:", st["current_output"],
                  "| input_enabled:", st["input_enabled"], "| note:", st["boot_note"])
            await ws.send_json({"type": "set_drums", "enabled": True})
            await ws.send_json({"type": "set_volume", "volume": 0.4})
            outs, ins = [], []
            loop = asyncio.get_event_loop()
            end = loop.time() + 3
            while loop.time() < end:
                m = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
                if m["type"] == "meters":
                    outs.append(max(m["out"])); ins.append(m.get("in") or 0)
            await ws.send_json({"type": "set_drums", "enabled": False})
            print("engine OUT mean=%.4f max=%.4f" % (statistics.mean(outs), max(outs)))
            print("mic IN    mean=%.4f max=%.4f" % (statistics.mean(ins), max(ins)))

asyncio.run(main())
