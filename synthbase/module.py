"""Module contract for synthbase.

A *module* is one Python file in ``modules/`` defining one or more Module
objects: a compiled SynthDef (the DSP recipe) plus the metadata that the
rack, bindings, and future GUI layers need (display name, kind, params).

Vibecoding a new module means copying an existing file in ``modules/`` and
changing the DSP body + param table. See CLAUDE.md for the house rules.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

from supriya import SynthDef


@dataclass(frozen=True)
class Param:
    """A controllable parameter: range, default, and scaling curve."""

    minimum: float
    maximum: float
    default: float
    curve: str = "lin"  # "lin", "exp", "toggle", or "select" (labeled options)
    options: tuple = ()  # labels for curve="select"; value = option index

    def from_unit(self, value: float) -> float:
        """Map a normalized 0..1 control value (MIDI CC, sensor, GUI slider)
        onto this parameter's range using its curve."""
        value = min(1.0, max(0.0, value))
        if self.curve == "toggle":
            return self.maximum if value >= 0.5 else self.minimum
        if self.curve == "select":
            n = max(1, len(self.options))
            return float(min(n - 1, int(value * n)))
        if self.curve == "exp":
            lo = max(self.minimum, 1e-6)
            return lo * (self.maximum / lo) ** value
        return self.minimum + value * (self.maximum - self.minimum)


def param(minimum: float, maximum: float, default: float, curve: str = "lin",
          options=()) -> Param:
    """Shorthand constructor used in module files."""
    if options:
        curve = "select"
        minimum, maximum = 0.0, float(len(options) - 1)
    return Param(minimum, maximum, default, curve, tuple(options))


@dataclass
class Module:
    """A DSP recipe plus metadata. Produced by the @module decorator."""

    name: str  # human-facing display name
    kind: str  # "source" (generates audio) or "effect" (processes audio in -> out)
    synthdef: SynthDef
    params: dict[str, Param] = field(default_factory=dict)
    source_file: str = ""

    @property
    def key(self) -> str:
        """Stable identifier == the synthdef (function) name."""
        return self.synthdef.effective_name


def module(*, name: str, kind: str, params: dict[str, Param] | None = None):
    """Decorator stacked on top of supriya's @synthdef().

    Usage::

        @module(name="Wobble Saw", kind="source", params={...})
        @synthdef()
        def wobble_saw(freq=220, amp=0.3, gate=1, out=0):
            ...
    """
    if kind not in ("source", "effect"):
        raise ValueError(f"kind must be 'source' or 'effect', got {kind!r}")

    def wrap(synthdef_obj: SynthDef) -> Module:
        if not isinstance(synthdef_obj, SynthDef):
            raise TypeError(
                "@module must wrap a compiled SynthDef — put @synthdef() "
                "directly below @module(...)"
            )
        return Module(name=name, kind=kind, synthdef=synthdef_obj, params=params or {})

    return wrap


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------

def load_module_file(path: Path) -> list[Module]:
    """Execute one module file and return the Module objects it defines.

    Raises on any error in the file — callers catch and report, so a broken
    module never takes anything else down.
    """
    path = Path(path)
    modname = f"synthmodules.{path.stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    py_module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = py_module
    try:
        spec.loader.exec_module(py_module)
    except Exception:
        sys.modules.pop(modname, None)
        raise
    found = [obj for obj in vars(py_module).values() if isinstance(obj, Module)]
    if not found:
        raise ValueError(f"{path.name} defines no Module (missing @module decorator?)")
    for mod in found:
        mod.source_file = str(path)
    return found


def load_all_modules(directory: Path) -> tuple[dict[str, Module], dict[str, Exception]]:
    """Load every module file in a directory.

    Returns (registry keyed by module key, errors keyed by filename).
    Broken files are reported, not fatal.
    """
    registry: dict[str, Module] = {}
    errors: dict[str, Exception] = {}
    for path in sorted(Path(directory).glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            for mod in load_module_file(path):
                registry[mod.key] = mod
        except Exception as exc:  # noqa: BLE001 — crash isolation is the point
            errors[path.name] = exc
    return registry, errors
