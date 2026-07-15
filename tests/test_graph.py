"""Graph-overlay logic tests that run anywhere (no scsynth, no audio).

    python tests/test_graph.py

Covers: audio-wire derivation from rack settings, graph_wires bookkeeping
(one-out-per-source, cycle rejection, disconnect memory, reapply-after-
rebuild), the drums target / to_chain compatibility mapping, and the v3
control plane: ctl_wires defaults, add/remove + validation, keys/arp
dispatch through the wires, deck sink resolution, and select_patch reset.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.rack import Rack  # noqa: E402
from synthbase.drums import DrumMachine  # noqa: E402
from synthbase.app import SynthApp, default_ctl_wires  # noqa: E402
from synthbase.looper import _FanSink  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def fake_inst(key, kind, settings, service=False):
    return SimpleNamespace(key=key, module=SimpleNamespace(kind=kind),
                           settings=dict(settings), service=service, node=None)


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
    orig_gw = app.graph_wire

    def fake_edit_chain(action, key, index=None):
        added.append((action, key))
        app.rack.instances.append(fake_inst(key, "effect", {}))

    app.edit_chain = fake_edit_chain
    app.spawn_unconnected("chorus")
    check("spawn snapshots wiring before add",
          app.graph_wires is not None and
          {"from": "pluck", "to": "echo"} in app.graph_wires)
    check("spawn adds then disconnects",
          added == [("add", "chorus")] and
          {"from": "chorus", "to": None} in app.graph_wires)


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
    check("default set is keys→arp, arp→{voice,deck,drone}, deck→voice",
          app.ctl_wires == [
              {"from": "keys", "to": "arp"},
              {"from": "arp", "to": "voice"},
              {"from": "arp", "to": "deck"},
              {"from": "arp", "to": "drone"},
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

    # arp fan-out resolution: arp→{voice, deck(voiced), drone}
    app3 = SynthApp(use_midi=False, use_reload=False)
    app3.voice = FakeNoteSink()
    sinks = app3._ctl_sinks("arp")
    check("arp sinks resolve voice + deck voiced tap + drone tap",
          app3.voice in sinks and app3._deck_voiced_tap in sinks
          and app3._drone_tap in sinks and len(sinks) == 3)
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
    app4.ctl_wires = [{"from": "deck", "to": "arp"}, {"from": "deck", "to": "drone"}]
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


def main():
    test_wires_derivation()
    test_graph_wire_bookkeeping()
    test_spawn_unconnected()
    test_ctl_wires()
    test_drums_target()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
