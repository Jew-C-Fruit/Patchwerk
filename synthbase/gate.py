"""GATE suite (backlog item 8): a hi/lo LEVEL signal kind — the "gate"
the original handover deferred "until a real use appears". This is that
use.

A gate is a LEVEL, never an edge — the strict counterpart to the PING
kind (an edge, never a level). The distinction stays hard in the wire
grammar: gate wires carry the current hi/lo of their source; a ping
wired to a toggle target goes through an explicit ALTERNATOR adapter
(ping 1 → hi, ping 2 → lo, …), it never "becomes" a gate.

NODES (Python-side discrete state — no kr bus; every toggle in the
system is Python state):

* SwitchGate ("switch", "switch.2", …): a manual hi/lo source. Click it
  in the GUI (set_switch) OR wire a ping into it to flip. Outputs its
  level.
* LogicGate ("logic", "logic.2", …): ONE card, an op dropdown —
  AND / OR / NOT / XOR / SR latch (Cole's pick, 07-23). Gate-ins:
  - AND/OR/XOR/NOT fan-in on the BARE node id (any number of wires).
    No inputs → lo, for every op (a fresh card must never power things
    on; NOT of nothing is lo by the same rule). NOT = not any(ins).
  - "SR latch" has two NAMED ins: "<id>:set" / "<id>:reset" (reset
    wins when both are hi). Changing op DROPS wires whose endpoint
    shape no longer exists (visible, honest patching).
  Outputs its level.

TOGGLE TARGETS (gate wire destinations; ping wires may land on the same
endpoints via the alternator):

* "<module_key>:pwr" — a chain module's enable/bypass (the power light).
* "arp:pwr" / "drums:pwr" — service enables.
* "deck:rec" / "deck:play" / "deck:stop" / "deck:clear" — the Loop
  Deck's control buttons. These are MOMENTARY actions, not states: a
  RISING EDGE (lo→hi) presses the button once (Cole's pick, 07-23).
  A ping here simply presses.
* a LogicGate input (chaining).

Levels propagate eagerly on any change (wire edit, switch flip, ping,
op change) via an iterative settle pass with a bounded iteration count —
a feedback loop (legal to patch) settles if it can and freezes if it
can't, it never spins. Level changes on toggle targets apply their
effect exactly on change (enable follows level; deck presses on rising
edges only).

Emits {"kind": "gate", "id": "<node id>", "on": bool} on any node
output change for the GUI lights.

Persistence: switches (id, on) + logics (id, op) ride the preset
snapshot; gate/ping wires ride ctl_wires (resume re-adds them).
"""

from __future__ import annotations

GATE_OPS = ("AND", "OR", "NOT", "XOR", "SR latch")
DECK_ACTIONS = {"rec": "record", "play": "play", "stop": "stop",
                "clear": "clear"}
_MAX_SETTLE = 24   # fixpoint iterations before a feedback loop freezes


class SwitchGate:
    """Manual hi/lo source. trigger() is the ping-sink interface."""

    def __init__(self, gates: "GateManager", sid: str) -> None:
        self.gates = gates
        self.id = sid
        self.on = False

    def trigger(self) -> None:              # a wired ping flips the switch
        self.gates.set_switch(self.id, on=not self.on)

    def settings(self) -> dict:
        return {"id": self.id, "on": bool(self.on)}


class LogicGate:
    def __init__(self, lid: str) -> None:
        self.id = lid
        self.op = "AND"
        self.out = False

    def settings(self) -> dict:
        return {"id": self.id, "op": self.op, "ops": list(GATE_OPS),
                "out": bool(self.out)}


class _PingToggleSink:
    """Adapter a ping wire resolves to when it lands on a toggle target:
    deck buttons PRESS, everything else ALTERNATES a per-wire level."""

    def __init__(self, gates: "GateManager", src: str, dst: str) -> None:
        self.gates, self.src, self.dst = gates, src, dst

    def trigger(self) -> None:
        self.gates.ping_to(self.src, self.dst)


class GateManager:
    def __init__(self, app) -> None:
        self.app = app
        self.switches: dict[str, SwitchGate] = {}
        self.logics: dict[str, LogicGate] = {}
        self._alt: dict[tuple[str, str], bool] = {}  # ping-alternator latches
        self._edge: dict[str, bool] = {}   # last level seen per effect target

    # -- node lifecycle --------------------------------------------------------

    def spawn_switch(self, want_id: str | None = None) -> str:
        from .app import alloc_id
        sid = want_id or alloc_id("switch", self.switches.keys())
        if sid not in self.switches:
            self.switches[sid] = SwitchGate(self, sid)
        return sid

    def spawn_logic(self, want_id: str | None = None) -> str:
        from .app import alloc_id
        lid = want_id or alloc_id("logic", self.logics.keys())
        if lid not in self.logics:
            self.logics[lid] = LogicGate(lid)
        return lid

    def remove_switch(self, sid: str) -> None:
        if self.switches.pop(sid, None) is None:
            raise KeyError(f"no switch {sid!r}")
        self._unwire_node(sid)
        self.recompute()

    def remove_logic(self, lid: str) -> None:
        if self.logics.pop(lid, None) is None:
            raise KeyError(f"no logic gate {lid!r}")
        self._unwire_node(lid)
        self.recompute()

    def _unwire_node(self, nid: str) -> None:
        def touches(w) -> bool:
            return nid in (self._base(w.get("from")), self._base(w.get("to")))
        self.app.ctl_wires = [w for w in self.app.ctl_wires if not touches(w)]
        self._alt = {k: v for k, v in self._alt.items()
                     if self._base(k[1]) != nid and self._base(k[0]) != nid}

    def set_switch(self, sid: str, on=None) -> None:
        sw = self.switches.get(sid)
        if sw is None:
            raise KeyError(f"no switch {sid!r}")
        if on is not None and bool(on) != sw.on:
            sw.on = bool(on)
            self._emit(sid, sw.on)
            self.recompute()

    def set_logic(self, lid: str, op=None) -> None:
        lg = self.logics.get(lid)
        if lg is None:
            raise KeyError(f"no logic gate {lid!r}")
        if op is not None and op in GATE_OPS and op != lg.op:
            was_sr, now_sr = lg.op == "SR latch", op == "SR latch"
            lg.op = op
            if was_sr != now_sr:
                # endpoint shape changed: drop wires that no longer land
                # anywhere (set/reset ins vs the bare fan-in) — visibly
                gone = ((f"{lid}:set", f"{lid}:reset") if was_sr else (lid,))
                self.app.ctl_wires = [w for w in self.app.ctl_wires
                                      if w.get("to") not in gone]
                self._alt = {k: v for k, v in self._alt.items()
                             if k[1] not in gone}
            if not now_sr:
                lg.out = False   # latch state does not survive the op swap
            self.recompute()

    # -- wire grammar helpers --------------------------------------------------

    @staticmethod
    def _base(ep) -> str:
        return str(ep).split(":", 1)[0]

    def is_gate_src(self, nid) -> bool:
        return nid in self.switches or nid in self.logics

    def is_toggle_dst(self, dst) -> bool:
        """Endpoints a gate wire may land on (ping lands here too, via
        the alternator)."""
        if dst is None:
            return False
        dst = str(dst)
        base, _, sub = dst.partition(":")
        if base == "deck" and sub in DECK_ACTIONS:
            return True
        if sub == "pwr":
            if base in ("arp", "drums"):
                return True
            try:                       # any chain module's enable toggle
                self.app.rack.find(base)
                return True
            except Exception:  # noqa: BLE001
                return False
        if base in self.logics:
            lg = self.logics[base]
            if lg.op == "SR latch":
                return sub in ("set", "reset")
            return sub == ""           # bare fan-in for AND/OR/NOT/XOR
        return False

    # -- levels ----------------------------------------------------------------

    def level_of_src(self, nid) -> bool:
        sw = self.switches.get(nid)
        if sw is not None:
            return sw.on
        lg = self.logics.get(nid)
        if lg is not None:
            return lg.out
        return False

    def _in_level(self, dst: str) -> bool:
        """OR over every wire into an endpoint: gate sources contribute
        their level; ping sources contribute their alternator latch."""
        lvl = False
        for w in self.app.ctl_wires:
            if w.get("to") != dst:
                continue
            src = w.get("from")
            if self.is_gate_src(src):
                lvl = lvl or self.level_of_src(src)
            elif (src, dst) in self._alt:
                lvl = lvl or self._alt[(src, dst)]
        return lvl

    def _gate_in_levels(self, lid: str) -> list[bool]:
        return [
            (self.level_of_src(w["from"]) if self.is_gate_src(w["from"])
             else self._alt.get((w["from"], lid), False))
            for w in self.app.ctl_wires
            if w.get("to") == lid and
            (self.is_gate_src(w.get("from")) or
             (w.get("from"), lid) in self._alt)
        ]

    # -- propagation -----------------------------------------------------------

    def recompute(self) -> None:
        """Settle logic outputs to a fixpoint (bounded — feedback loops
        freeze rather than spin), then apply toggle-target effects on
        level CHANGES only."""
        changed_nodes: set[str] = set()
        for _ in range(_MAX_SETTLE):
            dirty = False
            for lg in self.logics.values():
                if lg.op == "SR latch":
                    s = self._in_level(f"{lg.id}:set")
                    r = self._in_level(f"{lg.id}:reset")
                    new = False if r else (True if s else lg.out)
                else:
                    ins = self._gate_in_levels(lg.id)
                    if not ins:
                        new = False        # no inputs → lo, every op
                    elif lg.op == "AND":
                        new = all(ins)
                    elif lg.op == "OR":
                        new = any(ins)
                    elif lg.op == "XOR":
                        new = (sum(ins) % 2) == 1
                    else:                  # NOT (fan-in: NOR)
                        new = not any(ins)
                if new != lg.out:
                    lg.out = new
                    changed_nodes.add(lg.id)
                    dirty = True
            if not dirty:
                break
        for nid in sorted(changed_nodes):
            self._emit(nid, self.level_of_src(nid))
        self._apply_effects()

    def _apply_effects(self) -> None:
        """Push levels into their toggle targets — on CHANGE only (deck
        buttons: rising edge presses once)."""
        app = self.app
        targets = {w.get("to") for w in app.ctl_wires
                   if self.is_gate_src(w.get("from")) or
                   (w.get("from"), w.get("to")) in self._alt}
        for dst in sorted(t for t in targets if t and ":" in str(t)):
            base, _, sub = str(dst).partition(":")
            if base in self.logics:
                continue                    # logic ins are handled above
            lvl = self._in_level(dst)
            prev = self._edge.get(dst)
            if lvl == prev:
                continue
            self._edge[dst] = lvl
            try:
                if base == "deck" and sub in DECK_ACTIONS:
                    # RISING EDGE presses; attaching a wire whose source is
                    # already hi is not an edge (prev is None → no press)
                    if lvl and prev is not None:
                        app.set_looper(action=DECK_ACTIONS[sub])
                elif sub == "pwr":
                    if base == "arp":
                        app.set_arp(enabled=lvl)
                    elif base == "drums":
                        app.set_drums(enabled=lvl)
                    else:
                        app.set_enabled(base, lvl)
            except Exception:  # noqa: BLE001 — a dead target must not stop the pass
                pass

    def on_wire_change(self, src=None, dst=None, removed: bool = False) -> None:
        """Hook after a gate/ping wire edit: drop stale alternator latches
        and re-settle."""
        if removed and src is not None and dst is not None:
            self._alt.pop((src, dst), None)
            self._edge.pop(dst, None)      # a re-wire starts edge-fresh
        self.recompute()

    # -- ping adapter ----------------------------------------------------------

    def ping_sink(self, src: str, dst: str) -> _PingToggleSink:
        return _PingToggleSink(self, src, dst)

    def ping_to(self, src: str, dst: str) -> None:
        base, _, sub = str(dst).partition(":")
        if base == "deck" and sub in DECK_ACTIONS:
            try:                            # a ping just presses the button
                self.app.set_looper(action=DECK_ACTIONS[sub])
            except Exception:  # noqa: BLE001
                pass
            return
        # alternator: ping 1 → hi, ping 2 → lo, … (per wire)
        self._alt[(src, dst)] = not self._alt.get((src, dst), False)
        if base in self.logics:
            self.recompute()
        else:
            # direct ping→toggle: apply immediately through the same
            # change-only effect path
            self._apply_effects()

    # -- events / state / persistence ------------------------------------------

    def _emit(self, nid: str, on: bool) -> None:
        try:
            self.app._emit_midi_event({"kind": "gate", "id": nid,
                                       "on": bool(on)})
        except Exception:  # noqa: BLE001
            pass

    def state(self) -> dict:
        return {
            "switches": [s.settings() for s in self.switches.values()],
            "logics": [g.settings() for g in self.logics.values()],
        }

    def snapshot(self) -> dict:
        return {
            "switches": [{"id": s.id, "on": s.on}
                         for s in self.switches.values()],
            "logics": [{"id": g.id, "op": g.op}
                       for g in self.logics.values()],
        }

    def restore(self, data: dict) -> None:
        for s in (data or {}).get("switches", []):
            sid = s.get("id") or "switch"
            self.spawn_switch(want_id=sid)
            self.switches[sid].on = bool(s.get("on"))
        for g in (data or {}).get("logics", []):
            lid = g.get("id") or "logic"
            self.spawn_logic(want_id=lid)
            if g.get("op") in GATE_OPS:
                self.logics[lid].op = g["op"]
        self.recompute()
