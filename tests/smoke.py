"""Smoke test that runs anywhere (no audio hardware, no server boot).

    python tests/smoke.py

Verifies: every module file loads, synthdefs compile, params are sane,
patches parse, and control scaling behaves.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.module import load_all_modules  # noqa: E402
from synthbase.midi import midi_to_freq  # noqa: E402


def main() -> int:
    failures = 0

    registry, errors = load_all_modules(REPO / "modules")
    for fname, exc in errors.items():
        print(f"FAIL  {fname}: {exc!r}")
        failures += 1
    for key, mod in sorted(registry.items()):
        assert mod.kind in ("source", "effect")
        for pname, p in mod.params.items():
            assert p.minimum <= p.default <= p.maximum, f"{key}.{pname} default out of range"
            mid = p.from_unit(0.5)
            assert p.minimum <= mid <= p.maximum, f"{key}.{pname} scaling broken"
        print(f"ok    {key} ({mod.kind}, {len(mod.params)} params)")

    # Patches parse and reference known modules.
    import importlib.util

    def bad_target(kind, key, pname, owner):
        """Return 1 (and print) if key.param can't resolve; else 0. Keys may be
        instance ids ("lowpass.2") — the registry is keyed by module type."""
        t = key.split(".")[0]
        if t not in registry:
            print(f"FAIL  {owner}: {kind} -> unknown module {key!r}")
            return 1
        if pname not in registry[t].params:
            print(f"FAIL  {owner}: {kind} -> {key}.{pname} (no such param)")
            return 1
        return 0

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
            elif i == 0 and registry[key].kind != "source":
                print(f"FAIL  {patch_path.name}: chain must start with a source")
                failures += 1
        for cc, (key, pname) in (patch.get("bindings", {}).get("cc") or {}).items():
            if key in registry and pname not in registry[key].params:
                print(f"FAIL  {patch_path.name}: CC {cc} -> {key}.{pname} (no such param)")
                failures += 1
        # Artifix preset sections (optional): every key/param must resolve, and
        # allocation slots must be a real dim (0..5).
        for spec in (patch.get("lfos") or []):
            failures += bad_target("lfo", spec["key"], spec["param"], patch_path.name)
        for spec in (patch.get("living") or []):
            failures += bad_target("living", spec["key"], spec["param"], patch_path.name)
        for spec in (patch.get("allocations") or []):
            for t in (spec.get("targets") or []):
                slot = int(t["slot"])
                if not 0 <= slot <= 5:
                    print(f"FAIL  {patch_path.name}: alloc slot {slot} out of 0..5")
                    failures += 1
                failures += bad_target("alloc", t["key"], t["param"], patch_path.name)
        print(f"ok    patch {patch_path.name}")

    assert abs(midi_to_freq(69) - 440.0) < 1e-9
    assert abs(midi_to_freq(60) - 261.6255653) < 1e-3
    print("ok    midi_to_freq")

    # v6: the key shifter imports and its nearest-key mapping is sane
    from synthbase.keyshift import KeyShifter, nearest_offset  # noqa: F401
    assert nearest_offset(0) == 0 and nearest_offset(7) == -5
    assert all(abs(nearest_offset(k)) <= 6 for k in range(12))
    print("ok    synthbase.keyshift")

    # Artifix: the modulator synthdefs (not modules/, so checked explicitly)
    from synthbase.lfo import _lfo  # noqa: F401
    from synthbase.living import _living  # noqa: F401
    from synthbase.allocation import _alloc, _alloc_tap  # noqa: F401
    for sd in (_lfo, _living, _alloc, _alloc_tap):
        assert sd.effective_name, "modulator synthdef failed to compile"
    print("ok    modulator synthdefs (lfo, living, alloc)")

    print(f"\n{'PASS' if not failures else 'FAIL'} — {len(registry)} modules, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
