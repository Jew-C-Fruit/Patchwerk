"""Threshold (item 8a) tests — CI-safe: no scsynth, no audio.

    python tests/test_threshold.py

Covers: spawn/remove/id alloc, the CV wire (watch synth ADD_AFTER its LFO
norm, reading the shared norm bus, single-input source replace), configure
→ synth param mapping (level/hysteresis → Schmidt lo/hi, mode → edge
gates), the /tr edge-notify dispatch (tag routing, arm-delay swallow of
the spawn-time phantom falling edge), the ping wire grammar (threshold is
a ping SOURCE: out lands only on trigger-ins, nothing lands on it), LFO
removal unwiring, the pure-Python feed() Schmitt (sensors, item 8b stub),
and preset snapshot/restore round-trips.

Server interactions run against a fake supriya server so the exact
add_synth/set/free traffic is assertable (test_lfo's pattern).
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase import presets  # noqa: E402
from synthbase.lfo import _lfo_norm  # noqa: E402
from synthbase.threshold import ARM_DELAY, _threshold_watch  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---- fakes (test_lfo's shapes + register_osc_callback) -----------------------

class FakeBus:
    _n = 500

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
        self.callbacks = []

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

    def register_osc_callback(self, pattern=None, procedure=None, **kw):
        cb = SimpleNamespace(pattern=pattern, procedure=procedure)
        self.callbacks.append(cb)
        return cb


def make_app(server=True):
    app = SynthApp(use_midi=False, use_reload=False)
    if server:
        app.engine = SimpleNamespace(server=FakeServer(), root_group="ROOT")
    return app


def watch_synths(app):
    return [s for s in app.engine.server.synths
            if s.definition is _threshold_watch and not s.freed]


def spy(node):
    """Replace a ThresholdNode's fire with a recorder."""
    calls = []
    node.fire = lambda rising=None: calls.append(rising)
    return calls


def disarm(app, tid):
    """Backdate the arm window so /tr fires are not swallowed."""
    app.thresholds.instances[tid]["node"]._armed_at = (
        time.monotonic() - ARM_DELAY * 2)


# ---- instances + CV wire -----------------------------------------------------

def test_instances_and_wire():
    app = make_app()
    tid = app.spawn_threshold()
    check("first threshold id is 'threshold'", tid == "threshold", tid)
    tid2 = app.spawn_threshold()
    check("second threshold id suffixes", tid2 == "threshold.2", tid2)
    check("no watch synth before a CV wire", len(watch_synths(app)) == 0)

    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    ws = watch_synths(app)
    check("CV wire spawns ONE watch synth", len(ws) == 1)
    norm = [s for s in app.engine.server.synths if s.definition is _lfo_norm][0]
    check("watch reads the LFO's norm bus",
          ws[0].kwargs["kin"] == norm.kwargs["kout"], str(ws[0].kwargs))
    from supriya import AddAction
    check("watch spawns AFTER its norm (add_after)",
          ws[0].kwargs["target_node"] is norm
          and ws[0].kwargs["add_action"] == AddAction.ADD_AFTER)
    check("default mode gates: rising only",
          ws[0].kwargs["r_on"] == 1 and ws[0].kwargs["f_on"] == 0,
          str(ws[0].kwargs))
    check("/tr callback registered once",
          len(app.engine.server.callbacks) == 1
          and app.engine.server.callbacks[0].pattern == ["/tr"])

    # single-input: a second wire REPLACES the source
    lid2 = app.spawn_lfo()
    app.threshold_wire("add", tid, lid2)
    check("re-wire frees the old watch and spawns against the new LFO",
          ws[0].freed and len(watch_synths(app)) == 1)
    norm2 = [s for s in app.engine.server.synths
             if s.definition is _lfo_norm][1]
    check("new watch reads the NEW norm bus",
          watch_synths(app)[0].kwargs["kin"] == norm2.kwargs["kout"])
    check("state carries the source",
          app.thresholds.state()[0]["source"] == lid2)

    # distinct tags per instance
    app.threshold_wire("add", tid2, lid)
    tags = {r["tag"] for r in app.thresholds.instances.values()}
    check("each instance has a distinct SendTrig tag", len(tags) == 2)

    app.threshold_wire("remove", tid, lid2)
    check("wire remove frees the watch + clears the source",
          len(watch_synths(app)) == 1
          and app.thresholds.state()[0]["source"] is None)

    try:
        app.threshold_wire("add", tid, "lfo.9")
        check("wiring an unknown LFO raises", False)
    except KeyError:
        check("wiring an unknown LFO raises", True)

    app.remove_threshold(tid2)
    check("remove frees the remaining watch", len(watch_synths(app)) == 0)
    try:
        app.remove_threshold("threshold.9")
        check("removing an unknown threshold raises", False)
    except KeyError:
        check("removing an unknown threshold raises", True)


# ---- configure → synth params ------------------------------------------------

def test_configure():
    app = make_app()
    tid = app.spawn_threshold()
    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    ws = watch_synths(app)[0]

    app.set_threshold(tid, level=0.3, hysteresis=0.1)
    check("level/hysteresis map to Schmidt lo/hi",
          abs(ws.sets[-1]["lo"] - 0.2) < 1e-9
          and abs(ws.sets[-1]["hi"] - 0.4) < 1e-9,
          str(ws.sets))
    app.set_threshold(tid, mode="falling")
    check("falling mode gates: f_on only",
          ws.sets[-1]["r_on"] == 0 and ws.sets[-1]["f_on"] == 1)
    app.set_threshold(tid, mode="both")
    check("both mode gates: r_on + f_on",
          ws.sets[-1]["r_on"] == 1 and ws.sets[-1]["f_on"] == 1)
    app.set_threshold(tid, level=5.0, hysteresis=9.0)
    st = app.thresholds.state()[0]
    check("level clamps to ±1, hysteresis to 0..0.5",
          st["level"] == 1.0 and st["hysteresis"] == 0.5, str(st))
    try:
        app.set_threshold(tid, mode="sideways")
        check("unknown mode raises", False)
    except ValueError:
        check("unknown mode raises", True)


# ---- /tr dispatch + arm-delay ------------------------------------------------

def test_tr_dispatch():
    app = make_app()
    tid = app.spawn_threshold()
    tid2 = app.spawn_threshold()
    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    app.threshold_wire("add", tid2, lid)

    n1 = app.thresholds.instances[tid]["node"]
    n2 = app.thresholds.instances[tid2]["node"]
    c1, c2 = spy(n1), spy(n2)
    tag1 = app.thresholds.instances[tid]["tag"]
    tag2 = app.thresholds.instances[tid2]["tag"]
    cb = app.engine.server.callbacks[0].procedure

    # inside the arm window: swallowed (the spawn-time phantom edge)
    cb(SimpleNamespace(contents=(1001, tag1, 1.0)))
    check("fire inside the arm window is swallowed", c1 == [])

    disarm(app, tid)
    disarm(app, tid2)
    cb(SimpleNamespace(contents=(1001, tag1, 1.0)))
    check("/tr routes by tag to the right instance",
          c1 == [True] and c2 == [])
    cb(SimpleNamespace(contents=(1001, tag2, 0.0)))
    check("falling value reports rising=False", c2 == [False])
    cb(SimpleNamespace(contents=(1001, 424242, 1.0)))
    check("unknown tags fall through silently",
          c1 == [True] and c2 == [False])
    cb(SimpleNamespace(contents=("garbage",)))
    check("malformed /tr is ignored", c1 == [True])


# ---- ping fan-out + grammar --------------------------------------------------

def test_ping_grammar_and_fanout():
    app = make_app()
    tid = app.spawn_threshold()
    d1 = app.spawn_tonic()
    d2 = app.spawn_literal()

    app.set_ctl_wire("add", tid, d1)
    app.set_ctl_wire("add", tid, d2)
    check("threshold→deriver trigger-ins accepted",
          {"from": tid, "to": d1} in app.ctl_wires
          and {"from": tid, "to": d2} in app.ctl_wires)

    hits = []
    app.tonics[d1].trigger = lambda: hits.append("est")
    app.literals[d2].trigger = lambda: hits.append("lit")
    app.thresholds.instances[tid]["node"].fire()
    check("fire fans out to every wired trigger-in",
          sorted(hits) == ["est", "lit"], str(hits))

    for dst in ("arp", "voice", "deck"):
        try:
            app.set_ctl_wire("add", tid, dst)
            check(f"threshold→{dst} rejected (no trigger-in)", False)
        except ValueError:
            check(f"threshold→{dst} rejected (no trigger-in)", True)
    try:
        app.set_ctl_wire("add", "keys", tid)
        check("keys→threshold rejected (no note sink)", False)
    except ValueError:
        check("keys→threshold rejected (no note sink)", True)

    app.remove_threshold(tid)
    check("threshold removal drops its ping wires",
          not any(tid in (w["from"], w["to"]) for w in app.ctl_wires))

    for d in (*app.tonics.values(), *app.literals.values()):
        d.shutdown()


# ---- LFO removal hygiene -----------------------------------------------------

def test_lfo_removal():
    app = make_app()
    tid = app.spawn_threshold()
    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    check("wired", len(watch_synths(app)) == 1)
    app.remove_lfo(lid)
    check("removing the source LFO unwires the CV-in",
          len(watch_synths(app)) == 0
          and app.thresholds.state()[0]["source"] is None)


# ---- feed(): the Python-side Schmitt (item 8b stub) --------------------------

def test_feed():
    app = make_app(server=False)
    tid = app.spawn_threshold()
    node = app.thresholds.instances[tid]["node"]
    app.set_threshold(tid, level=0.0, hysteresis=0.1)

    calls = spy(node)
    node.feed(-0.5)                    # latch below — no fire on first value
    check("first feed only latches", calls == [])
    node.feed(0.05)                    # inside the window: no crossing yet
    check("inside-window value does not fire", calls == [])
    node.feed(0.2)                     # crosses hi → rising fire
    check("rising crossing fires once", calls == [True])
    node.feed(0.9)
    node.feed(0.15)
    check("no re-fire without leaving the window", calls == [True])
    node.feed(-0.2)                    # crosses lo → falling, but mode=rising
    check("falling crossing silent in rising mode", calls == [True])
    node.feed(0.2)
    check("re-crossing hi fires again", calls == [True, True])

    app.set_threshold(tid, mode="both")   # window/mode change → re-latch
    calls2 = spy(node)
    node.feed(0.2)                        # first feed after re-latch: latch only
    node.feed(-0.2)
    node.feed(0.2)
    check("both mode fires on each direction (after the re-latch)",
          calls2 == [False, True], str(calls2))


# ---- engine swap (device switch reboots the engine) --------------------------

def test_engine_swap():
    """A device switch (app.set_devices) reboots the engine: the NEW server
    must re-receive synthdefs and re-register the /tr callback. Found live
    2026-07-22: stale registration flags left every post-switch spawn
    emitting /s_new for defs the new scsynth never had."""
    app = make_app()
    tid = app.spawn_threshold()
    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    old = app.engine.server
    check("defs + callback on the FIRST server",
          len(old.synthdefs) > 0 and len(old.callbacks) == 1)

    # simulate set_devices' teardown half (clear + reset), then a NEW engine
    app.thresholds.clear()
    app.lfos.clear()
    app.thresholds.reset()
    app.lfos.reset()
    app.engine = SimpleNamespace(server=FakeServer(), root_group="ROOT")
    new = app.engine.server

    tid2 = app.spawn_threshold()
    lid2 = app.spawn_lfo()
    app.threshold_wire("add", tid2, lid2)
    check("LFO synthdefs re-sent to the NEW server",
          any(d is _lfo_norm for d in new.synthdefs), str(new.synthdefs))
    check("threshold synthdef re-sent to the NEW server",
          any(d is _threshold_watch for d in new.synthdefs))
    check("/tr callback re-registered on the NEW server",
          len(new.callbacks) == 1)
    check("watch synth spawns on the NEW server",
          len(watch_synths(app)) == 1)

    # belt & braces: even WITHOUT reset(), per-server tracking must notice
    # a fresh server object and re-send
    app.engine = SimpleNamespace(server=FakeServer(), root_group="ROOT")
    third = app.engine.server
    app.thresholds.instances[tid2]["node"].source = None  # force re-spawn path
    app.lfos.instances[lid2]["node"] = None
    lid3 = app.spawn_lfo()
    app.threshold_wire("add", tid2, lid3)
    check("per-server tracking re-sends even without reset()",
          any(d is _lfo_norm for d in third.synthdefs)
          and any(d is _threshold_watch for d in third.synthdefs)
          and len(third.callbacks) == 1,
          str((third.synthdefs, third.callbacks)))


# ---- persistence -------------------------------------------------------------

def test_persistence():
    app = make_app()
    tid = app.spawn_threshold()
    lid = app.spawn_lfo()
    app.threshold_wire("add", tid, lid)
    app.set_threshold(tid, level=-0.25, hysteresis=0.07, mode="both")
    d1 = app.spawn_tonic()
    app.set_ctl_wire("add", tid, d1)

    snap = app.thresholds.snapshot()
    check("snapshot carries level/hyst/mode/source",
          snap == [{"id": tid, "level": -0.25, "hysteresis": 0.07,
                    "mode": "both", "source": lid}], str(snap))

    # the app-level preset snapshot includes the section
    data = presets.snapshot(app)
    check("preset snapshot has a thresholds section",
          data.get("thresholds") == snap, str(data.get("thresholds")))

    # round-trip into a FRESH app (LFO restored first, like presets._apply)
    app2 = make_app()
    app2.lfos.restore(app.lfos.snapshot())
    app2.thresholds.restore(snap)
    st = app2.thresholds.state()
    check("restore round-trips settings + source",
          st and st[0]["level"] == -0.25 and st[0]["hysteresis"] == 0.07
          and st[0]["mode"] == "both" and st[0]["source"] == lid, str(st))
    check("restore respawns the watch synth", len(watch_synths(app2)) == 1)

    # headless restore keeps the data model without a server
    app3 = make_app(server=False)
    app3.lfos.restore(app.lfos.snapshot())
    app3.thresholds.restore(snap)
    check("headless restore keeps the source in the data model",
          app3.thresholds.state()[0]["source"] == lid)

    for d in app.tonics.values():
        d.shutdown()


def main():
    test_instances_and_wire()
    test_configure()
    test_tr_dispatch()
    test_ping_grammar_and_fanout()
    test_lfo_removal()
    test_feed()
    test_engine_swap()
    test_persistence()
    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)} failures")
        sys.exit(1)
    print("PASS — 0 failures")


if __name__ == "__main__":
    main()
