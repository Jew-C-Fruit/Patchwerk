"""GATE suite (item 8) tests — CI-safe: no scsynth, no audio, no MIDI.

    python tests/test_gate.py

Covers: switch/logic spawn + id alloc + settings/state shapes, the logic
truth tables (AND/OR/XOR fan-in, NOT-as-NOR, no-inputs → lo for EVERY
op), the SR latch (named set/reset ins, hold, reset-wins, op-swap wire
drops + latch clear), chaining + bounded feedback settle + self-wire
rejection, the wire grammar (gate levels land ONLY on toggle targets;
pings land on the same endpoints via the alternator; ping → deriver
regression), toggle effects through a fake rack (:pwr enable follows the
level, attach-while-hi applies immediately), deck buttons (rising edge
presses once, attach is not an edge, ping presses), removal hygiene
(node removal drops wires + alternator latches; edit_chain remove drops
"<key>:pwr" wires), preset snapshot/restore, and the gate GUI events.

Chain-module toggles run against a duck-typed RecordingRack
(test_graph's pattern) so the exact set_enabled traffic is assertable.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.gate import GATE_OPS  # noqa: E402
from synthbase import presets  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def seed_c_major(d):
    for _ in range(4):
        d.est.observe(48)
        d.est.observe(60)
        d.est.observe(67)


# ---- fakes (test_graph's RecordingRack shape + set_enabled/detach) -----------

class FakeNode:
    def __init__(self):
        self.sets = []

    def set(self, **kw):
        self.sets.append(kw)


def fake_inst(key, kind):
    return SimpleNamespace(key=key, module=SimpleNamespace(kind=kind),
                           settings={}, service=False, node=FakeNode(),
                           enabled=True, type=key.split(".", 1)[0])


class RecordingRack:
    """Duck-typed rack that records calls; no server anywhere.
    app.set_enabled goes through rack.set_enabled; gates.is_toggle_dst
    resolves "<key>:pwr" through rack.find."""

    def __init__(self, keys_kinds, wires=()):
        self.instances = [fake_inst(k, kind) for k, kind in keys_kinds]
        self._wires = [dict(w) for w in wires]
        self.calls = []
        self.mapped = set()

    def find(self, key):
        for i in self.instances:
            if i.key == key:
                return i
        raise KeyError(key)

    def audio_wires(self):
        return [dict(w) for w in self._wires]

    def audio_rewire(self, src, dst):
        self.calls.append(("rewire", src, dst))

    def audio_disconnect(self, src):
        self.calls.append(("disconnect", src))

    def reorder_for_wires(self, wires):
        self.calls.append(("reorder", len(wires)))

    def set_enabled(self, key, enabled):
        self.calls.append(("set_enabled", key, bool(enabled)))
        self.find(key).enabled = bool(enabled)

    def detach_instance(self, key):
        self.calls.append(("detach", key))
        self.instances = [i for i in self.instances if i.key != key]


class FakeArp:
    """app.set_arp calls configure(**kw) then settings(); enabled is the
    state under test for the "arp:pwr" toggle target."""

    def __init__(self):
        self.enabled = False

    def configure(self, **kw):
        if kw.get("enabled") is not None:
            self.enabled = bool(kw["enabled"])

    def settings(self):
        return {"enabled": self.enabled}


def out(app, lid):
    return app.gates.logics[lid].out


# ---- spawn / id alloc / settings shapes --------------------------------------

def test_spawn_and_state():
    app = SynthApp(use_midi=False, use_reload=False)
    check("first switch id", app.spawn_switch() == "switch")
    check("second switch suffixes", app.spawn_switch() == "switch.2")
    check("first logic id", app.spawn_logic() == "logic")
    check("second logic suffixes", app.spawn_logic() == "logic.2")

    check("switch settings shape",
          app.gates.switches["switch"].settings()
          == {"id": "switch", "on": False})
    ls = app.gates.logics["logic"].settings()
    check("logic settings shape",
          ls == {"id": "logic", "op": "AND", "ops": list(GATE_OPS),
                 "out": False})
    check("op list is the 5-op ladder ending in SR latch",
          len(GATE_OPS) == 5 and GATE_OPS[-1] == "SR latch")

    st = app.state()
    check("state carries switches",
          [s["id"] for s in st["switches"]] == ["switch", "switch.2"])
    check("state carries logics",
          [g["id"] for g in st["logics"]] == ["logic", "logic.2"])

    app.remove_switch("switch.2")
    app.remove_logic("logic.2")
    check("removed nodes gone",
          "switch.2" not in app.gates.switches
          and "logic.2" not in app.gates.logics)
    try:
        app.remove_switch("switch.2")
        check("double remove raises", False)
    except KeyError:
        check("double remove raises", True)


# ---- truth tables ------------------------------------------------------------

def test_truth_tables():
    app = SynthApp(use_midi=False, use_reload=False)
    s1 = app.spawn_switch()
    s2 = app.spawn_switch()
    lid = app.spawn_logic()

    # no inputs → lo, for EVERY op (a fresh card must never power things on)
    for op in GATE_OPS:
        app.set_logic(lid, op=op)
        check(f"no inputs → lo under {op}", out(app, lid) is False)
    app.set_logic(lid, op="NOT")
    check("NOT of nothing is explicitly lo (not hi)", out(app, lid) is False)

    # single input on the bare fan-in
    app.set_logic(lid, op="AND")
    app.set_ctl_wire("add", s1, lid)
    check("AND single lo input → lo", out(app, lid) is False)
    app.set_switch(s1, on=True)
    check("switch flip propagates immediately (AND 1-in hi)",
          out(app, lid) is True)
    app.set_logic(lid, op="OR")
    check("OR single hi input → hi", out(app, lid) is True)
    app.set_logic(lid, op="XOR")
    check("XOR single hi input → hi", out(app, lid) is True)
    app.set_switch(s1, on=False)
    check("XOR single lo input → lo", out(app, lid) is False)
    app.set_logic(lid, op="NOT")
    check("NOT single lo input → hi", out(app, lid) is True)
    app.set_switch(s1, on=True)
    check("NOT single hi input → lo", out(app, lid) is False)

    # two inputs
    app.set_ctl_wire("add", s2, lid)

    def levels(a, b):
        app.set_switch(s1, on=a)
        app.set_switch(s2, on=b)

    app.set_logic(lid, op="AND")
    levels(True, True)
    check("AND T,T → T", out(app, lid) is True)
    levels(True, False)
    check("AND T,F → F", out(app, lid) is False)
    levels(False, False)
    check("AND F,F → F", out(app, lid) is False)

    app.set_logic(lid, op="OR")
    levels(True, False)
    check("OR T,F → T", out(app, lid) is True)
    levels(False, False)
    check("OR F,F → F", out(app, lid) is False)

    app.set_logic(lid, op="XOR")
    levels(True, True)
    check("XOR T,T → F", out(app, lid) is False)
    levels(True, False)
    check("XOR T,F → T", out(app, lid) is True)
    levels(False, False)
    check("XOR F,F → F", out(app, lid) is False)

    app.set_logic(lid, op="NOT")
    levels(False, False)
    check("NOT (NOR) F,F → T", out(app, lid) is True)
    levels(True, False)
    check("NOT (NOR) T,F → F", out(app, lid) is False)


# ---- SR latch ----------------------------------------------------------------

def test_sr_latch():
    app = SynthApp(use_midi=False, use_reload=False)
    s_bare = app.spawn_switch()
    s_set = app.spawn_switch()
    s_reset = app.spawn_switch()
    lid = app.spawn_logic()

    # a bare fan-in wire exists; swapping to SR latch DROPS it (visible)
    app.set_ctl_wire("add", s_bare, lid)
    check("bare wire in place pre-swap",
          {"from": s_bare, "to": lid} in app.ctl_wires)
    app.set_logic(lid, op="SR latch")
    check("op swap to SR latch drops bare-id wires",
          {"from": s_bare, "to": lid} not in app.ctl_wires)

    # named set/reset ins
    app.set_ctl_wire("add", s_set, f"{lid}:set")
    app.set_ctl_wire("add", s_reset, f"{lid}:reset")
    check("latch starts lo", out(app, lid) is False)
    app.set_switch(s_set, on=True)
    check("set hi → latch hi", out(app, lid) is True)
    app.set_switch(s_set, on=False)
    check("latch holds after set drops", out(app, lid) is True)
    app.set_switch(s_set, on=True)
    app.set_switch(s_reset, on=True)
    check("reset wins when both hi", out(app, lid) is False)
    app.set_switch(s_reset, on=False)
    check("set still hi → latch hi again", out(app, lid) is True)

    # swapping op away drops the named wires AND clears the latch
    app.set_logic(lid, op="AND")
    check("op swap away drops set/reset wires",
          not any(w["to"] in (f"{lid}:set", f"{lid}:reset")
                  for w in app.ctl_wires))
    check("latch state does not survive the op swap (out lo)",
          out(app, lid) is False)


# ---- chaining + feedback -----------------------------------------------------

def test_chaining_and_feedback():
    app = SynthApp(use_midi=False, use_reload=False)
    s = app.spawn_switch()
    a = app.spawn_logic()          # NOT stage
    b = app.spawn_logic()          # AND stage
    app.set_logic(a, op="NOT")
    app.set_ctl_wire("add", s, a)
    app.set_ctl_wire("add", a, b)  # logic out chains into a logic in
    check("chain settles in one recompute (s lo → NOT hi → AND hi)",
          out(app, a) is True and out(app, b) is True)
    app.set_switch(s, on=True)
    check("chain re-settles on flip (s hi → NOT lo → AND lo)",
          out(app, a) is False and out(app, b) is False)

    # 2-node feedback loop: legal to patch, settles (bounded — never spins)
    app2 = SynthApp(use_midi=False, use_reload=False)
    la = app2.spawn_logic()
    lb = app2.spawn_logic()
    app2.set_logic(la, op="NOT")
    app2.set_logic(lb, op="NOT")
    app2.set_ctl_wire("add", la, lb)
    app2.set_ctl_wire("add", lb, la)      # returns ⇒ the settle pass bounded
    check("feedback loop reaches a stable complementary state",
          out(app2, la) != out(app2, lb))
    before = (out(app2, la), out(app2, lb))
    app2.gates.recompute()
    check("feedback fixpoint is stable under recompute",
          (out(app2, la), out(app2, lb)) == before)

    # direct self-wire is not legal
    try:
        app2.set_ctl_wire("add", la, la)
        check("direct self-wire rejected", False)
    except ValueError:
        check("direct self-wire rejected", True)


# ---- wire grammar ------------------------------------------------------------

def test_wire_grammar():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack([("pluck", "source"), ("echo", "effect")])
    s = app.spawn_switch()

    # gate out → note sink: refused (a gate is a level, not notes)
    try:
        app.set_ctl_wire("add", s, "arp")
        check("gate→arp note sink refused", False)
    except ValueError:
        check("gate→arp note sink refused", True)

    # gate out → deriver: refused (derivers take ping TRIGGERS, not levels)
    tid = app.spawn_tonic()
    try:
        app.set_ctl_wire("add", s, tid)
        check("gate→deriver refused", False)
    except ValueError:
        check("gate→deriver refused", True)

    # ping → "<key>:pwr": ACCEPTED, alternator semantics (1→hi, 2→lo)
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, "echo:pwr")
    check("ping→module :pwr accepted",
          {"from": bid, "to": "echo:pwr"} in app.ctl_wires)
    app.fire_button(bid)
    check("ping 1 → hi (module enabled)",
          app.rack.find("echo").enabled is True
          and ("set_enabled", "echo", True) in app.rack.calls)
    app.fire_button(bid)
    check("ping 2 → lo (module disabled)",
          app.rack.find("echo").enabled is False
          and ("set_enabled", "echo", False) in app.rack.calls)

    # ping → switch id flips the switch
    b2 = app.spawn_button()
    app.set_ctl_wire("add", b2, s)
    app.fire_button(b2)
    check("ping flips the switch on", app.gates.switches[s].on is True)
    app.fire_button(b2)
    check("second ping flips it back off", app.gates.switches[s].on is False)

    # ping → deriver still works (regression)
    b3 = app.spawn_button()
    app.set_ctl_wire("add", b3, tid)
    d = app.tonics[tid]
    seed_c_major(d)
    check("no commit before the ping", d.root is None)
    app.fire_button(b3)
    check("ping→deriver regression: fire commits", d.root == 0)

    for dd in app.tonics.values():
        dd.shutdown()


# ---- toggle effects (fake rack + fake arp) -----------------------------------

def test_toggle_effects():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack([("pluck", "source"), ("echo", "effect")])
    app.arp = FakeArp()

    s = app.spawn_switch()
    app.set_ctl_wire("add", s, "echo:pwr")
    app.set_switch(s, on=True)
    check("gate hi → rack.set_enabled(key, True)",
          ("set_enabled", "echo", True) in app.rack.calls
          and app.rack.find("echo").enabled is True)
    app.set_switch(s, on=False)
    check("gate lo → rack.set_enabled(key, False)",
          ("set_enabled", "echo", False) in app.rack.calls
          and app.rack.find("echo").enabled is False)

    # "arp:pwr" drives set_arp(enabled=...)
    app.set_ctl_wire("add", s, "arp:pwr")
    app.set_switch(s, on=True)
    check("arp:pwr hi enables the arp", app.arp.enabled is True)
    app.set_switch(s, on=False)
    check("arp:pwr lo disables the arp", app.arp.enabled is False)

    # attach-while-hi: :pwr targets take the LEVEL immediately (no edge)
    s2 = app.spawn_switch()
    app.set_switch(s2, on=True)
    app.rack.calls.clear()
    app.set_ctl_wire("add", s2, "pluck:pwr")
    check("attach-while-hi applies the level for :pwr",
          ("set_enabled", "pluck", True) in app.rack.calls
          and app.rack.find("pluck").enabled is True)


# ---- deck buttons (momentary: rising edge presses once) ----------------------

def test_deck_buttons():
    app = SynthApp(use_midi=False, use_reload=False)
    presses = []
    app.set_looper = lambda **kw: presses.append(dict(kw))

    s = app.spawn_switch()
    app.set_switch(s, on=True)              # HI before the wire exists
    app.set_ctl_wire("add", s, "deck:play")
    check("attach-while-hi does NOT press a deck button (no edge)",
          presses == [])
    app.set_switch(s, on=False)
    check("hi→lo does not press", presses == [])
    app.set_switch(s, on=True)
    check("lo→hi presses exactly once", presses == [{"action": "play"}])
    app.set_switch(s, on=False)
    check("release after the press stays silent",
          presses == [{"action": "play"}])

    # a ping into a deck button simply presses; endpoint map rec→record
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, "deck:rec")
    app.fire_button(bid)
    check("ping→deck:rec presses (rec→record endpoint map)",
          presses == [{"action": "play"}, {"action": "record"}])


# ---- removal hygiene ---------------------------------------------------------

def test_removal_hygiene():
    app = SynthApp(use_midi=False, use_reload=False)
    s = app.spawn_switch()
    lid = app.spawn_logic()
    app.set_ctl_wire("add", s, lid)
    app.set_switch(s, on=True)
    check("input hi drives the AND hi (pre-removal)", out(app, lid) is True)
    app.remove_switch(s)
    check("remove_switch drops its wires",
          not any(s in (w.get("from"), w.get("to")) for w in app.ctl_wires))
    check("logic recomputed after its input vanished (no inputs → lo)",
          out(app, lid) is False)

    # a fired ping→logic wire leaves an alternator latch; removing the
    # logic must clear it (no dangling latches)
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, lid)
    app.fire_button(bid)
    check("alternator latch drives the logic hi",
          out(app, lid) is True and app.gates._alt != {})
    app.remove_logic(lid)
    check("remove_logic drops its wires",
          not any(lid in (w.get("from"), str(w.get("to")).split(":", 1)[0])
                  for w in app.ctl_wires))
    check("remove_logic clears dangling alternator latches",
          app.gates._alt == {})

    # edit_chain remove drops the departed module's "<key>:pwr" wires
    app2 = SynthApp(use_midi=False, use_reload=False)
    app2.rack = RecordingRack(
        [("pluck", "source"), ("echo", "effect")],
        [{"from": "pluck", "to": "echo"}, {"from": "echo", "to": "master"}],
    )
    s2 = app2.spawn_switch()
    app2.set_ctl_wire("add", s2, "echo:pwr")
    check("pwr wire in place pre-surgery",
          {"from": s2, "to": "echo:pwr"} in app2.ctl_wires)
    app2.edit_chain("remove", "echo")
    check("edit_chain remove drops the module's :pwr wires",
          not any(w.get("to") == "echo:pwr" for w in app2.ctl_wires))


# ---- persistence -------------------------------------------------------------

def test_persistence():
    app = SynthApp(use_midi=False, use_reload=False)
    sid = app.spawn_switch()
    app.set_switch(sid, on=True)
    lid = app.spawn_logic()
    app.set_logic(lid, op="XOR")
    data = presets.snapshot(app)
    check("snapshot carries gates (switch on-state + logic op)",
          data["gates"] == {"switches": [{"id": sid, "on": True}],
                            "logics": [{"id": lid, "op": "XOR"}]})

    app2 = SynthApp(use_midi=False, use_reload=False)
    app2._build_patch = lambda name: None
    app2.rack = RecordingRack([("pluck", "source")])
    presets._apply(app2, data)
    check("restore respawns the switch ON",
          app2.gates.switches.get(sid) is not None
          and app2.gates.switches[sid].on is True)
    check("restore respawns the logic with its op",
          app2.gates.logics.get(lid) is not None
          and app2.gates.logics[lid].op == "XOR")
    check("restore recomputes (unwired XOR is lo)", out(app2, lid) is False)

    # resume order: _apply restores gates FIRST, then ctl_wires re-add —
    # a switch restored ON must drive a re-added :pwr wire immediately
    app2.rack.calls.clear()
    app2.set_ctl_wire("add", sid, "pluck:pwr")
    check("restored-ON switch drives a resumed :pwr wire",
          ("set_enabled", "pluck", True) in app2.rack.calls
          and app2.rack.find("pluck").enabled is True)


# ---- GUI events --------------------------------------------------------------

def test_events():
    app = SynthApp(use_midi=False, use_reload=False)
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))

    s = app.spawn_switch()
    app.set_switch(s, on=True)
    check("switch flip emits a gate event",
          {"kind": "gate", "id": s, "on": True} in events)

    n = len(events)
    app.set_switch(s, on=True)              # no change
    check("no event without an output change", len(events) == n)

    lid = app.spawn_logic()
    app.set_logic(lid, op="NOT")
    app.set_ctl_wire("add", s, lid)         # s hi → NOT lo: no change, quiet
    check("logic output change emits nothing while unchanged",
          not any(e.get("id") == lid for e in events))
    app.set_switch(s, on=False)             # s lo emits; NOT hi emits
    check("switch lo emits its gate event",
          {"kind": "gate", "id": s, "on": False} in events)
    check("logic output change emits its gate event",
          {"kind": "gate", "id": lid, "on": True} in events)


def main():
    test_spawn_and_state()
    test_truth_tables()
    test_sr_latch()
    test_chaining_and_feedback()
    test_wire_grammar()
    test_toggle_effects()
    test_deck_buttons()
    test_removal_hygiene()
    test_persistence()
    test_events()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
