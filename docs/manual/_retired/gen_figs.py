#!/usr/bin/env python3
"""Generate manual figures by driving gui/blocks.html — no rig, no audio.

The manual's figures are pictures of the interface. Screenshotting a running
instance means a rig session, a patch set up by hand, and images that go stale
silently the next time the GUI changes. Instead this drives the REAL
`gui/blocks.html` from a `file://` URL with a stubbed WebSocket, feeds it a
state snapshot, and crops the cards out.

The stub is lifted from `tests/check_blocks.py`, which has driven this page
headlessly since 2026-07-21 — this is the same proven path, pointed at
figure generation instead of assertions.

    python docs/manual/gen_figs.py --list         # what rendered
    python docs/manual/gen_figs.py --cards        # the module card portraits
    python docs/manual/gen_figs.py --one lowpass  # one card, for iterating

Fidelity note: `blocks.html` asks for `system-ui`. On macOS that is SF Pro; in
a Linux container it resolves to DejaVu Sans and the cards will be subtly
wider. Generate on the Mac for shipping figures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
BLOCKS = HERE / "blocks.html"
if not BLOCKS.exists():                       # in-repo location
    BLOCKS = HERE.parent.parent / "gui" / "blocks.html"
OUT = HERE / "img"

VIEWPORT = {"width": 1600, "height": 1000}
SCALE = 2
SETTLE_MS = 1200

# The WebSocket stub — verbatim in behaviour from tests/check_blocks.py.
STUB = """
  window.__sent = [];
  window.__wss = [];
  window.WebSocket = class {
    constructor(url) { this.url = url; this.readyState = 1;
      window.__wss.push(this);
      setTimeout(() => this.onopen && this.onopen(), 0); }
    send(d) { window.__sent.push(JSON.parse(d)); }
    close() {}
  };
"""

# Determinism: freeze the clock so LFO phase and any time-derived viz land in
# the same place on every run. Without this the same figure differs per build.
FREEZE = """
  const T0 = 1000;
  performance.now = () => T0;
  const _D = Date;
  window.Date = class extends _D {
    constructor(...a) { super(...(a.length ? a : [1767225600000])); }
    static now() { return 1767225600000; }
  };
"""


def param(v, lo=0.0, hi=1.0, curve="lin", options=None):
    return {"min": lo, "max": hi, "curve": curve, "options": options or [],
            "default": v, "lfo": False, "value": v}


def mod(key, name, kind, family, params, **extra):
    return {"key": key, "type": key.split(".")[0], "name": name, "kind": kind,
            "family": family, "enabled": True, "service": False,
            "params": params, **extra}


P = param  # brevity in the table below

# Every module, with its real declared ranges. Sourced from modules/*.py.
MODULES = {
    "wobble_saw": ("Wobble Saw", "source", "voice", {
        "freq": P(110, 20, 2000, "exp"), "wobble": P(4, 0.1, 20, "exp"),
        "depth": P(0.5), "amp": P(0.25)}),
    "pulse_pad": ("PW Pulse Pad", "source", "voice", {
        "freq": P(220, 20, 2000, "exp"),
        "wave": P(0, 0, 3, "lin", ["pulse", "saw", "tri", "sine"]),
        "detune": P(12.0, 0.0, 50.0), "porta": P(0, 0, 1),
        "glide": P(0.15, 0.01, 2.0, "exp"), "pwm": P(0.2, 0.0, 0.45),
        "attack": P(0.15, 0.005, 2.0, "exp"),
        "release": P(0.8, 0.05, 5.0, "exp"), "amp": P(0.22)}),
    "fm_bell": ("FM Bell", "source", "voice", {
        "freq": P(440, 20, 2000, "exp"), "ratio": P(3.51, 0.5, 8.0),
        "index": P(4.0, 0.0, 12.0), "decay": P(2.5, 0.1, 8.0, "exp"),
        "amp": P(0.25)}),
    "pluck": ("Pluck", "source", "voice", {
        "freq": P(220, 40, 1600, "exp"), "decay": P(4.0, 0.3, 12.0, "exp"),
        "damp": P(0.4, 0.0, 0.9), "amp": P(0.35)}),
    "wind": ("Wind", "source", "voice", {
        "center": P(700, 150, 4000, "exp"), "gust": P(0.6),
        "resonance": P(1.0, 0.2, 3.0), "amp": P(0.3)}),
    "audio_in": ("Audio In", "source", "input", {"gain": P(1.0, 0.0, 4.0)}),
    "drone": ("Drone", "source", "service", {
        "freq": P(55, 16, 500, "exp"), "amp": P(0.16), "porta": P(1, 0, 1),
        "glide": P(1.5, 0.05, 8.0, "exp"), "shape": P(0.35), "sub": P(0.4),
        "cutoff": P(900, 80, 8000, "exp")}),
    "power_sine_shaper": ("Psine Waveshaper", "source", "psine", {
        "freq": P(220, 20, 2000, "exp"), "p": P(2.0, 1, 64, "exp"),
        "amp": P(0.3)}),
    "power_sine_additive": ("Psine Harmonic Bank", "source", "psine", {
        "freq": P(220, 20, 2000, "exp"), "p": P(2.0, 1, 64, "exp"),
        "amp": P(0.3)}),
    "power_sine_blend": ("Psine Crossfade", "source", "psine", {
        "freq": P(220, 20, 2000, "exp"), "p": P(2.0, 1, 64, "exp"),
        "amp": P(0.3)}),
    "power_shaper": ("Power Shaper", "dual", "psine", {
        "freq": P(220, 20, 2000, "exp"), "p": P(2.0, 1, 64, "exp"),
        "drive": P(1.0, 0.25, 8.0, "exp"), "amp": P(0.3), "mix": P(1.0)}),
    "lowpass": ("Low-pass Filter", "effect", "filter", {
        "cutoff": P(1200, 60, 12000, "exp"), "resonance": P(0.5, 0.1, 1.0)}),
    "telephone": ("Telephone", "effect", "filter", {
        "low": P(380, 100, 1200, "exp"), "high": P(3200, 1200, 8000, "exp"),
        "crunch": P(3.0, 1.0, 12.0, "exp"), "mix": P(1.0)}),
    "echo": ("Echo", "effect", "time", {
        "time": P(0.375, 0.02, 2.0), "feedback": P(0.4, 0.0, 0.95),
        "mix": P(0.35)}),
    "reverb": ("Reverb", "effect", "time", {
        "room": P(0.6), "damp": P(0.5), "mix": P(0.3)}),
    "chorus": ("Chorus", "effect", "time", {
        "rate": P(0.4, 0.05, 4.0, "exp"), "depth": P(0.5), "mix": P(0.4)}),
    "flanger": ("Flanger", "effect", "time", {
        "rate": P(0.25, 0.05, 3.0, "exp"), "depth": P(0.7),
        "feedback": P(0.4, 0.0, 0.9), "mix": P(0.5)}),
    "phaser": ("Phaser", "effect", "time", {
        "rate": P(0.3, 0.05, 4.0, "exp"), "depth": P(0.8), "mix": P(0.5)}),
    "autopan": ("Auto Pan", "effect", "time", {
        "rate": P(0.5, 0.05, 10.0, "exp"), "depth": P(0.7)}),
    "drive": ("Drive", "effect", "dirt", {
        "gain": P(4.0, 1.0, 40.0, "exp"), "tone": P(4000, 500, 12000, "exp"),
        "mix": P(1.0)}),
    "bitcrush": ("Bitcrush", "effect", "dirt", {
        "srate": P(8000, 400, 44100, "exp"), "bits": P(10, 2, 16),
        "mix": P(1.0)}),
    "wavefolder": ("Wavefolder", "effect", "dirt", {
        "fold": P(2.5, 1.0, 12.0, "exp"), "symmetry": P(0.0, -0.5, 0.5),
        "mix": P(1.0)}),
    "compressor": ("Compressor", "effect", "dyn", {
        "threshold": P(0.3, 0.01, 1.0, "exp"), "ratio": P(4.0, 1.0, 20.0, "exp"),
        "attack": P(0.01, 0.001, 0.2, "exp"),
        "release": P(0.15, 0.02, 1.0, "exp"),
        "makeup": P(1.3, 0.5, 4.0, "exp")}),
    "pitchshift": ("Pitch Shift", "effect", "vox", {
        "semitones": P(0, -24, 24), "mix": P(1.0),
        "window": P(0.04, 0.02, 0.2, "exp"), "smear": P(0.002, 0.0, 0.02)}),
    "ringmod": ("Ring Mod", "effect", "vox", {
        "carrier": P(200, 20, 4000, "exp"), "mix": P(0.8)}),
    "scope_tap": ("Scope Tap", "effect", "effect", {"gain": P(1.0, 0.0, 2.0)}),
}

# FIG id per module, from FIGURES.md chapters 12-14.
CARD_FIGS = {
    "wobble_saw": "FIG-12-01", "pulse_pad": "FIG-12-02", "fm_bell": "FIG-12-03",
    "pluck": "FIG-12-04", "power_sine_shaper": "FIG-12-05",
    "power_sine_additive": "FIG-12-06", "power_sine_blend": "FIG-12-07",
    "audio_in": "FIG-13-01", "wind": "FIG-13-02", "drone": "FIG-13-03",
    "lowpass": "FIG-14-01", "telephone": "FIG-14-02", "echo": "FIG-14-03",
    "reverb": "FIG-14-04", "chorus": "FIG-14-05", "flanger": "FIG-14-06",
    "phaser": "FIG-14-07", "autopan": "FIG-14-08", "drive": "FIG-14-09",
    "bitcrush": "FIG-14-10", "wavefolder": "FIG-14-11",
    "compressor": "FIG-14-12", "pitchshift": "FIG-14-13",
    "ringmod": "FIG-14-14", "scope_tap": "FIG-14-15",
    "power_shaper": "FIG-14-18",
}


def build_state(keys: list[str], overrides: dict | None = None) -> dict:
    chain = []
    for k in keys:
        name, kind, family, params = MODULES[k]
        params = {p: dict(v) for p, v in params.items()}
        for pname, val in (overrides or {}).get(k, {}).items():
            if pname in params:
                params[pname]["value"] = params[pname]["default"] = val
        chain.append(mod(k, name, kind, family, params))
    first_playable = next(
        (c["key"] for c in chain
         if c["kind"] in ("source", "dual") and "freq" in c["params"]
         and "amp" in c["params"]), None)
    return {
        "patch": "manual", "patches": ["manual"], "volume": 0.8,
        "devices": {"inputs": [], "outputs": []}, "current_input": None,
        "current_output": None, "input_enabled": False, "boot_note": None,
        "chain": chain, "wires": [], "ctl_wires": [],
        "drums_target": None, "voice_target": first_playable,
        "voices": [{"id": "voice", "target": first_playable,
                    "policy": "mono", "slots": 1, "power": True}]
        if first_playable else [],
        "tonics": [], "keyshifts": [], "transpose": 0, "literals": [],
        "midi_inputs": [], "midi_port": None, "midi_enabled": False,
        "arp": {"enabled": False, "pattern": "up", "patterns": ["up", "down"],
                "division": "1/8", "divisions": ["1/8", "1/16"], "gate": 0.6,
                "octaves": 1},
        "transport": {"bpm": 100, "beats_per_bar": 4, "click": False,
                      "running": False, "downbeat": 1},
        "drone": {"enabled": False, "every": "1 bar", "everies": ["1 bar"],
                  "octave": 2, "root": None},
        "drums": {"enabled": False, "target": None, "to_chain": False,
                  "lanes": ["kick", "snare", "hat", "clap"], "steps": 16,
                  "patterns": {ln: [0] * 16 for ln in
                               ("kick", "snare", "hat", "clap")},
                  "levels": {"kick": 0.8, "snare": 0.7, "hat": 0.6,
                             "clap": 0.7}},
        "looper": {"state": "empty", "bars": 2, "level": 0.8,
                   "overdub": False, "position": "post", "loop_beats": 8,
                   "notes": []},
        "lfos": [], "presets": [], "thresholds": [], "logics": [],
        "relays": [], "buttons": [], "clocks": [], "transport_cards": [],
        "available": [{"key": k, "name": v[0], "kind": v[1], "family": v[2]}
                      for k, v in MODULES.items()],
        "module_errors": {},
    }


def boot(pw, state: dict):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB + FREEZE)
    page.goto(BLOCKS.as_uri())
    page.wait_for_timeout(350)
    page.evaluate("""() => { window.__msg =
        (m) => window.__wss[0].onmessage({data: JSON.stringify(m)}); }""")
    page.evaluate("(s) => window.__msg(Object.assign({type:'state'}, s))", state)
    page.wait_for_timeout(SETTLE_MS)
    page.add_style_tag(content="*,*::before,*::after{"
                               "transition:none!important;animation:none!important}")
    page.wait_for_timeout(120)
    return browser, page, errors


def gids(page) -> list[dict]:
    return page.evaluate("""[...document.querySelectorAll('.mod')].map(m => ({
        gid: m.dataset.gid, size: m.dataset.size,
        title: (m.querySelector('.title')||{}).textContent || ''}))""")


def shoot_card(page, gid: str, path: Path, pad: int = 26) -> bool:
    """Crop one card. Padding must exceed 5.5px — handles straddle the border.

    Wires are hidden for portraits: a neighbouring card's wire crosses the crop
    box and reads as an artifact. Handles live in a separate #handles overlay
    and MUST stay — they are part of what a card portrait has to show.
    """
    el = page.query_selector(f'.mod[data-gid="{gid}"]')
    if el is None:
        return False
    b = el.bounding_box()
    page.screenshot(path=str(path), clip={
        "x": max(0, b["x"] - pad), "y": max(0, b["y"] - pad),
        "width": b["width"] + pad * 2, "height": b["height"] + pad * 2})
    return True


# A card portrait should show the card doing something characteristic, not
# sitting at a default that makes three different modules look identical.
# All three psine generators collapse to the SAME pure sine at p=2 (that is
# the documented invariant), so portraits at the default would be three
# indistinguishable pictures. p=8 puts each one's rendering on show.
PORTRAIT_OVERRIDES = {
    "power_sine_shaper": {"p": 8.0},
    "power_sine_additive": {"p": 8.0},
    "power_sine_blend": {"p": 8.0},
    "power_shaper": {"p": 8.0},
}

# Cards that hide their graphic at their default size. The Pulse Pad drops its
# waveform at M and takes it back at L; a portrait at M would omit the very
# thing Ch 3.3.3 uses as its worked example.
PORTRAIT_SIZE = {"pulse_pad": "L"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cards", action="store_true")
    ap.add_argument("--one", default=None)
    args = ap.parse_args()

    if not BLOCKS.exists():
        sys.exit(f"blocks.html not found at {BLOCKS}")
    OUT.mkdir(parents=True, exist_ok=True)

    keys = [args.one] if args.one else list(MODULES)
    state = build_state(keys, PORTRAIT_OVERRIDES)

    with sync_playwright() as pw:
        browser, page, errors = boot(pw, state)
        # Portraits only: suppress the wire layer (see shoot_card) and force
        # the sizes that reveal a card's graphic.
        page.add_style_tag(content="#wires{display:none!important}")
        for key, size in PORTRAIT_SIZE.items():
            page.evaluate("""([k, sz]) => {
                const el = document.querySelector('.mod[data-gid="m:'+k+'"]');
                if (!el) return;
                const chip = [...el.querySelectorAll('.szchip, .sizechip, [data-sz]')]
                    .find(c => (c.textContent || c.dataset.sz || '').trim() === sz);
                if (chip) chip.click();
            }""", [key, size])
        page.wait_for_timeout(500)
        found = gids(page)
        if args.list or not (args.cards or args.one):
            for r in found:
                print(f"  {r['gid']:26} {r['size'] or '?':3}  {r['title']}")
            print(f"  ({len(found)} cards)   page errors: {errors or 'none'}")
            browser.close()
            return

        by_key = {r["gid"]: r for r in found}
        done, miss = 0, []
        for key in keys:
            fig = CARD_FIGS.get(key)
            if not fig:
                continue
            gid = next((g for g in by_key if g.endswith(key)), None)
            if gid and shoot_card(page, gid, OUT / f"{fig.lower()}.png"):
                done += 1
                print(f"  {fig}  {key:22} {by_key[gid]['size']}")
            else:
                miss.append(key)
        print(f"\n{done} card(s) written to {OUT}")
        if miss:
            print(f"MISSING: {', '.join(miss)}")
        print(f"page errors: {errors or 'none'}")
        browser.close()


if __name__ == "__main__":
    main()
