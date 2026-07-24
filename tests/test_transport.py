"""Transport cards tests (backlog item 9) — CI-safe: no scsynth, no
audio, no MIDI.

    python tests/test_transport.py

The master STOP/PLAY and TEMPO/CLICK become CARDS with wire-addressable
binary ins on the GLOBAL transport (not per-card): "transport:run"/
":click"/":accent" are LEVEL-ins (state follows, incl. first sight),
"transport:tap" is a TRIG-in (rising edge = tap tempo; attach is not an
edge). Covers: level-follow run/click/accent from a latched button,
endpoint grammar (bare "transport" and unknown subs refused), TapTempo
timing math via injected timestamps (mean of the last up-to-4 valid
0.25–2.0 s intervals; a lone/late/too-fast tap restarts with NO tempo
change), the tap wire's edge discipline, tempo-only guarantee (running
untouched), downbeat clamp + meter-shrink re-clamp + the pure
accent_on(beat) predicate, card presence (spawn/remove idempotent,
invalid which raises, state carries the sorted list, removal never
unwires the global endpoints), and preset snapshot/restore roundtrip
(cards + downbeat; empty default for old presets).

app.rack stays None throughout — set_transport(playing=...) walks rack
instances, and the None guard is exactly what existing suites rely on.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp, TRANSPORT_CARDS  # noqa: E402
from synthbase.transport import TapTempo  # noqa: E402
from synthbase import presets  # noqa: E402

FAILURES = []


def check(name, cond):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)


def make_app():
    return SynthApp(use_midi=False, use_reload=False)


def latch_button(app):
    """A latched (persistent) button — the level source for follows."""
    bid = app.spawn_button()
    app.set_button(bid, latch=True)
    return bid


def set_lvl(app, bid, lvl):
    """Drive a latched button's level to lvl (toggle only when needed)."""
    if app.buttons[bid].level != bool(lvl):
        app.button_down(bid)


# ---- wire grammar: transport endpoints ----------------------------------------

def test_endpoint_grammar():
    app = make_app()
    b = latch_button(app)
    for sub in ("run", "click", "accent", "tap"):
        check(f"transport:{sub} is a legal binary dst",
              app.gates.is_toggle_dst(f"transport:{sub}") is True)
    for bad in ("transport", "transport:bogus", "transport:pwr"):
        try:
            app.set_ctl_wire("add", b, bad)
            check(f"{bad!r} refused", False)
        except ValueError:
            check(f"{bad!r} refused", True)
    # transport ins are fan-in level ins, never single-input stealers
    check("transport:run is not single-input",
          app.gates.is_single_input("transport:run") is False)


# ---- level-follow: run / click / accent ----------------------------------------

def test_level_follow_run():
    app = make_app()
    tr = app.transport
    b = latch_button(app)
    check("transport starts running", tr.running is True)
    # LEVEL-IN semantics: attach applies the level on first sight
    app.set_ctl_wire("add", b, "transport:run")
    check("attach applies the level (lo → stopped)", tr.running is False)
    set_lvl(app, b, True)
    check("run hi → playing", tr.running is True)
    set_lvl(app, b, False)
    check("run lo → stopped", tr.running is False)
    set_lvl(app, b, True)
    check("run hi again → playing (both directions live)",
          tr.running is True)
    # removing the wire leaves the state where it was (no snap-back)
    app.set_ctl_wire("remove", b, "transport:run")
    check("unwiring leaves the play state alone", tr.running is True)


def test_level_follow_click_and_accent():
    app = make_app()
    tr = app.transport
    bc = latch_button(app)
    app.button_down(bc)                     # hi BEFORE the wire exists
    app.set_ctl_wire("add", bc, "transport:click")
    check("attach-while-hi applies for transport:click (first sight)",
          tr.click_enabled is True)
    set_lvl(app, bc, False)
    check("click lo → disabled", tr.click_enabled is False)
    set_lvl(app, bc, True)
    check("click hi → enabled", tr.click_enabled is True)

    ba = latch_button(app)
    check("accent defaults on", tr.click_accent is True)
    app.set_ctl_wire("add", ba, "transport:accent")
    check("attach applies the level (lo → accent off)",
          tr.click_accent is False)
    set_lvl(app, ba, True)
    check("accent hi → accent on", tr.click_accent is True)
    st = tr.settings()
    check("settings carries click/accent/running/downbeat",
          st["click"] is True and st["accent"] is True
          and st["running"] is True and st["downbeat"] == 0)


# ---- tap tempo: the TapTempo helper (injected timestamps) ----------------------

def test_tap_tempo_unit():
    tt = TapTempo()
    check("a lone tap changes nothing", tt.tap(now=10.0) is None)
    check("second tap 0.5 s later → 120",
          abs(tt.tap(now=10.5) - 120.0) < 1e-9)
    tt.tap(now=11.0)
    tt.tap(now=11.5)
    check("steady 0.5 s taps hold 120", abs(tt.tap(now=12.0) - 120.0) < 1e-9)
    # window: mean of the LAST up-to-4 intervals only
    bpm = tt.tap(now=12.25)                # intervals now 0.5,0.5,0.5,0.25
    check("window means the last 4 intervals",
          abs(bpm - 60.0 / 0.4375) < 1e-9)

    tt2 = TapTempo()
    tt2.tap(now=0.0)
    tt2.tap(now=0.5)
    check("late tap (>2 s) → None (sequence restarts, no change)",
          tt2.tap(now=5.0) is None)
    check("post-restart interval seeds FRESH (old 0.5s history gone)",
          abs(tt2.tap(now=6.0) - 60.0) < 1e-9)

    tt3 = TapTempo()
    tt3.tap(now=0.0)
    check("too-fast tap (<0.25 s) → None", tt3.tap(now=0.1) is None)

    # boundary intervals are valid: 0.25 s = 240 BPM, 2.0 s = 30 BPM
    tt4 = TapTempo()
    tt4.tap(now=0.0)
    check("0.25 s interval valid (240 BPM)",
          abs(tt4.tap(now=0.25) - 240.0) < 1e-9)
    tt5 = TapTempo()
    tt5.tap(now=0.0)
    check("2.0 s interval valid (30 BPM)",
          abs(tt5.tap(now=2.0) - 30.0) < 1e-9)


# ---- tap tempo: the wire (trig-in edge discipline + tempo-only) ----------------

def test_tap_trig_edges():
    app = make_app()
    taps = []
    app._transport_tap = lambda now=None: taps.append(1)

    bh = latch_button(app)
    app.button_down(bh)                     # hi BEFORE the wire exists
    app.set_ctl_wire("add", bh, "transport:tap")
    check("attach-while-hi is NOT a tap (no edge)", taps == [])
    app.button_down(bh)                     # hi → lo
    check("falling edge never taps", taps == [])
    app.button_down(bh)                     # lo → hi
    check("rising edge taps exactly once", taps == [1])
    app.button_down(bh)                     # back lo (fresh edge for below)

    # a momentary pulse = one tap per press
    bm = app.spawn_button()
    app.set_ctl_wire("add", bm, "transport:tap")
    app.fire_button(bm)
    check("momentary pulse taps once", taps == [1, 1])
    app.fire_button(bm)
    check("each pulse is its own tap", taps == [1, 1, 1])


def test_tap_sets_tempo_only():
    app = make_app()
    tr = app.transport
    tr.set_running(False)                   # prove tap never touches play
    bpm0 = tr.bpm
    app._transport_tap(now=100.0)
    check("first tap: no tempo change", tr.bpm == bpm0)
    app._transport_tap(now=100.5)
    app._transport_tap(now=101.0)
    app._transport_tap(now=101.5)           # 4 clean taps at 0.5 s
    check("4 clean taps at 0.5 s → bpm ≈ 120", abs(tr.bpm - 120.0) < 0.01)
    check("tap never touches running", tr.running is False)
    app._transport_tap(now=110.0)           # late — restart, no change
    check("a late tap leaves the tempo alone", abs(tr.bpm - 120.0) < 0.01)
    app._transport_tap(now=110.4)           # first valid interval reseeds
    check("post-restart pair sets the fresh interval (150)",
          abs(tr.bpm - 150.0) < 0.01)


# ---- downbeat: clamp, meter re-clamp, accent predicate -------------------------

def test_downbeat():
    app = make_app()
    tr = app.transport
    check("downbeat defaults to 0 and rides settings()",
          tr.downbeat == 0 and tr.settings()["downbeat"] == 0)
    app.set_transport(downbeat=2)
    check("set_transport(downbeat=2) lands", tr.downbeat == 2)
    app.set_transport(downbeat=99)
    check("downbeat clamps to beats_per_bar-1", tr.downbeat == 3)
    app.set_transport(downbeat=-5)
    check("negative downbeat clamps to 0", tr.downbeat == 0)
    app.set_transport(downbeat=3)
    app.set_transport(beats_per_bar=2)
    check("meter shrink re-clamps the downbeat", tr.downbeat == 1)
    app.set_transport(beats_per_bar=6, downbeat=5)
    check("combined meter+downbeat clamps against the NEW meter",
          tr.beats_per_bar == 6 and tr.downbeat == 5)

    # the pure accent predicate (what the click thread plays)
    app.set_transport(beats_per_bar=4, downbeat=2, accent=True)
    check("accent_on fires on beat_in_bar == downbeat",
          tr.accent_on(2) is True)
    check("accent_on quiet on beat 0 when downbeat moved",
          tr.accent_on(0) is False)
    app.set_transport(accent=False)
    check("accent toggle mutes the accent beat too",
          tr.accent_on(2) is False)


# ---- card presence --------------------------------------------------------------

def test_cards():
    app = make_app()
    check("card set starts empty (state carries [])",
          app.state()["transport_cards"] == [])
    check("the two cards are play + tempo",
          set(TRANSPORT_CARDS) == {"play", "tempo"})
    app.spawn_transport_card("play")
    app.spawn_transport_card("play")        # idempotent
    check("spawn is idempotent", app.state()["transport_cards"] == ["play"])
    app.spawn_transport_card("tempo")
    check("state carries the sorted card list",
          app.state()["transport_cards"] == ["play", "tempo"])
    try:
        app.spawn_transport_card("clicky")
        check("invalid spawn which raises", False)
    except ValueError:
        check("invalid spawn which raises", True)
    try:
        app.remove_transport_card("clicky")
        check("invalid remove which raises", False)
    except ValueError:
        check("invalid remove which raises", True)
    app.remove_transport_card("play")
    app.remove_transport_card("play")       # idempotent
    check("remove is idempotent", app.state()["transport_cards"] == ["tempo"])

    # removing a card NEVER unwires the transport's global endpoints
    b = latch_button(app)
    app.set_ctl_wire("add", b, "transport:run")
    app.remove_transport_card("tempo")
    check("card removal leaves the transport wire in place",
          {"from": b, "to": "transport:run"} in app.ctl_wires)
    set_lvl(app, b, True)
    was = app.transport.running
    set_lvl(app, b, False)
    check("the wire still applies after the card is gone",
          was is True and app.transport.running is False)


# ---- persistence -----------------------------------------------------------------

def test_persistence():
    app = make_app()
    app.spawn_transport_card("tempo")
    app.set_transport(downbeat=2)
    data = presets.snapshot(app)
    check("snapshot carries transport_cards", data["transport_cards"] == ["tempo"])
    check("snapshot carries the downbeat", data["transport"]["downbeat"] == 2)

    app2 = make_app()
    app2._build_patch = lambda name: None
    presets._apply(app2, data)
    check("restore brings the card back", app2.transport_cards == {"tempo"})
    check("restore brings the downbeat back", app2.transport.downbeat == 2)

    # an OLD preset (no transport_cards key, no downbeat) restores clean
    old = {k: v for k, v in data.items() if k != "transport_cards"}
    old["transport"] = {k: v for k, v in data["transport"].items()
                        if k != "downbeat"}
    app3 = make_app()
    app3._build_patch = lambda name: None
    presets._apply(app3, old)
    check("old preset → no cards (empty default)",
          app3.transport_cards == set())
    check("old preset → downbeat stays 0", app3.transport.downbeat == 0)


def main():
    test_endpoint_grammar()
    test_level_follow_run()
    test_level_follow_click_and_accent()
    test_tap_tempo_unit()
    test_tap_trig_edges()
    test_tap_sets_tempo_only()
    test_downbeat()
    test_cards()
    test_persistence()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
