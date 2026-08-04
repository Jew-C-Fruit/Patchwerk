"""Allocation-framework tests that run anywhere (no scsynth, no audio).

    python tests/test_allocation.py

Covers item 10: the voice pool (slot leasing, satellite nodes, param
mirroring, staleness), and the three policies — mono-latest (behaviour
preserved from the old midi.MonoVoice), poly (N slots, steal-oldest,
sustain, resize) and hold (the drone policy item 29 builds on).

Two regressions are pinned failing-first here:

* TWO MONO VOICES ON ONE TARGET used to stomp each other — both called
  set_params on the same node, so either one's note-off cut the other's
  held note. Cole: "multiple mono cards can spawn simultaneously, but they
  cannot successfully operate with distinct voices."
* EVERY silencing path closes its viz taps. A poly has N open at once, so
  an unpaired on is N times easier to leave behind.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.rack import Rack, type_of  # noqa: E402
from synthbase.allocation import (  # noqa: E402
    Hold, MonoLatest, Poly, VoicePool, midi_to_freq, pool_for,
)

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


class FakeNode:
    def __init__(self):
        self.sets = []
        self.freed = False
        self.free_calls = []      # every `force` value free() was called with
        self.moves = 0

    def set(self, **kw):
        self.sets.append(kw)

    def free(self, force=False):
        # Mirror supriya's REAL signature. `Node.free()` emits
        # `/n_set gate 0` for any synth that has a gate — a release, not a
        # free — and `/n_free` only when force=True. Every playable source
        # has a gate (rule 5), which also mandates done_action=0 so the node
        # survives release. The old mock took no `force` and set freed=True
        # unconditionally, so it happily "freed" satellites that were still
        # running on scsynth; the leak was invisible here and only showed up
        # as a live node count that climbed and never fell.
        self.freed = bool(force)
        self.free_calls.append(force)

    def pause(self):
        self.paused = True

    def unpause(self):
        self.paused = False

    def move(self, **kw):
        self.moves += 1

    # convenience: the last value this node was given for a param
    def last(self, name):
        for kw in reversed(self.sets):
            if name in kw:
                return kw[name]
        return None

    def gates(self):
        return [kw["gate"] for kw in self.sets if "gate" in kw]


class FakeBus(int):
    def free(self):
        pass


class FakeServer:
    """Enough server for the pool: it hands out nodes for satellites."""

    def __init__(self):
        self.spawned = []
        self._next_bus = 90

    def add_synth(self, synthdef, **kw):
        node = FakeNode()
        node.spawn_settings = dict(kw)
        self.spawned.append(node)
        return node

    def add_bus_group(self, **kw):
        self._next_bus += 2
        return FakeBus(self._next_bus)

    def live(self):
        """Satellites still running on the server — the count that leaked."""
        return [n for n in self.spawned if not n.freed]


def make_rack(playable=("pad",), server=None):
    server = server or FakeServer()
    rack = Rack(engine=SimpleNamespace(server=server, root_group=None), registry={})
    rack.instances = [
        SimpleNamespace(
            key=k, module=SimpleNamespace(kind="source", synthdef=object()),
            settings={"out": 16, "freq": 220.0, "gate": 0, "cutoff": 800.0},
            service=False, node=FakeNode(), enabled=True, type=type_of(k),
            bus_group=None,
            # item 11 added a SECOND owned bus group to Instance: a dual
            # module at the chain head owns a private in-bus as well as its
            # out-bus, and detach_instance/remove_instance free both. This
            # fake enumerates Instance's fields, so it needs the new one.
            in_bus_group=None,
        )
        for k in playable
    ]
    return rack


def target_node(rack, key="pad"):
    return rack.find(key).node


def taps(alloc):
    """Collect (note, on) viz taps an allocation emits."""
    seen = []
    alloc.on_voiced = lambda n, on: seen.append((n, on))
    return seen


def open_taps(seen):
    """Notes with an unmatched 'on' — the stuck-bar invariant."""
    still = []
    for note, on in seen:
        if on:
            still.append(note)
        elif note in still:
            still.remove(note)
    return still


# ---- the pool ---------------------------------------------------------------

def test_first_lease_is_the_target_node():
    """A lone voice must be byte-for-byte the pre-pool behaviour: it drives
    the target INSTANCE, so the card's freq/gate readout keeps moving."""
    rack = make_rack()
    pool = pool_for(rack, "pad")
    slots = pool.acquire(1)
    check("first lease is slot 0", slots[0].index == 0 and slots[0].is_primary)
    slots[0].set(freq=440.0, gate=1)
    inst = rack.find("pad")
    check("slot 0 writes through the instance",
          inst.settings["freq"] == 440.0 and inst.settings["gate"] == 1)
    check("slot 0 writes the target node", target_node(rack).last("gate") == 1)
    check("no satellite spawned for one voice",
          len(rack.engine.server.spawned) == 0)


def test_satellites_spawn_silent_and_sum():
    rack = make_rack()
    pool = pool_for(rack, "pad")
    pool.acquire(3)
    spawned = rack.engine.server.spawned
    check("two satellites for three slots", len(spawned) == 2)
    # the "playable sources must spawn gate=0" landmine: a fresh satellite
    # inheriting a live gate would sound a note nobody played
    check("satellites come up silent",
          all(n.spawn_settings.get("gate") == 0 for n in spawned))
    check("satellites share the target's out bus",
          all(n.spawn_settings.get("out") == 16 for n in spawned))


def test_mirror_skips_per_voice_params():
    rack = make_rack()
    pool = pool_for(rack, "pad")
    pool.acquire(2)
    sat = rack.engine.server.spawned[0]
    rack.set_param("pad", "cutoff", 1200.0)
    check("timbre param mirrors to satellites", sat.last("cutoff") == 1200.0)
    rack.set_params("pad", freq=880.0, gate=1)
    check("freq is NOT mirrored (it IS the voice)", sat.last("freq") is None)
    check("gate is NOT mirrored", sat.last("gate") is None)


def test_rewire_and_removal_reach_satellites():
    rack = make_rack()
    pool = pool_for(rack, "pad")
    pool.acquire(2)
    sat = rack.engine.server.spawned[0]
    rack.audio_disconnect("pad")
    check("satellites follow a disconnect onto the null bus",
          sat.last("out") == rack.find("pad").settings["out"])
    rack.remove_instance("pad")
    check("removing the target frees its satellites", sat.freed)
    check("removing the target drops the pool", "pad" not in rack.voice_pools)


def test_bypass_pauses_satellites():
    """Bypassing the card must silence ALL N voices, not just slot 0."""
    rack = make_rack()
    p = Poly(rack, "pad", voices=3)
    p.note_on(60)
    p.note_on(64)
    p.note_on(67)
    sats = list(rack.engine.server.spawned)
    rack.set_enabled("pad", False)
    check("bypass pauses the satellites too",
          all(getattr(n, "paused", False) for n in sats))
    rack.set_enabled("pad", True)
    check("un-bypass brings them back",
          all(getattr(n, "paused", True) is False for n in sats))


def test_satellites_do_not_accumulate():
    """Live-found (2026-07-26): spawn/dispose cycles took the node count
    9→13→17→21→25→29 and never down, because an unforced free() only
    RELEASES a gated synth. Five cycles must end where they started."""
    rack = make_rack()
    server = rack.engine.server
    for cycle in range(5):
        p = Poly(rack, "pad", voices=4)
        p.note_on(60)
        p.note_on(64)
        p.dispose()
        check(f"cycle {cycle + 1}: no satellite left running",
              server.live() == [])
    check("every satellite was force-freed, not merely released",
          all(n.free_calls and any(n.free_calls) for n in server.spawned))
    check("five 4-voice cycles spawned 15 satellites total",
          len(server.spawned) == 15)


def test_free_is_forced_everywhere():
    """Both teardown paths must force. An unforced free on a gated synth is
    a silent leak, and the production `except Exception: pass` would hide a
    signature mistake too."""
    rack = make_rack()
    pool = pool_for(rack, "pad")
    pool.acquire(3)
    sats = list(rack.engine.server.spawned)
    pool.release(pool._slots[1:2])          # release() path
    check("release() forces", sats[0].free_calls == [True])
    pool.dispose()                          # _free_satellites() path
    check("dispose() forces", sats[1].free_calls == [True])
    check("nothing survives", rack.engine.server.live() == [])


def test_stale_server_respawns_satellites():
    """The 'track the server OBJECT, not a boolean' landmine: a rebuild makes
    a NEW scsynth, so every satellite is stale."""
    rack = make_rack()
    pool = pool_for(rack, "pad")
    slots = pool.acquire(2)
    old = rack.engine.server.spawned[0]
    rack.engine.server = FakeServer()          # engine rebuild
    rack.find("pad").node = FakeNode()
    slots[1].set(freq=440.0, gate=1)
    check("stale satellite freed", old.freed)
    check("satellite respawned against the new server",
          len(rack.engine.server.spawned) == 1)
    check("respawned satellite takes the note",
          rack.engine.server.spawned[0].last("gate") == 1)


# ---- mono-latest: behaviour preserved ---------------------------------------

def test_mono_latest_note_stack():
    rack = make_rack()
    v = MonoLatest(rack, "pad")
    seen = taps(v)
    v.note_on(60)
    v.note_on(64)
    node = target_node(rack)
    check("newest note wins", node.last("freq") == midi_to_freq(64))
    v.note_off(64)
    check("falls back to the held note", node.last("freq") == midi_to_freq(60))
    v.note_off(60)
    check("empty held set closes the gate", node.last("gate") == 0)
    check("mono taps all close", open_taps(seen) == [])
    check("mono sounds one note at a time",
          seen == [(60, True), (60, False), (64, True),
                   (64, False), (60, True), (60, False)])


def test_mono_background_release_and_sustain():
    rack = make_rack()
    v = MonoLatest(rack, "pad")
    v.note_on(60)
    v.note_on(64)
    node = target_node(rack)
    v.note_off(60)                       # a background-held key
    check("background release does not retarget",
          node.last("freq") == midi_to_freq(64) and node.last("gate") == 1)
    v.set_sustain(True)
    v.note_off(64)
    check("pedal holds the last note", node.last("gate") == 1)
    v.set_sustain(False)
    check("pedal up releases it", node.last("gate") == 0)


def test_mono_transpose_and_bend():
    rack = make_rack()
    v = MonoLatest(rack, "pad")
    v.transpose = 12
    v.note_on(60)
    node = target_node(rack)
    check("transpose applies", node.last("freq") == midi_to_freq(72))
    v.set_bend(2.0)
    check("bend re-aims the sounding note",
          abs(node.last("freq") - midi_to_freq(72) * 2 ** (2 / 12)) < 1e-9)


# ---- the regression Cole reported -------------------------------------------

def test_two_mono_voices_are_independent():
    """voice and voice.2 on ONE source must sound as distinct voices."""
    rack = make_rack()
    a = MonoLatest(rack, "pad")
    b = MonoLatest(rack, "pad")
    check("second voice leases its own slot", a._slots[0].index != b._slots[0].index)
    a.note_on(60)
    b.note_on(67)
    node = target_node(rack)
    sat = rack.engine.server.spawned[0]
    check("voice A holds its own pitch", node.last("freq") == midi_to_freq(60))
    check("voice B sounds on the satellite", sat.last("freq") == midi_to_freq(67))
    b.note_off(67)
    check("B's note-off closes only B", sat.last("gate") == 0)
    check("A's note SURVIVES B's note-off", node.last("gate") == 1)
    a.note_off(60)
    check("A still closes normally", node.last("gate") == 0)


def test_retarget_releases_the_slot():
    rack = make_rack(playable=("pad", "bell"))
    a = MonoLatest(rack, "pad")
    b = MonoLatest(rack, "pad")
    check("B is on a satellite", not b._slots[0].is_primary)
    a.target_key = "bell"
    check("A moved to the new target", a.target_key == "bell")
    check("A takes bell's slot 0", a._slots[0].is_primary)
    c = MonoLatest(rack, "pad")
    check("A's freed pad slot is reused", c._slots[0].index == 0)


# ---- poly -------------------------------------------------------------------

def test_poly_sounds_n_notes_at_once():
    rack = make_rack()
    p = Poly(rack, "pad", voices=4)
    seen = taps(p)
    for n in (60, 64, 67, 72):
        p.note_on(n)
    nodes = [target_node(rack)] + rack.engine.server.spawned
    check("four slots for four voices", len(rack.engine.server.spawned) == 3)
    check("every note lands on its own slot",
          sorted(n.last("freq") for n in nodes)
          == sorted(midi_to_freq(x) for x in (60, 64, 67, 72)))
    check("every slot is open", all(n.last("gate") == 1 for n in nodes))
    check("four taps open", sorted(open_taps(seen)) == [60, 64, 67, 72])
    p.note_off(64)
    check("releasing one closes only that slot",
          sum(1 for n in nodes if n.last("gate") == 0) == 1)
    check("its tap closed", sorted(open_taps(seen)) == [60, 67, 72])


def test_poly_steals_the_oldest():
    rack = make_rack()
    p = Poly(rack, "pad", voices=3)
    seen = taps(p)
    p.note_on(60)
    p.note_on(64)
    p.note_on(67)
    p.note_on(72)                         # full — must steal
    check("the oldest note is the one stolen", 60 not in open_taps(seen))
    check("the stolen note emitted its note-off",
          (60, False) in seen and open_taps(seen) == [64, 67, 72])
    nodes = [target_node(rack)] + rack.engine.server.spawned
    holder = [n for n in nodes if n.last("freq") == midi_to_freq(72)]
    check("the new note took the stolen slot", len(holder) == 1)
    check("the stolen slot restruck its gate (0 then 1)",
          holder[0].gates()[-2:] == [0, 1])
    p.note_on(76)
    check("the NEXT steal takes the next-oldest", open_taps(seen) == [67, 72, 76])


def test_poly_same_note_reuses_its_voice():
    rack = make_rack()
    p = Poly(rack, "pad", voices=4)
    seen = taps(p)
    p.note_on(60)
    p.note_on(64)
    p.note_on(60)                          # retrigger, not a second slot
    check("a repeat does not cost a slot", len(p._sounding) == 2)
    check("a repeat does not double-open the tap",
          [t for t in seen if t == (60, True)] == [(60, True)])
    p.note_on(67)
    p.note_on(72)
    check("still room for two more (no leak)", len(p._sounding) == 4)


def test_poly_sustain():
    rack = make_rack()
    p = Poly(rack, "pad", voices=4)
    seen = taps(p)
    p.set_sustain(True)
    p.note_on(60)
    p.note_on(64)
    p.note_off(60)
    check("pedal holds a released note", sorted(open_taps(seen)) == [60, 64])
    p.set_sustain(False)
    check("pedal up releases it", open_taps(seen) == [64])
    check("the still-held note stays", 64 in open_taps(seen))


def test_poly_bend_moves_every_sounding_note():
    rack = make_rack()
    p = Poly(rack, "pad", voices=3)
    p.note_on(60)
    p.note_on(64)
    p.set_bend(2.0)
    nodes = [target_node(rack)] + rack.engine.server.spawned
    want = sorted(midi_to_freq(n) * 2 ** (2 / 12) for n in (60, 64))
    got = sorted(n.last("freq") for n in nodes if n.last("freq") is not None)
    check("bend re-aims all sounding notes",
          len(got) >= 2 and all(abs(a - b) < 1e-9 for a, b in zip(got[:2], want)))


def test_poly_all_off_closes_every_tap():
    """The standing invariant: every silencing path closes its taps."""
    rack = make_rack()
    p = Poly(rack, "pad", voices=6)
    seen = taps(p)
    for n in (60, 62, 64, 65, 67):
        p.note_on(n)
    p.all_off()
    check("all_off leaves no open tap", open_taps(seen) == [])
    nodes = [target_node(rack)] + rack.engine.server.spawned
    check("all_off closes every gate",
          all(n.last("gate") == 0 for n in nodes if n.last("gate") is not None))


def test_poly_resize_closes_dropped_notes():
    rack = make_rack()
    p = Poly(rack, "pad", voices=6)
    seen = taps(p)
    for n in (60, 62, 64, 65):
        p.note_on(n)
    p.set_voices(2)
    check("resize is a silencing path — taps close", open_taps(seen) == [])
    check("new size took", p.voices == 2 and len(p._slots) == 2)
    p.note_on(60)
    p.note_on(64)
    p.note_on(67)
    check("only two sound after shrinking", len(p._sounding) == 2)
    check("resize clamps to the ceiling",
          (p.set_voices(999), p.voices)[1] <= 16)


def test_poly_of_one_is_mono():
    rack = make_rack()
    p = Poly(rack, "pad", voices=1)
    p.note_on(60)
    p.note_on(64)
    node = target_node(rack)
    check("one slot means the newest note wins",
          node.last("freq") == midi_to_freq(64) and len(p._sounding) == 1)
    check("no satellites at all", rack.engine.server.spawned == [])


def test_poly_dispose_frees_slots():
    rack = make_rack()
    p = Poly(rack, "pad", voices=4)
    seen = taps(p)
    p.note_on(60)
    sats = list(rack.engine.server.spawned)
    p.dispose()
    check("dispose closes the taps", open_taps(seen) == [])
    check("dispose frees the satellites", all(n.freed for n in sats))
    check("the pool has the slots back",
          pool_for(rack, "pad").acquire(1)[0].index == 0)


# ---- hold (the drone policy item 29 builds on) -------------------------------

def test_hold_never_gates():
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(60)
    node = target_node(rack)
    check("hold steers freq", node.last("freq") == midi_to_freq(60))
    check("hold never touches gate", node.last("gate") is None)


def test_hold_holds_on_empty():
    rack = make_rack()
    h = Hold(rack, "pad")
    h.note_on(60)
    h.note_on(67)
    node = target_node(rack)
    h.note_off(60)
    check("releasing a non-root does not retarget",
          node.last("freq") == midi_to_freq(67))
    h.note_off(67)
    check("releasing the last note HOLDS the root",
          node.last("freq") == midi_to_freq(67))
    h.note_on(72)
    h.note_off(72)
    check("still holding after a new root", node.last("freq") == midi_to_freq(72))


def test_hold_falls_back_to_newest_held():
    rack = make_rack()
    h = Hold(rack, "pad")
    for n in (60, 64, 67):
        h.note_on(n)
    h.note_off(67)
    check("root release falls back to newest held",
          target_node(rack).last("freq") == midi_to_freq(64))
    h.all_off()
    check("all_off holds the root (a drone has no silence)",
          target_node(rack).last("freq") == midi_to_freq(64))


def test_hold_and_poly_share_one_target():
    """A drone and a poly voice on the same source must not fight — item 29
    aims a hold allocation at an ordinary playable source."""
    rack = make_rack()
    h = Hold(rack, "pad")
    p = Poly(rack, "pad", voices=2)
    check("they lease different slots",
          h._slots[0].index not in [s.index for s in p._slots])
    h.note_on(48)
    p.note_on(60)
    p.note_on(64)
    check("hold keeps its own pitch",
          target_node(rack).last("freq") == midi_to_freq(48))
    check("poly still sounds both its notes", len(p._sounding) == 2)


# ---- rack node freeing: same root cause, pre-existing on main ----------------
# These sit here rather than in test_graph.py because this file already owns
# the FakeNode that mirrors supriya's real free(force=...) signature — the
# mock whose absence hid the bug. The fix is in rack.py, not allocation.py.

def test_rack_detach_actually_frees_a_gated_instance():
    """Live-found: a GATED instance went 10→11→12→13 on repeated removal
    while a gateless effect correctly went 14→13. detach_instance is the
    path the app's module-remove actually takes."""
    rack = make_rack(playable=("pad", "bell"))
    node = rack.find("pad").node
    rack.detach_instance("pad")
    check("detach_instance force-frees", node.freed and node.free_calls == [True])
    check("the instance is gone from the rack",
          [i.key for i in rack.instances] == ["bell"])


def test_rack_remove_and_teardown_actually_free():
    rack = make_rack(playable=("pad", "bell"))
    node = rack.find("pad").node
    rack.remove_instance("pad")
    check("remove_instance force-frees", node.freed)
    rest = [i.node for i in rack.instances]
    rack.teardown()
    check("teardown force-frees every node", all(n.freed for n in rest))
    check("teardown empties the rack", rack.instances == [])


def test_rack_removal_takes_the_satellites_with_it():
    """The two bugs compose: removing a poly's target must free the target
    AND its satellites, or the loudest leak in the system is a live chord."""
    rack = make_rack(playable=("pad", "bell"))
    p = Poly(rack, "pad", voices=4)
    p.note_on(60)
    p.note_on(64)
    p.note_on(67)
    target = rack.find("pad").node
    sats = list(rack.engine.server.spawned)
    rack.detach_instance("pad")
    check("detach: target node freed", target.freed)
    # This is the LIVE path — app.edit_chain("remove") calls detach_instance,
    # not remove_instance. An earlier version of this test noticed that only
    # remove_instance popped the pool, wrote a comment explaining it away
    # ("the pool is dropped with the rack"), and asserted against
    # remove_instance instead. The excuse was wrong: the pool OBJECT dies
    # with the rack, but its satellite SYNTHS keep running on scsynth until
    # the next full teardown. Assert the path the app actually takes.
    check("detach: satellites freed too", sats and all(n.freed for n in sats))
    check("detach: nothing left running", rack.engine.server.live() == [])
    check("detach: the pool is deregistered", "pad" not in rack.voice_pools)

    # both removal paths must agree — they drifted once
    rack2 = make_rack(playable=("pad",))
    p2 = Poly(rack2, "pad", voices=4)
    p2.note_on(60)
    sats2 = list(rack2.engine.server.spawned)
    rack2.remove_instance("pad")
    check("remove_instance frees the satellites too",
          sats2 and all(n.freed for n in sats2))
    check("remove_instance: nothing left running",
          rack2.engine.server.live() == [])
    check("remove_instance: the pool is deregistered",
          "pad" not in rack2.voice_pools)


def test_no_pool_outlives_its_instance():
    """Structural invariant, checked across EVERY path that drops an
    instance: a registered pool must always have an instance behind it.

    Two separate bugs got here by one route — a removal path forgetting the
    pool — so assert the property rather than the call sites. `swap_module`
    is included because it keeps the id and replaces the node: the pool must
    SURVIVE that one, and re-clone its satellites against the new synthdef.
    """
    def orphans(rack):
        return set(rack.voice_pools) - {i.key for i in rack.instances}

    for path in ("detach_instance", "remove_instance"):
        rack = make_rack(playable=("pad", "bell"))
        Poly(rack, "pad", voices=3).note_on(60)
        getattr(rack, path)("pad")
        check(f"{path}: no orphaned pool", orphans(rack) == set())

    rack = make_rack(playable=("pad", "bell"))
    Poly(rack, "pad", voices=3).note_on(60)
    rack.teardown()
    check("teardown: no orphaned pool", orphans(rack) == set())
    check("teardown: no satellite running", rack.engine.server.live() == [])


def test_detach_with_two_allocations_on_one_target():
    """Item 29's repro verbatim: a mono voice AND a drone on `pad`. Detach
    left 1 satellite live with the pool still registered; via
    remove_instance it was 0. Both must now be 0."""
    for path in ("detach_instance", "remove_instance"):
        rack = make_rack(playable=("pad", "bell"))
        mono = MonoLatest(rack, "pad")
        drone = Hold(rack, "pad")
        mono.note_on(60)
        drone.note_on(48)
        live_before = len(rack.engine.server.live())
        getattr(rack, path)("pad")
        check(f"{path}: mono+drone leaves nothing running "
              f"(was {live_before} satellite(s))",
              rack.engine.server.live() == [])
        check(f"{path}: pool deregistered", "pad" not in rack.voice_pools)


# ---- policy registry --------------------------------------------------------

def test_policy_names():
    check("policy names are stable",
          (MonoLatest.policy, Poly.policy, Hold.policy)
          == ("mono-latest", "poly", "hold"))
    check("mono reports one slot", MonoLatest(make_rack(), "pad").voices == 1)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("PASS — allocation framework")
    return 0


if __name__ == "__main__":
    sys.exit(main())
