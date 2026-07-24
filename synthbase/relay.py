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
* AUDIO: graph_wires store the relay endpoints verbatim (app.graph_wire
  bypasses rack.find for them); resolve_audio() flattens each source's
  wire through closed relays to a real destination — open (or unwired-
  out) circuits park the source on the null bus. Resolved edges get the
  same cycle-guard walk graph_wire uses; a cycling source stays
  disconnected. reorder_for_wires always sees the RESOLVED list.
* MOD (Cole, 07-24 — "must be compatible with all wire types, including
  LFO"): app.mod_wires stores the LFO plane's relay-routed wires
  verbatim ("<lfo id>" → "<relay-id>:<k>" → "<module key>:<param>");
  resolve_mod() walks each LFO through CLOSED circuits to the params it
  actually drives and wires/unwires LFOManager destinations to match. An
  open circuit therefore leaves the param on its own knob value — the
  honest "no modulation reaching you" state — and closing it re-maps.
"""

from __future__ import annotations

MAX_CIRCUITS = 9


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
        self.kinds: dict[int, str] = {}     # circuit -> "audio"|"notes"|"binary"
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
        circuits re-settle, audio circuits re-resolve."""
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
            resolve_audio(self.app)
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


# -- audio resolution -------------------------------------------------------------

def resolved_wires(app) -> list[dict]:
    """app.graph_wires with relay endpoints RESOLVED away: each real
    source's wire follows circuit hops (closed → the circuit's out wire;
    open or out-unwired → None = parked). Wires FROM relay endpoints are
    virtual and dropped. Resolved edges then get the same cycle-guard
    walk graph_wire uses — a source whose resolved path cycles is left
    disconnected."""
    gw = app.graph_wires or []
    outs = {w.get("from"): w.get("to") for w in gw}
    res = []
    for w in gw:
        src = w.get("from")
        if relay_ep(app, src) is not None:
            continue                        # virtual edge — no node behind it
        dst, hops = w.get("to"), 0
        while dst not in (None, "master") and hops < 64:
            rk = relay_ep(app, dst)
            if rk is None:
                break
            dst = outs.get(dst) if rk[0].closed else None
            hops += 1
        res.append({"from": src, "to": dst})
    adj = {w["from"]: w["to"] for w in res}
    for w in res:
        cur, hops = w["to"], 0
        while cur not in (None, "master") and hops < 64:
            if cur == w["from"]:
                w["to"] = None              # cycle: leave it disconnected
                break
            cur = adj.get(cur)
            hops += 1
    return res


def resolve_audio(app) -> None:
    """(Re)apply the audio consequences of the relay layer: every source
    whose STORED wire lands on a relay endpoint is rewired to its
    resolved destination (or parked when the path is open/unwired/
    cyclic), then node order follows the resolved edges."""
    rack = app.rack
    if rack is None or app.graph_wires is None:
        return
    res = resolved_wires(app)
    resolved = {w["from"]: w["to"] for w in res}
    for w in app.graph_wires:
        src = w.get("from")
        if relay_ep(app, src) is not None:
            continue
        if relay_ep(app, w.get("to")) is None:
            continue                        # plain wire — graph_wire applied it
        dst = resolved.get(src)
        try:
            if dst is None:
                rack.audio_disconnect(src)
            else:
                rack.audio_rewire(src, dst)
        except Exception:  # noqa: BLE001 — one bad wire must not stop the rest
            pass
    try:
        rack.reorder_for_wires(res)
    except Exception:  # noqa: BLE001
        pass
