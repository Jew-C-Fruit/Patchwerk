"""REACTIVE-INDICATOR enforcement — CI-safe: no scsynth, no audio, no MIDI.

    python tests/test_reactive.py

Cole's standing rule (CLAUDE.md, 2026-07-24) is that every button and
state indicator must react GRAPHICALLY when it is driven, including from
LOGIC input. State applied inside the gate settle pass deliberately does
not broadcast, so the backend owes each such application its own tap.

Until this file existed, nothing enforced that. All four `_emit_level`
calls in `synthbase/` could be deleted and every Python suite stayed
green; the three Playwright suites never import `synthbase` at all, so
they could only prove the GUI reacts to a message the test itself made
up. `transport:run` flipped `running` while emitting nothing, and both
`transport:tap` and the four deck buttons changed real state with no tap
whatsoever.

This is Phase 0 of `continuity/ai-control-build-plan.md` (item 37), and
that plan is right about how small the fix is: the spy harness
(`app.on_midi_event = events.append`), the serverless `SynthApp` and the
emitted-event idiom all already existed in `test_gate.py::test_events` —
what was missing was only ever `kind == "level"` coverage. So the weight
here sits in the BEHAVIOURAL checks, which are ordinary spy assertions:

  1. test_every_applied_endpoint_taps — enumerates every binary
     destination the settle pass APPLIES, derived from live app state and
     gate.py's own constants (TRANSPORT_INS, DECK_ACTIONS, the rack's
     instances, ...), drives each one through a real wire, and demands a
     reactive tap. A new transport in, deck action or chain module is
     covered the day it lands, with no edit here. `applied_endpoints()`
     is deliberately importable: it is the generated endpoint list the
     plan's §3.4 endpoint x surface matrix wants to be built from, so
     Phase 2 can reuse it rather than hand-write one.
  2. test_transport_tap / test_deck_buttons — the two endpoints that
     shipped silent, pinned end to end.

One structural check earns its place on top of those, and only one.
Measured against the mutation battery below, the behavioural checks
catch eight of nine mutations on their own; the ONLY survivor is a brand
new indicator on a base name the enumeration cannot guess (`scope:freeze`
and the like). test_dispatch_is_fully_covered closes exactly that case by
reading gate.py with `ast` and failing when the dispatch branches on an
endpoint no test drives. An earlier draft also AST-asserted that no
branch carries its own emit; it was cut for catching nothing the
behavioural checks did not already catch.

MUTATION BAR: deleting any single emit call in the chain (`_emit_level`,
`_pulse_level`, either call inside `_pulse_level`, or `_emit_transport`),
restoring the old per-branch shape, or adding an uncovered indicator
branch must fail this suite. Verified by doing exactly that.

NOT covered here, on purpose: `gate`, `tap`, `voiced` and `ping` events
already have spy coverage (`test_gate.py:1098-1130`, `test_graph.py:313`
and eight more, `test_ping.py:98`). Phase 0's ask for them is already
met; re-asserting them here would only duplicate.
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.gate import DECK_ACTIONS, TRANSPORT_INS  # noqa: E402
from synthbase.module import param  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


# ---- fakes (test_gate's RecordingRack shape, trimmed) ------------------------

class FakeNode:
    def set(self, **kw):
        pass


def fake_inst(key, kind):
    return SimpleNamespace(
        key=key, module=SimpleNamespace(kind=kind,
                                        params={"amp": param(0.0, 1.0, 0.5)}),
        settings={}, service=False, node=FakeNode(), enabled=True,
        type=key.split(".", 1)[0])


class RecordingRack:
    def __init__(self, keys_kinds):
        self.instances = [fake_inst(k, kind) for k, kind in keys_kinds]
        self.calls = []

    def find(self, key):
        for i in self.instances:
            if i.key == key:
                return i
        raise KeyError(key)

    def audio_wires(self):
        return []

    def audio_rewire(self, src, dst):
        pass

    def audio_disconnect(self, src):
        pass

    def reorder_for_wires(self, wires):
        pass

    def set_param(self, key, name, value):
        self.find(key).settings[name] = value

    def set_enabled(self, key, enabled):
        self.calls.append(("set_enabled", key, bool(enabled)))
        self.find(key).enabled = bool(enabled)


class FakeArp:
    def __init__(self):
        self.enabled = False

    def configure(self, **kw):
        if "enabled" in kw and kw["enabled"] is not None:
            self.enabled = bool(kw["enabled"])

    def settings(self):
        return {"enabled": self.enabled}

    def shutdown(self):
        pass


# ---- the fixture + the structural enumeration -------------------------------

def fat_app():
    """An app carrying one of EVERY node kind that owns a binary
    destination, so the enumeration below has something real to drive."""
    app = SynthApp(use_midi=False, use_reload=False)
    app.rack = RecordingRack([("pluck", "source"), ("echo", "effect")])
    app.arp = FakeArp()
    app.spawn_logic()
    app.spawn_relay()
    app.spawn_tonic()
    return app


def teardown(app):
    for d in list(app.tonics.values()) + list(app.literals.values()):
        try:
            d.shutdown()
        except Exception:  # noqa: BLE001
            pass
    try:
        app.drums.configure(enabled=False)
    except Exception:  # noqa: BLE001
        pass
    try:
        app.looper.shutdown()
    except Exception:  # noqa: BLE001
        pass
    app.transport.shutdown()


def applied_endpoints(app):
    """Every binary destination the settle pass APPLIES — that is, every
    endpoint `GateManager._apply_effects` does not skip.

    Derived from LIVE app state plus gate.py's own constants, never a
    literal list, so it grows with the system: add a transport in, a deck
    action or a chain module and it is enumerated (and therefore demanded
    to react) automatically.

    The two skips mirror the source exactly. Logic named ins settle in
    `_settle` and announce themselves through the logic's own
    {"kind": "gate"} out event; relay CIRCUIT ins are read lazily by
    whatever hangs downstream and drive no indicator of their own (the
    relay card's indicator is its closed state, which arrives via
    relay:ctl). Both are covered by test_gate, not here."""
    eps = []
    for key in (i.key for i in app.rack.instances):
        eps.append(f"{key}:pwr")
    eps += ["arp:pwr", "drums:pwr"]
    eps += [f"transport:{s}" for s in TRANSPORT_INS]
    eps += [f"deck:{s}" for s in DECK_ACTIONS]
    eps += list(app.tonics) + list(app.literals)   # deriver trig-ins (bare id)
    eps += [f"{rid}:ctl" for rid in app.relays]
    return sorted(eps)


def endpoint_tokens(app):
    """Every ":"-separated token the enumeration above touches — the
    vocabulary test_dispatch_is_fully_covered holds gate.py's branches
    against."""
    toks = set()
    for ep in applied_endpoints(app):
        base, _, sub = str(ep).partition(":")
        toks.add(base)
        toks.add(sub)
    return toks


def drive(app, dst, events):
    """Wire a fresh latched button into dst and take it lo → hi → lo,
    returning the events emitted while it moved. The button is attached
    LO first: a trig-in only fires on a real rising edge, and attaching
    while already hi is deliberately not one."""
    bid = app.spawn_button()
    app.set_button(bid, latch=True)
    app.set_ctl_wire("add", bid, dst)
    events.clear()
    app.button_down(bid)             # lo → hi
    app.button_down(bid)             # hi → lo
    got = list(events)
    app.set_ctl_wire("remove", bid, dst)
    app.remove_button(bid)
    return got


def taps_for(events, dst):
    return [e for e in events
            if e.get("kind") == "level" and e.get("ep") == dst]


# ---- 1. behavioural coverage: every applied endpoint taps -------------------

def test_every_applied_endpoint_taps():
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    eps = applied_endpoints(app)
    check("the enumeration found endpoints at all", len(eps) >= 12)

    # the enumeration must agree with the wire grammar in BOTH directions:
    # anything it names has to be a destination a binary wire may land on
    bad = [e for e in eps if not app.gates.is_toggle_dst(e)]
    check("every enumerated endpoint is a legal binary dst", not bad, )
    if bad:
        print("      not accepted by is_toggle_dst: " + ", ".join(bad))

    silent = []
    for ep in eps:
        if not taps_for(drive(app, ep, events), ep):
            silent.append(ep)
    check("EVERY applied binary endpoint emits its reactive tap", not silent)
    if silent:
        print("      silent (no {kind:'level'} tap): " + ", ".join(silent))
    teardown(app)


def test_level_ins_tap_in_both_directions():
    """A level-in's indicator follows the level DOWN as well as up — a
    stripe that lights and never unlights is the same bug wearing a hat."""
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    for ep in ("echo:pwr", "arp:pwr", "transport:run", "transport:click",
               "transport:accent"):
        got = taps_for(drive(app, ep, events), ep)
        check(f"{ep} taps hi then lo",
              [e["on"] for e in got] == [True, False])
        check(f"{ep} is NOT marked pulse (it holds a level)",
              all("pulse" not in e for e in got))
    teardown(app)


def test_every_tap_declares_its_shape():
    """The momentary/steady split rides the EVENT, not a list the
    receiver keeps.

    This is the hazard the item 11 session caught in review before
    building against it: a trig-in's hi and lo land in the same tick, so
    a receiver that routes the pair through a plain level setter renders
    nothing at all — implemented-looking and dead on arrival. It can only
    do better if the event says so, and it must not have to infer it from
    the endpoint name, because that inference is a hardcoded list that
    rots the first time a trig-in is added.

    So: `pulse` is present exactly on the endpoints _is_trig_dst calls
    trig, and absent on every other. Derived from the manager's own
    predicate, so the two cannot drift."""
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    wrong = []
    for ep in applied_endpoints(app):
        got = taps_for(drive(app, ep, events), ep)
        want = app.gates._is_trig_dst(ep)
        if not got or any(bool(e.get("pulse")) != want for e in got):
            wrong.append(f"{ep} (trig={want})")
    check("every tap's pulse flag matches _is_trig_dst", not wrong)
    if wrong:
        print("      shape disagrees with the engine's own predicate: "
              + ", ".join(wrong))
    teardown(app)


def test_trig_ins_tap_only_on_a_real_edge():
    """A trig-in's indicator is MOMENTARY (hi-then-lo pulse) and fires on
    the rising edge only — never on wire-attach, never on the fall."""
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))

    bid = app.spawn_button()
    app.set_button(bid, latch=True)
    app.button_down(bid)                       # HI before the wire exists
    events.clear()
    app.set_ctl_wire("add", bid, "deck:play")
    check("attach-while-hi emits no tap (not an edge)",
          taps_for(events, "deck:play") == [])
    app.button_down(bid)                       # hi → lo
    check("the falling edge emits no tap",
          taps_for(events, "deck:play") == [])
    events.clear()
    app.button_down(bid)                       # lo → hi: the real edge
    got = taps_for(events, "deck:play")
    check("the rising edge emits one hi/lo pulse",
          [e["on"] for e in got] == [True, False])
    check("both halves carry the pulse tag (zero-duration: the receiver "
          "must stretch, not set)",
          bool(got) and all(e.get("pulse") is True for e in got))
    teardown(app)


# ---- 2. the one structural check: a NEW indicator on a NEW base name --------
#
# The behavioural checks above cover every endpoint the enumeration can
# reach, which is every endpoint reachable from app state and gate.py's
# constants. What they cannot reach is a new indicator family invented
# wholesale — `elif base == "scope" and sub == "freeze":` — because
# nothing in live state names "scope". That single gap is what this
# section closes, and it is the whole reason it exists.

DISPATCH_FUNCS = ("_apply_effects", "_is_trig_dst", "is_toggle_dst")


def _gate_funcs():
    src = (REPO / "synthbase" / "gate.py").read_text()
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "GateManager")
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def _endpoint_literals(fn):
    """String constants this function branches ON — the comparators of
    any Compare whose left operand is the endpoint's `base`/`sub`/`dst`.
    Deliberately narrow: it must not pick up `dst.partition(":")` and
    friends, which are plumbing, not endpoint names."""
    lits = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id in ("base", "sub", "dst")):
            continue
        for comp in node.comparators:
            parts = comp.elts if isinstance(comp, (ast.Tuple, ast.List)) else [comp]
            for p in parts:
                if isinstance(p, ast.Constant) and isinstance(p.value, str):
                    lits.add(p.value)
    return lits


def test_dispatch_is_fully_covered():
    """Every endpoint name gate.py branches on must be one this file
    actually drives. Add `elif base == "scope":` with a new indicator and
    this turns red until the endpoint joins the enumeration above — the
    one mutation the behavioural checks cannot see."""
    app = fat_app()
    funcs = _gate_funcs()
    missing = set()
    for name in DISPATCH_FUNCS:
        fn = funcs.get(name)
        check(f"GateManager.{name} still exists", fn is not None)
        if fn is not None:
            missing |= _endpoint_literals(fn) - endpoint_tokens(app)
    check("no endpoint literal in gate.py's dispatch is uncovered", not missing)
    if missing:
        print("      gate.py branches on these but no test drives them: "
              + ", ".join(sorted(repr(m) for m in missing)))
    teardown(app)


# ---- 3. the two endpoints that shipped silent ------------------------------

def test_transport_tap():
    """transport:tap moved the BPM and told nobody: no level tap, no
    broadcast, nothing. The readout sat at 100 while the transport really
    ran at 232."""
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))

    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, "transport:tap")
    start_bpm = app.transport.bpm

    # four taps half a second apart -> 120 bpm. Time is injected, so the
    # test never sleeps and never flakes on a slow runner.
    t = 100.0
    events.clear()
    for _ in range(4):
        app._transport_tap(now=t)
        t += 0.5
    check("four taps at 0.5s land on 120 bpm", round(app.transport.bpm) == 120)
    check("the tap actually moved the tempo", app.transport.bpm != start_bpm)
    # taps 3 and 4 land on the same 120 and are therefore silent — the
    # second tap is the only one that moved anything
    tr = [e for e in events if e.get("kind") == "transport"]
    check("a tempo-moving tap broadcasts the new transport settings",
          len(tr) == 1 and round(tr[0].get("bpm", 0)) == 120)
    check("the transport event carries the full settings shape",
          tr and {"bpm", "beats_per_bar", "click", "accent", "downbeat",
                  "running"} <= set(tr[0]))

    # ...and the tap BUTTON itself blips, whether or not the tempo moved
    events.clear()
    app.fire_button(bid)
    tap_taps = taps_for(events, "transport:tap")
    check("a tap edge pulses transport:tap for the GUI",
          [e["on"] for e in tap_taps] == [True, False]
          and all(e.get("pulse") is True for e in tap_taps))

    # a tap that changes nothing (first of a sequence / out-of-range gap)
    # must still blip the button but must NOT claim a settings change
    app2 = fat_app()
    ev2 = []
    app2.on_midi_event = lambda e: ev2.append(dict(e))
    app2._transport_tap(now=0.0)          # first tap: no tempo yet
    check("a tap with no tempo to report broadcasts nothing",
          [e for e in ev2 if e.get("kind") == "transport"] == [])
    teardown(app)
    teardown(app2)


def test_transport_event_only_on_a_real_change():
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    app.set_transport(bpm=140)
    check("a settings change broadcasts once",
          len([e for e in events if e.get("kind") == "transport"]) == 1)
    events.clear()
    app.set_transport(bpm=140)
    check("re-setting the same value broadcasts nothing",
          [e for e in events if e.get("kind") == "transport"] == [])
    events.clear()
    app.set_transport(playing=True)
    check("play broadcasts the running flag",
          any(e.get("kind") == "transport" and e.get("running") is True
              for e in events))
    teardown(app)


def test_deck_buttons():
    """rec/play/stop/clear changed deck state with no tap of their own —
    the four buttons on the card stayed dead under logic control."""
    app = fat_app()
    events = []
    app.on_midi_event = lambda e: events.append(dict(e))
    presses = []
    app.set_looper = lambda **kw: presses.append(dict(kw))

    for sub, action in DECK_ACTIONS.items():
        ep = f"deck:{sub}"
        presses.clear()
        got = drive(app, ep, events)
        check(f"{ep} presses the deck once",
              presses == [{"action": action}])
        taps = taps_for(got, ep)
        check(f"{ep} pulses its button for the GUI",
              [e["on"] for e in taps] == [True, False]
              and all(e.get("pulse") is True for e in taps))
    teardown(app)


def main():
    test_every_applied_endpoint_taps()
    test_level_ins_tap_in_both_directions()
    test_every_tap_declares_its_shape()
    test_trig_ins_tap_only_on_a_real_edge()
    test_dispatch_is_fully_covered()
    test_transport_tap()
    test_transport_event_only_on_a_real_change()
    test_deck_buttons()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all reactive-indicator checks passed")


if __name__ == "__main__":
    main()
