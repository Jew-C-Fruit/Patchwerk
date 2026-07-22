"""Routable-LFO tests (item 7) — CI-safe: no scsynth, no audio.

    python tests/test_lfo.py

Covers: standalone instances (spawn/remove/ids), the fan-out (one norm
bus, per-dest scale synths, distinct out buses), single-input stealing,
center = the destination's slider value (set_param_unit steers it, unwire
restores the setting), module-removal and rack-rebuild hygiene, snapshot/
restore round-trip, and MIGRATION of the pre-item-7 persistence format.

Server interactions run against a fake supriya server so the exact
add_synth/map/free traffic is assertable.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.lfo import SHAPES, _lfo_norm, _lfo_scale  # noqa: E402
from synthbase.module import Param  # noqa: E402
from synthbase.rack import Rack  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- fakes -------------------------------------------------------------------

class FakeBus:
    _n = 100

    def __init__(self):
        FakeBus._n += 1
        self.n = FakeBus._n
        self.freed = False

    def __int__(self):
        return self.n

    def free(self):
        self.freed = True


class FakeSynth:
    def __init__(self, definition, kwargs):
        self.definition = definition
        self.kwargs = kwargs
        self.sets = []
        self.freed = False

    def set(self, **kw):
        self.sets.append(kw)

    def free(self):
        self.freed = True


class FakeServer:
    def __init__(self):
        self.synthdefs = []
        self.synths = []
        self.buses = []

    def add_synthdefs(self, *defs):
        self.synthdefs.extend(defs)

    def sync(self):
        pass

    def add_bus(self, calculation_rate=None):
        b = FakeBus()
        self.buses.append(b)
        return b

    def add_synth(self, definition, **kwargs):
        s = FakeSynth(definition, kwargs)
        self.synths.append(s)
        return s


class FakeModNode:
    def __init__(self):
        self.maps = []
        self.sets = []

    def map(self, **kw):
        self.maps.append(kw)

    def set(self, **kw):
        self.sets.append(kw)


def fake_inst(key, params):
    module = SimpleNamespace(
        kind="effect",
        params={name: Param(*spec) for name, spec in params.items()},
    )
    settings = {n: p.default for n, p in module.params.items()}
    return SimpleNamespace(key=key, type=key.split(".")[0], module=module,
                           settings=settings, node=FakeModNode(),
                           enabled=True, service=False)


def make_app(server=True):
    app = SynthApp(use_midi=False, use_reload=False)
    if server:
        app.engine = SimpleNamespace(server=FakeServer(), root_group="ROOT")
    rack = Rack(engine=SimpleNamespace(server=None, root_group=None),
                registry={})
    rack.instances = [
        fake_inst("lowpass", {"cutoff": (100, 8000, 1200, "exp"),
                              "res": (0, 1, 0.3)}),
        fake_inst("echo", {"mix": (0, 1, 0.4), "time": (0.05, 2.0, 0.4)}),
    ]
    app.rack = rack
    return app


def norm_synths(app):
    return [s for s in app.engine.server.synths
            if s.definition is _lfo_norm and not s.freed]


def scale_synths(app):
    return [s for s in app.engine.server.synths
            if s.definition is _lfo_scale and not s.freed]


# ---- instances ---------------------------------------------------------------

def test_instances():
    app = make_app()
    lid = app.spawn_lfo()
    check("first LFO id is 'lfo'", lid == "lfo", lid)
    lid2 = app.spawn_lfo()
    check("second LFO id is 'lfo.2'", lid2 == "lfo.2", lid2)
    check("one norm synth per instance", len(norm_synths(app)) == 2)
    n = norm_synths(app)[0]
    check("norm synth carries rate/shape/depth + its bus",
          n.kwargs["rate"] == 1.0 and n.kwargs["depth"] == 0.25
          and isinstance(n.kwargs["kout"], int), str(n.kwargs))

    app.lfo_set(lid, rate=3.5, depth=0.6, shape="tri")
    check("lfo_set updates the norm synth",
          n.sets and n.sets[-1] == {"rate": 3.5, "depth": 0.6,
                                    "shape": SHAPES.index("tri")},
          str(n.sets))

    st = app.lfos.state()
    check("state lists both instances with dests",
          [e["id"] for e in st] == ["lfo", "lfo.2"]
          and all(e["dests"] == [] for e in st), str(st))

    app.remove_lfo(lid2)
    check("remove frees the norm synth", len(norm_synths(app)) == 1)
    try:
        app.remove_lfo("lfo.9")
        check("removing an unknown LFO raises", False)
    except KeyError:
        check("removing an unknown LFO raises", True)


# ---- fan-out + stealing ------------------------------------------------------

def test_fanout():
    app = make_app()
    lid = app.spawn_lfo()
    app.lfo_wire("add", lid, "lowpass", "cutoff")
    app.lfo_wire("add", lid, "echo", "mix")

    sc = scale_synths(app)
    check("one scale synth per destination", len(sc) == 2)
    norm_bus = norm_synths(app)[0].kwargs["kout"]
    check("both scale synths read the SHARED norm bus",
          all(s.kwargs["kin"] == norm_bus for s in sc),
          str([s.kwargs for s in sc]))
    check("each destination gets its OWN out bus",
          sc[0].kwargs["kout"] != sc[1].kwargs["kout"])
    from supriya import AddAction
    check("scale synths spawn AFTER their norm (add_after)",
          all(s.kwargs["target_node"] is norm_synths(app)[0]
              and s.kwargs["add_action"] == AddAction.ADD_AFTER
              for s in sc), str(sc[0].kwargs))
    lp = app.rack.find("lowpass")
    check("cutoff param mapped to the scale bus",
          lp.node.maps and lp.node.maps[-1] == {"cutoff": sc[0].kwargs["kout"]}
          or lp.node.maps[-1].get("cutoff") is not None, str(lp.node.maps))
    check("exp curve baked into the cutoff scale synth",
          sc[0].kwargs["is_exp"] == 1 and sc[1].kwargs["is_exp"] == 0)
    check("rack.mapped guards both params",
          ("lowpass", "cutoff") in app.rack.mapped
          and ("echo", "mix") in app.rack.mapped)

    st = app.lfos.state()[0]
    check("state carries the dest fan-out",
          {(d["key"], d["param"]) for d in st["dests"]}
          == {("lowpass", "cutoff"), ("echo", "mix")}, str(st))

    # single-input: a second LFO wiring the same param STEALS it
    lid2 = app.spawn_lfo()
    app.lfo_wire("add", lid2, "lowpass", "cutoff")
    d1 = app.lfos.instances[lid]["dests"]
    d2 = app.lfos.instances[lid2]["dests"]
    check("re-wiring steals the param from the old LFO",
          ("lowpass", "cutoff") not in d1 and ("lowpass", "cutoff") in d2)
    check("stolen dest's old scale synth freed", len(scale_synths(app)) == 2)
    check("param still guarded after the steal",
          ("lowpass", "cutoff") in app.rack.mapped)

    # wiring the same dest to its own LFO again is a no-op
    n_synths = len(app.engine.server.synths)
    app.lfo_wire("add", lid2, "lowpass", "cutoff")
    check("re-wiring the same dest is a no-op",
          len(app.engine.server.synths) == n_synths)


# ---- center = the destination's slider --------------------------------------

def test_center():
    app = make_app()
    lid = app.spawn_lfo()
    lp = app.rack.find("lowpass")
    lp.settings["cutoff"] = 1200.0
    app.lfo_wire("add", lid, "lowpass", "cutoff")
    sc = scale_synths(app)[0]
    check("center starts at the param's current value (exp unit)",
          abs(sc.kwargs["center"]
              - app.lfos._to_unit(lp.module.params["cutoff"], 1200.0)) < 1e-9,
          str(sc.kwargs))

    # the slider on a mapped param steers the center, not the node
    v = app.set_param_unit("lowpass", "cutoff", 0.9)
    check("set_param_unit steers the dest center",
          sc.sets and sc.sets[-1] == {"center": 0.9}, str(sc.sets))
    check("steered value stored in settings for restore",
          abs(lp.settings["cutoff"] - v) < 1e-9)
    check("mapped param never node.set while mapped",
          not any("cutoff" in s for s in lp.node.sets), str(lp.node.sets))
    st = app.lfos.state()[0]["dests"][0]
    check("state reports the per-dest center", st["center"] == 0.9, str(st))

    # unknown (key,param) is not handled
    check("set_center_unit misses cleanly",
          app.lfos.set_center_unit("echo", "time", 0.5) is False)

    # unwire: param unmapped + restored to the stored setting
    app.lfo_wire("remove", lid, "lowpass", "cutoff")
    check("unwire unmaps the param",
          lp.node.maps[-1] == {"cutoff": None}, str(lp.node.maps))
    check("unwire restores the slider value onto the node",
          lp.node.sets and abs(lp.node.sets[-1]["cutoff"] - v) < 1e-9,
          str(lp.node.sets))
    check("unwire releases the map guard",
          ("lowpass", "cutoff") not in app.rack.mapped)
    check("unwire frees the scale synth", len(scale_synths(app)) == 0)


# ---- hygiene: module removal + rack rebuild ---------------------------------

def test_hygiene():
    app = make_app()
    lid = app.spawn_lfo()
    app.lfo_wire("add", lid, "lowpass", "cutoff")
    app.lfo_wire("add", lid, "echo", "mix")

    app.lfos.on_module_removed("lowpass")
    check("module removal drops its dests only",
          set(app.lfos.instances[lid]["dests"]) == {("echo", "mix")})
    check("module removal releases the guard",
          ("lowpass", "cutoff") not in app.rack.mapped)
    check("module removal frees the dest synth", len(scale_synths(app)) == 1)

    app.lfos.on_rack_rebuilt()
    check("rack rebuild drops ALL dests", app.lfos.instances[lid]["dests"] == {})
    check("rack rebuild keeps the LFO instances",
          lid in app.lfos.instances and len(norm_synths(app)) == 1)

    app.lfos.clear()
    check("clear removes everything",
          app.lfos.instances == {} and len(norm_synths(app)) == 0)


# ---- persistence: round-trip + migration ------------------------------------

def test_persistence():
    app = make_app()
    lid = app.spawn_lfo()
    app.lfo_set(lid, rate=2.0, depth=0.5, shape=1)
    app.lfo_wire("add", lid, "lowpass", "cutoff")
    app.lfo_wire("add", lid, "echo", "mix")
    lid2 = app.spawn_lfo()
    app.lfo_set(lid2, rate=0.1)

    snap = app.lfos.snapshot()
    check("snapshot is the new instances format",
          "instances" in snap and len(snap["instances"]) == 2, str(snap))

    app2 = make_app()
    app2.lfos.restore(snap)
    st = {e["id"]: e for e in app2.lfos.state()}
    check("restore recreates ids + knobs",
          st["lfo"]["rate"] == 2.0 and st["lfo"]["depth"] == 0.5
          and st["lfo"]["shape"] == 1 and st["lfo.2"]["rate"] == 0.1, str(st))
    check("restore rewires the fan-out",
          {(d["key"], d["param"]) for d in st["lfo"]["dests"]}
          == {("lowpass", "cutoff"), ("echo", "mix")}, str(st))
    check("restore re-guards params",
          ("lowpass", "cutoff") in app2.rack.mapped)

    # ---- pre-item-7 migration ----
    app3 = make_app()
    old = {"lowpass.cutoff": {"rate": 0.29, "shape": 0, "depth": 0.84,
                              "center": 0.75},
           "echo.mix": {"rate": 5.0, "shape": 4, "depth": 0.2,
                        "center": 0.25}}
    app3.lfos.restore(old)
    st = app3.lfos.state()
    check("migration spawns one instance per old assignment",
          len(st) == 2, str(st))
    allD = {(d["key"], d["param"]) for e in st for d in e["dests"]}
    check("migration wires each old destination",
          allD == {("lowpass", "cutoff"), ("echo", "mix")}, str(st))
    lp = app3.rack.find("lowpass")
    p = lp.module.params["cutoff"]
    check("old center becomes the destination's param value",
          abs(lp.settings["cutoff"] - p.from_unit(0.75)) < 1e-6,
          str(lp.settings))
    e_lp = next(e for e in st if e["dests"]
                and e["dests"][0]["key"] == "lowpass")
    check("migrated knobs survive (rate/depth/shape)",
          abs(e_lp["rate"] - 0.29) < 1e-9 and abs(e_lp["depth"] - 0.84) < 1e-9)
    check("migrated center orbits the old center",
          abs(e_lp["dests"][0]["center"] - 0.75) < 1e-6, str(e_lp))

    # restore(None)/empty tolerated
    app3.lfos.restore(None)
    check("restore(None) clears cleanly", app3.lfos.state() == [])


def main():
    for fn in (test_instances, test_fanout, test_center, test_hygiene,
               test_persistence):
        print(f"\n-- {fn.__name__} --")
        fn()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
