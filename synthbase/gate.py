"""The BINARY signal kind (binary rework, 07-23): ONE hi/lo kind that
unifies the old PING (edge) and GATE (level) kinds.

THE MODEL — sources own LEVELS; edges DERIVE from level changes:

* Every binary SOURCE (button, threshold, logic out, relay circuit out)
  carries a persistent hi/lo level. A clock is the one pulse-only source:
  its persistent level is ALWAYS lo; each tick momentarily drives it hi
  and back (see pulse()).
* A "ping" is no longer its own kind — it is simply a PULSE (hi-then-lo)
  propagating through the graph. That is exactly what lets pings pass
  THROUGH logic as pings: edge in → edge out while the other leg is hi
  (a clock into AND:a with a latched button holding :b hi ticks the
  downstream trigger; :b lo blocks it).
* TRIG-INS fire on RISING edges only (lo→hi): deriver ids (trigger() =
  commit), deck buttons ("deck:rec|play|stop|clear"), "transport:tap"
  (tap tempo — item 9). Attaching a wire whose source is already hi is
  NOT an edge — nothing fires.
* All other binary ins FOLLOW the level, applied on change in both
  directions (including first sight): "<key>:pwr", "arp:pwr",
  "drums:pwr", logic ins, "relay:ctl" (a relay's closed state follows),
  "transport:run|click|accent" (the GLOBAL transport's play state,
  audible click and downbeat accent follow — item 9).

NODES owned here: LogicGate ("logic", "logic.2", ...) — ONE card, an op
dropdown: AND / OR / NOR / XOR / SR latch. EVERY op exposes exactly TWO
named single-input endpoints, ":a" and ":b" — the endpoint shape never
changes across op swaps, so wires never drop. NOR replaces the old NOT:
with one active connection NOR functions as NOT (hi,unwired → lo;
unwired/lo → hi — an unwired in already reads lo, so NOR(a) = !a
naturally). The SR latch reads :a as SET and :b as RESET (reset wins);
its state starts lo entering AND leaving SR. Bare-id destinations are
not valid. Adding a binary wire to an occupied single-input endpoint
REPLACES the existing wire (steal-on-drop, mirroring the GUI).

Levels settle eagerly on any change via a bounded fixpoint pass —
feedback loops freeze rather than spin — then edge-diffed effects apply
per destination endpoint.

Events: LOGIC out changes emit {"kind": "gate", "id", "on"} here;
buttons and thresholds emit the same shape from their own files, so the
GUI LEDs follow every level in the system from one event kind.

Persistence: logics (id, op) ride the preset snapshot; binary wires ride
ctl_wires (resume re-adds them). A legacy "switches" list in old presets
is ignored silently (the Switch node is gone — Relay replaced it).
Migration tolerance (saves from the shaped-endpoint day): op "NOT" maps
to "NOR" on configure/restore, and an incoming wire dst "<logic>:set"/
":reset" lands on ":a"/":b" (old resume files replay their wires through
set_ctl_wire, which round-trips through this manager).
"""

from __future__ import annotations

from .relay import MAX_CIRCUITS

GATE_OPS = ("AND", "OR", "NOR", "XOR", "SR latch")
LOGIC_INS = ("a", "b")           # EVERY op: exactly these two, always
# migration: shaped-endpoint saves (":set"/":reset" wires, op "NOT")
_LEGACY_INS = {"set": "a", "reset": "b"}
DECK_ACTIONS = {"rec": "record", "play": "play", "stop": "stop",
                "clear": "clear"}
# item 9: the GLOBAL transport's binary ins — level-ins run/click/accent
# (state follows) + the one trig-in "tap" (rising edge = tap tempo)
TRANSPORT_INS = ("run", "click", "accent", "tap")
_MAX_SETTLE = 24   # fixpoint iterations before a feedback loop freezes
_MAX_PASSES = 8    # settle+effects outer passes (relay ctl re-entry)


class LogicGate:
    def __init__(self, lid: str) -> None:
        self.id = lid
        self.op = "AND"
        self.out = False

    def settings(self) -> dict:
        return {"id": self.id, "op": self.op, "ops": list(GATE_OPS),
                "out": bool(self.out)}


class GateManager:
    def __init__(self, app) -> None:
        self.app = app
        self.logics: dict[str, LogicGate] = {}
        self._edge: dict[str, bool] = {}   # last level seen per effect target
        self._pulse: set[str] = set()      # sources momentarily forced hi
        self._busy = False                 # recompute re-entrancy latch
        self._again = False

    # -- node lifecycle --------------------------------------------------------

    def spawn_logic(self, want_id: str | None = None) -> str:
        from .app import alloc_id
        lid = want_id or alloc_id("logic", self.logics.keys())
        if lid not in self.logics:
            self.logics[lid] = LogicGate(lid)
        return lid

    def remove_logic(self, lid: str) -> None:
        if self.logics.pop(lid, None) is None:
            raise KeyError(f"no logic gate {lid!r}")
        self._unwire_node(lid)
        self.recompute()

    def _unwire_node(self, nid: str) -> None:
        def touches(w) -> bool:
            return nid in (self._base(w.get("from")), self._base(w.get("to")))
        dropped = [w.get("to") for w in self.app.ctl_wires if touches(w)]
        self.app.ctl_wires = [w for w in self.app.ctl_wires if not touches(w)]
        for d in dropped:                  # a later re-wire starts edge-fresh
            self._edge.pop(d, None)
        try:                               # orphaned relay circuits re-infer
            self.app._relay_refresh_kinds()
        except Exception:  # noqa: BLE001
            pass

    def set_logic(self, lid: str, op=None) -> None:
        lg = self.logics.get(lid)
        if lg is None:
            raise KeyError(f"no logic gate {lid!r}")
        if op == "NOT":
            op = "NOR"   # migration: NOR with one wired in IS the old NOT
        if op is not None and op in GATE_OPS and op != lg.op:
            # the endpoint shape is :a/:b for EVERY op — wires never drop
            was_sr = lg.op == "SR latch"
            lg.op = op
            if was_sr != (op == "SR latch"):
                # the latch neither survives the swap away NOR inherits the
                # previous op's out on the way in — a fresh SR starts lo
                lg.out = False
            self.recompute()

    # -- wire grammar helpers --------------------------------------------------

    @staticmethod
    def _base(ep) -> str:
        return str(ep).split(":", 1)[0]

    def is_toggle_dst(self, dst) -> bool:
        """Endpoints a BINARY wire may land on: level-ins (:pwr, logic
        named ins, relay circuit ins, relay:ctl, transport:run|click|
        accent) + trig-ins (deriver ids, deck buttons, transport:tap)."""
        if dst is None:
            return False
        dst = str(dst)
        base, _, sub = dst.partition(":")
        if base == "deck" and sub in DECK_ACTIONS:
            return True
        if base == "transport" and sub in TRANSPORT_INS:
            return True                # the GLOBAL transport (item 9)
        if sub == "pwr":
            if base in ("arp", "drums"):
                return True
            try:                       # any chain module's enable toggle
                self.app.rack.find(base)
                return True
            except Exception:  # noqa: BLE001
                return False
        if base in self.logics:
            # every op exposes exactly :a/:b; the legacy :set/:reset
            # aliases are accepted and canonicalized in on_wire_change
            return sub in LOGIC_INS or sub in _LEGACY_INS
        if base in getattr(self.app, "relays", {}):
            if sub == "ctl":
                return True
            return sub.isdigit() and 1 <= int(sub) <= MAX_CIRCUITS
        if sub == "" and self.app._deriver(base) is not None:
            return True                # deriver trig-in (rising edge = commit)
        return False

    def is_single_input(self, dst) -> bool:
        """Endpoints that hold at most ONE binary wire: logic named ins
        and relay:ctl. Adding a wire to an occupied one steals it."""
        base, _, sub = str(dst).partition(":")
        if base in self.logics and (sub in LOGIC_INS or sub in _LEGACY_INS):
            return True
        return base in getattr(self.app, "relays", {}) and sub == "ctl"

    def _canon(self, dst):
        """Canonicalize a legacy logic endpoint: ":set" → ":a", ":reset"
        → ":b" (migration for wires replayed from shaped-endpoint saves)."""
        base, _, sub = str(dst).partition(":")
        if base in self.logics and sub in _LEGACY_INS:
            return f"{base}:{_LEGACY_INS[sub]}"
        return dst

    def steal_input(self, dst) -> None:
        """Drop any existing wire into a single-input endpoint (the GUI's
        steal-on-drop, mirrored server-side) — edge state resets too."""
        self.app.ctl_wires = [w for w in self.app.ctl_wires
                              if w.get("to") != dst]
        self._edge.pop(dst, None)

    # -- levels ----------------------------------------------------------------

    def level_of_src(self, nid, _seen: set | None = None) -> bool:
        """A binary source's CURRENT level. Pulsed sources read hi for the
        duration of the pulse pass; clocks are otherwise always lo."""
        nid = str(nid)
        if nid in self._pulse:
            return True
        lg = self.logics.get(nid)
        if lg is not None:
            return bool(lg.out)
        app = self.app
        b = app.buttons.get(nid)
        if b is not None:
            return bool(getattr(b, "level", False))
        rec = app.thresholds.instances.get(nid)
        if rec is not None:
            return bool(rec["node"].out_level)   # mode-mapped Schmitt level
        if nid in app.clocks:
            return False               # pulse-only source
        base, _, sub = nid.partition(":")
        r = getattr(app, "relays", {}).get(base)
        if r is not None and sub.isdigit() and 1 <= int(sub) <= MAX_CIRCUITS:
            # a relay circuit's out level: OR of its in levels AND closed
            if not r.closed:
                return False
            seen = _seen if _seen is not None else set()
            if nid in seen:
                return False           # relay feedback loop freezes lo
            seen.add(nid)
            return any(self.level_of_src(w.get("from"), seen)
                       for w in app.ctl_wires if w.get("to") == nid)
        return False

    def _in_level(self, dst: str) -> bool:
        """OR over every binary wire into an endpoint."""
        return any(self.level_of_src(w.get("from"))
                   for w in self.app.ctl_wires if w.get("to") == dst)

    # -- propagation -----------------------------------------------------------

    def pulse(self, src: str) -> None:
        """Momentarily treat src's level as HI: rising edges fire and
        level-ins apply hi, then the level clears (falling edges are
        silent at trig-ins). This is how the clock ticks — and how a
        pulse passes THROUGH logic while the other leg is hi."""
        self._pulse.add(src)
        try:
            self.recompute()
        finally:
            self._pulse.discard(src)
        self.recompute()

    def on_source_level(self, src: str) -> None:
        """A source's persistent level changed (button press/release,
        threshold crossing) — re-settle and apply."""
        del src  # levels are read live; the hook exists for symmetry
        self.recompute()

    def recompute(self) -> None:
        """Settle logic outputs to a fixpoint (bounded — feedback loops
        freeze rather than spin), then edge-diff effects per destination
        endpoint. Re-entrant calls (a relay:ctl flip mid-pass) queue one
        more pass instead of recursing."""
        if self._busy:
            self._again = True
            return
        self._busy = True
        try:
            for _ in range(_MAX_PASSES):
                self._again = False
                self._settle()
                self._apply_effects()
                if not self._again:
                    break
        finally:
            self._busy = False

    def _settle(self) -> None:
        changed_nodes: set[str] = set()
        for _ in range(_MAX_SETTLE):
            dirty = False
            for lg in self.logics.values():
                a = self._in_level(f"{lg.id}:a")
                b = self._in_level(f"{lg.id}:b")
                if lg.op == "SR latch":
                    # :a is SET, :b is RESET — reset wins
                    new = False if b else (True if a else lg.out)
                elif lg.op == "AND":
                    new = a and b
                elif lg.op == "OR":
                    new = a or b
                elif lg.op == "XOR":
                    new = a != b
                else:                      # NOR (one wired in acts as NOT)
                    new = not (a or b)
                if new != lg.out:
                    lg.out = new
                    changed_nodes.add(lg.id)
                    dirty = True
            if not dirty:
                break
        for nid in sorted(changed_nodes):
            self._emit(nid, self.logics[nid].out)

    def _apply_effects(self) -> None:
        """Edge-diff every wired destination endpoint against _edge:
        trig-ins fire on rising edges (never on wire-attach), level-ins
        apply on change (both directions, incl. first sight)."""
        app = self.app
        targets = {w.get("to") for w in app.ctl_wires
                   if app._is_ping_src(w.get("from"))}
        for dst in sorted(t for t in targets if t):
            base, _, sub = str(dst).partition(":")
            if base in self.logics:
                continue                    # logic ins are settled above
            r = getattr(app, "relays", {}).get(base)
            if r is not None and sub.isdigit():
                continue                    # circuit ins are read lazily
            lvl = self._in_level(dst)
            prev = self._edge.get(dst)
            if lvl == prev:
                continue
            self._edge[dst] = lvl
            try:
                if base == "deck" and sub in DECK_ACTIONS:
                    # TRIG-IN: rising edge presses once; attaching a wire
                    # whose source is already hi is not an edge
                    if lvl and prev is not None:
                        app.set_looper(action=DECK_ACTIONS[sub])
                elif base == "transport":
                    # item 9: the GLOBAL transport's binary ins
                    if sub == "tap":
                        # TRIG-IN: rising edge = one tap (TEMPO only);
                        # attach-while-hi is not an edge
                        if lvl and prev is not None:
                            app._transport_tap()
                    elif sub == "run":
                        app.set_transport(playing=lvl)   # LEVEL-IN follows
                    elif sub == "click":
                        app.set_transport(click=lvl)     # LEVEL-IN follows
                    elif sub == "accent":
                        app.set_transport(accent=lvl)    # LEVEL-IN follows
                elif sub == "" and app._deriver(base) is not None:
                    # TRIG-IN: rising edge commits once
                    if lvl and prev is not None:
                        app._deriver(base).trigger()
                elif sub == "pwr":
                    # LEVEL-IN: enable follows, incl. first sight
                    if base == "arp":
                        app.set_arp(enabled=lvl)
                    elif base == "drums":
                        app.set_drums(enabled=lvl)
                    else:
                        app.set_enabled(base, lvl)
                elif r is not None and sub == "ctl":
                    # LEVEL-IN: the relay's closed state follows
                    r.set_closed(lvl)
            except Exception:  # noqa: BLE001 — a dead target must not stop the pass
                pass

    def on_wire_change(self, src=None, dst=None, removed: bool = False) -> None:
        """Hook after a binary wire edit: re-settle (and start the removed
        endpoint edge-fresh, so a later re-wire never inherits stale edge
        state). A just-added wire on a LEGACY logic endpoint (":set"/
        ":reset" from an old resume file) is canonicalized to ":a"/":b"
        here — stealing the canonical endpoint's occupant first, so the
        single-input rule holds."""
        del src
        if removed and dst is not None:
            self._edge.pop(dst, None)
        if not removed and dst is not None:
            canon = self._canon(dst)
            if canon != dst:
                moved = [w for w in self.app.ctl_wires
                         if w.get("to") == dst]
                self.steal_input(canon)     # single-input: replace occupant
                for w in moved:
                    w["to"] = canon
                self._edge.pop(dst, None)
        self.recompute()

    # -- events / state / persistence ------------------------------------------

    def _emit(self, nid: str, on: bool) -> None:
        try:
            self.app._emit_midi_event({"kind": "gate", "id": nid,
                                       "on": bool(on)})
        except Exception:  # noqa: BLE001
            pass

    def state(self) -> dict:
        return {"logics": [g.settings() for g in self.logics.values()]}

    def snapshot(self) -> dict:
        return {"logics": [{"id": g.id, "op": g.op}
                           for g in self.logics.values()]}

    def restore(self, data: dict) -> None:
        # a legacy "switches" list (pre-binary-rework presets) is IGNORED
        for g in (data or {}).get("logics", []):
            lid = g.get("id") or "logic"
            self.spawn_logic(want_id=lid)
            op = g.get("op")
            if op == "NOT":
                op = "NOR"   # migration: NOR with one wired in IS NOT
            if op in GATE_OPS:
                self.logics[lid].op = op
        self.recompute()
