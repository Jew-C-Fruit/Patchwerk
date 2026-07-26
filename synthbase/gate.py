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

GATE_OPS = ("AND", "OR", "NOR", "XOR", "SR latch", "T latch")
LOGIC_INS = ("a", "b")           # EVERY op: exactly these two, always
# ops that carry STATE across recomputes: their out is not a pure function
# of the current input levels, so entering or leaving one starts it lo
# rather than inheriting whatever the previous op happened to be showing.
_STATEFUL_OPS = frozenset({"SR latch", "T latch"})
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
        # T latch only: the last level SAMPLED at :a, so a rising edge can
        # be told from a steady hi. None = never sampled, which is how
        # "attaching a wire whose source is already hi is not an edge"
        # stays true here as it does for every other trig-in.
        self.a_prev: bool | None = None

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
            self._forget_edge(d)
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
            was_stateful = lg.op in _STATEFUL_OPS
            lg.op = op
            if was_stateful or op in _STATEFUL_OPS:
                # a latch neither survives the swap away NOR inherits the
                # previous op's out on the way in — a fresh latch starts lo.
                # SR -> T counts: they are different latches, not one latch
                # with a different label.
                lg.out = False
            lg.a_prev = None       # the next hi at :a is not a stale edge
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
        """Endpoints that hold at most ONE binary wire: logic named ins,
        relay:ctl, and (Cole, 07-24) every relay CIRCUIT in — a circuit is
        a 1:1 contact set, not a fan-in bus. Adding steals."""
        base, _, sub = str(dst).partition(":")
        if base in self.logics and (sub in LOGIC_INS or sub in _LEGACY_INS):
            return True
        if base not in getattr(self.app, "relays", {}):
            return False
        return sub == "ctl" or (sub.isdigit()
                                and 1 <= int(sub) <= MAX_CIRCUITS)

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
        self._forget_edge(dst)

    def _forget_edge(self, dst) -> None:
        """A T latch's :a lost its wire — drop the sampled level so the
        next source to land there starts edge-fresh instead of reading an
        attach as a rising edge (mirrors popping _edge for trig-ins)."""
        base, _, sub = str(dst or "").partition(":")
        lg = self.logics.get(base)
        if lg is not None and _LEGACY_INS.get(sub, sub) == "a":
            lg.a_prev = None

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
            # PHASE 1 — the combinational net, to a fixpoint. Edge-triggered
            # ops HOLD here: a toggle evaluated once per iteration would flip
            # on every pass and never converge.
            dirty = False
            for lg in self.logics.values():
                a = self._in_level(f"{lg.id}:a")
                b = self._in_level(f"{lg.id}:b")
                if lg.op == "SR latch":
                    # :a is SET, :b is RESET — reset wins
                    new = False if b else (True if a else lg.out)
                elif lg.op == "T latch":
                    # :b is RESET and wins, exactly as SR's does; the toggle
                    # at :a is an EDGE and belongs to phase 2.
                    new = False if b else lg.out
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
            if dirty:
                continue

            # PHASE 2 — sample rising edges, ONCE per real edge, and only
            # once the combinational net around them has settled. Sampling
            # updates a_prev, so a second phase-2 pass inside the same
            # recompute sees no edge; the outer loop then re-settles and
            # lets a toggle propagate into whatever it feeds (chained T
            # latches divide by 2 per stage, one stage per iteration).
            for lg in self.logics.values():
                if lg.op != "T latch":
                    continue
                a = self._in_level(f"{lg.id}:a")
                prev, lg.a_prev = lg.a_prev, a
                if not a or prev is None or prev:
                    continue           # steady, falling, or first sight
                if self._in_level(f"{lg.id}:b"):
                    continue           # held in reset — the edge is eaten
                lg.out = not lg.out
                changed_nodes.add(lg.id)
                dirty = True
            if not dirty:
                break
        for nid in sorted(changed_nodes):
            self._emit(nid, self.logics[nid].out)

    def _is_trig_dst(self, dst) -> bool:
        """EDGE-triggered destinations (a rising edge fires once): deck
        buttons, "transport:tap", deriver commits. Everything else that
        this manager drives FOLLOWS the level. Kept as a predicate rather
        than inlined in the dispatch so the reactive tap below can pick
        the right shape (momentary pulse vs. persistent level) for an
        endpoint without re-deriving the branch it took."""
        base, _, sub = str(dst).partition(":")
        if base == "deck" and sub in DECK_ACTIONS:
            return True
        if base == "transport" and sub == "tap":
            return True
        try:
            return sub == "" and self.app._deriver(base) is not None
        except Exception:  # noqa: BLE001
            return False

    def _apply_effects(self) -> None:
        """Edge-diff every wired destination endpoint against _edge:
        trig-ins fire on rising edges (never on wire-attach), level-ins
        apply on change (both directions, incl. first sight).

        EVERY endpoint this pass drives announces itself with a reactive
        tap, emitted ONCE at the bottom of the dispatch rather than per
        branch (REACTIVE-INDICATOR DOCTRINE, Cole 07-24). That placement
        is the point: a new indicator added as a new branch here reacts
        without its author having to remember the tap, which is exactly
        the gap that let transport:tap and the deck buttons ship silent.
        tests/test_reactive.py holds the line."""
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
            trig = self._is_trig_dst(dst)
            fired = False       # a trig-in that actually fired this pass
            try:
                if base == "deck" and sub in DECK_ACTIONS:
                    # TRIG-IN: rising edge presses once; attaching a wire
                    # whose source is already hi is not an edge
                    if lvl and prev is not None:
                        app.set_looper(action=DECK_ACTIONS[sub])
                        fired = True
                elif base == "transport":
                    # item 9: the GLOBAL transport's binary ins
                    if sub == "tap":
                        # TRIG-IN: rising edge = one tap (TEMPO only);
                        # attach-while-hi is not an edge
                        if lvl and prev is not None:
                            app._transport_tap()
                            fired = True
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
                        fired = True
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
                # the ONE reactive tap for every endpoint above. It sits
                # INSIDE the try on purpose: a target that threw did not
                # change, and a stale indicator is honester than a lying
                # one. Trig-ins are momentary, level-ins persist.
                if trig:
                    if fired:
                        self._pulse_level(dst)
                else:
                    self._emit_level(dst, lvl)
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

    def _emit_level(self, endpoint: str, on: bool, pulse: bool = False) -> None:
        """Announce that a binary in drove its target (REACTIVE-INDICATOR
        DOCTRINE, Cole 07-24): the GUI's power stripes, transport play/stop
        and click/accent lights must react to LOGIC input exactly as they
        react to a click. These applications happen deep in the settle pass
        (app.set_enabled / set_transport), which deliberately do NOT
        broadcast state — so without this event the indicator would stay
        stale until the next unrelated broadcast.

        `pulse` marks the tap as MOMENTARY — a trig-in firing, with no
        level to hold. The flag is carried on the EVENT rather than left
        for the GUI to infer from the endpoint name, and that is
        load-bearing: a receiver that has to keep its own list of which
        endpoints flash is a list that rots the first time a trig-in is
        added, which is the exact failure this whole doctrine exists to
        prevent. Dispatch on the flag, never on the name. Absent (not
        False) on steady taps, so a steady event stays byte-identical to
        what clients already parse."""
        try:
            ev = {"kind": "level", "ep": str(endpoint), "on": bool(on)}
            if pulse:
                ev["pulse"] = True
            self.app._emit_midi_event(ev)
        except Exception:  # noqa: BLE001
            pass

    def _pulse_level(self, endpoint: str) -> None:
        """A TRIG-IN fired. Its indicator is MOMENTARY — there is no level
        to hold — so announce the pulse the binary plane itself uses: hi,
        then lo, BOTH tagged `pulse`.

        The two land in the same tick, always: this pulse has zero real
        duration. A receiver that routes it through a plain level setter
        therefore renders NOTHING — it sets on, then immediately off,
        inside one frame, and the fix looks implemented while being dead
        on arrival. The `pulse` tag is the instruction not to do that:
        stretch it (blocks.html's pulseBin/PULSE_MS decay idiom, which
        already exists for the {"kind": "gate"} LEDs) and ignore the
        trailing lo. The lo is still sent so the stream stays honest for
        anything replaying or analysing paired edges."""
        self._emit_level(endpoint, True, pulse=True)
        self._emit_level(endpoint, False, pulse=True)

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
