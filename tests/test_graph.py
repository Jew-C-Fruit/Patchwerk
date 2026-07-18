"""Graph-overlay logic tests that run anywhere (no scsynth, no audio).

    python tests/test_graph.py

Covers: audio-wire derivation from rack settings, graph_wires bookkeeping
(one-out-per-source, cycle rejection, disconnect memory, reapply-after-
rebuild), the drums target / to_chain compatibility mapping, the control
plane (ctl_wires defaults, add/remove + validation, keys/arp dispatch
through the wires, deck sink resolution, select_patch reset), and the v5
structure: instance ids (duplicates, legacy type-key resolution), multiple
mono voices, and tonic-deriver → drone root-follow.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.rack import Rack, alloc_id, type_of  # noqa: E402
from synthbase.drums import DrumMachine  # noqa: E402
from synthbase.app import SynthApp, default_ctl_wires  # noqa: E402
from synthbase.keyshift import KeyShifter, nearest_offset  # noqa: E402
from synthbase.looper import _FanSink  # noqa: E402
from synthbase.midi import MonoVoice  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


class FakeNode:
    def __init__(self):
        self.sets = []

    def set(self, **kw):
        self.sets.append(kw)


def fake_inst(key, kind, settings, service=False, type=None):
    return SimpleNamespace(key=key, module=SimpleNamespace(kind=kind),
                           settings=dict(settings), service=service,
                           node=FakeNode(), enabled=True,
                           type=type or type_of(key))


def make_rack(instances):
    rack = Rack(engine=SimpleNamespace(server=None, root_group=None), registry={})
    rack.instances = list(instances)
    return rack


# ---- audio_wires derivation --------------------------------------------------

def test_wires_derivation():
    # linear chain S(out=16) -> A(in=16, out=18) -> B(in=18, out=0)
    rack = make_rack([
        fake_inst("pluck", "source", {"out": 16}),
        fake_inst("echo", "effect", {"in_bus": 16, "out": 18}),
        fake_inst("reverb", "effect", {"in_bus": 18, "out": 0}),
        fake_inst("drone", "source", {"out": 16}, service=True),
    ])
    wires = rack.audio_wires()
    check("linear derivation", wires == [
        {"from": "pluck", "to": "echo"},
        {"from": "echo", "to": "reverb"},
        {"from": "reverb", "to": "master"},
    ])
    check("services excluded", all(w["from"] != "drone" for w in wires))

    # two cascades + fan-in: S1->A->master, S2->A (sum), unmapped bus -> master
    rack2 = make_rack([
        fake_inst("pluck", "source", {"out": 16}),
        fake_inst("wind", "source", {"out": 16}),
        fake_inst("echo", "effect", {"in_bus": 16, "out": 0}),
        fake_inst("fm_bell", "source", {"out": 20}),  # summed tail bus
    ])
    wires2 = rack2.audio_wires()
    check("fan-in derivation",
          {"from": "pluck", "to": "echo"} in wires2 and
          {"from": "wind", "to": "echo"} in wires2)
    check("tail-routed bus reads master",
          {"from": "fm_bell", "to": "master"} in wires2)

    # a disconnected module (out == null bus) produces no wire
    rack.instances[0].settings["out"] = 99
    rack._null_bus = 99
    check("null bus hidden", all(w["from"] != "pluck" for w in rack.audio_wires()))
    rack._null_bus = None


# ---- graph_wires bookkeeping on SynthApp --------------------------------------

class RecordingRack:
    """Duck-typed rack that records rewire calls; no server anywhere."""

    def __init__(self, keys_kinds, wires):
        self.instances = [fake_inst(k, kind, {}) for k, kind in keys_kinds]
        self._wires = wires
        self.calls = []

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

    def set_param(self, key, name, value):
        self.calls.append(("set_param", key, name, value))
        self.find(key).settings[name] = value

    def set_params(self, key, **values):
        self.calls.append(("set_params", key, dict(values)))
        self.find(key).settings.update(values)


def make_app():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack(
        [("pluck", "source"), ("echo", "effect"), ("reverb", "effect")],
        [{"from": "pluck", "to": "echo"},
         {"from": "echo", "to": "reverb"},
         {"from": "reverb", "to": "master"}],
    )
    return app


def test_graph_wire_bookkeeping():
    app = make_app()
    check("overlay starts unset", app.graph_wires is None)

    app.graph_wire("add", "pluck", "reverb")  # skip echo
    check("init from derived wiring + one-out rule",
          {"from": "pluck", "to": "reverb"} in app.graph_wires and
          all(w != {"from": "pluck", "to": "echo"} for w in app.graph_wires))
    check("rewire applied", ("rewire", "pluck", "reverb") in app.rack.calls)
    check("reorder ran", any(c[0] == "reorder" for c in app.rack.calls))

    app.graph_wire("remove", "echo")
    check("disconnect stored as to=None",
          {"from": "echo", "to": None} in app.graph_wires)
    check("disconnect applied", ("disconnect", "echo") in app.rack.calls)

    # cycle: reverb -> master exists; echo->reverb removed. add reverb -> ... -> reverb
    app.graph_wire("add", "echo", "reverb")
    try:
        app.graph_wire("add", "reverb", "echo")
        check("cycle rejected", False)
    except ValueError:
        check("cycle rejected", True)

    try:
        app.graph_wire("add", "nope", "master")
        check("unknown src rejected", False)
    except KeyError:
        check("unknown src rejected", True)

    # reapply after a "rebuild": keys that vanished are skipped, wires re-imposed
    app.rack.calls.clear()
    app.graph_wires.append({"from": "ghost", "to": "master"})
    app._reapply_graph_wires()
    check("reapply re-imposes wires",
          ("rewire", "echo", "reverb") in app.rack.calls and
          ("rewire", "reverb", "master") in app.rack.calls)
    check("reapply skips vanished keys",
          all(c[1] != "ghost" for c in app.rack.calls if len(c) > 1))


def test_spawn_unconnected():
    app = make_app()
    added = []

    def fake_edit_chain(action, key, index=None):
        added.append((action, key))
        app.rack.instances.append(fake_inst(key, "effect", {}))
        return key  # v5: edit_chain add returns the fresh instance id

    app.edit_chain = fake_edit_chain
    new_id = app.spawn_unconnected("chorus")
    check("spawn snapshots wiring before add",
          app.graph_wires is not None and
          {"from": "pluck", "to": "echo"} in app.graph_wires)
    # parking-on-null is now edit_chain("add")'s job (spawn no longer fires a
    # redundant graph_wire remove); spawn just delegates a single add.
    check("spawn delegates exactly one add to edit_chain",
          added == [("add", "chorus")])
    check("spawn returns the fresh id", new_id == "chorus")


# ---- ctl_wires: the wire-defined control plane -----------------------------------

class FakeNoteSink:
    """MonoVoice-shaped recorder for dispatch checks."""

    def __init__(self):
        self.ons, self.offs = [], []
        self.all_offs = 0
        self.target_key = None   # looper._deck_voice pokes this on voice

    def note_on(self, note, velocity=100): self.ons.append(note)
    def note_off(self, note): self.offs.append(note)
    def all_off(self): self.all_offs += 1
    def set_sustain(self, on): pass
    def set_bend(self, semitones): pass


def test_ctl_wires():
    app = SynthApp(use_midi=False, use_reload=False)
    check("default ctl wires derived", app.ctl_wires == default_ctl_wires())
    check("default set is keys→arp, arp→{voice,deck}, deck→voice",
          app.ctl_wires == [
              {"from": "keys", "to": "arp"},
              {"from": "arp", "to": "voice"},
              {"from": "arp", "to": "deck"},
              {"from": "deck", "to": "voice"},
          ])

    # add / remove / dedupe
    app.set_ctl_wire("add", "keys", "voice")
    check("add appends", {"from": "keys", "to": "voice"} in app.ctl_wires)
    n = len(app.ctl_wires)
    app.set_ctl_wire("add", "keys", "voice")
    check("add dedupes", len(app.ctl_wires) == n)
    app.set_ctl_wire("remove", "keys", "voice")
    check("remove removes", {"from": "keys", "to": "voice"} not in app.ctl_wires)

    # validation: self-wires, keys-as-destination, unknown nodes all rejected
    for src, dst in (("arp", "arp"), ("deck", "deck"), ("arp", "keys"),
                     ("voice", "arp"), ("drone", "voice"), ("nope", "voice"),
                     ("keys", "nope")):
        try:
            app.set_ctl_wire("add", src, dst)
            check(f"reject {src}→{dst}", False)
        except ValueError:
            check(f"reject {src}→{dst}", True)

    # dispatch: notes walk the wires — keys→arp default, rewire = new router
    app2 = SynthApp(use_midi=False, use_reload=False)
    app2.arp = FakeNoteSink()
    app2.voice = FakeNoteSink()
    app2.note_on(60)
    check("keys→arp default routes to arp",
          app2.arp.ons == [60] and app2.voice.ons == [])
    app2.set_ctl_wire("remove", "keys", "arp")
    check("removing arp's last input silences it", app2.arp.all_offs >= 1)
    app2.note_on(61)
    check("arp with no inbound wire receives nothing", app2.arp.ons == [60])
    check("unwired keys dead-end silently", app2.voice.ons == [])
    app2.set_ctl_wire("add", "keys", "voice")
    app2.note_on(62)
    app2.note_off(62)
    check("keys→voice direct bypasses the arp",
          app2.voice.ons == [62] and app2.voice.offs == [62]
          and app2.arp.ons == [60])

    # arp fan-out resolution: arp→{voice, deck(voiced)}
    app3 = SynthApp(use_midi=False, use_reload=False)
    app3.voice = FakeNoteSink()
    sinks = app3._ctl_sinks("arp")
    check("arp sinks resolve voice + deck voiced tap",
          app3.voice in sinks and app3._deck_voiced_tap in sinks
          and len(sinks) == 2)
    app3.set_ctl_wire("add", "keys", "deck")
    check("keys→deck resolves the RAW record tap",
          app3._deck_raw_tap in app3._ctl_sinks("keys"))

    # deck replay sink resolution (looper reads the same wires)
    app4 = SynthApp(use_midi=False, use_reload=False)
    app4.arp = FakeNoteSink()
    check("deck→voice default resolves (deck-voice path, arp fallback w/o engine)",
          app4.looper._sink() is app4.arp)
    app4.ctl_wires = [{"from": "deck", "to": "arp"}]
    check("deck→arp resolves to the arp", app4.looper._sink() is app4.arp)
    app4.ctl_wires = [{"from": "deck", "to": "arp"}, {"from": "deck", "to": "voice"}]
    check("deck multi-out fans", isinstance(app4.looper._sink(), _FanSink))
    app4.ctl_wires = [{"from": "keys", "to": "deck"}]
    check("deck with no outgoing wire is silent", app4.looper._sink() is None)

    # state exposes the wires; select_patch resets them to default
    app5 = SynthApp(use_midi=False, use_reload=False)
    app5.ctl_wires = [{"from": "keys", "to": "voice"}]
    app5._build_patch = lambda name: None   # no engine in this test
    app5.select_patch("mock")
    check("select_patch resets ctl_wires to default",
          app5.ctl_wires == default_ctl_wires())


# ---- tap emission: one tagged viz event per source-fire ---------------------------

def test_tap_emission():
    app = SynthApp(use_midi=False, use_reload=False)
    app.arp = FakeNoteSink()
    app.voice = FakeNoteSink()
    taps = []
    app.on_midi_event = lambda e: taps.append(dict(e))

    app.note_on(60)   # keys dispatch (default wires: keys→arp)
    tap = [e for e in taps if e.get("kind") == "tap"]
    check("keys dispatch emits exactly one src=keys tap",
          tap == [{"kind": "tap", "src": "keys", "note": 60, "on": True}])
    check("tap reached the wired sink too", app.arp.ons == [60])

    taps.clear()
    app.set_ctl_wire("add", "keys", "voice")   # 2 outgoing edges now
    app.note_on(61)
    tap = [e for e in taps if e.get("kind") == "tap"]
    check("tap is per source-fire, not per edge", len(tap) == 1)

    taps.clear()
    app.note_off(61)
    tap = [e for e in taps if e.get("kind") == "tap"]
    check("note_off taps with on=False",
          tap == [{"kind": "tap", "src": "keys", "note": 61, "on": False}])

    taps.clear()
    app._arp_out.note_on(64)   # the arp's scheduled fire path
    tap = [e for e in taps if e.get("kind") == "tap"]
    check("arp fire emits exactly one src=arp tap (2 default edges)",
          tap == [{"kind": "tap", "src": "arp", "note": 64, "on": True}])


# ---- v6 regression: every silencing path CLOSES its open taps ----------------------
# (an "on" tap with no "off" pins a full-width bar on the note monitor and,
# recorded, a full-width smear on the deck — the reported v5 rendering bugs)

def test_alloff_closes_taps():
    app = SynthApp(use_midi=False, use_reload=False)
    app.arp = FakeNoteSink()
    app.voice = FakeNoteSink()
    taps = []
    app.on_midi_event = lambda e: taps.append(dict(e))

    # keys panic: held notes must close their taps
    app.note_on(60)
    app.note_on(64)
    taps.clear()
    app.all_notes_off()
    offs = sorted(e["note"] for e in taps
                  if e.get("kind") == "tap" and e.get("src") == "keys"
                  and e.get("on") is False)
    check("panic closes every open keys tap", offs == [60, 64])
    taps.clear()
    app.all_notes_off()
    check("panic is idempotent (no duplicate offs)",
          not [e for e in taps if e.get("kind") == "tap"])

    # arp output stop (pool empty / arp disable / patch switch → sink.all_off)
    app._arp_out.note_on(72)
    taps.clear()
    app._arp_out.all_off()
    check("arp all_off closes its open arp taps",
          {"kind": "tap", "src": "arp", "note": 72, "on": False} in taps)

    # deriver thru: all_off closes its open thru taps
    tid = app.spawn_tonic()
    d = app.tonics[tid]
    d.note_on(55)
    taps.clear()
    d.all_off()
    check("tonic deriver all_off closes its open thru taps",
          {"kind": "tap", "src": "tonic", "note": 55, "on": False} in taps)
    d.shutdown()

    # deck replay release: _release_all emits src=deck off taps
    app2 = SynthApp(use_midi=False, use_reload=False)
    app2.arp = FakeNoteSink()
    taps2 = []
    app2.on_midi_event = lambda e: taps2.append(dict(e))
    app2.ctl_wires = [{"from": "deck", "to": "arp"}]
    app2.looper._sounding = {67}
    app2.looper._release_all()
    check("deck release closes its replay taps",
          {"kind": "tap", "src": "deck", "note": 67, "on": False} in taps2)
    check("deck release also released downstream", app2.arp.offs == [67])

    # deck RECORD tap: panic while recording closes the take's open notes
    app3 = SynthApp(use_midi=False, use_reload=False)
    rec = []
    app3.looper.record_raw = lambda note, on: rec.append((note, on))
    app3.looper.state = "recording"
    app3._deck_raw_tap.note_on(60)
    app3._deck_raw_tap.all_off()
    check("deck record tap closes open notes in the take",
          rec == [(60, True), (60, False)])


# ---- v7 regression: the deck's take must stay PAIRED ------------------------------
# (an unpaired on in state.looper.notes draws a full-width smear on the deck
# viz; an orphan off draws a bogus 0-to-beat bar -- both seen live)

def test_deck_take_closure():
    app = SynthApp(use_midi=False, use_reload=False)
    l = app.looper

    # (1) armed grace: an on clamps to beat 0; its off DURING armed must pair
    # (v6 dropped it -> the take held a phantom note for the whole loop)
    l.state = "armed"
    l._loop_beats = 8.0
    l._events = []
    l._record_start_beat = app.transport.beats_now()
    l._record(60, True)
    check("armed on clamps to beat 0", (0.0, 60, True) in l._events)
    l._record(60, False)
    check("armed off pairs the clamped on (no phantom full-loop note)",
          (0.0, 60, False) in l._events)
    # an off during armed for a note we never clamped stays dropped
    l._record(64, False)
    check("armed off without a clamped on is dropped",
          not any(n == 64 for _, n, _ in l._events))

    # (2) recording->playing: _finish_recording ships authoritative notes with
    # synthesized window-close offs for notes still open
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    l.state = "recording"
    l._events = [(0.5, 62, True)]
    l.overdub = False
    l._finish_recording()
    check("window close synthesizes the off", any(
        n == 62 and not on for _, n, on in l._events))
    looper_evs = [e for e in events if e.get("kind") == "looper"]
    check("recording->playing emits authoritative notes incl. the close",
          bool(looper_evs) and any(n == 62 and not on
                                   for _, n, on in looper_evs[-1]["notes"]))

    # (3) stop while overdubbing: open record-side notes close at stop
    app2 = SynthApp(use_midi=False, use_reload=False)
    l2 = app2.looper
    l2.state = "overdubbing"
    l2.overdub = True
    l2._loop_beats = 8.0
    l2._record_start_beat = app2.transport.beats_now() - 3.0
    l2._events = [(1.0, 60, True), (2.0, 64, True), (2.5, 64, False)]
    l2._stop()
    offs60 = [b for b, n, on in l2._events if n == 60 and not on]
    check("stop closes the held overdub note", len(offs60) == 1)
    check("stop leaves paired notes alone",
          sum(1 for _, n, on in l2._events if n == 64 and not on) == 1)

    # (4) overdub toggled off mid-note: the transition closes opens too
    app3 = SynthApp(use_midi=False, use_reload=False)
    l3 = app3.looper
    l3.state = "overdubbing"
    l3.overdub = True
    l3._loop_beats = 8.0
    l3._record_start_beat = app3.transport.beats_now() - 2.0
    l3._events = [(0.5, 55, True)]
    l3.configure(overdub=False)
    check("overdub-off transition closes open notes",
          l3.state == "playing" and
          any(n == 55 and not on for _, n, on in l3._events))

    # (5) deck release must NOT re-record through a deck->arp->deck loop
    class _ReentrantSink(FakeNoteSink):
        def __init__(self, looper):
            super().__init__()
            self.looper = looper

        def note_off(self, note):
            super().note_off(note)
            self.looper.record_voiced(note, False)  # echoes back like arp->deck

    app4 = SynthApp(use_midi=False, use_reload=False)
    l4 = app4.looper
    app4.arp = _ReentrantSink(l4)
    app4.ctl_wires = [{"from": "deck", "to": "arp"}]
    l4.state = "recording"
    l4._loop_beats = 8.0
    l4._record_start_beat = app4.transport.beats_now()
    l4._events = []
    l4._sounding = {67}
    l4._release_all()
    check("release_all is self-fire guarded (no re-recorded orphan offs)",
          l4._events == [])
    check("release_all still released downstream", app4.arp.offs == [67])


# ---- v7 regression: every keys off path emits its src=keys tap ---------------------

def test_keys_off_paths():
    import mido
    from synthbase.midi import MidiRouter

    app = SynthApp(use_midi=False, use_reload=False)
    app.arp = FakeNoteSink()
    taps = []
    app.on_midi_event = lambda e: taps.append(dict(e))
    keys_taps = lambda: [(e["note"], e["on"]) for e in taps
                         if e.get("kind") == "tap" and e.get("src") == "keys"]

    # hardware MIDI via the router (CP88 path): note_off AND vel-0 note_on
    r = MidiRouter(None, voice=app._keys, on_event=app._emit_midi_event)
    r._handle(mido.Message("note_on", note=60, velocity=90))
    r._handle(mido.Message("note_off", note=60))
    r._handle(mido.Message("note_on", note=64, velocity=80))
    r._handle(mido.Message("note_on", note=64, velocity=0))   # vel-0 == off
    check("router note_off + vel-0 note_on close their keys taps",
          keys_taps() == [(60, True), (60, False), (64, True), (64, False)])

    # sustain pedal up via the router must not strand anything
    taps.clear()
    r._handle(mido.Message("note_on", note=50, velocity=90))
    r._handle(mido.Message("control_change", control=64, value=127))
    r._handle(mido.Message("note_off", note=50))
    r._handle(mido.Message("control_change", control=64, value=0))
    check("sustained note tap still closes at key release",
          (50, False) in keys_taps())


# ---- v8: palette/alloc lifecycle on a FAKE ENGINE (real Rack + edit_chain) --------

class _FakeBus:
    _n = [16]

    def __init__(self, count=2):
        self.id = _FakeBus._n[0]
        _FakeBus._n[0] += count

    def __int__(self): return self.id
    def free(self): pass


class _FakeSynth:
    def __init__(self, **kw): self.kw = kw
    def set(self, **kw): self.kw.update(kw)
    def free(self): pass
    def move(self, *a, **k): pass
    def pause(self): pass
    def unpause(self): pass


class _FakeServer:
    def add_synthdefs(self, *a): pass
    def sync(self): pass

    def add_bus_group(self, calculation_rate=None, count=2):
        return _FakeBus(count)

    def add_synth(self, synthdef, add_action=None, target_node=None, **kw):
        return _FakeSynth(**kw)


class _FakeEngine:
    def __init__(self):
        self.server = _FakeServer()
        self.root_group = None
        self.boot_note = None

    def register(self, *m): pass


class _FakeMaster:
    _master_node = None
    volume = 0.8
    def start(self): pass
    def stop(self): pass


def _fparam(default=0.5):
    return SimpleNamespace(default=default, minimum=0.0, maximum=1.0,
                           curve="lin", options=())


def _fmod(key, kind, params, gate=False):
    names = dict.fromkeys(list(params) + (["gate"] if gate else []), 0)
    return SimpleNamespace(key=key, name=key.title(), kind=kind,
                           family=kind, service=False,
                           params={p: _fparam() for p in params},
                           synthdef=SimpleNamespace(parameters=names,
                                                    effective_name=key))


def make_engine_app():
    app = SynthApp(use_midi=False, use_reload=False)
    app.registry = {
        "pluck": _fmod("pluck", "source", ["freq", "amp", "gate"], gate=True),
        "chorus": _fmod("chorus", "effect", ["mix"]),
        "echo": _fmod("echo", "effect", ["amp"]),
    }
    app.engine = _FakeEngine()
    app.master = _FakeMaster()
    app._build_from({"chain": [("pluck", {}), ("chorus", {}), ("echo", {})]},
                    "mock")
    return app


def test_spawn_delete_respawn_cycle():
    """v8 #1 guard: palette types must never leave the registry, and freed
    instance ids must be re-allocatable after a delete."""
    app = make_engine_app()
    n1 = app.spawn_unconnected("pluck")
    check("second source allocs .2", n1 == "pluck.2")
    check("registry retains the type after spawn", "pluck" in app.registry)
    app.edit_chain("remove", "pluck.2")
    check("instance removed", all(i.key != "pluck.2"
                                  for i in app.rack.instances))
    check("registry retains the type after delete", "pluck" in app.registry)
    n2 = app.spawn_unconnected("pluck")
    check("respawn after delete reuses the freed id", n2 == "pluck.2")
    e1 = app.spawn_unconnected("echo")
    app.edit_chain("remove", e1)
    check("effect respawn-after-delete works too",
          app.spawn_unconnected("echo") == "echo.2")
    app.transport.shutdown()


def test_snip_heal_audio():
    """v8 #2: removing X from A→X→B on the AUDIO graph reconnects A→B."""
    app = make_engine_app()
    app.graph_wires = app.rack.audio_wires()  # pluck→chorus→echo→master
    check("premise: chorus is mid-chain",
          {"from": "pluck", "to": "chorus"} in app.graph_wires)
    app.edit_chain("remove", "chorus")
    check("audio snip-heal bridged A→B",
          {"from": "pluck", "to": "echo"} in app.graph_wires)
    check("no dangling wires to the removed module", all(
        "chorus" not in (w.get("from"), w.get("to"))
        for w in app.graph_wires))
    app.transport.shutdown()


# ---- incremental edit_chain: survivors are never respawned (mass-delete fix) ------

def test_incremental_add_preserves_survivors():
    """Adding a module must NOT rebuild the rack: every existing instance keeps
    its exact node + bus objects (identity), proving no teardown-rebuild."""
    app = make_engine_app()
    before = {i.key: (id(i.node), id(i)) for i in app.rack.instances
              if not i.service}
    new_id = app.spawn_unconnected("echo")     # echo.2, parked
    after = {i.key: (id(i.node), id(i)) for i in app.rack.instances
             if not i.service}
    check("new module appended", new_id == "echo.2" and new_id in after)
    check("every original module survived", all(k in after for k in before))
    check("survivors keep the SAME node objects (not respawned)",
          all(after[k] == before[k] for k in before))
    app.transport.shutdown()


def test_incremental_remove_frees_only_one():
    """Removing a module frees exactly that node and leaves the rest untouched."""
    app = make_engine_app()
    freed = []
    for i in app.rack.instances:
        i.node.free = (lambda k=i.key: freed.append(k))  # record who gets freed
    keep = {i.key: id(i.node) for i in app.rack.instances
            if not i.service and i.key != "chorus"}
    app.graph_wires = app.rack.audio_wires()
    app.edit_chain("remove", "chorus")
    surv = {i.key: id(i.node) for i in app.rack.instances if not i.service}
    check("only the removed node was freed", freed == ["chorus"])
    check("all other modules still present", set(surv) == set(keep))
    check("survivors keep identical node objects", all(surv[k] == keep[k]
                                                       for k in keep))
    app.transport.shutdown()


def test_add_spawn_failure_no_mass_delete():
    """THE regression guard: if the NEW module's spawn raises, the existing rack
    must survive intact (the teardown-before-build bug lost everything here)."""
    app = make_engine_app()
    app.graph_wires = app.rack.audio_wires()
    before = [i.key for i in app.rack.instances if not i.service]
    before_nodes = {i.key: id(i.node) for i in app.rack.instances
                    if not i.service}

    orig_add_synth = app.engine.server.add_synth
    def boom(*a, **k):
        raise RuntimeError("scsynth refused /s_new (simulated heavy-module fail)")
    app.engine.server.add_synth = boom
    raised = False
    try:
        app.edit_chain("add", "echo")     # this spawn will fail
    except RuntimeError:
        raised = True
    app.engine.server.add_synth = orig_add_synth

    after = [i.key for i in app.rack.instances if not i.service]
    check("failed add raised (surfaces to the GUI toast, not silent loss)", raised)
    check("NO mass delete — every existing module survived", after == before)
    check("survivors were never respawned", all(
        id(i.node) == before_nodes[i.key]
        for i in app.rack.instances if not i.service))
    app.transport.shutdown()


# ---- streamlined edit flow: no greedy per-edit node churn -------------------------

def _count_moves(app):
    """Wrap every live node's .move to count scsynth reorder ops."""
    calls = {"n": 0}
    for i in app.rack.instances:
        if i.node is not None:
            orig = i.node.move
            i.node.move = lambda *a, _o=orig, **k: (calls.__setitem__("n", calls["n"] + 1), _o(*a, **k))[1]
    return calls


def _order_valid(app):
    keys = [i.key for i in app.rack.instances if not i.service]
    pos = {k: n for n, k in enumerate(keys)}
    for w in app.graph_wires or []:
        a, b = w.get("from"), w.get("to")
        if a in pos and b in pos and pos[a] >= pos[b]:
            return False
    return True


def test_add_issues_no_node_moves():
    """Adding a module must not move ANY existing node (parked, silent)."""
    app = make_engine_app()
    app.graph_wires = app.rack.audio_wires()
    calls = _count_moves(app)
    app.spawn_unconnected("echo")
    check("bare add triggers zero node reorders", calls["n"] == 0)
    app.transport.shutdown()


def test_reorder_noop_when_already_ordered():
    """reorder_for_wires must do ZERO node moves when the order already holds."""
    app = make_engine_app()
    app.graph_wires = app.rack.audio_wires()  # pluck→chorus→echo→master, in order
    calls = _count_moves(app)
    app.rack.reorder_for_wires(app.graph_wires)
    check("redundant reorder moves nothing (cheap path)", calls["n"] == 0)
    app.transport.shutdown()


def test_order_invariant_after_edits():
    """After a mix of adds, splices and removes, every wire's src precedes its
    dst in the authoritative instance order (so reorder can stay cheap)."""
    app = make_engine_app()
    app.graph_wires = app.rack.audio_wires()
    a = app.spawn_unconnected("echo")      # echo.2, parked
    app.graph_wire("add", "pluck", a)      # splice pluck→echo.2
    app.graph_wire("add", a, "chorus")     # → chorus
    b = app.spawn_unconnected("chorus")    # chorus.2, parked
    app.graph_wire("add", a, b)            # re-aim echo.2 → chorus.2
    app.graph_wire("add", b, "master")
    app.edit_chain("remove", "echo")       # remove original tail-ish module
    check("instance order satisfies every wire after edits", _order_valid(app))
    check("no wires reference removed 'echo'", all(
        "echo" not in (w.get("from"), w.get("to")) or w.get("from", "").startswith("echo.")
        or w.get("to", "").startswith("echo.")
        for w in app.graph_wires))
    app.transport.shutdown()


# ---- v8: ctl snip-heal on tonic/keyshift removal -----------------------------------

def test_snip_heal_ctl():
    # 1-in/1-out through a deriver: heal A→B
    app = SynthApp(use_midi=False, use_reload=False)
    app.arp = FakeNoteSink()
    app.spawn_tonic()
    app.ctl_wires = []
    app.set_ctl_wire("add", "keys", "tonic")
    app.set_ctl_wire("add", "tonic", "arp")
    app.remove_tonic("tonic")
    check("removing a 1-in/1-out deriver heals A→B",
          app.ctl_wires == [{"from": "keys", "to": "arp"}])

    # multi-in: ambiguous — wires just drop
    app.spawn_tonic()
    app.ctl_wires = [{"from": "keys", "to": "tonic"},
                     {"from": "deck", "to": "tonic"},
                     {"from": "tonic", "to": "arp"}]
    app.remove_tonic("tonic")
    check("multi-in deriver removal drops (no invented wires)",
          app.ctl_wires == [])

    # heal that would self-loop is dropped, not raised
    app.spawn_tonic()
    app.ctl_wires = [{"from": "arp", "to": "tonic"},
                     {"from": "tonic", "to": "arp"}]
    app.remove_tonic("tonic")
    check("self-loop heal is dropped silently", app.ctl_wires == [])

    # tonic→drone wires are a different signal kind: never healed into
    app2 = SynthApp(use_midi=False, use_reload=False)
    app2.rack = RecordingRack([("pluck", "source"), ("drone", "source")], [])
    app2.spawn_tonic()
    app2.ctl_wires = [{"from": "keys", "to": "tonic"},
                      {"from": "tonic", "to": "drone"}]
    app2.remove_tonic("tonic")
    check("tonic-out (drone) wires drop, never heal",
          app2.ctl_wires == [])

    # keyshift: heal PER LANE (each lane is its own A→X→B)
    app3 = SynthApp(use_midi=False, use_reload=False)
    app3.arp = FakeNoteSink()
    kid = app3.spawn_keyshift()
    app3.ctl_wires = []
    app3.set_ctl_wire("add", "keys", f"{kid}:1")
    app3.set_ctl_wire("add", f"{kid}:1", "arp")
    app3.set_ctl_wire("add", "arp", f"{kid}:2")
    app3.set_ctl_wire("add", f"{kid}:2", "deck")
    app3.set_ctl_wire("add", "deck", f"{kid}:3")   # lane 3: in only — drop
    app3.remove_keyshift(kid)
    check("keyshift removal heals each 1-in/1-out lane",
          {"from": "keys", "to": "arp"} in app3.ctl_wires and
          {"from": "arp", "to": "deck"} in app3.ctl_wires)
    check("half-wired lanes drop; nothing else invented",
          len(app3.ctl_wires) == 2)


# ---- v6: key shifter ------------------------------------------------------------

def test_keyshift_offsets():
    # semitone distance from C, mapped NEAREST (>6 wraps down an octave)
    check("offset C = 0", nearest_offset(0) == 0)
    check("offset F# = +6", nearest_offset(6) == 6)
    check("offset G = -5 (nearest, not +7)", nearest_offset(7) == -5)
    check("offset A = -3", nearest_offset(9) == -3)
    check("offset B = -1", nearest_offset(11) == -1)
    check("offset E = +4", nearest_offset(4) == 4)
    check("offsets all within ±6",
          all(abs(nearest_offset(k)) <= 6 for k in range(12)))


def test_keyshift_lanes_no_merge():
    app = SynthApp(use_midi=False, use_reload=False)
    kid = app.spawn_keyshift()
    check("first shifter id is keyshift", kid == "keyshift")
    check("second shifter suffixes", app.spawn_keyshift() == "keyshift.2")
    app.remove_keyshift("keyshift.2")

    v1, v2 = FakeNoteSink(), FakeNoteSink()
    app.voices["voice"] = v1
    app.voices["voice.2"] = v2
    app.ctl_wires = []
    app.set_ctl_wire("add", "keys", "keyshift:1")
    app.set_ctl_wire("add", "keys", "keyshift:2")
    app.set_ctl_wire("add", "keyshift:1", "voice")
    app.set_ctl_wire("add", "keyshift:2", "voice.2")
    app.set_keyshift(kid, key=7)   # G → -5

    ks = app.keyshifts[kid]
    ks.lane_note_on(1, 60)
    check("lane 1 in → lane 1 out ONLY (no merge)",
          v1.ons == [55] and v2.ons == [])
    ks.lane_note_on(2, 62)
    check("lane 2 in → lane 2 out ONLY", v2.ons == [57] and v1.ons == [55])
    ks.lane_note_off(1, 60)
    check("lane 1 off stays on lane 1", v1.offs == [55] and v2.offs == [])

    # full dispatch through the wires: keys fans into both lanes
    v1.ons.clear(); v2.ons.clear()
    app.note_on(64)
    check("keys → both lanes shift and fan to their own outs",
          v1.ons == [59] and v2.ons == [59])
    app.note_off(64)

    # validation: lanes 1..4 only; bare/self wiring rejected
    for src, dst in (("keys", "keyshift:5"), ("keys", "keyshift:0"),
                     ("keys", "keyshift"), ("keyshift:1", "keyshift:2"),
                     ("keyshift", "voice"), ("keys", "keyshift.9:1")):
        try:
            app.set_ctl_wire("add", src, dst)
            check(f"reject {src}→{dst}", False)
        except (ValueError, KeyError):
            check(f"reject {src}→{dst}", True)

    # taps: output fires are tagged with the shifter's id
    taps = []
    app.on_midi_event = lambda e: taps.append(dict(e))
    ks.lane_note_on(3, 60)
    ks.lane_note_off(3, 60)
    check("shifter taps its output fires",
          [(e["note"], e["on"]) for e in taps
           if e.get("kind") == "tap" and e.get("src") == kid] ==
          [(55, True), (55, False)])

    # removal drops the shifter and its lane wires; v8 snip-heal bridges
    # each 1-in/1-out lane (keys→ks:1→voice, keys→ks:2→voice.2)
    app.remove_keyshift(kid)
    check("remove_keyshift drops the shifter + lane wires (healed pairwise)",
          kid not in app.keyshifts and
          sorted((w["from"], w["to"]) for w in app.ctl_wires) ==
          [("keys", "voice"), ("keys", "voice.2")])


def test_keyshift_off_uses_ons_offset():
    app = SynthApp(use_midi=False, use_reload=False)
    kid = app.spawn_keyshift()
    v = FakeNoteSink()
    app.voices["voice"] = v
    app.ctl_wires = [{"from": "keyshift:1", "to": "voice"}]
    ks = app.keyshifts[kid]

    ks.configure(key=0)          # C: no shift
    ks.lane_note_on(1, 60)       # sounds 60
    ks.configure(key=2)          # D: +2 — mid-note key change
    ks.lane_note_off(1, 60)
    check("off uses the ON's offset across a key change (no stuck note)",
          v.ons == [60] and v.offs == [60])

    ks.lane_note_on(1, 60)       # now shifts to 62
    check("next on uses the new key", v.ons[-1] == 62)
    ks.lane_note_off(1, 60)
    check("and its off matches", v.offs[-1] == 62)

    # all_off releases at the SHIFTED pitches
    ks.configure(key=9)          # A → -3
    ks.lane_note_on(1, 70)       # sounds 67
    ks.configure(key=0)
    ks.lane_all_off(1)
    check("lane all_off releases at the shifted pitch", v.all_offs >= 1)
    app.remove_keyshift(kid)


def test_keyshift_progression():
    app = SynthApp(use_midi=False, use_reload=False)
    kid = app.spawn_keyshift()
    ks = app.keyshifts[kid]
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))

    ks.configure(key=5, length=4, steps=[None, None, None, None])
    check("empty track = static key", ks.active == 5)
    check("settings shape", set(ks.settings()) ==
          {"id", "key", "length", "steps", "active"})

    ks.configure(steps=[0, None, 7, None])
    # fake transport: drive on_beat directly, beat 0 of each bar steps
    ks.on_beat(0, 0)
    check("bar 0 lands step 0 (C)", ks.active == 0)
    ks.on_beat(0, 1)
    ks.on_beat(0, 2)
    check("mid-bar beats do not step", ks.active == 0)
    ks.on_beat(1, 0)
    check("null step holds the previous key", ks.active == 0)
    ks.on_beat(2, 0)
    check("bar 2 lands step 2 (G)", ks.active == 7)
    ks.on_beat(3, 0)
    check("bar 3 holds", ks.active == 7)
    ks.on_beat(6, 0)   # 6 % 4 = 2 → G again (no event: unchanged)
    check("track wraps with bar % length", ks.active == 7)
    ks.on_beat(8, 0)   # 8 % 4 = 0 → C
    check("wrap back to step 0", ks.active == 0)
    check("keyshift events emitted on active changes",
          [e["active"] for e in events if e.get("kind") == "keyshift"] == [0, 7, 0])

    # app's beat handler drives it (through the real hook)
    ks.configure(steps=[3, None, None, None])
    app._handle_beat(4, 0)
    check("app._handle_beat rides the progression", ks.active == 3)

    # length clamps + steps resize; state exposes the shifter
    ks.configure(length=99)
    check("length clamps to 32", ks.length == 32 and len(ks.steps) == 32)
    ks.configure(length=2)
    check("shrinking keeps prefix", ks.length == 2 and ks.steps == [3, None])
    app.voices.clear()
    st_keyshifts = [k.settings() for k in app.keyshifts.values()]
    check("keyshifts exposed like voices/tonics",
          st_keyshifts and st_keyshifts[0]["id"] == kid)
    app.remove_keyshift(kid)


def test_keyshift_persistence_shape():
    """ctl_wires with lane endpoints survive like any other wire — the app
    stores them verbatim and _ctl_sinks re-resolves live after rebuilds."""
    app = SynthApp(use_midi=False, use_reload=False)
    kid = app.spawn_keyshift()
    app.ctl_wires = []
    app.set_ctl_wire("add", "keys", f"{kid}:1")
    app.set_ctl_wire("add", f"{kid}:1", "arp")
    wires_before = [dict(w) for w in app.ctl_wires]
    # a rebuild recreates arp/voices but never touches ctl_wires/keyshifts
    app.arp = FakeNoteSink()
    check("lane wires stored verbatim", app.ctl_wires == wires_before)
    check("lane sinks re-resolve live",
          app.keyshifts[kid].lane_in(1) is not None and
          app.arp in [s for s in app._ctl_sinks(f"{kid}:1")])
    # deck replay resolves lane endpoints too
    app.ctl_wires = [{"from": "deck", "to": f"{kid}:3"}]
    sink = app.looper._sink()
    check("deck replay resolves a keyshift lane",
          sink is app.keyshifts[kid].lane_in(3))
    app.remove_keyshift(kid)


# ---- drums target / to_chain compat --------------------------------------------

def test_drums_target():
    rack = make_rack([
        fake_inst("pluck", "source", {"out": 16}),
        fake_inst("echo", "effect", {"in_bus": 16, "out": 0}),
    ])
    app = SimpleNamespace(rack=rack, engine=None)
    d = DrumMachine(app)
    check("default target master", d.target == "master")

    d.configure(to_chain=True)
    check("to_chain True → chain head", d.target == "pluck")
    d.configure(to_chain=False)
    check("to_chain False → master", d.target == "master")
    d.configure(target="echo")
    check("explicit target wins", d.target == "echo")
    d.configure(levels={"kick": 0.5})   # unrelated configure must not touch target
    check("target sticky", d.target == "echo")
    d.configure(target=None)
    check("target None = disconnected", d.target is None)

    snap = d.snapshot()
    check("snapshot carries target", snap["target"] is None and "to_chain" not in snap)
    d2 = DrumMachine(app)
    d2.restore({"enabled": False, "to_chain": True})   # legacy preset
    check("legacy restore maps to_chain", d2.target == "pluck")
    d2.restore(snap)
    check("modern restore", d2.target is None)
    check("settings exposes legacy to_chain",
          DrumMachine(app).settings()["to_chain"] is False)


# ---- v5: instance ids -----------------------------------------------------------

def test_instance_ids():
    # id allocation: first instance = type key, then ".2", ".3"
    check("alloc first id = type", alloc_id("lowpass", []) == "lowpass")
    check("alloc second id suffixes",
          alloc_id("lowpass", ["lowpass"]) == "lowpass.2")
    check("alloc skips taken suffixes",
          alloc_id("lowpass", ["lowpass", "lowpass.2"]) == "lowpass.3")
    check("type_of strips the suffix",
          type_of("lowpass.2") == "lowpass" and type_of("lowpass") == "lowpass")

    # duplicate module keys in a plain chain spec auto-suffix
    norm = Rack._normalize(["pluck", "lowpass", "lowpass", ("lowpass", {})])
    check("normalize auto-suffixes duplicates",
          [k for k, _ in norm] == ["pluck", "lowpass", "lowpass.2", "lowpass.3"])

    # two lowpass instances: independent params + independent wiring
    rack = make_rack([
        fake_inst("pluck", "source", {"out": 16}),
        fake_inst("lowpass", "effect", {"in_bus": 16, "out": 18, "cutoff": 500}),
        fake_inst("lowpass.2", "effect", {"in_bus": 18, "out": 0, "cutoff": 500}),
    ])
    rack.set_param("lowpass.2", "cutoff", 2000)
    check("duplicate instances keep independent params",
          rack.find("lowpass").settings["cutoff"] == 500 and
          rack.find("lowpass.2").settings["cutoff"] == 2000)
    check("set_param hit only the addressed node",
          rack.find("lowpass").node.sets == [] and
          rack.find("lowpass.2").node.sets == [{"cutoff": 2000}])
    wires = rack.audio_wires()
    check("wires carry instance ids",
          {"from": "pluck", "to": "lowpass"} in wires and
          {"from": "lowpass", "to": "lowpass.2"} in wires and
          {"from": "lowpass.2", "to": "master"} in wires)

    # legacy type-key resolution: bare type finds the FIRST instance of it
    check("find prefers the exact id", rack.find("lowpass").key == "lowpass")
    rack2 = make_rack([
        fake_inst("pluck", "source", {"out": 16}),
        fake_inst("lowpass.2", "effect", {"in_bus": 16, "out": 0}),
    ])
    check("legacy type key resolves to first instance of the type",
          rack2.find("lowpass").key == "lowpass.2")
    try:
        rack2.find("nope")
        check("unknown id still raises", False)
    except KeyError:
        check("unknown id still raises", True)


# ---- v5: multiple mono voices ------------------------------------------------------

def test_multi_voice():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack(
        [("pluck", "source"), ("fm_bell", "source"), ("echo", "effect")],
        [{"from": "pluck", "to": "echo"}, {"from": "echo", "to": "master"}],
    )
    for k in ("pluck", "fm_bell"):
        app.rack.find(k).settings.update({"gate": 0, "freq": 220})
    app.voices["voice"] = MonoVoice(app.rack, "pluck")

    vid = app.spawn_voice()
    check("second voice id is voice.2", vid == "voice.2")
    check("state exposes voices with targets", sorted(
        (v["id"], v["target"]) for v in
        [{"id": i, "target": v.target_key} for i, v in app.voices.items()]
    ) == [("voice", "pluck"), ("voice.2", "pluck")])

    app.set_voice_target("fm_bell", voice="voice.2")
    check("voices retarget independently",
          app.voices["voice"].target_key == "pluck" and
          app.voices["voice.2"].target_key == "fm_bell")

    # keys fan to whichever voices are wired
    app.ctl_wires = [{"from": "keys", "to": "voice"},
                     {"from": "keys", "to": "voice.2"}]
    app.rack.calls.clear()
    app.note_on(60)
    tgt = [c[1] for c in app.rack.calls if c[0] == "set_params"]
    check("keys fan to both wired voices, each at its own target",
          tgt == ["pluck", "fm_bell"])
    app.note_off(60)

    # only one wired → only that voice fires
    app.ctl_wires = [{"from": "keys", "to": "voice.2"}]
    app.rack.calls.clear()
    app.note_on(62)
    tgt = [c[1] for c in app.rack.calls if c[0] == "set_params"]
    check("unwired voice stays silent", tgt == ["fm_bell"])
    app.note_off(62)

    # transpose is global
    app.set_transpose(5)
    check("transpose hits every voice",
          all(v.transpose == 5 for v in app.voices.values()))

    # removal: wires drop with the voice; the primary is fixed
    app.ctl_wires = [{"from": "keys", "to": "voice.2"}]
    app.remove_voice("voice.2")
    check("remove_voice drops the voice + its wires",
          "voice.2" not in app.voices and app.ctl_wires == [])
    try:
        app.remove_voice("voice")
        check("primary voice cannot be removed", False)
    except ValueError:
        check("primary voice cannot be removed", True)


# ---- v5: tonic deriver → drone root-follow --------------------------------------------

def test_tonic_drone():
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack([("pluck", "source"), ("drone", "source")], [])
    app.rack.find("drone").settings["freq"] = 55.0
    events = []
    app.on_midi_event = events.append

    tid = app.spawn_tonic()
    check("first deriver id is tonic", tid == "tonic")
    check("second deriver suffixes", app.spawn_tonic() == "tonic.2")
    app.remove_tonic("tonic.2")

    # tonic outs only connect to tonic ins (drone instances)
    try:
        app.set_ctl_wire("add", "keys", "drone")
        check("non-tonic src rejected into a drone", False)
    except ValueError:
        check("non-tonic src rejected into a drone", True)
    app.set_ctl_wire("add", "tonic", "drone")
    check("tonic→drone wire accepted",
          {"from": "tonic", "to": "drone"} in app.ctl_wires)

    # thru: notes wired INTO the deriver observe + fan out its ctl wires
    sink = FakeNoteSink()
    app.voices["voice"] = sink
    app.set_ctl_wire("add", "keys", "tonic")
    app.set_ctl_wire("add", "tonic", "voice")
    d = app.tonics["tonic"]
    app.note_on(48)  # C2, heavy bass evidence
    check("deriver thru forwards the unmodified stream", sink.ons == [48])
    check("deriver emitted a thru tap", any(
        e.get("kind") == "tap" and e.get("src") == "tonic" for e in events))

    # fake estimator feed → grid decision drives the wired drone's freq
    for _ in range(4):
        d.est.observe(48)
        d.est.observe(60)
        d.est.observe(67)  # C major-ish: root should land on C (pc 0)
    d.decide()
    check("decide() picked C as root", d.root == 0)
    freq_sets = [c for c in app.rack.calls
                 if c[0] == "set_param" and c[1] == "drone" and c[2] == "freq"]
    check("root change drove the wired drone's freq", len(freq_sets) >= 1
          and abs(freq_sets[-1][3] - 65.41) < 0.1)   # C2 at octave 2
    check("tonic_out event emitted", any(
        e.get("kind") == "tonic_out" and e.get("id") == "tonic"
        and e.get("root") == "C" for e in events))

    # follow toggle off: further root changes leave the drone alone
    app.set_drone_follow("drone", False)
    app.rack.calls.clear()
    for _ in range(30):
        d.est.observe(43)
        d.est.observe(50)
        d.est.observe(55)  # strong G evidence
    d.decide()
    check("root moved on", d.root == 7)
    check("follow off leaves the drone alone", not any(
        c[0] == "set_param" and c[1] == "drone" for c in app.rack.calls))
    # ...and toggling back on re-applies the current root immediately
    app.set_drone_follow("drone", True)
    check("follow on re-pitches immediately", any(
        c[0] == "set_param" and c[1] == "drone" and c[2] == "freq"
        for c in app.rack.calls))

    # octave knob re-pitches wired drones
    app.rack.calls.clear()
    app.set_tonic("tonic", octave=1)
    fs = [c for c in app.rack.calls if c[0] == "set_param" and c[1] == "drone"]
    check("octave change re-pitches at the new octave",
          fs and abs(fs[-1][3] - 49.0) < 0.5)  # G1 at octave 1

    # legacy set_drone maps to a deriver+drone pair with default wiring
    app2 = SynthApp(use_midi=False, use_reload=False)
    app2.rack = RecordingRack([("pluck", "source")], [])
    app2.registry = {}
    app2.set_drone(enabled=True, every="2 bars", octave=3)
    check("legacy set_drone ensures a tonic deriver",
          "tonic" in app2.tonics and app2.tonics["tonic"].every == "2 bars"
          and app2.tonics["tonic"].octave == 3)
    check("legacy drone settings shape preserved", set(
        app2._legacy_drone_settings()) ==
        {"enabled", "every", "everies", "octave", "root"})
    for d2 in app2.tonics.values():
        d2.shutdown()
    for d2 in app.tonics.values():
        d2.shutdown()


def main():
    test_wires_derivation()
    test_graph_wire_bookkeeping()
    test_spawn_unconnected()
    test_ctl_wires()
    test_tap_emission()
    test_alloff_closes_taps()
    test_deck_take_closure()
    test_keys_off_paths()
    test_spawn_delete_respawn_cycle()
    test_snip_heal_audio()
    test_incremental_add_preserves_survivors()
    test_incremental_remove_frees_only_one()
    test_add_spawn_failure_no_mass_delete()
    test_add_issues_no_node_moves()
    test_reorder_noop_when_already_ordered()
    test_order_invariant_after_edits()
    test_snip_heal_ctl()
    test_keyshift_offsets()
    test_keyshift_lanes_no_merge()
    test_keyshift_off_uses_ons_offset()
    test_keyshift_progression()
    test_keyshift_persistence_shape()
    test_drums_target()
    test_instance_ids()
    test_multi_voice()
    test_tonic_drone()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
