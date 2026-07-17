"""Preset-loader checks — SynthApp._apply_patch_mods against patches/artifix.py.

    python tests/test_preset.py

No engine/audio: a stub app records the manager calls the loader makes, so we
verify the schema wiring (w-list -> w0..w5, targets -> wire calls, cfg passthru)
deterministically. Complements smoke.py, which only validates that the preset's
keys/params resolve.
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from synthbase.app import SynthApp  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


class Recorder:
    """Records (method, args, kwargs) and hands back a fake allocation id."""

    def __init__(self, calls, name):
        self.calls, self.name = calls, name

    def assign(self, key, pname, **cfg):
        self.calls.append((self.name, "assign", (key, pname), cfg))
        return f"{key}.{pname}"

    def spawn(self, **cfg):
        self.calls.append((self.name, "spawn", (), cfg))
        return "alloc"

    def wire(self, aid, slot, key, pname, gain=1.0):
        self.calls.append((self.name, "wire", (aid, slot, key, pname, gain), {}))


class StubApp:
    def __init__(self):
        self.calls = []
        self.lfos = Recorder(self.calls, "lfos")
        self.living = Recorder(self.calls, "living")
        self.allocation = Recorder(self.calls, "allocation")


def load_patch(name):
    path = REPO / "patches" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p_{name}", path)
    py = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(py)
    return py.PATCH


# A synthetic patch exercising ALL three loader sections. Kept independent of
# patches/artifix.py (whose default no longer carries lfo/allocation) so this
# still covers the full _apply_patch_mods path; smoke.py validates the real
# artifix preset separately.
SYNTH_PATCH = {
    "chain": [("artifix_gen", {})],
    "living": [{"key": "artifix_gen", "param": "morph",
                "life": 0.50, "wander": 0.30, "depth": 0.40}],
    "lfos": [{"key": "artifix_gen", "param": "detune",
              "rate": 0.15, "shape": 0, "depth": 0.45}],
    "allocations": [{"r": 1.0, "w": [0.50, 0.55, 0.50, 0.40, 0.45, 0.30],
                     "targets": [
                         {"slot": 1, "key": "artifix_gen", "param": "harm"},
                         {"slot": 2, "key": "artifix_gen", "param": "bright"},
                         {"slot": 4, "key": "artifix_gen", "param": "res"}]}],
}


def main():
    app = StubApp()
    # call the real loader with our stub as `self`
    SynthApp._apply_patch_mods(app, SYNTH_PATCH)

    calls = app.calls

    living = [c for c in calls if c[0] == "living" and c[1] == "assign"]
    check("living assign fired once", len(living) == 1, str(living))
    if living:
        (key, pname), cfg = living[0][2], living[0][3]
        check("living targets artifix_gen.morph",
              key == "artifix_gen" and pname == "morph", f"{key}.{pname}")
        check("living cfg passes life/wander/depth",
              cfg.get("life") == 0.50 and cfg.get("wander") == 0.30
              and cfg.get("depth") == 0.40, str(cfg))
        check("living cfg has no key/param leakage",
              "key" not in cfg and "param" not in cfg, str(cfg))

    lfos = [c for c in calls if c[0] == "lfos" and c[1] == "assign"]
    check("lfo assign fired once", len(lfos) == 1, str(lfos))
    if lfos:
        (key, pname), cfg = lfos[0][2], lfos[0][3]
        check("lfo targets artifix_gen.detune",
              key == "artifix_gen" and pname == "detune", f"{key}.{pname}")
        check("lfo cfg passes rate/shape/depth",
              cfg.get("rate") == 0.15 and cfg.get("shape") == 0
              and cfg.get("depth") == 0.45, str(cfg))

    spawn = [c for c in calls if c[0] == "allocation" and c[1] == "spawn"]
    check("allocation spawn fired once", len(spawn) == 1, str(spawn))
    if spawn:
        cfg = spawn[0][3]
        check("alloc w-list expanded to w0..w5",
              all(f"w{i}" in cfg for i in range(6)), str(cfg))
        check("alloc w0/w1 match the preset list",
              cfg.get("w0") == 0.50 and cfg.get("w1") == 0.55, str(cfg))
        check("alloc r passed through", cfg.get("r") == 1.0, str(cfg))
        check("alloc spawn got no raw 'w' or 'targets' kwargs",
              "w" not in cfg and "targets" not in cfg, str(cfg))

    wires = [c for c in calls if c[0] == "allocation" and c[1] == "wire"]
    slots = sorted(c[2][1] for c in wires)
    check("allocation wired 3 dims", len(wires) == 3, str(wires))
    check("alloc wired slots are 1,2,4", slots == [1, 2, 4], str(slots))
    params = {c[2][1]: c[2][3] for c in wires}   # slot -> param
    check("alloc slot 1 -> harm, 2 -> bright, 4 -> res",
          params.get(1) == "harm" and params.get(2) == "bright"
          and params.get(4) == "res", str(params))

    # order matters: spawn must precede its wires (tap adds after the node)
    order = [c[1] for c in calls if c[0] == "allocation"]
    check("spawn precedes wire calls",
          order and order[0] == "spawn" and set(order[1:]) == {"wire"},
          str(order))

    # a malformed entry must be skipped, not abort the whole load
    app2 = StubApp()
    bad = {"chain": [], "living": [{"key": "nope"}],  # missing 'param' -> KeyError
           "lfos": [{"key": "artifix_gen", "param": "detune", "rate": 0.2}]}
    SynthApp._apply_patch_mods(app2, bad)
    check("bad living entry skipped, later lfo still applied",
          any(c[0] == "lfos" for c in app2.calls)
          and not any(c[0] == "living" for c in app2.calls), str(app2.calls))

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
