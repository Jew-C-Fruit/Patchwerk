#!/usr/bin/env python3
"""Generate the non-portrait manual figures by driving gui/blocks.html.

Companion to gen_figs.py, which does the 26 module card portraits. This one
does the boards, the chrome, the player cards and the before/after pairs —
each figure is a SCENE: a state to push, optional messages and clicks, and a
capture target.

    python docs/manual/gen_scenes.py --list          # scene ids
    python docs/manual/gen_scenes.py --all
    python docs/manual/gen_scenes.py FIG-03-09       # one scene, for iterating

State shapes are taken from tests/check_blocks.py, which has driven this page
headlessly since 2026-07-21. Where a shape does not appear there it is derived
from synthbase/app.py's state() and marked DERIVED in the scene comment.

Fidelity: blocks.html asks for system-ui. On macOS that is SF Pro; in a Linux
container it resolves to DejaVu Sans and cards render subtly wider. Generate on
the Mac for shipping figures.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from gen_figs import (BLOCKS, MODULES, OUT, SCALE, STUB, FREEZE, VIEWPORT,
                      build_state, mod, param)

P = param
SETTLE = 900


# --------------------------------------------------------------------------
# state fragments
# --------------------------------------------------------------------------

def chain_of(*keys):
    out = []
    for k in keys:
        name, kind, family, params = MODULES[k]
        out.append(mod(k, name, kind, family, {p: dict(v) for p, v in params.items()}))
    return out


def wires(*pairs):
    return [{"from": a, "to": b} for a, b in pairs]


def st(**over):
    """Base state with the manual's conventions, then overrides."""
    s = build_state(["wobble_saw"])
    s.update(chain=[], wires=[], ctl_wires=[], voices=[], voice_target=None)
    s.update(over)
    return s


MONO_VOICE = {"id": "voice", "target": None, "policy": "mono-latest",
              "slots": 1, "power": None}


def voice(target, vid="voice", policy="mono-latest", slots=1, power=None):
    return {"id": vid, "target": target, "policy": policy,
            "slots": slots, "power": power}


LFO = {"id": "lfo", "rate": 1.0, "shape": 0, "depth": 0.5,
       "shapes": ["sine", "tri", "ramp", "square", "s&h"],
       "dests": [{"key": "lowpass", "param": "cutoff", "center": 0.45}]}

THRESHOLD = {"id": "threshold", "level": 0.35, "hysteresis": 0.02,
             "mode": "rising", "modes": ["rising", "falling", "both"],
             "source": "lfo", "on": False}

RELAY = {"id": "relay", "closed": True,
         "circuits": {"1": {"kind": "audio"}, "2": {"kind": "notes"},
                      "3": {"kind": "binary"}, "4": {"kind": "mod"}}}

BUTTON = {"id": "button", "binding": {"kind": "key", "code": "KeyN"},
          "armed": False, "latch": False, "on": False}
BUTTON_LATCH = {"id": "button.2", "binding": None, "armed": False,
                "latch": True, "on": True}
CLOCK = {"id": "clock", "division": "1/4",
         "divisions": ["1/1", "1/2", "1/4", "1/8", "1/16"]}

KEYSHIFT = {"id": "keyshift", "key": 0, "length": 8,
            "steps": [0, None, 5, None, 7, None, 5, None], "active": 0}

TONIC = {"id": "tonic", "every": "1 bar",
         "everies": ["1 beat", "2 beats", "1 bar", "2 bars", "4 bars", "deck"],
         "octave": 2, "root": "C", "memory": 6.0, "bass": 0.06,
         "deck_feed": False, "scale": None, "listening": "triadic",
         "listenings": ["triadic", "root+fifth", "chromatic"]}

DRUMS_LOADED = {
    "enabled": True, "target": "master", "to_chain": False,
    "lanes": ["kick", "snare", "hat", "clap"], "steps": 16,
    "patterns": {"kick":  [1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
                 "snare": [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
                 "hat":   [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1],
                 "clap":  [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]},
    "levels": {"kick": 0.8, "snare": 0.7, "hat": 0.6, "clap": 0.7}}

# DERIVED from synthbase/looper.py — check_blocks.py only ever sends "empty".
DECK_NOTES = [[0.0, 60, True], [0.5, 60, False], [1.0, 64, True],
              [1.5, 64, False], [2.0, 67, True], [3.0, 67, False],
              [4.0, 72, True], [5.5, 72, False]]


def deck(state, notes=None, events=0):
    return {"state": state, "bars": 2, "level": 0.8, "overdub": False,
            "position": "post", "loop_beats": 8, "midi": True,
            "events": events, "notes": notes or []}


# --------------------------------------------------------------------------
# boards
# --------------------------------------------------------------------------

MINIMAL = dict(
    chain=chain_of("wobble_saw"),
    wires=wires(("wobble_saw", "master")),
    ctl_wires=wires(("keys", "voice")),
    voices=[voice("wobble_saw")], voice_target="wobble_saw")

WITH_ARP = dict(
    chain=chain_of("wobble_saw", "lowpass", "echo"),
    wires=wires(("wobble_saw", "lowpass"), ("lowpass", "echo"),
                ("echo", "master")),
    ctl_wires=wires(("keys", "arp"), ("arp", "voice")),
    voices=[voice("wobble_saw")], voice_target="wobble_saw")

FANOUT = dict(
    chain=chain_of("wobble_saw", "fm_bell", "pluck", "reverb"),
    wires=wires(("wobble_saw", "reverb"), ("fm_bell", "reverb"),
                ("pluck", "reverb"), ("reverb", "master")),
    ctl_wires=wires(("keys", "voice"), ("keys", "poly"), ("keys", "hold")),
    voices=[voice("wobble_saw"), voice("fm_bell", "poly", "poly", 8),
            voice("pluck", "hold", "hold", 1, True)],
    voice_target="wobble_saw")

# The showpiece: every wire kind present at once.
SHOWPIECE = dict(
    chain=chain_of("pulse_pad", "power_sine_blend", "lowpass", "drive",
                   "echo", "reverb"),
    wires=wires(("pulse_pad", "lowpass"), ("power_sine_blend", "lowpass"),
                ("lowpass", "drive"), ("drive", "echo"), ("echo", "reverb"),
                ("reverb", "master")),
    ctl_wires=wires(("keys", "arp"), ("arp", "voice"), ("keys", "keyshift:1"),
                    ("keyshift:1", "poly"), ("button", "logic:a"),
                    ("clock", "logic:b"), ("logic", "arp:pwr"),
                    ("threshold", "drums:pwr")),
    mod_wires=wires(("lfo", "relay:1"), ("relay:1", "lowpass:cutoff")),
    voices=[voice("pulse_pad"), voice("power_sine_blend", "poly", "poly", 6)],
    voice_target="pulse_pad",
    lfos=[LFO], thresholds=[THRESHOLD], relays=[RELAY],
    logics=[{"id": "logic", "op": "AND",
             "ops": ["AND", "OR", "NOR", "XOR", "SR latch", "T latch"],
             "out": False}],
    buttons=[BUTTON], clocks=[CLOCK], keyshifts=[KEYSHIFT],
    tonics=[TONIC], drums=DRUMS_LOADED,
    transport_cards=["play", "tempo"],
    transport={"bpm": 112, "beats_per_bar": 4, "click": True, "accent": True,
               "downbeat": 0, "running": True,
               "divisions": ["1/4", "1/8", "1/16"]})

HERO = dict(SHOWPIECE, looper=deck("playing", DECK_NOTES, 8))

LOGIC_BENCH = dict(
    chain=chain_of("wobble_saw"), wires=wires(("wobble_saw", "master")),
    buttons=[BUTTON, BUTTON_LATCH], clocks=[CLOCK], thresholds=[THRESHOLD],
    relays=[RELAY],
    logics=[{"id": "logic", "op": "AND", "out": True,
             "ops": ["AND", "OR", "NOR", "XOR", "SR latch", "T latch"]},
            {"id": "logic.2", "op": "XOR", "out": False,
             "ops": ["AND", "OR", "NOR", "XOR", "SR latch", "T latch"]},
            {"id": "logic.3", "op": "NOR", "out": False,
             "ops": ["AND", "OR", "NOR", "XOR", "SR latch", "T latch"]},
            {"id": "logic.4", "op": "SR latch", "out": True,
             "ops": ["AND", "OR", "NOR", "XOR", "SR latch", "T latch"]}],
    ctl_wires=wires(("button", "logic:a"), ("clock", "logic:b")),
    voices=[voice("wobble_saw")], voice_target="wobble_saw")

MOD_BENCH = dict(
    chain=chain_of("wobble_saw", "lowpass", "scope_tap", "echo"),
    wires=wires(("wobble_saw", "lowpass"), ("lowpass", "scope_tap"),
                ("scope_tap", "echo"), ("echo", "master")),
    lfos=[LFO], thresholds=[THRESHOLD],
    ctl_wires=wires(("keys", "voice")),
    voices=[voice("wobble_saw")], voice_target="wobble_saw")


def with_lfo_flag(board, key, pname):
    """Mark a param as LFO-driven so its row wears the amplitude band."""
    b = {k: (list(v) if isinstance(v, list) else v) for k, v in board.items()}
    b["chain"] = [dict(c) for c in b["chain"]]
    for c in b["chain"]:
        if c["key"] == key:
            c["params"] = {p: dict(v) for p, v in c["params"].items()}
            c["params"][pname]["lfo"] = True
    return b


# --------------------------------------------------------------------------
# scenes:  id -> (state overrides, actions, capture spec)
#   capture: ("page",) | ("el", gid) | ("sel", css) | ("region", css_list)
# --------------------------------------------------------------------------

MSG_METERS = {"type": "meters", "out": [0.62, 0.55]}

# Boards dense enough to overflow the frame — run tidy before the shot.
TIDY = {"FIG-01-01", "FIG-03-01", "FIG-15-04", "FIG-15-05", "FIG-06-03"}

# Cropping a card out of a wired board leaves severed wire stubs running to the
# crop edge, which read as artifacts. Hide the wire layer for card crops —
# EXCEPT where the wire is the subject of the figure.
WIRES_MATTER = {"FIG-08-02"}


def scenes() -> dict:
    return {
        # ---- chrome -------------------------------------------------------
        "FIG-03-02": (SHOWPIECE, [MSG_METERS], ("sel", "#hdr")),
        "FIG-03-09": (SHOWPIECE, [], ("sel", "#palette")),
        "FIG-11-01": (SHOWPIECE, [], ("sel", "#palette")),

        # ---- whole boards -------------------------------------------------
        "FIG-01-01": (HERO, [MSG_METERS], ("page",)),
        "FIG-01-03": (MINIMAL, [], ("page",)),
        "FIG-03-01": (SHOWPIECE, [MSG_METERS], ("page",)),
        "FIG-06-02": (MINIMAL, [], ("page",)),
        "FIG-06-03": (FANOUT, [], ("page",)),
        "FIG-06-04": (WITH_ARP, [], ("page",)),
        "FIG-15-01": (MINIMAL, [], ("page",)),
        "FIG-15-02": (WITH_ARP, [], ("page",)),
        "FIG-15-04": (FANOUT, [], ("page",)),
        "FIG-15-05": (SHOWPIECE, [MSG_METERS], ("page",)),

        # ---- single cards -------------------------------------------------
        "FIG-05-07": (dict(MINIMAL, volume=0.72), [MSG_METERS], ("el", "master")),
        "FIG-06-07": (dict(MINIMAL, keyshifts=[KEYSHIFT]), [], ("el", "keyshift")),
        "FIG-07-02": (LOGIC_BENCH, [
            {"type": "midi", "event": {"kind": "gate", "id": "logic", "on": True}},
            {"type": "midi", "event": {"kind": "gate", "id": "button", "on": True}},
        ], ("els", ["logic", "logic.2", "logic.3", "logic.4",
                    "button", "button.2", "clock", "threshold", "relay"])),
        "FIG-07-03": (LOGIC_BENCH, [
            {"type": "midi", "event": {"kind": "gate", "id": "relay", "on": True}},
        ], ("el", "relay")),
        "FIG-07-05": (MOD_BENCH, [], ("el", "threshold")),
        "FIG-08-01": (with_lfo_flag(MOD_BENCH, "lowpass", "cutoff"), [],
                      ("el", "lfo:lfo")),
        "FIG-08-02": (with_lfo_flag(MOD_BENCH, "lowpass", "cutoff"), [],
                      ("els", ["lfo:lfo", "m:lowpass"])),
        "FIG-08-05": (MOD_BENCH, [], ("el", "m:scope_tap")),
        "FIG-09-03": (dict(MINIMAL, drums=DRUMS_LOADED), [], ("el", "drums")),
        "FIG-11-02": (dict(MINIMAL, chain=chain_of("pulse_pad"),
                           wires=wires(("pulse_pad", "master")),
                           voices=[voice("pulse_pad")],
                           voice_target="pulse_pad"),
                      [], ("el", "m:pulse_pad")),

        # ---- transport: card and top bar agreeing -------------------------
        "FIG-09-01": (dict(MINIMAL, transport_cards=["play", "tempo"],
                           transport={"bpm": 112, "beats_per_bar": 4,
                                      "click": True, "accent": True,
                                      "downbeat": 0, "running": True,
                                      "divisions": ["1/4", "1/8"]}),
                      [{"type": "beat", "bar": 0, "beat": 1,
                        "downbeat": True, "loop": None}],
                      ("topregion",)),
    }


# Before/after pairs — each half is a full scene, stitched side by side.
PAIRS = {
    "FIG-03-12": ("power indicator: off, then on", [
        (dict(MINIMAL, chain=[dict(chain_of("wobble_saw")[0], enabled=False)]),
         [], "m:wobble_saw"),
        (MINIMAL, [], "m:wobble_saw")]),
    "FIG-05-06": ("bypassed, then enabled", [
        (dict(WITH_ARP, chain=[dict(c, enabled=(c["key"] != "lowpass"))
                               for c in WITH_ARP["chain"]]), [], "m:lowpass"),
        (WITH_ARP, [], "m:lowpass")]),
    "FIG-10-01": ("the deck idle, recording, playing", [
        (dict(MINIMAL, looper=deck("empty")), [], "deck"),
        (dict(MINIMAL, looper=deck("recording", DECK_NOTES[:4], 4)),
         [{"type": "beat", "bar": 0, "beat": 2, "downbeat": False,
           "loop": 2.4}], "deck"),
        (dict(MINIMAL, looper=deck("playing", DECK_NOTES, 8)),
         [{"type": "beat", "bar": 1, "beat": 3, "downbeat": False,
           "loop": 5.1}], "deck")]),
}


# --------------------------------------------------------------------------

def boot(pw, state, msgs, mode=None, tidy=False):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
    page = ctx.new_page()
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.add_init_script(STUB + FREEZE)
    page.goto(BLOCKS.as_uri())
    page.wait_for_timeout(320)
    page.evaluate("""() => { window.__msg =
        (m) => window.__wss[0].onmessage({data: JSON.stringify(m)}); }""")
    # Deterministic geometry: clear any remembered card positions first.
    page.evaluate("() => { try { posMem = {}; fposMem = {}; } catch (e) {} }")
    page.evaluate("(s) => window.__msg(Object.assign({type:'state'}, s))", state)
    page.wait_for_timeout(SETTLE)
    if mode:
        page.evaluate("(m) => setMode(m)", mode)
        page.wait_for_timeout(600)
    if tidy:
        # Dense boards overflow the 16:10 frame. Tidy is the interface's own
        # answer to that — it compacts each connected tree into a column in
        # signal order, which is also how a reader should see the patch.
        page.evaluate("() => { try { compactLayout(); } catch (e) "
                      "{ document.getElementById('tidy').click(); } }")
        page.wait_for_timeout(800)
    for m in msgs:
        page.evaluate("(m) => window.__msg(m)", m)
    if msgs:
        page.wait_for_timeout(400)
    page.add_style_tag(content="*,*::before,*::after{"
                               "transition:none!important;animation:none!important}")
    page.wait_for_timeout(120)
    return browser, page, errs


def _box_union(page, gids):
    boxes = []
    for g in gids:
        el = page.query_selector(f'.mod[data-gid="{g}"]')
        if el:
            boxes.append(el.bounding_box())
    if not boxes:
        return None
    x0 = min(b["x"] for b in boxes); y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def capture(page, spec, path: Path, pad=30) -> bool:
    kind = spec[0]
    if kind == "page":
        page.screenshot(path=str(path))
        return True
    if kind == "topregion":
        page.screenshot(path=str(path), clip={"x": 0, "y": 0,
                                              "width": VIEWPORT["width"],
                                              "height": 430})
        return True
    if kind == "sel":
        el = page.query_selector(spec[1])
        if not el:
            return False
        el.screenshot(path=str(path))
        return True
    box = (_box_union(page, [spec[1]]) if kind == "el"
           else _box_union(page, spec[1]))
    if not box:
        return False
    page.screenshot(path=str(path), clip={
        "x": max(0, box["x"] - pad), "y": max(0, box["y"] - pad),
        "width": box["width"] + pad * 2, "height": box["height"] + pad * 2})
    return True


def stitch(paths: list[Path], out: Path, gap=28) -> None:
    """Side-by-side composite on the plate colour, tops aligned."""
    ims = [Image.open(p).convert("RGB") for p in paths]
    h = max(i.height for i in ims)
    w = sum(i.width for i in ims) + gap * (len(ims) - 1)
    canvas = Image.new("RGB", (w, h), (13, 13, 13))
    x = 0
    for im in ims:
        canvas.paste(im, (x, (h - im.height) // 2))
        x += im.width + gap
    canvas.save(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("figs", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    SC, PR = scenes(), PAIRS
    if args.list:
        for k in sorted(SC):
            print(f"  {k}  single")
        for k in sorted(PR):
            print(f"  {k}  composite ({len(PR[k][1])} frames) — {PR[k][0]}")
        return

    want = set(args.figs) if args.figs else (set(SC) | set(PR))
    OUT.mkdir(parents=True, exist_ok=True)
    ok, bad, errs_all = [], [], {}

    with sync_playwright() as pw:
        for fig in sorted(want & set(SC)):
            state_over, msgs, spec = SC[fig]
            b, page, errs = boot(pw, st(**state_over), msgs,
                                 tidy=(fig in TIDY))
            if spec[0] in ("el", "els") and fig not in WIRES_MATTER:
                page.add_style_tag(content="#wires{display:none!important}")
                page.wait_for_timeout(80)
            good = capture(page, spec, OUT / f"{fig.lower()}.png")
            (ok if good else bad).append(fig)
            if errs:
                errs_all[fig] = errs[:2]
            print(f"  {'ok  ' if good else 'MISS'} {fig}  {spec[0]}")
            b.close()

        for fig in sorted(want & set(PR)):
            label, frames = PR[fig]
            tmp = []
            for n, (state_over, msgs, gid) in enumerate(frames):
                b, page, errs = boot(pw, st(**state_over), msgs)
                page.add_style_tag(content="#wires{display:none!important}")
                page.wait_for_timeout(80)
                p = OUT / f"_{fig.lower()}_{n}.png"
                if capture(page, ("el", gid), p):
                    tmp.append(p)
                if errs:
                    errs_all.setdefault(fig, []).extend(errs[:1])
                b.close()
            if len(tmp) == len(frames):
                stitch(tmp, OUT / f"{fig.lower()}.png")
                for p in tmp:
                    p.unlink()
                ok.append(fig)
                print(f"  ok   {fig}  composite — {label}")
            else:
                bad.append(fig)
                print(f"  MISS {fig}  composite ({len(tmp)}/{len(frames)})")

    print(f"\n{len(ok)} written, {len(bad)} missed")
    if bad:
        print("missed:", ", ".join(bad))
    for f, e in errs_all.items():
        print(f"page errors {f}: {e}")


if __name__ == "__main__":
    main()
