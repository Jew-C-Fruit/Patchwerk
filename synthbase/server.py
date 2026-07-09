"""Web GUI server: serves gui/index.html and a websocket control channel.

Protocol (JSON messages):

  client -> server:
    {"type": "set_param", "key": "lowpass", "name": "cutoff", "unit": 0.7}
    {"type": "set_enabled", "key": "echo", "enabled": false}   (module bypass)
    {"type": "set_volume", "volume": 0.8}
    {"type": "note_on", "note": 60} / {"type": "note_off", "note": 60}
    {"type": "all_notes_off"}
    {"type": "select_patch", "patch": "demo"}
    {"type": "set_devices", "input": "MacBook Pro Microphone", "output": null}
    {"type": "set_midi", "port": "CP88/CP73 Port1", "enabled": true}
    {"type": "set_arp", "enabled": true, "rate": 8, "gate": 0.6, "octaves": 2, "pattern": "updown"}

  server -> client:
    {"type": "state", ...full snapshot...}       (on connect and after changes)
    {"type": "meters", "out": [l, r], "in": x}   (~15 Hz)
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import WSMsgType, web

from .app import SynthApp

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
METER_INTERVAL = 1 / 15


class GuiServer:
    def __init__(self, app: SynthApp, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.synth = app
        self.host = host
        self.port = port
        self.clients: set[web.WebSocketResponse] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.web_app = web.Application()
        self.web_app.router.add_get("/", self._index)
        self.web_app.router.add_get("/ws", self._ws)

    # -- http ----------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(GUI_DIR / "index.html")

    # -- websocket --------------------------------------------------------------

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json({"type": "state", **self.synth.state()})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    await self._handle(json.loads(msg.data), sender=ws)
                except Exception as exc:  # noqa: BLE001 — GUI must never crash the synth
                    await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            self.clients.discard(ws)
            # If the controlling window went away, silence held notes.
            if not self.clients:
                self.synth.all_notes_off()
        return ws

    async def _handle(self, m: dict, sender=None) -> None:
        t = m.get("type")
        loop = asyncio.get_running_loop()
        if t == "set_param":
            self.synth.set_param_unit(m["key"], m["name"], m["unit"])
            # echo to *other* clients only, so the sender's slider isn't fought
            await self._broadcast_state(exclude=sender)
        elif t == "set_enabled":
            self.synth.set_enabled(m["key"], m["enabled"])
            await self._broadcast_state(exclude=sender)
        elif t == "set_arp":
            self.synth.set_arp(
                enabled=m.get("enabled"), rate=m.get("rate"), gate=m.get("gate"),
                octaves=m.get("octaves"), pattern=m.get("pattern"),
            )
            await self._broadcast_state(exclude=sender)
        elif t == "set_midi":
            self.synth.set_midi(m.get("port"), m.get("enabled", True))
            await self._broadcast_state()
        elif t == "set_volume":
            self.synth.set_volume(m["volume"])
        elif t == "note_on":
            self.synth.note_on(m["note"], m.get("velocity", 100))
        elif t == "note_off":
            self.synth.note_off(m["note"])
        elif t == "all_notes_off":
            self.synth.all_notes_off()
        elif t == "select_patch":
            # rebuilds nodes; quick, but keep the event loop responsive
            await loop.run_in_executor(None, self.synth.select_patch, m["patch"])
            await self._broadcast_state()
        elif t == "set_devices":
            # full engine reboot — takes a second or two
            await loop.run_in_executor(
                None, self.synth.set_devices, m.get("input"), m.get("output")
            )
            await self._broadcast_state()
        else:
            raise ValueError(f"unknown message type {t!r}")

    async def _broadcast(self, payload: dict, exclude=None) -> None:
        dead = []
        for ws in self.clients:
            if ws is exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def _broadcast_state(self, exclude=None) -> None:
        await self._broadcast({"type": "state", **self.synth.state()}, exclude=exclude)

    # -- physical controls -> GUI ------------------------------------------------

    def _midi_event_from_thread(self, event: dict) -> None:
        """Called on the MIDI thread; hop onto the server's event loop."""
        if self.loop is not None and self.clients:
            asyncio.run_coroutine_threadsafe(self._push_midi(event), self.loop)

    async def _push_midi(self, event: dict) -> None:
        # A bound CC also updates that one slider (virtual follows physical).
        if event.get("kind") == "cc" and event.get("bound"):
            key, name = event["bound"]
            await self._broadcast({
                "type": "param", "key": key, "name": name,
                "value": event["value"], "unit": event["unit"],
            })
        await self._broadcast({"type": "midi", "event": event})

    # -- meters ---------------------------------------------------------------

    async def _meter_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self.clients:
                levels = await loop.run_in_executor(None, self.synth.levels)
                await self._broadcast({"type": "meters", **levels})
            await asyncio.sleep(METER_INTERVAL)

    # -- run -------------------------------------------------------------------

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.synth.on_midi_event = self._midi_event_from_thread
        runner = web.AppRunner(self.web_app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        print(f"[gui] open http://{self.host}:{self.port}")
        meter_task = asyncio.create_task(self._meter_loop())
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            meter_task.cancel()
            await runner.cleanup()
