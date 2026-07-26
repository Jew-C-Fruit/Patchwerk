"""Web GUI server: serves gui/blocks.html and a websocket control channel.

blocks.html is the ONLY page served — at "/" and at "/blocks" (an alias kept
for bookmarks). The earlier pages are archived under gui/legacy/, unserved
and unmaintained; there is no /legacy route.

Protocol (JSON messages):

  client -> server (module/instance "key"s are INSTANCE ids in v5 —
  "lowpass", "lowpass.2", ...; legacy type keys resolve to the FIRST
  instance of that type):
    {"type": "set_param", "key": "lowpass.2", "name": "cutoff", "unit": 0.7}
    {"type": "set_enabled", "key": "echo", "enabled": false}   (module bypass)
    {"type": "set_volume", "volume": 0.8}
    {"type": "note_on", "note": 60} / {"type": "note_off", "note": 60}
    {"type": "all_notes_off"}
    {"type": "sustain", "on": true}
        (GLOBAL pedal — the arp latch plus every mono voice. Global by
         doctrine, not wire-defined, like panic and the transport.)
    {"type": "set_transpose", "semitones": 0}   (GLOBAL pitch reference)
    {"type": "edit_chain", "action": "add"|"remove"|"move",
     "key": "lowpass.2", "index": 3}
        (linear-chain edit; runs in an executor since it rebuilds nodes.
         Wires survive — removal splice-heals A->X->B to A->B.)
    {"type": "set_looper", "action": "rec"|"play"|"stop"|"clear",
     "bars": 4, "level": 1.0, "overdub": true}
        (the Loop Deck, §9. A "position" key from old clients is accepted
         and IGNORED — pre/post is decided by wiring, not by a field.)
    {"type": "scope", "key": "scope.2"}
        (poll one scope for a capture. A capture BLOCKS — server sync plus
         a ~46 ms record window — so it runs as a background task and is
         COALESCED PER KEY: one capture in flight per scope, and a poll for
         a key already capturing is dropped. Replies {"type": "scope_data"}
         to the REQUESTING socket only, not broadcast.)
    {"type": "save_preset", "name": "..."} /
    {"type": "load_preset", "name": "..."} /
    {"type": "delete_preset", "name": "..."}
        (named presets carry no transport play state by design, so loading
         one mid-performance can neither stop nor start the rig. Only the
         .resume.json restart block carries "running" — see item 32.)
    {"type": "select_patch", "patch": "demo"}
    {"type": "set_devices", "input": "MacBook Pro Microphone", "output": null}
    {"type": "set_midi", "port": "CP88/CP73 Port1", "enabled": true}
    {"type": "set_arp", "enabled": true, "division": "1/8", "gate": 0.6, "octaves": 2, "pattern": "updown"}
    {"type": "set_transport", "bpm": 110, "beats_per_bar": 4, "click": true,
     "accent": true, "playing": true, "downbeat": 0}
        (downbeat = 0-based beat-in-bar carrying the click ACCENT + the
         beat event's downbeat flag; grid math stays beat-0-anchored)
    {"type": "spawn_transport_card", "which": "play"|"tempo"}
    {"type": "remove_transport_card", "which": "play"|"tempo"}
        (item 9: transport CARDS — canvas views of the ONE global
         transport, in lockstep with the top bar via the state broadcast
         (state carries "transport_cards"). The binary wire ins live on
         the GLOBAL transport, not the card: "transport:run"/":click"/
         ":accent" (levels follow) + "transport:tap" (rising edge = tap
         tempo, 0.25–2.0 s intervals, mean of the last 4). Removing a
         card never unwires them.)
    {"type": "set_drone", "enabled": true, "every": "1 bar", "octave": 2}
        (LEGACY: maps onto a tonic-deriver + drone-instance pair)
    {"type": "graph_wire", "action": "add"|"remove", "from": "pluck", "to": "echo"|"master"}
    {"type": "ctl_wire", "action": "add"|"remove", "from": "keys", "to": "arp"}
        (control-plane wiring among keys/arp/deck/voice ids/tonic ids/drone
         instance ids/keyshift lanes/relay circuits — the graph IS the note
         router; drones are MONO ctl note-sinks since the drone rework;
         binary wires ride the same message, kind inferred from the source;
         set_looper's old "position" is accepted and ignored)
    {"type": "spawn_module", "key": "reverb"}      (key = module TYPE; adds a
         fresh instance — duplicates allowed — audio out unconnected)
    {"type": "swap_synth", "id": "fm_bell.2", "key": "pluck"}   (Instrument
         card: swap the instance's module type IN PLACE — same id, buses,
         wires, node order; shared params carry over, the rest reset)
    {"type": "spawn_voice"} / {"type": "remove_voice", "id": "voice.2"}
    {"type": "spawn_poly", "voices": 8}                 (a POLY voice:
         "poly", "poly.2", ... — N notes at once on ONE target source,
         stealing the oldest when full. Removed with remove_voice, and a
         ctl-wire destination exactly like a mono voice.)
    {"type": "set_poly_voices", "id": "poly", "voices": 8}   (1..16; notes
         sounding on slots that go away are closed)
    {"type": "spawn_tonic"} / {"type": "remove_tonic", "id": "tonic.2"}
    {"type": "set_tonic", "id": "tonic", "every": "1 bar", "octave": 2,
     "memory": 6.0, "bass": 0.06, "listening": "triadic", "deck_feed": false}
        (the ESTIMATOR deriver: two-layer scale-aware INSTANT derivation;
         every may also be "deck" (loop-synced commits). Its analysis —
         weights/scores/leading/confidence/scale — broadcasts ~5 Hz as
         {"type": "deriver", "id", ...} for the card histogram + scale
         readout)
    {"type": "spawn_logic"} / {"type": "remove_logic", "id": "logic.2"}
    {"type": "set_logic", "id": "logic",
     "op": "AND"|"OR"|"NOR"|"XOR"|"SR latch"|"T latch"}
        (the BINARY plane: ONE hi/lo signal kind — sources own levels,
         edges derive from level changes; trig-ins fire on RISING edges.
         Binary wires ride ctl_wires, kind inferred from the source
         (button/clock/threshold/logic, binary relay circuits). Logic ins
         are ALWAYS the two single-input endpoints "<id>:a"/"<id>:b"
         for every op (SR latch: a=set, b=reset; T latch: a=toggle on
         RISING edge, b=reset and wins; NOR with one wired leg acts as
         NOT; occupied ins steal; legacy :set/:reset remapped).
         Other dsts: "<key>:pwr", "arp:pwr", "drums:pwr" (level follows),
         "deck:rec|play|stop|clear" + deriver ids (rising edge fires),
         relay circuit ins + "relay:ctl". Level changes emit
         {"kind": "gate", "id", "on"} taps for the GUI LEDs.)
    {"type": "spawn_relay"} / {"type": "remove_relay", "id": "relay.2"}
    {"type": "set_relay", "id": "relay", "closed": true}
        (type-agnostic switched junction, 9 circuits: endpoints
         "relay:1".."relay:9" carry audio (graph_wire), notes or binary
         (ctl_wire) or mod (mod_wire) — a circuit's kind = kind of its
         first wire, and a circuit is 1:1 on BOTH sides (adding steals).
         closed gates flow per kind (opening all_offs note circuits);
         "relay:ctl" is a binary level-in driving closed; set_relay is
         the manual click, last writer wins. Item 25: a claimed AUDIO
         circuit is a permanent lagged-gate synth, so its wires are REAL
         graph wires open or closed — state "wires" broadcasts the STORED
         graph wires, relay endpoints verbatim, and open/close moves only
         the synth's gate param (clickless, ~10 ms lag, tails ring out).)
    {"type": "spawn_literal"} / {"type": "remove_literal", "id": "literal.2"}
    {"type": "set_literal", "id": "literal", "every": "immediate",
     "extract": "lowest-held", "place": "absolute", "fold_octave": 3,
     "transpose": 0, "hold_on_empty": true}
        (the LITERAL deriver: deterministic, zero-lag extract×place)
    {"type": "spawn_button"} / {"type": "remove_button", "id": "button.2"}
    {"type": "set_button", "id": "button", "binding": {"kind": "key",
     "code": "KeyN"} | {"kind": "cc", "cc": 20} | null, "armed": true,
     "latch": false}
        (a binary LEVEL source: momentary (default) = hi while held,
         latch = press toggles. armed = pairing mode: the next NON-TONAL
         input — a MIDI CC server-side, an unassigned computer key
         client-side — becomes the binding; a bound CC follows the level
         (momentary) or toggles on rising crossings (latch))
    {"type": "button_down", "id": "button"} / {"type": "button_up", ...}
        (hold gestures; fire_button stays as click compat: press+release)
    {"type": "fire_button", "id": "button"}   (manual click / bound key)
    {"type": "spawn_clock"} / {"type": "remove_clock", "id": "clock.2"}
    {"type": "set_clock", "id": "clock", "division": "1/4"}
        (transport-locked PULSE (hi-then-lo) every division; wires ride
         ctl_wire with the kind inferred from the source endpoint)
    {"type": "spawn_keyshift"} / {"type": "remove_keyshift", "id": "keyshift.2"}
    {"type": "set_keyshift", "id": "keyshift", "key": 7, "length": 8,
     "steps": [0, null, 7, ...]}   (key/steps = pitch-class distance from C;
        lanes wire via ctl_wire endpoints "keyshift:1".."keyshift:4")
    {"type": "set_voice_target", "key": "pluck", "voice": "voice.2"}
        (re-aim a mono voice; "voice" when omitted)
    {"type": "set_drums", "target": "echo"|"master"|null}  (drums audio out routing)
    {"type": "spawn_lfo"} / {"type": "remove_lfo", "id": "lfo.2"}
    {"type": "lfo_set", "id": "lfo", "rate": 1.0, "depth": 0.25, "shape": 0}
        (routable LFO, item 7: a standalone modulation node — NO center
         knob; each destination orbits its own slider value)
    {"type": "lfo_wire", "action": "add"|"remove", "id": "lfo",
     "key": "lowpass.2", "name": "cutoff"}
        (modulation fan-out: one LFO drives any number of params; a param
         is single-input — wiring an already-mapped param steals it)
    {"type": "mod_wire", "action": "add"|"remove",
     "from": "lfo", "to": "relay:3"}
        (the mod plane THROUGH a relay, 07-24: endpoints are an LFO id, a
         relay circuit ("relay:3", in or out by direction) or a param
         ("lowpass.2:cutoff"). Stored in state.mod_wires and RESOLVED
         through the closed circuits — an open circuit leaves the param on
         its own knob value. Circuits are 1:1 on both sides; adding steals.)
    {"type": "spawn_threshold"} / {"type": "remove_threshold", "id": "threshold.2"}
    {"type": "set_threshold", "id": "threshold", "level": 0.0,
     "hysteresis": 0.02, "mode": "rising"|"falling"|"both"}
        (item 8: CV edge → ping. level/hysteresis are in the LFO's
         NORMALIZED bipolar terms; a server-side Schmidt comparator
         edge-notifies via one SendTrig /tr per crossing — never polled)
    {"type": "threshold_wire", "action": "add"|"remove", "id": "threshold",
     "lfo": "lfo.2"}
        (the CV-in: single-input mod wire from an LFO; the ping-out rides
         ctl_wire like button/clock and lands only on trigger-ins)

  server -> client:
    {"type": "state", ...full snapshot...}       (on connect and after changes)
    {"type": "meters", "out": [l, r], "in": x}   (~15 Hz)
    {"type": "error", "message": "..."}
    {"type": "param", "key": "lowpass.2", "name": "cutoff", ...}
        (a param moved from somewhere OTHER than the sending client — MIDI
         CC, an LFO, a preset load — so knobs track without a full state
         round-trip. The originating socket is excluded.)
    {"type": "beat", "bar": b, "beat": n, ...}   (transport tick)
    {"type": "tonic", ...}      (every 4th meter tick, ~5 Hz — the legacy
                                 header strip: first deriver's normalized
                                 weights + root)
    {"type": "deriver", "id", ...}
        (~5 Hz per tonic deriver: weights/scores/leading/confidence/scale
         for the card histogram + scale readout)
    {"type": "midi", "event": {...}}    (raw MIDI in, for the monitors)
    {"type": "scope_data", ...}
        (one scope capture, sent ONLY to the socket that polled for it)

  server -> client EVENT TAPS — the note/binary/monitor plane. Each is a
  {"kind": ...} envelope, routed by the GUI to whichever monitor is local
  to that path (or to the global feed when the monitor is unwired):
    "tap"                  one per SOURCE FIRE, tagged {"src": <node id>} —
                           one per fire, NOT one per outgoing edge
    "key" / "voiced"       raw controller input / post-voicing notes
    "keyshift"             a key shifter's lane output
    "tonic_out"            a deriver's amber TONIC out
    "loop_note" / "looper" deck replay notes / deck transport state
    "gate"                 a logic gate's output level changed (LED)
    "level"                {"ep", "on"} — the REACTIVE-INDICATOR tap. State
                           applied inside the gate settle pass does not
                           broadcast, so the backend emits this itself; it
                           is what makes the power stripe, the Play/Stop
                           card and the click/accent LEDs react to LOGIC
                           input and not merely to clicks.
    "ping" / "ping_bound"  a trigger fired / a ping endpoint was bound
    "cc" / "bend" / "sustain"    MIDI controller traffic
    "drum_step"            the 16-step drum machine's position

    EVERY silencing path must close its open notes AND their taps (panic,
    arp stop, deck stop, rebuilds, record-window exits): an unpaired "on"
    is both a stuck note and a stuck monitor bar.
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
        elif t == "swap_synth":
            await loop.run_in_executor(
                None, lambda: self.synth.swap_synth(m["id"], m["key"]))
            await self._broadcast_state()
        elif t == "spawn_voice":
            self.synth.spawn_voice()
            await self._broadcast_state()
        elif t == "spawn_poly":
            self.synth.spawn_poly(int(m.get("voices", 8)))
            await self._broadcast_state()
        elif t == "set_poly_voices":
            self.synth.set_poly_voices(m["id"], int(m["voices"]))
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
                memory=m.get("memory"), bass=m.get("bass"),
                listening=m.get("listening"), deck_feed=m.get("deck_feed"))
            await self._broadcast_state(exclude=sender)
        elif t == "spawn_relay":
            self.synth.spawn_relay()
            await self._broadcast_state()
        elif t == "remove_relay":
            self.synth.remove_relay(m["id"])
            await self._broadcast_state()
        elif t == "set_relay":
            self.synth.set_relay(m["id"], closed=m.get("closed"))
            await self._broadcast_state(exclude=sender)
        elif t == "spawn_logic":
            self.synth.spawn_logic()
            await self._broadcast_state()
        elif t == "remove_logic":
            self.synth.remove_logic(m["id"])
            await self._broadcast_state()
        elif t == "set_logic":
            self.synth.set_logic(m["id"], op=m.get("op"))
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
            if "latch" in m:
                kw["latch"] = m.get("latch")
            self.synth.set_button(m["id"], **kw)
            await self._broadcast_state(exclude=sender)
        elif t == "fire_button":
            self.synth.fire_button(m["id"])   # hot path: no state broadcast
        elif t == "button_down":
            self.synth.button_down(m["id"])   # hot path: no state broadcast
        elif t == "button_up":
            self.synth.button_up(m["id"])     # hot path: no state broadcast
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
        elif t == "spawn_lfo":
            self.synth.spawn_lfo()
            await self._broadcast_state()
        elif t == "remove_lfo":
            await loop.run_in_executor(None, lambda: self.synth.remove_lfo(m["id"]))
            await self._broadcast_state()
        elif t == "lfo_wire":
            await loop.run_in_executor(
                None, lambda: self.synth.lfo_wire(
                    m.get("action", "add"), m["id"], m["key"], m["name"]))
            await self._broadcast_state()
        elif t == "mod_wire":
            await loop.run_in_executor(
                None, lambda: self.synth.mod_wire(
                    m.get("action", "add"), m.get("from"), m.get("to")))
            await self._broadcast_state()
        elif t == "lfo_set":
            self.synth.lfo_set(m["id"], rate=m.get("rate"), depth=m.get("depth"),
                               shape=m.get("shape"))
        elif t == "spawn_threshold":
            self.synth.spawn_threshold()
            await self._broadcast_state()
        elif t == "remove_threshold":
            await loop.run_in_executor(
                None, lambda: self.synth.remove_threshold(m["id"]))
            await self._broadcast_state()
        elif t == "set_threshold":
            kw = {}
            for k in ("level", "hysteresis", "mode"):
                if k in m:
                    kw[k] = m[k]
            self.synth.set_threshold(m["id"], **kw)
            await self._broadcast_state(exclude=sender)
        elif t == "threshold_wire":
            await loop.run_in_executor(
                None, lambda: self.synth.threshold_wire(
                    m.get("action", "add"), m["id"], m.get("lfo")))
            await self._broadcast_state()
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
                playing=m.get("playing"), downbeat=m.get("downbeat"),
            )
            # broadcast to ALL incl. sender: the play/stop button must flip
            await self._broadcast_state()
        elif t == "spawn_transport_card":
            self.synth.spawn_transport_card(m["which"])
            await self._broadcast_state()
        elif t == "remove_transport_card":
            self.synth.remove_transport_card(m["which"])
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
                                 # positional downbeat flag (item 9) —
                                 # independent of the accent's on/off
                                 "downbeat":
                                     beat == self.synth.transport.downbeat,
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
