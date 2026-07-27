"""Rig fixture for tests/live_dual_mode.py — a DUAL module, alone.

Nothing is wired into Power Shaper here, which is GEN mode by definition:
`App._sync_dual_modes` derives mode from the audio graph, and an empty
in-edge means mode 0. The live check then spawns a generator and wires it
in at runtime to cross into FX mode and back.

Underscore-prefixed, so `list_patches()` keeps it out of the GUI's patch
menu — it is a test fixture, not a patch anyone would load.
"""

PATCH = {"chain": ["power_shaper"], "bindings": {}}
