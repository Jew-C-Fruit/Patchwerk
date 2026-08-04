#!/usr/bin/env python3
"""Every module that CLAIMS to be note-playable must be REACHABLE by a voice.

The gap this closes, in one sentence: item 11's Power Shaper shipped through
21 green suites as a module that could never make a sound. Its DSP was
correct and every existing test agreed with itself — but `App`'s four
playability predicates all read `kind == "source"`, a `dual` is neither
`source` nor `effect`, so no voice would ever aim at it, its `gate` stayed 0
and the envelope never opened. Nothing anywhere asserted the join between
"this module declares freq + gate" and "the app can actually drive it".

WHY THIS FILE IS STRUCTURAL, NOT A LIST
=======================================
A test that enumerated today's playable modules would have passed on the
broken tree too — `power_shaper` simply would not have been in the list, and
nobody would have noticed the omission. So the claim is DERIVED:

    a module DECLARES playability by exposing `freq` and `gate` in its
    synthdef, because those are the two controls a voice writes.

Every module making that declaration is then required to be reachable. A new
module gets checked the day it is added; a new KIND gets checked the day a
module uses it. Neither needs an edit here — that is the point. The same
derivation runs over the synthetic kind matrix in
`test_every_generating_kind_is_reachable`, so a third kind is covered before
any module adopts it.

No server: instances are duck-typed around the REAL `Module` objects loaded
from `modules/`, so the predicates under test see real kinds and real
synthdef parameter sets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402
from synthbase.module import KINDS, generates, load_all_modules  # noqa: E402
from synthbase.rack import Rack, type_of  # noqa: E402

FAILURES: list[str] = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


class FakeNode:
    def __init__(self):
        self.sets = []

    def set(self, **kw):
        self.sets.append(kw)


def fake_inst(key, mod, settings):
    return SimpleNamespace(key=key, module=mod, settings=dict(settings),
                           service=False, node=FakeNode(), enabled=True,
                           type=type_of(key), bus_group=None,
                           in_bus_group=None)


def make_app(instances):
    """A SynthApp with a duck-typed rack holding `instances`. No server."""
    app = SynthApp(use_midi=False, use_reload=False)
    rack = Rack(engine=SimpleNamespace(server=None, root_group=None),
                registry={})
    rack.instances = list(instances)
    app.rack = rack
    return app


def settings_for(mod):
    """What the rack would put in `settings` for this module.

    Param defaults, plus the `gate=0` the rack injects for anything that
    generates — that injection is why `gate` can be in settings without
    being a user-facing param, and the predicates read settings, not params.
    """
    s = {name: p.default for name, p in mod.params.items()}
    if generates(mod.kind) and "gate" in mod.synthdef.parameters:
        s.setdefault("gate", 0)
    if "freq" in mod.synthdef.parameters:
        s.setdefault("freq", 220.0)
    s["out"] = 0
    return s


def declares_playable(mod) -> bool:
    """The module's own claim: a voice writes `freq` and `gate`, so a module
    exposing both is asking to be played. Deliberately read off the SYNTHDEF
    rather than off `params` — `gate` is not a user-facing knob."""
    pars = mod.synthdef.parameters
    return "freq" in pars and "gate" in pars


# ---------------------------------------------------------------------------

def test_every_declared_playable_module_is_reachable():
    registry, errors = load_all_modules(REPO / "modules")
    check("modules load cleanly", not errors, str(errors))

    playable = [(k, m) for k, m in sorted(registry.items())
                if declares_playable(m)]
    check("some module declares itself playable (the sweep is not vacuous)",
          bool(playable))

    for key, mod in playable:
        inst = fake_inst(key, mod, settings_for(mod))
        app = make_app([inst])
        try:
            check(f"{key} ({mod.kind}): the app's own predicate accepts it",
                  app._is_playable(inst))
            # the three reachability paths a voice actually travels
            check(f"{key} ({mod.kind}): a voice finds it by scanning the rack",
                  app._guess_voice_target() == key,
                  f"got {app._guess_voice_target()!r}")
            try:
                app.set_voice_target(key)
                aimed = app.voices.get("voice")
                check(f"{key} ({mod.kind}): set_voice_target aims a voice at it",
                      aimed is not None and aimed.target_key == key,
                      f"target={getattr(aimed, 'target_key', None)!r}")
            except Exception as exc:  # noqa: BLE001
                check(f"{key} ({mod.kind}): set_voice_target aims a voice at it",
                      False, repr(exc))
        finally:
            app.transport.shutdown()


def test_a_module_that_does_not_declare_playability_is_not_aimed_at():
    """The converse, so the sweep cannot pass by calling everything playable."""
    registry, _ = load_all_modules(REPO / "modules")
    quiet = [(k, m) for k, m in sorted(registry.items())
             if not declares_playable(m)]
    check("some module does NOT declare playability", bool(quiet))
    for key, mod in quiet[:6]:
        inst = fake_inst(key, mod, settings_for(mod))
        app = make_app([inst])
        try:
            check(f"{key} ({mod.kind}): correctly NOT treated as playable",
                  not app._is_playable(inst))
            check(f"{key} ({mod.kind}): no voice is guessed onto it",
                  app._guess_voice_target() is None,
                  f"got {app._guess_voice_target()!r}")
        finally:
            app.transport.shutdown()


def test_every_generating_kind_is_reachable():
    """The kind matrix, independent of which kinds modules happen to use.

    This is what catches a NEW kind. `KINDS` is the enum; anything
    `generates()` says produces audio must be playable once it exposes
    freq + gate, and anything that does not must not be. A kind added to
    `KINDS` and wired into `generates()` but forgotten in the playability
    predicate fails here on the day it is added — with no module adopting it
    and no edit to this file.
    """
    registry, _ = load_all_modules(REPO / "modules")
    donor = next(m for m in registry.values() if declares_playable(m))
    # A deliberate TRIPWIRE, not an invariant: adding a kind should bring you
    # here, because this file is where playability is decided. Update the set,
    # then make sure the per-kind loop below still passes for the new one.
    check("kind roster unchanged — a new kind must be considered here",
          set(KINDS) == {"source", "effect", "dual"},
          f"{sorted(KINDS)} — if the new kind generates, it must be playable")
    for kind in KINDS:
        mod = SimpleNamespace(kind=kind, name=f"synthetic {kind}",
                              params=donor.params, synthdef=donor.synthdef)
        inst = fake_inst(f"synth_{kind}", mod, settings_for(donor))
        app = make_app([inst])
        try:
            want = generates(kind)
            check(f"kind {kind!r}: playable == generates({kind!r}) == {want}",
                  app._is_playable(inst) is want)
            check(f"kind {kind!r}: rack scan agrees",
                  (app._guess_voice_target() == f"synth_{kind}") is want,
                  f"got {app._guess_voice_target()!r}")
        finally:
            app.transport.shutdown()


def test_a_dual_is_reachable_even_though_it_also_takes_audio_in():
    """The specific regression, stated as itself.

    A dual is a generator that ALSO takes audio in. The bug was reading
    "takes audio in" as "is an effect, therefore not playable". Assert both
    halves hold at once, so a future simplification that collapses the two
    questions back into one is caught here.
    """
    registry, _ = load_all_modules(REPO / "modules")
    duals = [(k, m) for k, m in registry.items() if m.kind == "dual"]
    if not duals:
        print("note  no dual module in the registry — kind matrix still covers it")
        return
    from synthbase.module import takes_audio_in
    for key, mod in duals:
        inst = fake_inst(key, mod, settings_for(mod))
        app = make_app([inst])
        try:
            check(f"{key}: takes audio in (it is not a plain source)",
                  takes_audio_in(mod.kind))
            check(f"{key}: AND is note-playable (the bug said otherwise)",
                  app._is_playable(inst))
            check(f"{key}: a voice reaches it",
                  app._guess_voice_target() == key)
        finally:
            app.transport.shutdown()


def main():
    test_every_declared_playable_module_is_reachable()
    test_a_module_that_does_not_declare_playability_is_not_aimed_at()
    test_every_generating_kind_is_reachable()
    test_a_dual_is_reachable_even_though_it_also_takes_audio_in()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
