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


def type_of(iid: str) -> str:
    """Module type of an instance id: "lowpass.2" -> "lowpass"."""
    return str(iid).split(".", 1)[0]


def alloc_id(type_key: str, existing) -> str:
    """First free instance id for a type: "lowpass", then "lowpass.2", ..."""
    existing = set(existing)
    if type_key not in existing:
        return type_key
    n = 2
    while f"{type_key}.{n}" in existing:
        n += 1
    return f"{type_key}.{n}"


@dataclass
class Instance:
    """A running module: its node on the server plus current settings.

    v5: `key` is a UNIQUE instance id ("lowpass", "lowpass.2", ...);
    `type` is the module key (synthdef name) the registry is keyed by.
    """

    key: str
    module: Module
    settings: dict[str, Any]
    node: Any = None
    bus_group: Any = None  # audio bus group feeding the *next* stage (None for last)
    enabled: bool = True
    service: bool = False  # side-instance (drone, LFO) rather than a chain stage
    type: str = ""         # module key; defaults to type_of(key)

    def __post_init__(self) -> None:
        if not self.type:
            self.type = type_of(self.key)

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
        self._tail_router = None                     # bus->hardware bypass when the chain ends on a summed source
        self._null_bus = None                        # persistent silent bus: "disconnected" outputs park here

    # -- building ------------------------------------------------------------

    @staticmethod
    def _normalize(chain_spec: ChainSpec) -> list[tuple[str, dict]]:
        normalized = []
        seen: set[str] = set()
        for entry in chain_spec:
            if isinstance(entry, str):
                key, settings = entry, {}
            else:
                key, settings = entry
            # duplicates in a plain patch spec auto-suffix into fresh ids
            if key in seen:
                key = alloc_id(type_of(key), seen)
            seen.add(key)
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
        need_tail_router = False
        for index, (key, overrides) in enumerate(stages):
            mod = self._lookup(key)
            is_last = index == len(stages) - 1

            if mod.kind == "effect" and prev_bus_group is None:
                raise ValueError(
                    f"chain starts with effect {key!r}; first module must be a source"
                )

            settings = {name: p.default for name, p in mod.params.items()}
            settings.update(overrides)
            if (mod.kind == "source" and "gate" not in settings
                    and "gate" in mod.synthdef.parameters):
                # playable sources start SILENT — the synthdef default gate=1
                # otherwise drones at default freq after every (re)build
                settings["gate"] = 0

            bus_group = None  # bus this stage OWNS (feeds the next stage)
            if mod.kind == "source" and prev_bus_group is not None:
                # Extra source mid-chain (e.g. audio_in alongside a signal
                # gen): SUM into the running bus — a fresh bus here would
                # orphan everything upstream (the "generators go dead" bug).
                settings["out"] = int(prev_bus_group)
                need_tail_router = is_last  # summed bus still needs a reader
            elif is_last:
                settings["out"] = 0
            else:
                bus_group = server.add_bus_group(
                    calculation_rate=CalculationRate.AUDIO, count=2
                )
                settings["out"] = int(bus_group)
            if mod.kind == "effect":
                settings["in_bus"] = int(prev_bus_group)

            node = server.add_synth(
                mod.synthdef,
                add_action=AddAction.ADD_TO_TAIL,
                target_node=self.engine.root_group,
                **settings,
            )
            self.instances.append(
                Instance(key=key, module=mod, settings=settings, node=node,
                         bus_group=bus_group, type=mod.key)
            )
            if bus_group is not None:
                prev_bus_group = bus_group

        if need_tail_router:
            # all-source chain (or chain ending on a summed source): route the
            # shared bus to hardware
            self._tail_router = server.add_synth(
                _bypass,
                add_action=AddAction.ADD_TO_TAIL,
                target_node=self.engine.root_group,
                in_bus=int(prev_bus_group),
                out=0,
            )

    def teardown(self) -> None:
        if self._null_bus is not None:
            try:
                self._null_bus.free()
            except Exception:  # noqa: BLE001
                pass
            self._null_bus = None
        if self._tail_router is not None:
            try:
                self._tail_router.free()
            except Exception:  # noqa: BLE001
                pass
            self._tail_router = None
        for inst in self.instances:
            if inst.node is not None:
                inst.node.free()
            if inst.bus_group is not None:
                inst.bus_group.free()
        self.instances = []

    # -- service sources (drone, future LFO modules) -----------------------------

    def add_service_source(self, module: Module, overrides: dict | None = None,
                           iid: str | None = None) -> Instance:
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
        iid = iid or self.alloc_id(module.key)
        inst = Instance(
            key=iid, module=module, settings=settings, node=node, service=True,
            type=module.key,
        )
        self.instances.append(inst)
        self.registry[module.key] = module
        return inst

    def alloc_id(self, type_key: str) -> str:
        return alloc_id(type_key, (i.key for i in self.instances))

    def remove_instance(self, key: str) -> None:
        inst = self.find(key)
        if inst.node is not None:
            inst.node.free()
        if inst.bus_group is not None:
            inst.bus_group.free()
        self.instances.remove(inst)

    # -- incremental chain edits (in-place, no whole-rack rebuild) ----------------

    def add_module(self, key: str) -> Instance:
        """Spawn ONE module in place, parked on the null bus (connected to
        nothing), without touching any running module. `key` is an instance id;
        its type is resolved via the registry. Sources start silent (gate=0);
        effects get a PRIVATE in_bus so a later detach can free it safely.
        Wiring is applied afterwards through graph_wire/audio_rewire, exactly
        like every other live edit."""
        assert self.engine.server is not None, "engine not booted"
        mod = self._lookup(key)
        self.engine.register(mod)
        settings = {name: p.default for name, p in mod.params.items()}
        if (mod.kind == "source" and "gate" not in settings
                and "gate" in mod.synthdef.parameters):
            settings["gate"] = 0
        owned = None
        if mod.kind == "effect":
            owned = self.engine.server.add_bus_group(
                calculation_rate=CalculationRate.AUDIO, count=2
            )
            settings["in_bus"] = int(owned)
        settings["out"] = self.null_bus()          # parked until wired
        node = self.engine.server.add_synth(
            mod.synthdef,
            add_action=AddAction.ADD_TO_TAIL,
            target_node=self.engine.root_group,
            **settings,
        )
        inst = Instance(key=key, module=mod, settings=settings, node=node,
                        bus_group=owned, type=mod.key)
        self.instances.append(inst)
        return inst

    def detach_instance(self, key: str) -> None:
        """Free a single module's node in place. Free its owned bus group ONLY
        if no surviving instance still reads/writes that bus number — the safety
        valve for linear-built chains where an effect's in_bus is a neighbour's
        owned out-bus. Worst case is a small bounded leak (bus kept alive with no
        owner), reclaimed at the next full teardown; never a dangling read."""
        inst = self.find(key)
        self.instances.remove(inst)
        if inst.node is not None:
            inst.node.free()
        if inst.bus_group is not None:
            bus = int(inst.bus_group)
            still_used = any(
                int(i.settings.get("in_bus", -1)) == bus
                or int(i.settings.get("out", -1)) == bus
                for i in self.instances
            )
            if not still_used:
                try:
                    inst.bus_group.free()
                except Exception:  # noqa: BLE001
                    pass

    # -- runtime control -------------------------------------------------------

    def find(self, key: str) -> Instance:
        """Look up an instance by id. Legacy compat: a bare TYPE key resolves
        to the FIRST instance of that type (old clients are type-keyed)."""
        for inst in self.instances:
            if inst.key == key:
                return inst
        for inst in self.instances:
            if inst.type == key:
                return inst
        raise KeyError(f"no instance of {key!r} in rack")

    def set_param(self, key: str, name: str, value: float) -> None:
        inst = self.find(key)
        key = inst.key  # normalize a legacy type key to the instance id
        inst.settings[name] = value
        if (key, name) in self.mapped:
            return  # LFO drives this param; value is stored for later restore
        if inst.enabled or inst.module.kind == "source":  # paused sources accept sets
            inst.node.set(**{name: value})

    def set_params(self, key: str, **values: float) -> None:
        inst = self.find(key)
        key = inst.key
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

    # -- graph overlay: live audio rewiring WITHOUT a rebuild ---------------------
    #
    # Every effect already owns a unique stereo input bus (its predecessor's
    # bus_group), so rewiring is just: point the source node's `out` at the
    # destination's in-bus and make sure the source executes BEFORE the
    # destination on the server (supriya: node.move(target, ADD_BEFORE) →
    # /n_before). "master" means hardware bus 0. Disconnecting parks the
    # output on a persistent silent bus.

    def null_bus(self) -> int:
        """Lazy per-rack silent stereo bus for disconnected outputs."""
        if self._null_bus is None:
            assert self.engine.server is not None
            self._null_bus = self.engine.server.add_bus_group(
                calculation_rate=CalculationRate.AUDIO, count=2
            )
        return int(self._null_bus)

    def _dst_bus(self, dst_key: str) -> int:
        """Bus an audio wire INTO dst_key lands on. Effects: their in_bus.
        Sources: their own out bus (fan-in by summing). Master: hardware 0."""
        if dst_key == "master":
            return 0
        dst = self.find(dst_key)
        if dst.module.kind == "effect":
            return int(dst.settings["in_bus"])
        return int(dst.settings.get("out", 0))

    def _set_nonservice_order(self, order_keys: list[str]) -> None:
        """Rewrite self.instances so its NON-SERVICE entries follow order_keys,
        leaving service entries pinned at their current slots. Keeps the list
        authoritative for scsynth execution order (so reorder_for_wires can
        cheaply skip when order is already valid)."""
        lookup = {i.key: i for i in self.instances if not i.service}
        ordered = [lookup[k] for k in order_keys if k in lookup]
        # defensive: keep any non-service instance the caller forgot to list,
        # in its current relative position, so we never drop or StopIteration
        listed = {i.key for i in ordered}
        for i in self.instances:
            if not i.service and i.key not in listed:
                ordered.append(i)
        seq = iter(ordered)
        self.instances = [i if i.service else next(seq) for i in self.instances]

    def audio_rewire(self, src_key: str, dst_key: str) -> None:
        """Point src's audio out at dst's input bus, live, and reorder the
        src node before dst so the signal arrives within the same block."""
        src = self.find(src_key)
        bus = self._dst_bus(dst_key)
        src.settings["out"] = bus
        if src.node is None:
            return
        src.node.set(out=bus)
        try:
            if dst_key == "master":
                src.node.move(self.engine.root_group, AddAction.ADD_TO_TAIL)
            else:
                dst = self.find(dst_key)
                if dst.node is not None:
                    src.node.move(dst.node, AddAction.ADD_BEFORE)
        except Exception:  # noqa: BLE001 — a failed reorder still leaves audio flowing
            pass
        # keep the instance list in step with the single node we just moved,
        # so the list stays a faithful mirror of scsynth order
        order = [i.key for i in self.instances
                 if not i.service and i.key != src.key]
        if dst_key != "master" and dst_key in order:
            order.insert(order.index(dst_key), src.key)
        else:
            order.append(src.key)   # master (tail) or dangling dst
        self._set_nonservice_order(order)

    def audio_disconnect(self, src_key: str) -> None:
        """Silence src's output by parking it on the rack's null bus."""
        src = self.find(src_key)
        bus = self.null_bus()
        src.settings["out"] = bus
        if src.node is not None:
            src.node.set(out=bus)

    def audio_wires(self) -> list[dict]:
        """Derive current audio wiring from settings: map each effect's
        in_bus back to a key; bus 0 (or a tail-routed bus) is master."""
        in_map = {}
        for inst in self.instances:
            if inst.service or inst.module.kind != "effect":
                continue
            if "in_bus" in inst.settings:
                in_map[int(inst.settings["in_bus"])] = inst.key
        null = int(self._null_bus) if self._null_bus is not None else None
        out = []
        for inst in self.instances:
            if inst.service:
                continue
            bus = int(inst.settings.get("out", 0))
            if null is not None and bus == null:
                continue  # disconnected
            if bus in in_map:
                out.append({"from": inst.key, "to": in_map[bus]})
            else:
                # bus 0 = hardware; an unmapped bus is the all-source chain's
                # summed bus, which the tail router forwards to hardware
                out.append({"from": inst.key, "to": "master"})
        return out

    def reorder_for_wires(self, wires: list[dict]) -> None:
        """Ensure every wire's src executes before its dst. CHEAP PATH FIRST:
        if the current (authoritative) instance order already satisfies every
        wire, do nothing — no scsynth node moves at all. Only when a wire is
        violated do we topological-sort and move nodes. Services (drone/LFO
        writers at the head) are left alone."""
        keys = [i.key for i in self.instances if not i.service]
        pos = {k: n for n, k in enumerate(keys)}
        kset = set(keys)

        # Fast check: is the current order already valid? (the common case —
        # wires are added consistent with existing order, so ZERO server ops)
        edges = [(w.get("from"), w.get("to")) for w in wires]
        edges = [(a, b) for a, b in edges if a in kset and b in kset]
        if all(pos[a] < pos[b] for a, b in edges):
            self._move_tail_router()
            return

        indeg = {k: 0 for k in keys}
        adj = {k: [] for k in keys}
        for a, b in edges:
            adj[a].append(b)
            indeg[b] += 1
        ready = [k for k in keys if indeg[k] == 0]
        order = []
        while ready:
            k = ready.pop(0)
            order.append(k)
            for b in adj[k]:
                indeg[b] -= 1
                if indeg[b] == 0:
                    ready.append(b)
        if len(order) != len(keys):
            return  # cycle in the wire list — refuse to reorder
        for k in order:
            inst = self.find(k)
            if inst.node is None:
                continue
            try:
                inst.node.move(self.engine.root_group, AddAction.ADD_TO_TAIL)
            except Exception:  # noqa: BLE001
                pass
        self._set_nonservice_order(order)   # keep the list authoritative
        self._move_tail_router()

    def _move_tail_router(self) -> None:
        if self._tail_router is not None:
            try:
                self._tail_router.move(self.engine.root_group, AddAction.ADD_TO_TAIL)
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
            if inst.type != new_module.key:   # hot reload matches by TYPE
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
        """Registry lookup for an instance id OR a bare type key."""
        try:
            return self.registry[type_of(key)]
        except KeyError:
            known = ", ".join(sorted(self.registry)) or "(none loaded)"
            raise KeyError(f"unknown module {key!r}; loaded modules: {known}") from None
