"""Relay: a type-agnostic switched junction (binary rework, 07-23) —
the replacement for the old SwitchGate.

Spawnable ("relay", "relay.2", ...; app.relays dict, keyshift-style). One
node holds up to MAX_CIRCUITS independent CIRCUITS plus a control-in:

* Circuit k's endpoint is "<relay-id>:<k>" (k = 1..9). The wire INTO the
  endpoint is the circuit's IN; the wire FROM it is its OUT. A circuit is
  a 1:1 CONTACT SET (Cole, 07-24) — exactly one wire per side, adding
  steals — and it is type-agnostic: its KIND is inferred from its FIRST
  wire and then enforced (mixing kinds on one circuit is rejected):
  - audio:  graph_wires touching the endpoint (rack modules in/out)
  - notes:  ctl wires from note sources (keys/arp/derivers/keyshift...)
  - binary: ctl wires from binary sources (button/clock/threshold/logic)
  - mod:    mod_wires from an LFO out to a param (see below)
* "<relay-id>:ctl" is a binary LEVEL-in: closed FOLLOWS the wired level
  (single-input; wiring an occupied ctl steals). set_relay is the manual
  click — last writer wins.

closed defaults to False (open). Per kind:

* NOTES: a _CircuitIn note-sink adapter per circuit forwards
  note_on/note_off/sustain/bend to the circuit's downstream sinks ONLY
  while closed; all_off passes ALWAYS (keyshift pragmatics — no stuck
  notes). On OPENING, every note circuit all_offs downstream.
* BINARY: the circuit's out level = OR(in levels) AND closed, computed
  lazily in gates.level_of_src; pulses pass while closed. On a closed
  change gates.recompute() lets downstream edges fire naturally.
* AUDIO (item 25 — the lagged-gate model; the old resolution layer is
  GONE): graph_wires store the relay endpoints verbatim (app.graph_wire
  bypasses rack.find for them) and every CLAIMED audio circuit is a tiny
  PERMANENT synth (In.ar → × gate → Out.ar, ~10 ms Lag on the gate)
  owned by RelayAudioManager — the LFOManager lifecycle pattern:
  synthdef registration tracked per server OBJECT, every server touch
  guarded so the model works headless. The relay's wires are therefore
  REAL graph wires that exist open or closed: a source wired into
  "<relay-id>:<k>" writes that circuit's own in-bus, the gate synth
  writes the circuit's out-wire destination, and open/close moves only
  the gate param (clickless, and reverb/echo tails downstream of an
  opening relay ring out honestly). apply_audio() is the one entry
  point — sync gates to the claims, point everything at its bus, order
  every wire's src before its dst (gate synths included).
* MOD (Cole, 07-24 — "must be compatible with all wire types, including
  LFO"): app.mod_wires stores the LFO plane's relay-routed wires
  verbatim ("<lfo id>" → "<relay-id>:<k>" → "<module key>:<param>");
  resolve_mod() walks each LFO through CLOSED circuits to the params it
  actually drives and wires/unwires LFOManager destinations to match. An
  open circuit therefore leaves the param on its own knob value — the
  honest "no modulation reaching you" state — and closing it re-maps.
"""

from __future__ import annotations

from supriya import AddAction, synthdef
from supriya.ugens import In, Lag, Out

MAX_CIRCUITS = 9


@synthdef()
def _relay_gate(in_bus=0, out=0, gate=0.0):
    """One claimed audio circuit: a permanent lagged gate. ~10 ms of Lag
    opens/closes without clicks; cost is one stereo multiply."""
    sig = In.ar(bus=in_bus, channel_count=2)
    Out.ar(bus=out, source=sig * Lag.kr(source=gate, lag_time=0.01))


def relay_ep(app, ep):
    """Parse a relay CIRCUIT endpoint: "<relay-id>:<k>" (k 1..MAX) →
    (RelayNode, k), else None."""
    if not isinstance(ep, str) or ":" not in ep:
        return None
    base, _, sub = ep.partition(":")
    r = getattr(app, "relays", {}).get(base)
    if r is not None and sub.isdigit() and 1 <= int(sub) <= MAX_CIRCUITS:
        return (r, int(sub))
    return None


class _CircuitIn:
    """Note-sink adapter for one circuit's IN (what a note ctl wire into
    "<relay-id>:<k>" resolves to) — the keyshift _LaneIn shape. Forwards
    only while the relay is closed; all_off passes ALWAYS."""

    def __init__(self, relay: "RelayNode", k: int) -> None:
        self.relay = relay
        self.k = k

    def _each(self, fn) -> None:
        for s in self.relay.app._ctl_sinks(f"{self.relay.id}:{self.k}"):
            try:
                fn(s)
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    def note_on(self, note: int, velocity: int = 100) -> None:
        if self.relay.closed:
            self._each(lambda s: s.note_on(note, velocity))

    def note_off(self, note: int) -> None:
        if self.relay.closed:
            self._each(lambda s: s.note_off(note))

    def all_off(self) -> None:
        self._each(lambda s: s.all_off())   # silencing passes regardless

    def set_sustain(self, on: bool) -> None:
        if self.relay.closed:
            self._each(lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        if self.relay.closed:
            self._each(lambda s: s.set_bend(semitones))


class RelayNode:
    def __init__(self, app, rid: str = "relay") -> None:
        self.app = app
        self.id = rid
        self.closed = False                 # default OPEN
        # circuit -> "audio"|"notes"|"binary"|"mod"
        self.kinds: dict[int, str] = {}
        self._ins = {k: _CircuitIn(self, k)
                     for k in range(1, MAX_CIRCUITS + 1)}

    # -- wiring ------------------------------------------------------------------

    def circuit_in(self, k: int) -> _CircuitIn:
        if not 1 <= int(k) <= MAX_CIRCUITS:
            raise ValueError(
                f"{self.id} has circuits 1..{MAX_CIRCUITS}, not {k!r}")
        return self._ins[int(k)]

    def claim(self, k: int, kind: str) -> None:
        """A circuit's kind = kind of its FIRST wire; later wires must
        match (a mixed circuit would be un-switchable honestly)."""
        cur = self.kinds.get(int(k))
        if cur is None:
            self.kinds[int(k)] = kind
        elif cur != kind:
            raise ValueError(
                f"{self.id}:{k} carries {cur} wires — not {kind}")

    def refresh_kinds(self) -> None:
        """Forget the kind of any circuit no wire touches anymore."""
        app = self.app
        planes = (app.ctl_wires, app.graph_wires or [],
                  getattr(app, "mod_wires", None) or [])
        for k in list(self.kinds):
            ep = f"{self.id}:{k}"
            if not any(ep in (w.get("from"), w.get("to"))
                       for plane in planes for w in plane):
                self.kinds.pop(k, None)

    # -- the switch --------------------------------------------------------------

    def set_closed(self, closed) -> None:
        """Flip the relay (manual click or the ctl level following). On
        change: note circuits all_off downstream when opening, binary
        circuits re-settle, audio circuits move their GATE PARAM only —
        no rewiring, no reorder (item 25) — and mod circuits re-map."""
        closed = bool(closed)
        if closed == self.closed:
            return
        self.closed = closed
        try:
            self.app._emit_midi_event(
                {"kind": "gate", "id": self.id, "on": closed})
        except Exception:  # noqa: BLE001
            pass
        if not closed:
            for k, kind in list(self.kinds.items()):
                if kind == "notes":
                    self._ins[k].all_off()   # no stuck notes downstream
        if any(kind == "binary" for kind in self.kinds.values()):
            self.app.gates.recompute()       # downstream edges fire naturally
        if any(kind == "audio" for kind in self.kinds.values()):
            self.app.relay_audio.set_gate(self)
        if any(kind == "mod" for kind in self.kinds.values()):
            resolve_mod(self.app)             # params re-map / fall back

    # -- state -------------------------------------------------------------------

    def settings(self) -> dict:
        return {"id": self.id, "closed": bool(self.closed),
                "circuits": {str(k): {"kind": kind}
                             for k, kind in sorted(self.kinds.items())}}


# -- mod (LFO) resolution ---------------------------------------------------------

def param_ep(app, ep):
    """Parse a PARAM endpoint: "<module key>:<param>" → (instance id,
    param) when the module and the param really exist right now, else
    None. Relay circuits are checked FIRST at every call site — a bare
    "relay:3" would otherwise look like a param on module "relay"."""
    if not isinstance(ep, str) or ":" not in ep:
        return None
    key, _, pname = ep.rpartition(":")
    rack = getattr(app, "rack", None)
    if rack is None or not key or not pname:
        return None
    try:
        inst = rack.find(key)
    except Exception:  # noqa: BLE001 — an unknown key is simply not a param ep
        return None
    if pname not in inst.module.params:
        return None
    return (inst.key, pname)


def mod_dests(app) -> dict:
    """Walk app.mod_wires: which params each LFO ACTUALLY drives right
    now, following circuit hops (closed → the circuit's out wire; open or
    out-unwired → nothing). Returns {(key, param): lfo id}."""
    wires = getattr(app, "mod_wires", None) or []
    lfos = getattr(app, "lfos", None)
    if lfos is None:
        return {}
    # a circuit OUT is 1:1, so one entry per circuit endpoint is enough
    circ_out = {w["from"]: w.get("to") for w in wires
                if relay_ep(app, w.get("from")) is not None}
    res = {}
    for w in wires:
        lid = w.get("from")
        if relay_ep(app, lid) is not None or lid not in lfos.instances:
            continue                        # virtual edge / dead LFO
        dst, hops = w.get("to"), 0
        while dst is not None and hops < 64:
            rk = relay_ep(app, dst)
            if rk is None:
                break
            dst = circ_out.get(dst) if rk[0].closed else None
            hops += 1
        kp = param_ep(app, dst) if dst is not None else None
        if kp is not None:
            res[kp] = lid
    return res


def resolve_mod(app) -> None:
    """(Re)apply the mod plane: params reachable from an LFO through
    CLOSED circuits get wired; anything THIS layer wired and can no longer
    reach is unwired, so the param settles back on its own knob value.
    Direct (non-relay) LFO destinations are never touched — app._mod_managed
    is the set this layer owns."""
    lfos = getattr(app, "lfos", None)
    if lfos is None:
        return
    want = mod_dests(app)
    held = getattr(app, "_mod_managed", None)
    if held is None:
        held = app._mod_managed = set()
    for kp in list(held):
        if kp in want:
            continue
        owner = lfos._owner_of(*kp)
        if owner is not None:
            try:
                lfos.unwire(owner, *kp)
            except Exception:  # noqa: BLE001 — one bad dest must not stop the rest
                pass
        held.discard(kp)
    for kp, lid in want.items():
        if lfos._owner_of(*kp) != lid:
            try:
                lfos.wire(lid, *kp)
            except Exception:  # noqa: BLE001
                continue
        held.add(kp)


# -- the audio plane: permanent lagged-gate synths (item 25) -----------------------

class RelayAudioManager:
    """One permanent ``_relay_gate`` synth per CLAIMED audio circuit.

    Record per endpoint "<relay-id>:<k>": {"bus": the circuit's own stereo
    in-bus group, "node": the gate synth, "dst": the circuit's out-wire
    destination (bookkeeping — set by apply_audio)}. LFOManager lifecycle
    pattern throughout: synthdef registration is tracked per server OBJECT
    (an engine swap gets fresh defs no matter who forgot to call reset()),
    and every server touch is guarded so the data model works headless
    (bus/node None)."""

    def __init__(self, app) -> None:
        self.app = app
        self.circuits: dict[str, dict] = {}
        self._registered_server = None

    # -- server plumbing -----------------------------------------------------

    def _server(self):
        eng = self.app.engine
        return eng.server if eng and getattr(eng, "server", None) else None

    def _ensure_synthdefs(self, server) -> None:
        if self._registered_server is not server:
            server.add_synthdefs(_relay_gate)
            server.sync()
            self._registered_server = server

    def reset(self) -> None:
        """Engine went away — server-side objects are already gone."""
        self._registered_server = None

    def clear(self) -> None:
        for ep in list(self.circuits):
            self._release(ep)

    # -- circuit lifecycle ----------------------------------------------------

    def _ensure(self, ep: str, relay: "RelayNode") -> dict:
        rec = self.circuits.get(ep)
        if rec is None:
            rec = {"bus": None, "node": None, "dst": None}
            self.circuits[ep] = rec
        server = self._server()
        if server is not None and rec["node"] is None:
            self._ensure_synthdefs(server)
            rec["bus"] = server.add_bus_group(
                calculation_rate="audio", count=2)
            rec["node"] = server.add_synth(
                _relay_gate,
                add_action=AddAction.ADD_TO_TAIL,
                target_node=self.app.engine.root_group,
                in_bus=int(rec["bus"]),
                out=self._park_bus(),
                gate=1.0 if relay.closed else 0.0,
            )
        return rec

    def _release(self, ep: str) -> None:
        rec = self.circuits.pop(ep, None)
        if rec is None:
            return
        for obj in (rec["node"], rec["bus"]):
            try:
                if obj is not None:
                    obj.free()
            except Exception:  # noqa: BLE001
                pass

    def sync(self) -> None:
        """Gate synths mirror the CLAIMS: ensure one per claimed audio
        circuit, release the rest (kind forgotten / relay removed)."""
        wanted = {}
        for r in getattr(self.app, "relays", {}).values():
            for k, kind in r.kinds.items():
                if kind == "audio":
                    wanted[f"{r.id}:{k}"] = r
        for ep in [e for e in self.circuits if e not in wanted]:
            self._release(ep)
        for ep, r in wanted.items():
            self._ensure(ep, r)

    # -- the switch (set_closed's audio consequence) ---------------------------

    def set_gate(self, relay: "RelayNode") -> None:
        """Open/close = the gate param ONLY (10 ms lag in the synthdef):
        no rewiring, no node moves, tails ring out downstream."""
        g = 1.0 if relay.closed else 0.0
        for k, kind in relay.kinds.items():
            if kind != "audio":
                continue
            rec = self.circuits.get(f"{relay.id}:{k}")
            if rec is not None and rec["node"] is not None:
                try:
                    rec["node"].set(gate=g)
                except Exception:  # noqa: BLE001
                    pass

    # -- bus arithmetic ---------------------------------------------------------

    def in_bus(self, ep: str):
        rec = self.circuits.get(ep)
        return None if rec is None or rec["bus"] is None else int(rec["bus"])

    def _park_bus(self):
        rack = self.app.rack
        if rack is not None and hasattr(rack, "null_bus"):
            try:
                return rack.null_bus()
            except Exception:  # noqa: BLE001
                pass
        return 0

    def _dst_bus(self, dst):
        """Bus a wire INTO dst lands on: circuit in-bus for relay
        endpoints, rack._dst_bus for modules/"master", park when None."""
        if dst is None:
            return self._park_bus()
        if dst in self.circuits:
            return self.in_bus(dst)
        rack = self.app.rack
        try:
            return rack._dst_bus(dst)
        except Exception:  # noqa: BLE001 — dangling dst: park, don't crash
            return self._park_bus()

    def point(self, ep: str, dst) -> None:
        """Aim circuit ep's gate synth at its out-wire destination (None =
        out unwired = park on the null bus; the gate keeps running)."""
        rec = self.circuits.get(ep)
        if rec is None:
            return
        rec["dst"] = dst
        bus = self._dst_bus(dst)
        if rec["node"] is not None and bus is not None:
            try:
                rec["node"].set(out=bus)
            except Exception:  # noqa: BLE001
                pass


def apply_audio(app) -> None:
    """THE audio-plane entry point (replaces the retired resolve_audio):
    called on any wire edit / rebuild / relay removal — NEVER on open/
    close (that is set_gate, a single param). Steps: (1) sync gate synths
    to the claims; (2) every stored wire is REAL — sources into circuits
    write the circuit's in-bus, gate synths write their out-wire's bus;
    (3) order every wire's src before its dst, gate synths included."""
    rack = app.rack
    if rack is None:
        return
    mgr = app.relay_audio
    mgr.sync()
    gw = app.graph_wires
    if gw is None:
        return
    if not mgr.circuits:
        # no claimed audio circuits: plain wires were applied at their edit
        # sites — only the (cheap-pathed) ordering is left
        try:
            rack.reorder_for_wires(gw)
        except Exception:  # noqa: BLE001
            pass
        return
    # -- wiring -----------------------------------------------------------------
    for w in gw:
        src, dst = w.get("from"), w.get("to")
        if src in mgr.circuits:
            mgr.point(src, dst)               # gate synth → its destination
        elif isinstance(dst, str) and dst in mgr.circuits:
            bus = mgr.in_bus(dst)             # source → the circuit's in-bus
            try:
                inst = rack.find(src)
                if bus is not None:
                    inst.settings["out"] = bus
                    if inst.node is not None:
                        inst.node.set(out=bus)
            except Exception:  # noqa: BLE001 — one bad wire must not stop the rest
                pass
    outs = {w.get("from") for w in gw}
    for ep in mgr.circuits:                   # out-unwired circuits park
        if ep not in outs:
            mgr.point(ep, None)
    # -- ordering: src before dst across BOTH node kinds --------------------------
    rack_keys = [i.key for i in rack.instances if not i.service]
    universe = rack_keys + [e for e in mgr.circuits if e not in rack_keys]
    uset = set(universe)
    edges = [(w.get("from"), w.get("to")) for w in gw]
    edges = [(a, b) for a, b in edges if a in uset and b in uset]
    indeg = {k: 0 for k in universe}
    adj = {k: [] for k in universe}
    for a, b in edges:
        adj[a].append(b)
        indeg[b] += 1
    ready = [k for k in universe if indeg[k] == 0]
    order = []
    while ready:
        k = ready.pop(0)
        order.append(k)
        for b in adj[k]:
            indeg[b] -= 1
            if indeg[b] == 0:
                ready.append(b)
    if len(order) != len(universe):
        return  # cycle in the stored wires — refuse to reorder
    for k in order:
        node = (mgr.circuits[k]["node"] if k in mgr.circuits
                else getattr(rack.find(k), "node", None))
        if node is None:
            continue
        try:
            node.move(app.engine.root_group, AddAction.ADD_TO_TAIL)
        except Exception:  # noqa: BLE001
            pass
    for meth, arg in (("_set_nonservice_order",
                       [k for k in order if k not in mgr.circuits]),
                      ("_move_tail_router", None)):
        fn = getattr(rack, meth, None)
        if fn is not None:
            try:
                fn(arg) if arg is not None else fn()
            except Exception:  # noqa: BLE001
                pass
