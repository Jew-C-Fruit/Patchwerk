"""Smoke test that runs anywhere (no audio hardware, no server boot).

    python tests/smoke.py

Verifies: every module file loads, synthdefs compile, params are sane,
patches parse, control scaling behaves, and every test suite is actually
wired into CI.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.module import KINDS, load_all_modules  # noqa: E402
from synthbase.midi import midi_to_freq  # noqa: E402


def main() -> int:
    failures = 0

    registry, errors = load_all_modules(REPO / "modules")
    for fname, exc in errors.items():
        print(f"FAIL  {fname}: {exc!r}")
        failures += 1
    for key, mod in sorted(registry.items()):
        assert mod.kind in KINDS
        # kind predicates agree with the kind (item 11's dual is BOTH)
        assert mod.generates == (mod.kind in ("source", "dual"))
        assert mod.takes_audio_in == (mod.kind in ("effect", "dual"))
        # anything that reads audio must actually declare an in_bus, and a
        # plain source must not — the contract the dual kind widens, not drops
        assert ("in_bus" in mod.synthdef.parameters) == mod.takes_audio_in, \
            f"{key}: in_bus presence disagrees with kind {mod.kind!r}"
        for pname, p in mod.params.items():
            assert p.minimum <= p.default <= p.maximum, f"{key}.{pname} default out of range"
            mid = p.from_unit(0.5)
            assert p.minimum <= mid <= p.maximum, f"{key}.{pname} scaling broken"
        print(f"ok    {key} ({mod.kind}, {len(mod.params)} params)")

    # Patches parse and reference known modules.
    import importlib.util

    for patch_path in sorted((REPO / "patches").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"p_{patch_path.stem}", patch_path)
        py = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(py)
        patch = py.PATCH
        chain = [(e, {}) if isinstance(e, str) else e for e in patch["chain"]]
        for i, (key, _) in enumerate(chain):
            if key not in registry:
                print(f"FAIL  {patch_path.name}: unknown module {key!r}")
                failures += 1
            elif i == 0 and not registry[key].generates:
                print(f"FAIL  {patch_path.name}: chain must start with a source")
                failures += 1
        for cc, (key, pname) in (patch.get("bindings", {}).get("cc") or {}).items():
            if key in registry and pname not in registry[key].params:
                print(f"FAIL  {patch_path.name}: CC {cc} -> {key}.{pname} (no such param)")
                failures += 1
        print(f"ok    patch {patch_path.name}")

    assert abs(midi_to_freq(69) - 440.0) < 1e-9
    assert abs(midi_to_freq(60) - 261.6255653) < 1e-3
    print("ok    midi_to_freq")

    # v6: the key shifter imports and its nearest-key mapping is sane
    from synthbase.keyshift import KeyShifter, nearest_offset  # noqa: F401
    assert nearest_offset(0) == 0 and nearest_offset(7) == -5
    assert all(abs(nearest_offset(k)) <= 6 for k in range(12))
    print("ok    synthbase.keyshift")

    # Every test suite is WIRED INTO CI. A suite that exists, passes
    # locally and is referenced by no job is the same failure mode as an
    # indicator with no tap: it looks covered and is not. test_power_sine
    # sat unreferenced exactly that way, and nothing could have noticed —
    # so the check is for the CLASS, not for that one file.
    #
    # A suite that genuinely cannot run headless declares WHY in its own
    # source (`CI_EXEMPT = "reason"`). The exemption travels with the
    # file, so deleting the suite deletes its excuse and re-wiring it
    # means deleting the marker — neither can rot into a stale allowlist
    # maintained over here.
    #
    # Scope is deliberately `test_*.py`: that prefix is the repo's
    # convention for "a suite that should run". The gui_check*/check_*
    # scripts are NOT swept, because gui_check{,6,7} are deliberately
    # unmaintained snapshots (CLAUDE.md) and sweeping them would mean
    # three exemption markers to say one thing.
    ci_yml = REPO / ".github/workflows/ci.yml"
    ci = ci_yml.read_text() if ci_yml.exists() else ""
    if not ci:
        print(f"FAIL  {ci_yml} missing — cannot verify CI wiring")
        failures += 1
    for suite in sorted((REPO / "tests").glob("test_*.py")):
        why = re.search(r"^CI_EXEMPT\s*=\s*['\"](.+?)['\"]",
                        suite.read_text(), re.M)
        wired = suite.name in ci
        if wired and why:
            print(f"FAIL  {suite.name}: runs in CI but declares CI_EXEMPT "
                  f"({why.group(1)}) — drop one or the other")
            failures += 1
        elif wired:
            print(f"ok    {suite.name} wired into CI")
        elif why:
            print(f"ok    {suite.name} exempt from CI — {why.group(1)}")
        else:
            print(f"FAIL  {suite.name}: no CI job runs it and it declares no "
                  f"CI_EXEMPT reason — add it to {ci_yml.name}, or say why not")
            failures += 1

    print(f"\n{'PASS' if not failures else 'FAIL'} — {len(registry)} modules, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
