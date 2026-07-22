"""Ping signal tests (CI-safe: no scsynth, no audio, no MIDI hardware).

    python tests/test_ping.py

Covers: button/clock spawn + id alloc, the ping wire grammar (ping-outs
land ONLY on trigger-ins), fan-out to multiple derivers, the deriver's
timer↔ping override predicate, MIDI-CC pairing capture + rising-edge
firing (and that tonal/note input can never bind), clock phase-locked
ticking + stopped-transport silence, and preset snapshot/restore.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
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


def test_spawn_and_grammar():
    app = SynthApp(use_midi=False, use_reload=False)
    check("first button id", app.spawn_button() == "button")
    check("second button suffixes", app.spawn_button() == "button.2")
    check("first clock id", app.spawn_clock() == "clock")
    app.remove_button("button.2")
    check("removed button gone", "button.2" not in app.buttons)

    tid = app.spawn_tonic()

    # ping-out → trigger-in only
    app.set_ctl_wire("add", "button", tid)
    check("button→deriver trigger-in accepted",
          {"from": "button", "to": tid} in app.ctl_wires)
    for dst in ("arp", "voice", "deck"):
        try:
            app.set_ctl_wire("add", "button", dst)
            check(f"button→{dst} rejected (no trigger-in)", False)
        except ValueError:
            check(f"button→{dst} rejected (no trigger-in)", True)
    # ...and note sources still can't land on nothing new
    try:
        app.set_ctl_wire("add", "keys", "button")
        check("keys→button rejected (buttons take no notes)", False)
    except ValueError:
        check("keys→button rejected (buttons take no notes)", True)

    # remove a wired button: its wires go with it
    app.remove_button("button")
    check("button removal drops its wires",
          not any("button" in (w["from"], w["to"]) for w in app.ctl_wires))

    for d in app.tonics.values():
        d.shutdown()
    for c in app.clocks.values():
        c.shutdown()


def test_fire_fanout_and_override():
    app = SynthApp(use_midi=False, use_reload=False)
    t1 = app.spawn_tonic()
    t2 = app.spawn_tonic()
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, t1)
    app.set_ctl_wire("add", bid, t2)
    d1, d2 = app.tonics[t1], app.tonics[t2]
    seed_c_major(d1)
    seed_c_major(d2)
    check("no commit before the ping", d1.root is None and d2.root is None)
    app.fire_button(bid)
    check("one ping commits EVERY wired deriver (fan-out)",
          d1.root == 0 and d2.root == 0)

    # timer override predicate: wired ping source suppresses the grid timer
    check("wired deriver reports ping-driven", d1._ping_driven())
    app.set_ctl_wire("remove", bid, t1)
    check("unwired deriver resumes its own timer", not d1._ping_driven())

    # ping viz tap emitted on fire
    taps = []
    app.on_midi_event = lambda e: taps.append(dict(e))
    app.fire_button(bid)
    check("fire emits a ping viz tap",
          {"kind": "ping", "src": bid} in taps)

    for d in app.tonics.values():
        d.shutdown()


def test_midi_pairing_and_edge():
    app = SynthApp(use_midi=False, use_reload=False)
    tid = app.spawn_tonic()
    bid = app.spawn_button()
    app.set_ctl_wire("add", bid, tid)
    d = app.tonics[tid]
    b = app.buttons[bid]

    # arm, then a NOTE-ish event arrives: taps/voiced/bend never bind
    app.set_button(bid, armed=True)
    app._emit_midi_event({"kind": "tap", "src": "keys", "note": 60, "on": True})
    app._emit_midi_event({"kind": "voiced", "note": 60, "on": True})
    app._emit_midi_event({"kind": "bend", "semitones": 1.0})
    check("tonal/other events never bind an armed button",
          b.armed and b.binding is None)

    # the next CC captures (and does not fire)
    seed_c_major(d)
    app._emit_midi_event({"kind": "cc", "cc": 21, "unit": 0.9})
    check("armed button captures the next CC",
          b.binding == {"kind": "cc", "cc": 21} and not b.armed)
    check("capture itself does not fire", d.root is None)

    # bound CC fires on the RISING edge only
    app._emit_midi_event({"kind": "cc", "cc": 21, "unit": 0.2})   # low
    check("low value does not fire", d.root is None)
    app._emit_midi_event({"kind": "cc", "cc": 21, "unit": 0.8})   # rising
    check("rising edge fires the ping", d.root == 0)
    seeded = d.root
    app._emit_midi_event({"kind": "cc", "cc": 21, "unit": 0.9})   # still high
    check("held-high does not re-fire (edge, not level)", d.root == seeded)
    app._emit_midi_event({"kind": "cc", "cc": 22, "unit": 0.9})
    check("other CCs pass through untouched", d.root == seeded)

    # arming a second button disarms the first (one pairing at a time)
    b2id = app.spawn_button()
    app.set_button(bid, armed=True)
    app.set_button(b2id, armed=True)
    check("arming another button disarms the first",
          not b.armed and app.buttons[b2id].armed)

    # a key binding round-trips through configure (client-side capture)
    app.set_button(b2id, binding={"kind": "key", "code": "KeyN"}, armed=False)
    check("key binding stored",
          app.buttons[b2id].binding == {"kind": "key", "code": "KeyN"})

    for dd in app.tonics.values():
        dd.shutdown()


def test_clock_ticks():
    app = SynthApp(use_midi=False, use_reload=False)
    cid = app.spawn_clock()
    c = app.clocks[cid]
    app.set_clock(cid, division="1/16")   # 0.25 beat = 150 ms @ 100 bpm
    check("division applied", c.division == "1/16")
    app.set_clock(cid, division="nope")
    check("bad division ignored", c.division == "1/16")

    # item 6: multi-bar periods — the clock's OWN ladder extends past 1/1
    from synthbase.ping import CLOCK_DIVISIONS
    from synthbase.transport import DIVISIONS
    for d, beats in (("2/1", 8.0), ("4/1", 16.0), ("8/1", 32.0)):
        app.set_clock(cid, division=d)
        check(f"multi-bar division {d} accepted", c.division == d)
        check(f"{d} is {beats} beats", CLOCK_DIVISIONS[d] == beats)
    ds = c.settings()["divisions"]
    check("clock ladder = multi-bar + full global ladder",
          ds[:3] == ["8/1", "4/1", "2/1"] and ds[3:] == list(DIVISIONS))
    check("global DIVISIONS untouched (no multi-bar leak)",
          not any(d in DIVISIONS for d in ("2/1", "4/1", "8/1"))
          and "2/1" not in app.transport.settings()["divisions"])
    # grid math: a 2/1 clock phase-locks to multiples of 8 beats from beat 0
    gb, _ = app.transport.next_grid(CLOCK_DIVISIONS["2/1"])
    check("multi-bar grid point is a whole multiple of its period",
          gb % 8.0 == 0.0 and gb > app.transport.beats_now())
    app.set_clock(cid, division="1/16")   # restore for shutdown path

    fires = []
    c.fire = lambda: fires.append(time.monotonic())   # count ticks
    time.sleep(0.65)
    check("clock ticks on the transport grid (>=3 in 0.65s @150ms)",
          len(fires) >= 3)
    if len(fires) >= 3:
        gaps = [b - a for a, b in zip(fires, fires[1:])]
        check("ticks are evenly spaced (phase-locked)",
              all(abs(g - 0.15) < 0.06 for g in gaps))

    # stopped transport: no ticks (and no busy-spin)
    app.transport.set_running(False)
    time.sleep(0.3)
    n = len(fires)
    time.sleep(0.4)
    check("stopped transport stops the clock", len(fires) == n)
    app.transport.set_running(True)

    c.shutdown()
    app.transport.shutdown()


def test_preset_roundtrip():
    app = SynthApp(use_midi=False, use_reload=False)
    bid = app.spawn_button()
    app.set_button(bid, binding={"kind": "cc", "cc": 30})
    cid = app.spawn_clock()
    app.set_clock(cid, division="4/1")   # a multi-bar period must round-trip
    data = presets.snapshot(app)
    check("snapshot carries buttons",
          data["buttons"] == [{"id": bid, "binding": {"kind": "cc", "cc": 30}}])
    check("snapshot carries clocks",
          data["clocks"] == [{"id": cid, "division": "4/1"}])

    app2 = SynthApp(use_midi=False, use_reload=False)
    app2._build_patch = lambda name: None
    presets._apply(app2, data)
    check("restore respawns the button with its binding",
          app2.buttons.get(bid) is not None
          and app2.buttons[bid].binding == {"kind": "cc", "cc": 30})
    check("restore respawns the clock with its division",
          app2.clocks.get(cid) is not None
          and app2.clocks[cid].division == "4/1")
    for c in (*app.clocks.values(), *app2.clocks.values()):
        c.shutdown()


def main():
    test_spawn_and_grammar()
    test_fire_fanout_and_override()
    test_midi_pairing_and_edge()
    test_clock_ticks()
    test_preset_roundtrip()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
