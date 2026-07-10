"""Rack: instantiates modules in order and wires them together with buses.

This is the layer that answers "which modules, in which order". A chain
spec is plain data (see patches/), so a future GUI edits data — not code.

Conventions (see CLAUDE.md):
- Audio is stereo (2 channels) between stages.
- Effect synthdefs declare ``in_bus`` and ``out`` params; sources just ``out``.
- Stage N's output bus feeds stage N+1's input; the last stage outs to
  hardware bus 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from supriya import AddAction, CalculationRate, synthdef
from supriya.ugens import In, Out

from .engine import Engine
from .module import Module


@synthdef()
def _bypass(in_bus=0, out=0):
    """True bypass for disabled effects: copy input to output unchanged."""
    Out.ar(bus=out, source=In.ar(bus=in_bus, channel_count=2))

ChainSpec = list  # list of str | (str, dict) — normalized by Rack.build


@dataclass
class Instance:
    """A running module: its node on the server plus current settings."""

    key: str
    module: Module
    settings: dict[str, Any]
    node: Any = None
    bus_group: Any = None  # audio bus group feeding the *next* stage (None for last)
    enabled: bool = True
    service: bool = False  # side-instance (drone, LFO) rather than a chain stage

    @property
    def display(self) -> str:
        return f"{self.module.name} ({self.key})"


class Rack:
    def __init__(self, engine: Engine, registry: dict[str, Module]) -> None:
        self.engine = engine
        self.registry = registry
        self.instances: list[Instance] = []
        self.mapped: set[tuple[str, str]] = set()   # (key, param) driven by LFOs
        self.on_node_replaced = None                 # callback(key) after respawn/re-enable

    # -- building ------------------------------------------------------------

    @staticmethod
    def _normalize(chain_spec: ChainSpec) -> list[tuple[str, dict]]:
        normalized = []
        for entry in chain_spec:
            if isinstance(entry, str):
                normalized.append((entry, {}))
            else:
                key, settings = entry
                normalized.append((key, dict(settings)))
        return normalized

    def build(self, chain_spec: ChainSpec) -> None:
        """Instantiate the chain in order. Call once on a fresh rack."""
        assert self.engine.server is not None, "engine not booted"
        assert not self.instances, "rack already built"
        server = self.engine.server

        stages = self._normalize(chain_spec)
        if not stages:
            return

        # Register every synthdef used (plus the bypass), then allocate
        # buses and nodes in order.
        server.add_synthdefs(_bypass)
        self.engine.register(*(self._lookup(key) for key, _ in stages))

        prev_bus_group = None
        for index, (key, overrides) in enumerate(stages):
            mod = self._lookup(key)
            is_last = index == len(stages) - 1

            if mod.kind == "effect" and prev_bus_group is None:
                raise ValueError(
                    f"chain starts with effect {key!r}; first module must be a source"
                )

            settings = {name: p.default for name, p in mod.params.items()}
            settings.update(overrides)

            bus_group = None
            if not is_last:
                bus_group = server.add_bus_group(
                    calculation_rate=CalculationRate.AUDIO, count=2
                )
            settings["out"] = 0 if is_last else int(bus_group)
            if mod.kind == "effect":
                settings["in_bus"] = int(prev_bus_group)

            node = server.add_synth(
                mod.synthdef,
                add_action=AddAction.ADD_TO_TAIL,
                target_node=self.engine.root_group,
                **settings,
            )
            self.instances.append(
                Instance(key=key, module=mod, settings=settings, node=node, bus_group=bus_group)
            )
            prev_bus_group = bus_group

    def teardown(self) -> None:
        for inst in self.instances:
            if inst.node is not None:
                inst.node.free()
            if inst.bus_group is not None:
                inst.bus_group.free()
        self.instances = []

    # -- service sources (drone, future LFO modules) -----------------------------

    def add_service_source(self, module: Module, overrides: dict | None = None) -> Instance:
        """Add an extra source alongside the chain's head, writing into the
        same bus as the first source so it rides the whole effect chain."""
        assert self.engine.server is not None and self.instances, "rack not built"
        if module.kind != "source":
            raise ValueError("service instances must be sources")
        first = self.instances[0]
        settings = {name: p.default for name, p in module.params.items()}
        settings.update(overrides or {})
        settings["out"] = first.settings["out"]
        self.engine.register(module)
        node = self.engine.server.add_synth(
            module.synthdef,
            add_action=AddAction.ADD_TO_HEAD,
            target_node=self.engine.root_group,
            **settings,
        )
        inst = Instance(
            key=module.key, module=module, settings=settings, node=node, service=True
        )
        self.instances.append(inst)
        self.registry[module.key] = module
        return inst

    def remove_instance(self, key: str) -> None:
        inst = self.find(key)
        if inst.node is not None:
            inst.node.free()
        if inst.bus_group is not None:
            inst.bus_group.free()
        self.instances.remove(inst)

    # -- runtime control -------------------------------------------------------

    def find(self, key: str) -> Instance:
        for inst in self.instances:
            if inst.key == key:
                return inst
        raise KeyError(f"no instance of {key!r} in rack")

    def set_param(self, key: str, name: str, value: float) -> None:
        inst = self.find(key)
        inst.settings[name] = value
        if (key, name) in self.mapped:
            return  # LFO drives this param; value is stored for later restore
        if inst.enabled or inst.module.kind == "source":  # paused sources accept sets
            inst.node.set(**{name: value})

    def set_params(self, key: str, **values: float) -> None:
        inst = self.find(key)
        inst.settings.update(values)
        live = {k: v for k, v in values.items() if (key, k) not in self.mapped}
        if live and (inst.enabled or inst.module.kind == "source"):
            inst.node.set(**live)

    def set_enabled(self, key: str, enabled: bool) -> None:
        """Toggle a module in the running chain.

        Sources pause/unpause (silence, state kept). Effects are swapped
        with a passthrough synth so the rest of the chain keeps flowing —
        a true bypass, not a mute.
        """
        inst = self.find(key)
        enabled = bool(enabled)
        if inst.enabled == enabled:
            return
        server = self.engine.server
        if inst.module.kind == "source":
            (inst.node.unpause if enabled else inst.node.pause)()
        elif enabled:
            inst.node = server.add_synth(
                inst.module.synthdef,
                add_action=AddAction.REPLACE,
                target_node=inst.node,
                **inst.settings,
            )
        else:
            inst.node = server.add_synth(
                _bypass,
                add_action=AddAction.REPLACE,
                target_node=inst.node,
                in_bus=inst.settings["in_bus"],
                out=inst.settings["out"],
            )
        inst.enabled = enabled
        if enabled and self.on_node_replaced:
            try:
                self.on_node_replaced(key)
            except Exception:  # noqa: BLE001
                pass

    # -- hot reload -------------------------------------------------------------

    def respawn(self, new_module: Module) -> bool:
        """Replace the running node(s) of a module with a new definition,
        in place (same position in the chain), keeping current settings.

        Returns True if anything was replaced.
        """
        assert self.engine.server is not None
        server = self.engine.server
        replaced = False
        for inst in self.instances:
            if inst.key != new_module.key:
                continue
            # Merge: keep live settings, adopt defaults for any new params.
            settings = {name: p.default for name, p in new_module.params.items()}
            settings.update(inst.settings)
            if not inst.enabled and inst.module.kind == "effect":
                # Node is currently a passthrough; the new definition takes
                # over when the module is re-enabled.
                inst.module = new_module
                inst.settings = settings
                replaced = True
                continue
            new_node = server.add_synth(
                new_module.synthdef,
                add_action=AddAction.REPLACE,
                target_node=inst.node,
                **settings,
            )
            if not inst.enabled:  # disabled source: keep it silent
                new_node.pause()
            inst.module = new_module
            inst.node = new_node
            inst.settings = settings
            replaced = True
            if self.on_node_replaced:
                try:
                    self.on_node_replaced(inst.key)
                except Exception:  # noqa: BLE001
                    pass
        if replaced:
            self.registry[new_module.key] = new_module
        return replaced

    # -- helpers ---------------------------------------------------------------

    def _lookup(self, key: str) -> Module:
        try:
            return self.registry[key]
        except KeyError:
            known = ", ".join(sorted(self.registry)) or "(none loaded)"
            raise KeyError(f"unknown module {key!r}; loaded modules: {known}") from None
