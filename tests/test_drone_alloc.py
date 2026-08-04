"""Item 29 — the drone as an ALLOCATION. Runs anywhere (no scsynth, no audio).

    python tests/test_drone_alloc.py

Item 10 built the `Hold` policy (last-note priority, no gate from the note
stream, hold on empty) and proved the policy itself. This file covers what
item 29 adds on top of it and around it:

* POWER — a drone aimed at an ordinary playable source has to hold that
  source's gate open, and that is a separate axis from the note stream.
* the two things item 10 flagged as unsettled:
  - PITCH REFERENCE: a drone follows global TRANSPOSE (a key change) and
    deliberately ignores BEND (a gesture on what you play, against the
    drone);
  - SHARING: a Hold and a Poly on one target must stay out of each other's
    way, including when the drone's power moves.
* item 32's invariant, preserved: effective gate = POWER and
  transport.running, re-pushed on every node-creating path.
* the reactive-indicator doctrine: the POWER tap fires from BOTH routes
  (a set_drone_power call and a binary wire into "<id>:pwr"), which is the
  proof the card's indicator is allowed to depend on it.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.allocation import (  # noqa: E402
    Hold, MonoLatest, Poly, midi_to_freq, pool_for,
)
from synthbase.app import SynthApp  # noqa: E402
from synthbase.rack import Rack, type_of  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


# ---- harness ----------------------------------------------------------------


class FakeNode:
    def __init__(self):
        self.sets = []
        self.freed = False
        self.free_calls = []      # every `force` value free() was called with

    def set(self, **kw):
        self.sets.append(kw)

    def free(self, force=False):
        # Mirrors supriya's REAL signature, as test_allocation.py's does.
        # `Node.free()` emits `/n_set gate 0` for a synth that HAS a gate —
        # a release, not a free — and `/n_free` only when force=True. Every
        # playable source has a gate (rule 5), which also mandates
        # done_action=0 so the node survives release. A mock that took no
        # `force` and set freed=True regardless is what hid the 07-26
        # satellite leak, and a drone HOLDING a gate open is the same shape.
        self.freed = bool(force)
        self.free_calls.append(force)

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False

    def move(self, **kw):
        pass

    def last(self, name):
        for kw in reversed(self.sets):
            if name in kw:
                return kw[name]
        return None

    def gates(self):
        return [kw["gate"] for kw in self.sets if "gate" in kw]


class FakeServer:
    def __init__(self):
        self.spawned = []

    def add_synth(self, synthdef, **kw):
        node = FakeNode()
        node.spawn_settings = dict(kw)
        self.spawned.append(node)
        return node

    def add_bus_group(self, **kw):
        return 90

    def live(self):
        """Satellites still running on the server — the count that leaked."""
        return [n for n in self.spawned if not n.freed]


#: a playable source: freq + gate. The drone MODULE is deliberately gateless.
PLAYABLE = {"out": 16, "freq": 220.0, "gate": 0, "cutoff": 800.0}
GATELESS = {"out": 16, "freq": 55.0, "glide": 1.5}


def make_rack(specs=(("pad", PLAYABLE),)):
    rack = Rack(engine=SimpleNamespace(server=FakeServer(), root_group=None),
                registry={})
    rack.instances = [
        SimpleNamespace(
            key=k, module=SimpleNamespace(kind="source", synthdef=object(),
                                          name=k.title(), key=type_of(k),
                                          family="synth", params={}),
            settings=dict(s), service=False, node=FakeNode(), enabled=True,
            type=type_of(k), bus_group=None,
        )
        for k, s in specs
    ]
    return rack


def node(rack, key="pad"):
    return rack.find(key).node


def make_app(specs=(("pad", PLAYABLE),)):
    """A SynthApp with a rack but no engine — enough for the ctl plane."""
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = make_rack(specs)
    app.events = []
    app.on_midi_event = app.events.append
    return app


def slot_node(alloc, rack):
    """The node an allocation's first slot actually writes to: the target
    instance for slot 0, its own satellite above that."""
    slot = alloc._slots[0]
    return rack.find(alloc.target_key).node if slot.index == 0 else slot.node


def levels(app, ep):
    return [e["on"] for e in app.events
            if e.get("kind") == "level" and e.get("ep") == ep]


# ---- POWER ------------------------------------------------------------------


def test_power_gates_a_playable_target():
    """Hold never gates from NOTES; POWER is what makes it audible."""
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(60)
    check("a note alone never opens the gate", node(rack).last("gate") is None)
    h.set_gate_open(True)
    check("power opens the target's gate", node(rack).last("gate") == 1)
    check("power did not disturb the root",
          node(rack).last("freq") == midi_to_freq(60))
    h.note_on(67)
    check("the gate STAYS open across a new root", node(rack).gates() == [1])
    h.set_gate_open(False)
    check("power closes it again", node(rack).last("gate") == 0)


def test_power_is_inert_on_a_gateless_target():
    """The drone MODULE sounds while its node runs — there is no gate to
    hold. Writing one would put a phantom param into inst.settings and from
    there into the broadcast state."""
    rack = make_rack((("drone", GATELESS),))
    h = Hold(rack, "drone")
    h.note_on(48)
    h.set_gate_open(True)
    check("no gate written to a gateless target",
          node(rack, "drone").last("gate") is None)
    check("no phantom gate param in settings",
          "gate" not in rack.find("drone").settings)
    check("freq still steers", node(rack, "drone").last("freq") == midi_to_freq(48))


def test_dispose_closes_the_gate():
    """Removing the card must not leave the source sounding with nothing
    left to switch it off."""
    rack = make_rack()
    h = Hold(rack, "pad")
    h.set_gate_open(True)
    h.dispose()
    check("dispose closes the held gate", node(rack).last("gate") == 0)
    check("dispose hands the slot back",
          pool_for(rack, "pad").acquire(1)[0].index == 0)


def test_retarget_releases_the_old_source():
    rack = make_rack((("pad", PLAYABLE), ("pad2", PLAYABLE)))
    h = Hold(rack, "pad")
    h.note_on(60)
    h.set_gate_open(True)
    h.target_key = "pad2"
    check("the abandoned source is released", node(rack, "pad").last("gate") == 0)
    check("the new source is powered", node(rack, "pad2").last("gate") == 1)
    check("the root follows the drone, not the source",
          node(rack, "pad2").last("freq") == midi_to_freq(60))


# ---- the satellite leak, drone-shaped (inherited from 9dda485) --------------
# A drone HOLDING a gate open is the exact shape that leaks: supriya's
# unforced free() only sends `/n_set gate 0` to a gated synth, and rule 5's
# done_action=0 means it goes on running. Every path that lets go of a
# drone's slot is checked here, not just the obvious one.


def test_drone_dispose_force_frees_its_satellite():
    rack = make_rack()
    server = rack.engine.server
    MonoLatest(rack, "pad")            # takes slot 0, so the drone gets one
    h = Hold(rack, "pad")
    h.note_on(48)
    h.set_gate_open(True)
    sat = h._slots[0].node
    h.dispose()
    check("the drone's satellite was force-freed",
          sat is not None and sat.free_calls == [True])
    check("nothing left running", server.live() == [])


def test_drone_spawn_dispose_cycles_do_not_accumulate():
    """The poly version of this check is upstream; a drone differs in that
    it holds its gate OPEN for the whole life of the slot, which is what
    makes an unforced free look like it worked."""
    rack = make_rack()
    server = rack.engine.server
    MonoLatest(rack, "pad")
    for cycle in range(5):
        h = Hold(rack, "pad")
        h.note_on(48)
        h.set_gate_open(True)
        h.dispose()
        check(f"cycle {cycle + 1}: no drone satellite left running",
              server.live() == [])


def test_drone_retarget_force_frees_the_satellite_it_leaves():
    rack = make_rack((("pad", PLAYABLE), ("pad2", PLAYABLE)))
    MonoLatest(rack, "pad")
    h = Hold(rack, "pad")
    h.set_gate_open(True)
    sat = h._slots[0].node
    h.target_key = "pad2"
    check("the abandoned satellite was force-freed",
          sat is not None and sat.free_calls == [True])


def test_rebind_hands_the_old_slots_back():
    """rebind() must RELEASE, not just forget. Both app paths that swap the
    rack tear the old one down first — but assuming someone else frees it is
    how the 07-26 leak happened."""
    rack = make_rack()
    MonoLatest(rack, "pad")
    h = Hold(rack, "pad")
    h.note_on(55)
    h.set_gate_open(True)
    sat = h._slots[0].node
    h.rebind(make_rack())
    check("rebind force-freed the old satellite",
          sat is not None and sat.free_calls == [True])
    check("the old pool has the lease back",
          pool_for(rack, "pad")._leased == {0})


# ---- pitch reference (the item 10 open question, settled) --------------------


def test_drone_follows_transpose():
    """A deriver hands the drone a RAW note number while the voice sounding
    the melody applies transpose — a drone that ignored it sat exactly
    `transpose` semitones away from everything else."""
    rack = make_rack()
    h = Hold(rack, "pad")
    h.transpose = 2
    h.note_on(60)
    check("transpose applies to the root",
          abs(node(rack).last("freq") - midi_to_freq(62)) < 1e-9)


def test_drone_ignores_bend():
    """Bend is a gesture on what you PLAY, against the drone. Bending the
    reference with the melody leaves every interval unchanged."""
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(60)
    h.set_bend(2.0)
    check("bend does not move the root",
          node(rack).last("freq") == midi_to_freq(60))
    check("bend is not even stored", h.bend == 0.0)


def test_drone_module_sink_follows_transpose():
    """The behaviour change, at the path it actually changes: `_DroneSink`
    aimed at the raw MIDI pitch, so a transposed rig left its drone module
    exactly `transpose` semitones adrift from the voice playing over it."""
    app = make_app((("drone", GATELESS),))
    app.set_transpose(2)
    app.set_ctl_wire("add", "keys", "drone")
    app.note_on(60)
    check("a drone MODULE follows global transpose too",
          abs(node(app.rack, "drone").last("freq") - midi_to_freq(62)) < 1e-9)


def test_drone_module_sink_survives_a_rebuild():
    """An Allocation binds a rack where `_DroneSink` read app.rack live, so
    the sink has to be rebound or it writes into a dead rack forever."""
    app = make_app((("drone", GATELESS),))
    app.set_ctl_wire("add", "keys", "drone")
    app.note_on(48)
    app.rack = make_rack((("drone", GATELESS),))   # a rebuild
    app.note_on(55)
    check("notes land on the FRESH rack after a rebuild",
          node(app.rack, "drone").last("freq") == midi_to_freq(55))


# ---- sharing one target with a poly voice (item 10 flagged this) ------------


def test_drone_power_does_not_touch_the_poly_sharing_the_target():
    rack = make_rack()
    h = Hold(rack, "pad")
    p = Poly(rack, "pad", voices=2)
    check("different slots", h._slots[0].index not in [s.index for s in p._slots])
    h.set_gate_open(True)
    h.note_on(48)
    p.note_on(60)
    p.note_on(64)
    check("poly still sounds both notes", len(p._sounding) == 2)
    poly_nodes = [s.node for s in p._slots if s.node is not None]
    h.set_gate_open(False)
    check("powering the drone down leaves poly's gates alone",
          all(n.last("gate") == 1 for n in poly_nodes) or not poly_nodes)
    check("...and the poly notes are still tracked", len(p._sounding) == 2)


def test_a_drone_on_a_satellite_slot_still_sounds():
    """Order matters: if the poly is built first the drone gets a SATELLITE,
    which spawns gate=0 and would be silent forever without power."""
    rack = make_rack()
    p = Poly(rack, "pad", voices=2)
    h = Hold(rack, "pad")
    check("the drone landed on a satellite", h._slots[0].index > 0)
    h.note_on(48)
    h.set_gate_open(True)
    sat = h._slots[0].node
    check("the satellite exists", sat is not None)
    check("power opens the satellite", sat is not None and sat.last("gate") == 1)
    check("and it is aimed at the drone's root",
          sat is not None and sat.last("freq") == midi_to_freq(48))
    p.note_on(60)
    check("the poly is unaffected", len(p._sounding) == 1)


# ---- surviving a rebuild ----------------------------------------------------


def test_rebind_re_asserts_root_and_power():
    """A rebuild makes a NEW rack: pools die with it and satellites respawn
    at gate=0. A drone that was sounding has to be told again."""
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(55)
    h.set_gate_open(True)
    fresh = make_rack()
    h.rebind(fresh)
    check("rebind re-aims the held root",
          node(fresh).last("freq") == midi_to_freq(55))
    check("rebind re-opens the gate", node(fresh).last("gate") == 1)
    check("rebind released nothing it still needs", h._slots != [])


def test_a_rebuild_re_powers_the_drone_card():
    """_make_voices rebuilds every allocation against the fresh rack; a
    drone has to come back sounding, or a hot reload silently kills it."""
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_transport(playing=True)
    app.set_drone_power(vid, True)
    app.set_ctl_wire("add", "keys", vid)
    app.note_on(55)
    app.rack = make_rack()            # a rebuild
    app._make_voices({})
    # the mono voice is rebuilt first and takes slot 0, so the drone lands
    # on a satellite — which spawns gate=0 and proves the re-push
    n = slot_node(app.voices[vid], app.rack)
    check("the rebuilt drone is powered again", n.last("gate") == 1)
    check("...and re-aimed at its root", n.last("freq") == midi_to_freq(55))
    app.set_transport(playing=False)
    app.rack = make_rack()
    app._make_voices({})
    n = slot_node(app.voices[vid], app.rack)
    check("a rebuild while STOPPED stays silent (item 32)",
          n.last("gate") == 0)


def test_rebind_is_a_no_op_on_the_same_rack():
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(55)
    before = len(node(rack).sets)
    h.rebind(rack)
    check("same rack, no re-write", len(node(rack).sets) == before)


# ---- the app: the drone CARD ------------------------------------------------


def test_spawn_and_id_namespace():
    app = make_app()
    vid = app.spawn_drone_voice()
    check("first drone voice id", vid == "hold")
    check("it is a Hold", isinstance(app.voices[vid], Hold))
    check("policy reaches the state", app.voices[vid].policy == "hold")
    check("second one suffixes", app.spawn_drone_voice() == "hold.2")
    # "drone" is a MODULE type; a ctl node with that id would shadow the
    # instance in _ctl_sinks, which is why the id type is "hold"
    check("the id does not collide with the drone module type",
          all(not v.startswith("drone") for v in app.voices))


def test_tone_in_is_an_ordinary_ctl_destination():
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_ctl_wire("add", "keys", vid)
    check("keys→drone voice accepted",
          {"from": "keys", "to": vid} in app.ctl_wires)
    app.set_drone_power(vid, True)
    app.set_transport(playing=True)
    app.note_on(60)
    check("a note through the wire steers the target",
          node(app.rack).last("freq") == midi_to_freq(60))
    app.note_off(60)
    check("releasing the last note HOLDS it",
          node(app.rack).last("freq") == midi_to_freq(60))
    check("...and never closes the gate", node(app.rack).last("gate") == 1)


def test_power_and_transport_running_are_ANDed():
    """Item 32's invariant: a drone is gateless from the note stream, so a
    stopped transport is the only other thing that silences it."""
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_transport(playing=False)
    app.set_drone_power(vid, True)
    check("power while STOPPED does not sound", node(app.rack).last("gate") == 0)
    check("...but the user's intent is remembered",
          app._drone_powers[vid] is True)
    app.set_transport(playing=True)
    check("play opens it", node(app.rack).last("gate") == 1)
    app.set_transport(playing=False)
    check("stop closes it again", node(app.rack).last("gate") == 0)


def test_state_reports_power_as_intent():
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_transport(playing=False)
    app.set_drone_power(vid, True)
    entry = [v for v in app.state()["voices"] if v["id"] == vid][0]
    check("state carries the policy", entry["policy"] == "hold")
    check("state carries POWER as intent, not the effective gate",
          entry["power"] is True)
    check("one slot", entry["slots"] == 1)
    other = [v for v in app.state()["voices"] if v["id"] == "voice"]
    check("gated policies report power=None",
          not other or other[0]["power"] is None)


def test_removal_closes_the_gate_and_drops_the_power_wire():
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_transport(playing=True)
    app.set_drone_power(vid, True)
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, f"{vid}:pwr")
    check("the power wire is stored",
          any(w.get("to") == f"{vid}:pwr" for w in app.ctl_wires))
    app.remove_voice(vid)
    check("removal closes the gate it was holding",
          node(app.rack).last("gate") == 0)
    check("removal drops the power wire",
          not any(w.get("to") == f"{vid}:pwr" for w in app.ctl_wires))
    check("and the power intent", vid not in app._drone_powers)


# ---- the reactive-indicator doctrine ----------------------------------------


def test_power_tap_fires_from_a_direct_call():
    app = make_app()
    vid = app.spawn_drone_voice()
    app.events.clear()
    app.set_drone_power(vid, True)
    app.set_drone_power(vid, False)
    check("a direct power change emits its level tap",
          levels(app, f"{vid}:pwr") == [True, False])


def test_power_tap_fires_from_a_binary_wire():
    """The half that matters: state applied inside the gate settle pass does
    NOT broadcast, so without this tap a logic-driven power change would
    leave the card reading the opposite."""
    app = make_app()
    vid = app.spawn_drone_voice()
    app.set_transport(playing=True)
    bid = app.spawn_button()
    check("<id>:pwr is a legal binary destination",
          app.gates.is_toggle_dst(f"{vid}:pwr"))
    app.set_ctl_wire("add", bid, f"{vid}:pwr")
    app.events.clear()
    app.buttons[bid].press()
    check("logic input drives the power", app._drone_powers[vid] is True)
    check("logic input opens the gate", node(app.rack).last("gate") == 1)
    check("logic input emits the level tap",
          levels(app, f"{vid}:pwr")[-1:] == [True])
    app.events.clear()
    app.buttons[bid].release()
    check("the falling edge closes it", node(app.rack).last("gate") == 0)
    check("the falling edge taps too",
          levels(app, f"{vid}:pwr")[-1:] == [False])


def test_power_never_bypasses_the_target_module():
    """set_enabled would take down any poly voice sharing the source."""
    app = make_app()
    vid = app.spawn_drone_voice()
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, f"{vid}:pwr")
    app.buttons[bid].release()
    check("the target module stays enabled", app.rack.find("pad").enabled)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("PASS — item 29: drone as an allocation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
