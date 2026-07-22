"""Web GUI server: serves gui/index.html and a websocket control channel.

Protocol (JSON messages):

  client -> server (module/instance "key"s are INSTANCE ids in v5 —
  "lowpass", "lowpass.2", ...; legacy type keys resolve to the FIRST
  instance of that type):
    {"type": "set_param", "key": "lowpass.2", "name": "cutoff", "unit": 0.7}
    {"type": "set_enabled", "key": "echo", "enabled": false}   (module bypass)
    {"type": "set_volume", "volume": 0.8}
    {"type": "note_on", "note": 60} / {"type": "note_off", "note": 60}
    {"type": "all_notes_off"}
    {"type": "select_patch", "patch": "demo"}
    {"type": "set_devices", "input": "MacBook Pro Microphone", "output": null}
    {"type": "set_midi", "port": "CP88/CP73 Port1", "enabled": true}
    {"type": "set_arp", "enabled": true, "division": "1/8", "gate": 0.6, "octaves": 2, "pattern": "updown"}
    {"type": "set_transport", "bpm": 110, "beats_per_bar": 4, "click": true}
    {"type": "set_drone", "enabled": true, "every": "1 bar", "octave": 2}
        (LEGACY: maps onto a tonic-deriver + drone-instance pair)
    {"type": "graph_wire", "action": "add"|"remove", "from": "pluck", "to": "echo"|"master"}
    {"type": "ctl_wire", "action": "add"|"remove", "from": "keys", "to": "arp"}
        (control-plane wiring among keys/arp/deck/voice ids/tonic ids/drone
         instance ids — the graph IS the note router; drones are MONO ctl
         note-sinks since the drone rework; set_looper's old "position" is
         accepted and ignored)
    {"type": "spawn_module", "key": "reverb"}      (key = module TYPE; adds a
         fresh instance — duplicates allowed — audio out unconnected)
    {"type": "spawn_voice"} / {"type": "remove_voice", "id": "voice.2"}
    {"type": "spawn_tonic"} / {"type": "remove_tonic", "id": "tonic.2"}
    {"type": "set_tonic", "id": "tonic", "every": "1 bar", "octave": 2,
     "memory": 6.0, "stickiness": 1.25, "bass": 0.06, "listening": "triadic"}
        (the ESTIMATOR deriver: statistical, settle-and-land; its analysis
         — weights/scores/leading/confidence — broadcasts ~5 Hz as
         {"type": "deriver", "id", ...} for the card histogram)
    {"type": "spawn_literal"} / {"type": "remove_literal", "id": "literal.2"}
    {"type": "set_literal", "id": "literal", "every": "immediate",
     "extract": "lowest-held", "place": "absolute", "fold_octave": 3,
     "transpose": 0, "hold_on_empty": true}
        (the LITERAL deriver: deterministic, zero-lag extract×place)
    {"type": "spawn_button"} / {"type": "remove_button", "id": "button.2"}
    {"type": "set_button", "id": "button", "binding": {"kind": "key",
     "code": "KeyN"} | {"kind": "cc", "cc": 20} | null, "armed": true}
        (armed = pairing mode: the next NON-TONAL input — a MIDI CC server-
         side, an unassigned computer key client-side — becomes the binding;
         MIDI note messages can never bind or fire)
    {"type": "fire_button", "id": "button"}   (manual click / bound key)
    {"type": "spawn_clock"} / {"type": "remove_clock", "id": "clock.2"}
    {"type": "set_clock", "id": "clock", "division": "1/4"}
        (transport-locked ping every division; ping wires ride ctl_wire
         with the kind inferred from the button/clock source endpoint —
         ping-outs land ONLY on trigger-ins, e.g. a deriver)
    {"type": "spawn_keyshift"} / {"type": "remove_keyshift", "id": "keyshift.2"}
    {"type": "set_keyshift", "id": "keyshift", "key": 7, "length": 8,
     "steps": [0, null, 7, ...]}   (key/steps = pitch-class distance from C;
        lanes wire via ctl_wire endpoints "keyshift:1".."keyshift:4")
    {"type": "set_voice_target", "key": "pluck", "voice": "voice.2"}
        (re-aim a mono voice; "voice" when omitted)
    {"type": "set_drums", "target": "echo"|"master"|null}  (drums audio out routing)

  server -> client:
    {"type": "state", ...full snapshot...}       (on connect and after changes)
    {"type": "meters", "out": [l, r], "in": x}   (~15 Hz)
    {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from aiohttp import WSMsgType, web

from .app import SynthApp

GUI_DIR = Path(__file__).resolve().parent.parent / "gui"
METER_INTERVAL = 1 / 20


class GuiServer:
    def __init__(self, app: SynthApp, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.synth = app
        self.host = host
        self.port = port
        self.clients: set[web.WebSocketResponse] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._scope_inflight: set[str] = set()  # keys with a capture in flight
        self.web_app = web.Application()
        self.web_app.router.add_get("/", self._index)
        self.web_app.router.add_get("/blocks", self._blocks)
        self.web_app.router.add_post("/restart", self._restart)
        self.web_app.router.add_get("/ws", self._ws)

    # -- http ----------------------------------------------------------------

    async def _index(self, request: web.Request) -> web.FileResponse:
        # blocks IS the UI. flex + the original are ARCHIVED under gui/legacy/
        # (kept in the repo for reference, not served, not part of releases)
        return web.FileResponse(
            GUI_DIR / "blocks.html", headers={"Cache-Control": "no-store"},
        )

    async def _restart(self, request: web.Request) -> web.Response:
        """FULL backend reload: snapshot everything performable + the wiring,
        re-exec this process in place, restore on boot. The GUI's watchdog
        reconnects by itself; layout lives client-side and survives."""
        from . import presets
        try:
            presets.write_resume(self.synth)
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(exc)}, status=500)
        loop = asyncio.get_running_loop()
        loop.call_later(0.4, self._reexec)   # let the response flush first
        return web.json_response({"ok": True})

    def _reexec(self) -> None:
        try:
            if self.synth.engine and self.synth.engine.server:
                self.synth.engine.server.quit()   # scsynth dies with us
        except Exception:  # noqa: BLE001
            pass
        os.execv(sys.executable,
                 [sys.executable, "-u", "-m", "synthbase", *sys.argv[1:]])

    async def _blocks(self, request: web.Request) -> web.FileResponse:
        # /blocks kept as an alias of / (bookmarks, muscle memory)
        return web.FileResponse(
            GUI_DIR / "blocks.html", headers={"Cache-Control": "no-store"},
        )

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
            value = self.synth.set_param_unit(m["key"], m["name"], m["unit"])
            # Tiny targeted echo to *other* clients only — never a full state
            # snapshot (state building is for structural changes, not knob
            # streams; see the audio_devices cache note).
            await self._broadcast(
                {"type": "param", "key": m["key"], "name": m["name"],
                 "value": value, "unit": m["unit"]},
                exclude=sender,
            )
        elif t == "set_enabled":
            self.synth.set_enabled(m["key"], m["enabled"])
            await self._broadcast_state()
        elif t == "edit_chain":
            await loop.run_in_executor(
                None, lambda: self.synth.edit_chain(
                    m["action"], m["key"], m.get("index")))
            await self._broadcast_state()
        elif t == "set_transpose":
            self.synth.set_transpose(m.get("semitones", 0))
            await self._broadcast_state(exclude=sender)
        elif t == "set_drums":
            kw = dict(enabled=m.get("enabled"), patterns=m.get("patterns"),
                      levels=m.get("levels"), to_chain=m.get("to_chain"))
            if "target" in m:   # null is meaningful (= disconnected) — only
                kw["target"] = m["target"]   # forward when explicitly present
            self.synth.set_drums(**kw)
            await self._broadcast_state()
        elif t == "graph_wire":
            await loop.run_in_executor(
                None, lambda: self.synth.graph_wire(
                    m.get("action", "add"), m.get("from"), m.get("to")))
            await self._broadcast_state()
        elif t == "spawn_module":
            await loop.run_in_executor(
                None, lambda: self.synth.spawn_unconnected(m["key"]))
            await self._broadcast_state()
        elif t == "spawn_voice":
            self.synth.spawn_voice()
            await self._broadcast_state()
        elif t == "remove_voice":
            self.synth.remove_voice(m["id"])
            await self._broadcast_state()
        elif t == "spawn_tonic":
            self.synth.spawn_tonic()
            await self._broadcast_state()
        elif t == "remove_tonic":
            self.synth.remove_tonic(m["id"])
            await self._broadcast_state()
        elif t == "spawn_literal":
            self.synth.spawn_literal()
            await self._broadcast_state()
        elif t == "remove_literal":
            self.synth.remove_literal(m["id"])
            await self._broadcast_state()
        elif t == "set_literal":
            self.synth.set_literal(
                m["id"], every=m.get("every"), extract=m.get("extract"),
                place=m.get("place"), fold_octave=m.get("fold_octave"),
                transpose=m.get("transpose"),
                hold_on_empty=m.get("hold_on_empty"))
            await self._broadcast_state(exclude=sender)
        elif t == "set_tonic":
            self.synth.set_tonic(
                m["id"], every=m.get("every"), octave=m.get("octave"),
                memory=m.get("memory"), stickiness=m.get("stickiness"),
                bass=m.get("bass"), listening=m.get("listening"))
            await self._broadcast_state(exclude=sender)
        elif t == "spawn_keyshift":
            self.synth.spawn_keyshift()
            await self._broadcast_state()
        elif t == "remove_keyshift":
            self.synth.remove_keyshift(m["id"])
            await self._broadcast_state()
        elif t == "set_keyshift":
            self.synth.set_keyshift(m["id"], key=m.get("key"),
                                    length=m.get("length"), steps=m.get("steps"))
            # clicking client already painted its card — update the others
            await self._broadcast_state(exclude=sender)
        elif t == "spawn_button":
            self.synth.spawn_button()
            await self._broadcast_state()
        elif t == "remove_button":
            self.synth.remove_button(m["id"])
            await self._broadcast_state()
        elif t == "set_button":
            kw = {}
            if "binding" in m:
                kw["binding"] = m["binding"]
            if "armed" in m:
                kw["armed"] = m["armed"]
            self.synth.set_button(m["id"], **kw)
            await self._broadcast_state(exclude=sender)
        elif t == "fire_button":
            self.synth.fire_button(m["id"])   # hot path: no state broadcast
        elif t == "spawn_clock":
            self.synth.spawn_clock()
            await self._broadcast_state()
        elif t == "remove_clock":
            self.synth.remove_clock(m["id"])
            await self._broadcast_state()
        elif t == "set_clock":
            self.synth.set_clock(m["id"], division=m.get("division"))
            await self._broadcast_state(exclude=sender)
        elif t == "set_voice_target":
            self.synth.set_voice_target(m["key"], m.get("voice", "voice"))
            await self._broadcast_state()
        elif t == "set_looper":
            # "position" from old clients is dropped here — pre/post is wiring
            self.synth.set_looper(action=m.get("action"), bars=m.get("bars"),
                                  level=m.get("level"), overdub=m.get("overdub"))
            await self._broadcast_state()
        elif t == "ctl_wire":
            self.synth.set_ctl_wire(m.get("action", "add"), m.get("from"), m.get("to"))
            await self._broadcast_state()
        elif t == "lfo_assign":
            await loop.run_in_executor(None, lambda: self.synth.lfo_assign(m["key"], m["name"]))
            await self._broadcast_state()
        elif t == "lfo_unassign":
            await loop.run_in_executor(None, lambda: self.synth.lfo_unassign(m["id"]))
            await self._broadcast_state()
        elif t == "lfo_set":
            self.synth.lfo_set(m["id"], rate=m.get("rate"), depth=m.get("depth"),
                               center=m.get("center"), shape=m.get("shape"))
        elif t == "save_preset":
            await loop.run_in_executor(None, self.synth.save_preset, m["name"])
            await self._broadcast_state()
        elif t == "load_preset":
            await loop.run_in_executor(None, self.synth.load_preset, m["name"])
            await self._broadcast_state()
        elif t == "delete_preset":
            self.synth.delete_preset(m["name"])
            await self._broadcast_state()
        elif t == "set_drone":
            self.synth.set_drone(
                enabled=m.get("enabled"), every=m.get("every"), octave=m.get("octave"),
            )
            # broadcast to EVERYONE incl. sender — enabling adds the drone's
            # module card, which the clicking client needs to see too
            await self._broadcast_state()
        elif t == "set_transport":
            self.synth.set_transport(
                bpm=m.get("bpm"), beats_per_bar=m.get("beats_per_bar"),
                click=m.get("click"), accent=m.get("accent"),
                playing=m.get("playing"),
            )
            # broadcast to ALL incl. sender: the play/stop button must flip
            await self._broadcast_state()
        elif t == "set_arp":
            self.synth.set_arp(
                enabled=m.get("enabled"), division=m.get("division"),
                gate=m.get("gate"), octaves=m.get("octaves"),
                pattern=m.get("pattern"),
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
        elif t == "scope":
            # A scope capture BLOCKS (server sync + ~46 ms record window) —
            # awaiting it here would stall the per-socket message loop, so every
            # note/param/edit queued behind a scope poll waits too (audio lags
            # the GUI by the whole backlog). Run it as a background task, and
            # coalesce PER KEY: one capture in flight per scope, a duplicate
            # poll for a key already capturing is dropped. Per-key (not global)
            # so N scopes each get serviced fairly — a global flag starved every
            # scope but the first in each poll burst.
            key = m["key"]
            if key not in self._scope_inflight:
                self._scope_inflight.add(key)
                asyncio.create_task(self._run_scope(key, sender))
        elif t == "sustain":
            # global pedal: the arp latch + every mono voice
            self.synth._keys.set_sustain(bool(m.get("on")))
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

    async def _run_scope(self, key: str, ws) -> None:
        """Background one-shot scope capture; self-clears the busy flag so the
        next poll can start. Errors (dead socket, module gone mid-capture) are
        swallowed — a scope must never wedge the control plane."""
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, self.synth.scope.capture, key)
            await ws.send_json({"type": "scope_data", **data})
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._scope_inflight.discard(key)

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

    def _beat_from_thread(self, bar: int, beat: int) -> None:
        if self.loop is not None and self.clients:
            try:
                loop_phase = self.synth.looper.phase()
            except Exception:  # noqa: BLE001
                loop_phase = None
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "beat", "bar": bar, "beat": beat,
                                 "loop": loop_phase}), self.loop
            )

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
        tick = 0
        while True:
            if self.clients:
                levels = await loop.run_in_executor(None, self.synth.levels)
                await self._broadcast({"type": "meters", **levels})
                tick += 1
                if tick % 4 == 0:  # ~5 Hz
                    # legacy header strip (archived GUIs)
                    tonic = await loop.run_in_executor(None, self.synth.tonic_state)
                    await self._broadcast({"type": "tonic", **tonic})
                    # per-estimator analysis: the card histogram breathes on
                    # this steady tick (weights + scores + leading + committed
                    # + confidence), not only at commit decisions
                    for d in list(self.synth.tonics.values()):
                        try:
                            a = await loop.run_in_executor(None, d.analysis)
                        except Exception:  # noqa: BLE001
                            continue
                        await self._broadcast({"type": "deriver", **a})
            await asyncio.sleep(METER_INTERVAL)

    # -- run -------------------------------------------------------------------

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.synth.on_midi_event = self._midi_event_from_thread
        self.synth.on_beat_event = self._beat_from_thread
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
