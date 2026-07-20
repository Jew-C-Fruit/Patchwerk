"""synthbase — a thin, vibecoding-friendly synth base on SuperCollider + supriya.

Layers:
- module.py   the module contract (@module + @synthdef) and file loader
- engine.py   scsynth lifecycle (boot/quit, synthdef registration)
- rack.py     chains: which modules, in which order, wired with buses
- midi.py     MIDI notes/CCs -> rack params (mono voice, bindings)
- watcher.py  hot reload of module files into the running rack
- cli.py      `python -m synthbase devices|test|play`
"""

from .engine import Engine
from .harmonics import odd_harmonic_bank, power_law_coeffs, square_blend_coeffs
from .module import Module, Param, module, param
from .rack import Rack

__all__ = ["Engine", "Module", "Param", "Rack", "module", "param",
           "odd_harmonic_bank", "power_law_coeffs", "square_blend_coeffs"]
