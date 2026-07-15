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
    check("spawn adds then disconnects",
          added == [("add", "chorus")] and
          {"from": "chorus", "to": None} in app.graph_wires)
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
    test_drums_target()
    test_instance_ids()
    test_multi_voice()
    test_tonic_drone()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
