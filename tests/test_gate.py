"""BINARY plane tests (binary rework) — CI-safe: no scsynth, no audio,
no MIDI.

    python tests/test_gate.py

ONE hi/lo signal kind: sources own LEVELS, edges DERIVE from level
changes, a ping is just a pulse (hi-then-lo) propagating through the
graph. Covers: logic gates (named single-input ins :a/:b, NOT :a only,
SR :set/:reset; bare-id dst refused; truth tables via latched buttons;
unwired in = lo; steal-on-drop; op-swap wire drops + latch clear; SR
protocol), the rising-edge trig system (momentary press fires once,
latch fires on latch-on only, clock pulse ticks, attach-while-hi is not
an edge), PING-THRU-LOGIC (a clock pulse passes an AND while the other
leg is held hi), level ins (:pwr follows the level both ways incl.
first sight, deck buttons rising-edge press, arp:pwr), bounded feedback
settle + self-wire rejection, the Relay (notes / binary / audio
circuits, kind claim + mismatch rejection, relay:ctl level control +
set_relay last-writer-wins, removal hygiene on both wire planes),
preset snapshot/restore (logics + relays + button latch; legacy
"switches" ignored), and the {"kind": "gate"} GUI events.

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
    resolves "<key>:pwr" through rack.find; relay audio resolution
    lands in audio_rewire/audio_disconnect/reorder_for_wires."""

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
    state under test for the "arp:pwr" level-in."""

    def __init__(self):
        self.enabled = False

    def configure(self, **kw):
        if kw.get("enabled") is not None:
            self.enabled = bool(kw["enabled"])

    def settings(self):
        return {"enabled": self.enabled}


class FakeNoteSink:
    """MonoVoice-shaped recorder (test_graph's pattern) for the relay
    notes-circuit checks."""

    def __init__(self):
        self.ons, self.offs = [], []
        self.all_offs = 0
        self.target_key = None

    def note_on(self, note, velocity=100): self.ons.append(note)
    def note_off(self, note): self.offs.append(note)
    def all_off(self): self.all_offs += 1
    def set_sustain(self, on): pass
    def set_bend(self, semitones): pass


def out(app, lid):
    return app.gates.logics[lid].out


def latch_button(app):
    """A latched (persistent) button — the level source for truth tables."""
    bid = app.spawn_button()
    app.set_button(bid, latch=True)
    return bid


def set_lvl(app, bid, lvl):
    """Drive a latched button's level to lvl (toggle only when needed)."""
    if app.buttons[bid].level != bool(lvl):
        app.button_down(bid)


def hook(app, tid):
    """Replace a deriver's trigger with a counter; returns the hit list."""
    hits = []
    app._deriver(tid).trigger = lambda: hits.append(1)
    return hits


# ---- spawn / ids / settings / state shapes -----------------------------------

def test_spawn_and_state():
    app = SynthApp(use_midi=False, use_reload=False)
    check("the SwitchGate is gone (no spawn_switch)",
          not hasattr(app, "spawn_switch"))
    check("first logic id", app.spawn_logic() == "logic")
    check("second logic suffixes", app.spawn_logic() == "logic.2")
    check("first relay id", app.spawn_relay() == "relay")
    check("second relay suffixes", app.spawn_relay() == "relay.2")

    ls = app.gates.logics["logic"].settings()
    check("logic settings shape",
          ls == {"id": "logic", "op": "AND", "ops": list(GATE_OPS),
                 "out": False})
    check("op list is the 5-op ladder ending in SR latch",
          len(GATE_OPS) == 5 and GATE_OPS[-1] == "SR latch")
    rs = app.relays["relay"].settings()
    check("relay settings shape (open by default, no circuits yet)",
          rs == {"id": "relay", "closed": False, "circuits": {}})

    st = app.state()
    check("state carries logics",
          [g["id"] for g in st["logics"]] == ["logic", "logic.2"])
    check("state carries relays",
          [r["id"] for r in st["relays"]] == ["relay", "relay.2"])
    check("state has NO switches key", "switches" not in st)

    app.remove_logic("logic.2")
    app.remove_relay("relay.2")
    check("removed nodes gone",
          "logic.2" not in app.gates.logics and "relay.2" not in app.relays)
    try:
        app.remove_logic("logic.2")
        check("double logic remove raises", False)
    except KeyError:
        check("double logic remove raises", True)
    try:
        app.remove_relay("relay.2")
        check("double relay remove raises", False)
    except KeyError:
        check("double relay remove raises", True)


# ---- wire grammar: named single-input ins ------------------------------------

def test_wire_grammar():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_logic()
    b1 = latch_button(app)
    b2 = latch_button(app)

    # bare-id destinations died with the fan-in model
    try:
        app.set_ctl_wire("add", b1, lid)
        check("bare-id logic dst refused", False)
    except ValueError:
        check("bare-id logic dst refused", True)

    # named ins follow the op's shape
    app.set_ctl_wire("add", b1, f"{lid}:a")
    check("named :a in accepted",
          {"from": b1, "to": f"{lid}:a"} in app.ctl_wires)
    try:
        app.set_ctl_wire("add", b1, f"{lid}:c")
        check("unknown named in refused", False)
    except ValueError:
        check("unknown named in refused", True)

    # steal-on-drop: a second wire into an occupied single-input REPLACES
    app.set_ctl_wire("add", b2, f"{lid}:a")
    check("steal-on-drop replaces the first wire",
          {"from": b1, "to": f"{lid}:a"} not in app.ctl_wires
          and {"from": b2, "to": f"{lid}:a"} in app.ctl_wires)
    check("stolen endpoint holds exactly one wire",
          sum(1 for w in app.ctl_wires if w["to"] == f"{lid}:a") == 1)

    # NOT exposes :a only
    app.set_logic(lid, op="NOT")
    try:
        app.set_ctl_wire("add", b1, f"{lid}:b")
        check("NOT has no :b in", False)
    except ValueError:
        check("NOT has no :b in", True)

    # a binary source can't land on a note sink
    try:
        app.set_ctl_wire("add", b1, "arp")
        check("binary→arp note sink refused", False)
    except ValueError:
        check("binary→arp note sink refused", True)


# ---- truth tables (latched buttons as level sources) -------------------------

def test_truth_tables():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_logic()

    # unwired ins read lo — AND/OR/XOR sit lo; NOT of lo is hi (an honest
    # inverter, unlike the old NOT-as-NOR-of-nothing)
    for op in ("AND", "OR", "XOR"):
        app.set_logic(lid, op=op)
        check(f"unwired ins → lo under {op}", out(app, lid) is False)
    app.set_logic(lid, op="SR latch")
    check("unwired SR latch sits lo", out(app, lid) is False)
    app.set_logic(lid, op="NOT")
    check("NOT of an unwired (lo) in is hi", out(app, lid) is True)
    # NOTE (backend behavior): swapping INTO SR while the previous op's
    # out is hi seeds the latch hi — only LEAVING SR clears it. Tested
    # from a lo out above; the hi-seeding path is left as documented.

    b1 = latch_button(app)
    b2 = latch_button(app)
    app.set_logic(lid, op="AND")
    app.set_ctl_wire("add", b1, f"{lid}:a")
    app.set_ctl_wire("add", b2, f"{lid}:b")

    def levels(a, b):
        set_lvl(app, b1, a)
        set_lvl(app, b2, b)

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

    # op swap to NOT: the :b endpoint dies, its wire drops with it
    app.set_ctl_wire("add", b2, f"{lid}:b")   # re-add after XOR tests
    app.set_logic(lid, op="NOT")
    check("op swap drops dead-shaped wires (:b gone)",
          not any(w["to"] == f"{lid}:b" for w in app.ctl_wires))
    check(":a wire survives the swap",
          any(w["to"] == f"{lid}:a" for w in app.ctl_wires))
    set_lvl(app, b1, False)
    check("NOT lo → hi", out(app, lid) is True)
    set_lvl(app, b1, True)
    check("NOT hi → lo", out(app, lid) is False)


# ---- SR latch ----------------------------------------------------------------

def test_sr_latch():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_logic()
    b_a = latch_button(app)
    b_set = latch_button(app)
    b_reset = latch_button(app)

    # a :a wire exists; swapping to SR latch DROPS it (shape died, visible)
    app.set_ctl_wire("add", b_a, f"{lid}:a")
    app.set_logic(lid, op="SR latch")
    check("op swap to SR drops the :a wire",
          not any(w["to"] == f"{lid}:a" for w in app.ctl_wires))

    # the full protocol on the named set/reset ins
    app.set_ctl_wire("add", b_set, f"{lid}:set")
    app.set_ctl_wire("add", b_reset, f"{lid}:reset")
    check("latch starts lo", out(app, lid) is False)
    set_lvl(app, b_set, True)
    check("set hi → latch hi", out(app, lid) is True)
    set_lvl(app, b_set, False)
    check("latch holds after set drops", out(app, lid) is True)
    set_lvl(app, b_set, True)
    set_lvl(app, b_reset, True)
    check("reset wins when both hi", out(app, lid) is False)
    set_lvl(app, b_reset, False)
    check("set still hi → latch hi again", out(app, lid) is True)

    # leaving SR: named wires drop AND the latch clears
    app.set_logic(lid, op="AND")
    check("op swap away drops set/reset wires",
          not any(w["to"] in (f"{lid}:set", f"{lid}:reset")
                  for w in app.ctl_wires))
    check("latch state does not survive leaving SR (out lo)",
          out(app, lid) is False)


# ---- rising-edge trig system -------------------------------------------------

def test_trig_edges():
    app = SynthApp(use_midi=False, use_reload=False)

    # momentary button: press fires the wired deriver ONCE, release never
    t1 = app.spawn_tonic()
    b = app.spawn_button()
    app.set_ctl_wire("add", b, t1)
    hits1 = hook(app, t1)
    check("wire-attach (source lo) fires nothing", hits1 == [])
    app.button_down(b)
    check("momentary press fires the deriver once", hits1 == [1])
    app.button_up(b)
    check("release fires nothing", hits1 == [1])
    app.button_down(b)
    app.button_up(b)
    check("next press fires again (fresh edge)", hits1 == [1, 1])

    # latched button: fires on latch-ON only
    t2 = app.spawn_tonic()
    bl = latch_button(app)
    app.set_ctl_wire("add", bl, t2)
    hits2 = hook(app, t2)
    app.button_down(bl)          # latch on
    check("latch-on fires once", hits2 == [1])
    app.button_down(bl)          # latch off
    check("latch-off fires nothing", hits2 == [1])

    # wire-attach with the source already HI is NOT an edge
    t3 = app.spawn_tonic()
    bh = latch_button(app)
    app.button_down(bh)          # hi BEFORE the wire exists
    app.set_ctl_wire("add", bh, t3)
    hits3 = hook(app, t3)
    check("attach-while-hi does NOT fire", hits3 == [])
    app.button_down(bh)          # hi → lo: silent
    app.button_down(bh)          # lo → hi: the real edge
    check("only the post-attach rising edge fires", hits3 == [1])

    # clock fire() = a pulse: the wired deriver commits once per tick
    t4 = app.spawn_tonic()
    cid = app.spawn_clock()
    c = app.clocks[cid]
    c.shutdown()                 # stop the grid thread; tick manually
    app.set_ctl_wire("add", cid, t4)
    hits4 = hook(app, t4)
    c.fire()
    check("clock tick pulses the deriver once", hits4 == [1])
    c.fire()
    check("each tick is its own pulse", hits4 == [1, 1])
    check("the clock's persistent level stays lo",
          app.gates.level_of_src(cid) is False)

    for d in app.tonics.values():
        d.shutdown()


# ---- PING-THRU-LOGIC (the headline feature) ----------------------------------

def test_ping_thru_logic():
    app = SynthApp(use_midi=False, use_reload=False)
    cid = app.spawn_clock()
    c = app.clocks[cid]
    c.shutdown()                 # manual ticks only
    lid = app.spawn_logic()      # AND
    bl = latch_button(app)
    tid = app.spawn_tonic()

    app.set_ctl_wire("add", cid, f"{lid}:a")
    app.set_ctl_wire("add", bl, f"{lid}:b")
    app.set_ctl_wire("add", lid, tid)
    hits = hook(app, tid)

    c.fire()
    check("clock through AND with :b lo commits nothing", hits == [])
    set_lvl(app, bl, True)
    check("latching :b hi is itself no edge at the deriver", hits == [])
    c.fire()
    check("clock pulse passes THROUGH the AND while :b is hi (1 commit)",
          len(hits) == 1)
    c.fire()
    check("every tick passes while :b holds (2 commits)", len(hits) == 2)
    check("logic out falls back lo after the pulse", out(app, lid) is False)
    set_lvl(app, bl, False)
    c.fire()
    check(":b lo blocks the pulse again", len(hits) == 2)

    for d in app.tonics.values():
        d.shutdown()


# ---- level ins: :pwr / arp:pwr / deck buttons --------------------------------

def test_level_ins():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack([("pluck", "source"), ("echo", "effect")])
    app.arp = FakeArp()

    # hold-to-enable: a momentary button's level drives :pwr both ways
    b = app.spawn_button()
    app.set_ctl_wire("add", b, "echo:pwr")
    app.rack.calls.clear()
    app.button_down(b)
    check(":pwr follows the held button (hi → enabled)",
          ("set_enabled", "echo", True) in app.rack.calls
          and app.rack.find("echo").enabled is True)
    app.button_up(b)
    check(":pwr follows the release (lo → disabled)",
          ("set_enabled", "echo", False) in app.rack.calls
          and app.rack.find("echo").enabled is False)

    # attach-while-hi: level-ins take the level immediately (first sight)
    bh = latch_button(app)
    app.button_down(bh)
    app.rack.calls.clear()
    app.set_ctl_wire("add", bh, "pluck:pwr")
    check("attach-while-hi applies the level for :pwr",
          ("set_enabled", "pluck", True) in app.rack.calls
          and app.rack.find("pluck").enabled is True)

    # arp:pwr drives set_arp(enabled=...)
    bl = latch_button(app)
    app.set_ctl_wire("add", bl, "arp:pwr")
    set_lvl(app, bl, True)
    check("arp:pwr hi enables the arp", app.arp.enabled is True)
    set_lvl(app, bl, False)
    check("arp:pwr lo disables the arp", app.arp.enabled is False)

    # deck buttons: TRIG-ins — rising edge presses once, attach never
    presses = []
    app.set_looper = lambda **kw: presses.append(dict(kw))
    bd = latch_button(app)
    app.button_down(bd)                      # HI before the wire exists
    app.set_ctl_wire("add", bd, "deck:play")
    check("attach-while-hi does NOT press a deck button (no edge)",
          presses == [])
    app.button_down(bd)                      # hi → lo
    check("hi→lo does not press", presses == [])
    app.button_down(bd)                      # lo → hi
    check("lo→hi presses exactly once", presses == [{"action": "play"}])
    app.button_down(bd)                      # back lo
    check("falling stays silent", presses == [{"action": "play"}])

    # a momentary pulse into deck:rec presses (rec→record endpoint map)
    bm = app.spawn_button()
    app.set_ctl_wire("add", bm, "deck:rec")
    app.fire_button(bm)
    check("pulse→deck:rec presses (rec→record endpoint map)",
          presses == [{"action": "play"}, {"action": "record"}])


# ---- feedback ----------------------------------------------------------------

def test_feedback():
    app = SynthApp(use_midi=False, use_reload=False)
    la = app.spawn_logic()
    lb = app.spawn_logic()
    app.set_logic(la, op="NOT")
    app.set_logic(lb, op="NOT")
    app.set_ctl_wire("add", la, f"{lb}:a")
    app.set_ctl_wire("add", lb, f"{la}:a")   # returns ⇒ settle pass bounded
    check("cross loop settles to a stable complementary state",
          out(app, la) != out(app, lb))
    before = (out(app, la), out(app, lb))
    app.gates.recompute()
    check("feedback fixpoint stable under recompute",
          (out(app, la), out(app, lb)) == before)

    try:
        app.set_ctl_wire("add", la, f"{la}:a")
        check("direct self-wire refused", False)
    except ValueError:
        check("direct self-wire refused", True)


# ---- relay: notes circuit ----------------------------------------------------

def test_relay_notes():
    app = SynthApp(use_midi=False, use_reload=False)
    app.arp = FakeNoteSink()
    rid = app.spawn_relay()
    app.ctl_wires = []                       # drop the default keys→arp path
    app.set_ctl_wire("add", "keys", f"{rid}:1")
    app.set_ctl_wire("add", f"{rid}:1", "arp")
    check("notes circuit claims its kind",
          app.relays[rid].kinds.get(1) == "notes")

    app.note_on(60)
    check("open relay blocks notes", app.arp.ons == [])
    app.set_relay(rid, closed=True)
    app.note_on(62)
    check("closed relay passes notes", app.arp.ons == [62])

    app.note_on(64)                          # held through the opening
    n_offs = app.arp.all_offs
    app.set_relay(rid, closed=False)
    check("opening all_offs downstream (no stuck notes)",
          app.arp.all_offs == n_offs + 1)
    app.note_off(64)
    check("open relay blocks note_off too (all_off already silenced)",
          app.arp.offs == [])

    # kind mismatch: a binary wire can't land on a notes circuit
    b = app.spawn_button()
    try:
        app.set_ctl_wire("add", b, f"{rid}:1")
        check("binary wire into a notes circuit refused", False)
    except ValueError:
        check("binary wire into a notes circuit refused", True)


# ---- relay: binary circuit + relay:ctl ---------------------------------------

def test_relay_binary_and_ctl():
    app = SynthApp(use_midi=False, use_reload=False)
    rid = app.spawn_relay()
    lid = app.spawn_logic()
    app.set_logic(lid, op="OR")
    bl = latch_button(app)

    app.set_ctl_wire("add", bl, f"{rid}:2")      # claims binary
    check("binary circuit claims its kind",
          app.relays[rid].kinds.get(2) == "binary")
    app.set_ctl_wire("add", f"{rid}:2", f"{lid}:a")

    set_lvl(app, bl, True)
    check("open relay kills the level (logic stays lo)",
          out(app, lid) is False)
    app.set_relay(rid, closed=True)
    check("closing passes the level (logic follows hi)",
          out(app, lid) is True)
    app.set_relay(rid, closed=False)
    check("opening drops the level (logic back lo)",
          out(app, lid) is False)

    # a note wire can't land on the binary-claimed circuit
    try:
        app.set_ctl_wire("add", "keys", f"{rid}:2")
        check("note wire into a binary circuit refused", False)
    except ValueError:
        check("note wire into a binary circuit refused", True)

    # relay:ctl — closed FOLLOWS the wired level
    bc = latch_button(app)
    app.set_ctl_wire("add", bc, f"{rid}:ctl")
    set_lvl(app, bc, True)
    check("relay:ctl hi closes the relay", app.relays[rid].closed is True)
    set_lvl(app, bc, False)
    check("relay:ctl lo opens the relay", app.relays[rid].closed is False)

    # set_relay is the manual click: LAST WRITER WINS either way
    set_lvl(app, bc, True)
    app.set_relay(rid, closed=False)
    check("manual click overrides the wired level (last writer wins)",
          app.relays[rid].closed is False)
    set_lvl(app, bc, False)
    set_lvl(app, bc, True)
    check("the next ctl edge overrides the manual click",
          app.relays[rid].closed is True)


# ---- relay: audio circuit + removal hygiene ----------------------------------

def test_relay_audio_and_removal():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack(
        [("pluck", "source"), ("echo", "effect")],
        [{"from": "pluck", "to": "echo"}, {"from": "echo", "to": "master"}],
    )
    rid = app.spawn_relay()

    app.graph_wire("add", "pluck", f"{rid}:3")
    app.graph_wire("add", f"{rid}:3", "echo")
    check("audio endpoints accepted + stored verbatim",
          {"from": "pluck", "to": f"{rid}:3"} in app.graph_wires
          and {"from": f"{rid}:3", "to": "echo"} in app.graph_wires)
    check("audio circuit claims its kind",
          app.relays[rid].kinds.get(3) == "audio")
    check("open circuit parks the source (disconnect recorded)",
          ("disconnect", "pluck") in app.rack.calls)

    app.rack.calls.clear()
    app.set_relay(rid, closed=True)
    check("closing rewires the source through the circuit",
          ("rewire", "pluck", "echo") in app.rack.calls)
    app.rack.calls.clear()
    app.set_relay(rid, closed=False)
    check("opening disconnects/parks the source again",
          ("disconnect", "pluck") in app.rack.calls)

    # kind mismatch: a binary wire can't land on the audio circuit
    b = app.spawn_button()
    try:
        app.set_ctl_wire("add", b, f"{rid}:3")
        check("binary wire into an audio circuit refused", False)
    except ValueError:
        check("binary wire into an audio circuit refused", True)

    # removal hygiene: wires scrubbed on BOTH planes
    app.set_ctl_wire("add", b, f"{rid}:ctl")
    app.remove_relay(rid)
    check("removal scrubs ctl wires touching the relay",
          not any(str(w.get("from")).startswith(rid)
                  or str(w.get("to")).startswith(rid)
                  for w in app.ctl_wires))
    check("removal scrubs graph wires (source parked, virtual edge gone)",
          {"from": "pluck", "to": None} in app.graph_wires
          and not any(str(w.get("from")).startswith(rid)
                      or str(w.get("to") or "").startswith(rid)
                      for w in app.graph_wires))


# ---- persistence -------------------------------------------------------------

def test_persistence():
    app = SynthApp(use_midi=False, use_reload=False)
    lid = app.spawn_logic()
    app.set_logic(lid, op="XOR")
    rid = app.spawn_relay()
    app.set_relay(rid, closed=True)
    bid = latch_button(app)

    data = presets.snapshot(app)
    check("snapshot carries logics {id, op}",
          data["gates"] == {"logics": [{"id": lid, "op": "XOR"}]})
    check("snapshot carries relays {id, closed}",
          data["relays"] == [{"id": rid, "closed": True}])
    check("snapshot carries the button latch",
          data["buttons"] == [{"id": bid, "binding": None, "latch": True}])

    app2 = SynthApp(use_midi=False, use_reload=False)
    app2._build_patch = lambda name: None
    presets._apply(app2, data)
    check("restore respawns the logic with its op",
          app2.gates.logics.get(lid) is not None
          and app2.gates.logics[lid].op == "XOR")
    check("restore recomputes (unwired XOR is lo)", out(app2, lid) is False)
    check("restore respawns the relay CLOSED",
          app2.relays.get(rid) is not None
          and app2.relays[rid].closed is True)
    check("restore respawns the button latched",
          app2.buttons.get(bid) is not None
          and app2.buttons[bid].latch is True)

    # a legacy "switches" section (pre-rework preset) is ignored silently
    try:
        app2.gates.restore({"switches": [{"id": "switch", "on": True}],
                            "logics": [{"id": "logic.9", "op": "OR"}]})
        ok = True
    except Exception:  # noqa: BLE001
        ok = False
    check("legacy switches key ignored silently (logics still restore)",
          ok and "logic.9" in app2.gates.logics
          and "switch" not in app2.gates.logics)
    st = app2.state()
    check("restored state has logics+relays and NO switches key",
          "logics" in st and "relays" in st and "switches" not in st)


# ---- GUI events --------------------------------------------------------------

def test_events():
    app = SynthApp(use_midi=False, use_reload=False)
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))

    # button level change
    bl = latch_button(app)
    app.button_down(bl)
    check("button level change emits a gate event",
          {"kind": "gate", "id": bl, "on": True} in events)

    # logic out change (AND with :b unwired stays lo → quiet on attach)
    lid = app.spawn_logic()
    app.set_ctl_wire("add", bl, f"{lid}:a")  # bl hi, but AND :b lo: no change
    check("logic emits nothing while its out is unchanged",
          not any(e.get("id") == lid for e in events))
    app.set_logic(lid, op="OR")              # OR of hi → out flips hi
    check("logic out change emits its gate event",
          {"kind": "gate", "id": lid, "on": True} in events)
    app.button_down(bl)                      # bl lo → OR back lo
    check("button lo emits its gate event",
          {"kind": "gate", "id": bl, "on": False} in events)
    check("logic falling out emits too",
          {"kind": "gate", "id": lid, "on": False} in events)

    n = len(events)
    app.gates.recompute()                    # nothing changed
    check("no event without a level change", len(events) == n)

    # relay closed change
    rid = app.spawn_relay()
    app.set_relay(rid, closed=True)
    check("relay closed change emits a gate event",
          {"kind": "gate", "id": rid, "on": True} in events)
    n = len(events)
    app.set_relay(rid, closed=True)          # no change
    check("no relay event without a closed change", len(events) == n)


def main():
    test_spawn_and_state()
    test_wire_grammar()
    test_truth_tables()
    test_sr_latch()
    test_trig_edges()
    test_ping_thru_logic()
    test_level_ins()
    test_feedback()
    test_relay_notes()
    test_relay_binary_and_ctl()
    test_relay_audio_and_removal()
    test_persistence()
    test_events()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
