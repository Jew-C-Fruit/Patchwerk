"""Routable LFOs: standalone modulation nodes with fan-out (item 7).

An LFO is a first-class node now ("lfo", "lfo.2", ... — spawn/remove like
any deriver), not a per-assignment appendage. Each instance runs ONE
``_lfo_norm`` synth writing a BIPOLAR normalized signal (osc * depth,
-1..1) onto its own control bus; every wired destination adds a tiny
``_lfo_scale`` synth that reads that shared bus and maps it onto the
target parameter's real range — so one LFO fans out to any number of
params, each with its own curve, and the modulation itself still runs
sample-accurately inside scsynth.

CENTER IS GONE as an LFO knob: each destination orbits its OWN slider
value. Moving a mapped param's slider steers that destination's center
(``set_center_unit``) — the same value the slider would set unmapped, so
un-wiring simply leaves the knob where you last put it. rack.set_param
skips node.set for mapped params (the bus mapping owns the node), storing
the value for the center/restore path instead.

Wire hygiene: a param is SINGLE-INPUT — wiring an LFO onto an already-
mapped param steals it from whichever LFO held it (matching the GUI's
re-target drop). Removing a module drops its destinations; a rack rebuild
(patch switch / preset load) drops ALL destinations but keeps the LFO
nodes themselves, like every other spawned ctl node.

Persistence: ``snapshot()`` emits {"instances": [...]}; ``restore()``
also accepts the pre-item-7 format ({"<key>.<param>": {rate, shape,
depth, center}}) and MIGRATES it — one instance per old assignment, the
old center applied to the destination's param value first so behavior is
preserved exactly.
"""

from __future__ import annotations

import threading

from supriya import AddAction, synthdef
from supriya.ugens import (
    In, LFNoise0, LFPulse, LFSaw, LFTri, LinExp, Out, Select, SinOsc,
)

from .rack import alloc_id

SHAPES = ("sine", "tri", "ramp", "square", "s&h")


@synthdef()
def _lfo_norm(rate=1.0, shape=0, depth=0.25, kout=0):
    osc = Select.kr(
        selector=shape,
        sources=[
            SinOsc.kr(frequency=rate),
            LFTri.kr(frequency=rate),
            LFSaw.kr(frequency=rate),
            LFPulse.kr(frequency=rate) * 2 - 1,
            LFNoise0.kr(frequency=rate),
        ],
    )
    Out.kr(bus=kout, source=osc * depth)


@synthdef()
def _lfo_scale(kin=0, center=0.5, lo=0.0, hi=1.0, is_exp=0, kout=0):
    norm = In.kr(bus=kin)
    unit = (center + norm * 0.5).clip(0.0, 1.0)
    linear = lo + unit * (hi - lo)
    expo = LinExp.kr(source=unit, input_minimum=0, input_maximum=1,
                     output_minimum=lo.clip(1e-4, 1e9), output_maximum=hi)
    Out.kr(bus=kout, source=linear * (1 - is_exp) + expo * is_exp)


class LFOManager:
    """All LFO instances + their destination fan-outs.

    Instance record: {"settings": {rate, shape, depth}, "bus": norm bus,
    "node": norm synth, "dests": {(key, param) -> dest record}}.
    Dest record: {"key", "param", "bus", "node", "center"}.
    Server objects are None when no engine is up (headless tests) — every
    server touch is guarded, the data model works either way.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.instances: dict[str, dict] = {}
        self._lock = threading.Lock()
        # PER-SERVER registration: a device switch reboots the engine
        # (app.set_devices → stop/start) and a plain boolean survives
        # pointing at the DEAD server — every later spawn then /s_new's a
        # synthdef the new scsynth never received ("SynthDef not found",
        # found live 2026-07-22). Track the server OBJECT instead so a
        # fresh server always re-receives the defs, whether or not anyone
        # remembered to call reset().
        self._registered_server = None

    # -- server plumbing ---------------------------------------------------------

    def _server(self):
        eng = self.app.engine
        return eng.server if eng and getattr(eng, "server", None) else None

    def _ensure_synthdefs(self, server) -> None:
        if self._registered_server is not server:
            server.add_synthdefs(_lfo_norm, _lfo_scale)
            server.sync()
            self._registered_server = server

    def reset(self) -> None:
        """Engine went away — server-side objects are already gone."""
        self._registered_server = None

    # -- instances ---------------------------------------------------------------

    def spawn(self, want_id: str | None = None) -> str:
        with self._lock:
            lid = want_id or alloc_id("lfo", self.instances.keys())
            if lid in self.instances:
                return lid
            rec = {
                "settings": {"rate": 1.0, "shape": 0, "depth": 0.25},
                "bus": None, "node": None, "dests": {},
            }
            server = self._server()
            if server is not None:
                self._ensure_synthdefs(server)
                rec["bus"] = server.add_bus(calculation_rate="control")
                rec["node"] = server.add_synth(
                    _lfo_norm,
                    add_action=AddAction.ADD_TO_HEAD,
                    target_node=self.app.engine.root_group,
                    kout=int(rec["bus"]),
                    **rec["settings"],
                )
            self.instances[lid] = rec
            return lid

    def remove(self, lid: str) -> None:
        with self._lock:
            rec = self.instances.get(lid)
            if rec is None:
                raise KeyError(f"no LFO {lid!r}")
        for key, pname in list(rec["dests"]):
            self.unwire(lid, key, pname)
        with self._lock:
            self.instances.pop(lid, None)
        for obj in (rec["node"], rec["bus"]):
            try:
                if obj is not None:
                    obj.free()
            except Exception:  # noqa: BLE001
                pass

    def configure(self, lid: str, **kw) -> None:
        rec = self.instances.get(lid)
        if rec is None:
            raise KeyError(f"no LFO {lid!r}")
        updates = {}
        for k in ("rate", "depth"):
            if kw.get(k) is not None:
                updates[k] = float(kw[k])
        if kw.get("shape") is not None:
            s = kw["shape"]
            updates["shape"] = SHAPES.index(s) if isinstance(s, str) else int(s)
        if updates:
            rec["settings"].update(updates)
            try:
                if rec["node"] is not None:
                    rec["node"].set(**updates)
            except Exception:  # noqa: BLE001
                pass

    # -- destinations (the fan-out) ----------------------------------------------

    def _owner_of(self, key: str, pname: str) -> str | None:
        for lid, rec in self.instances.items():
            if (key, pname) in rec["dests"]:
                return lid
        return None

    def wire(self, lid: str, key: str, pname: str) -> None:
        """Wire an LFO onto a param. Steals an already-mapped param from its
        current LFO (params are single-input; matches the GUI's re-target
        drop). Center starts at the param's CURRENT value."""
        rec = self.instances.get(lid)
        if rec is None:
            raise KeyError(f"no LFO {lid!r}")
        rack = self.app.rack
        inst = rack.find(key)
        key = inst.key  # normalize a legacy type key to the instance id
        if pname not in inst.module.params:
            raise KeyError(f"{key} has no param {pname!r}")
        p = inst.module.params[pname]
        holder = self._owner_of(key, pname)
        if holder == lid:
            return
        if holder is not None:
            self.unwire(holder, key, pname)
        current = inst.settings.get(pname, p.default)
        dest = {"key": key, "param": pname, "bus": None, "node": None,
                "center": self._to_unit(p, current)}
        server = self._server()
        if server is not None and rec["node"] is not None:
            dest["bus"] = server.add_bus(calculation_rate="control")
            dest["node"] = server.add_synth(
                _lfo_scale,
                add_action=AddAction.ADD_AFTER,   # always downstream of its norm
                target_node=rec["node"],
                kin=int(rec["bus"]), center=dest["center"],
                lo=p.minimum, hi=p.maximum,
                is_exp=1 if p.curve == "exp" else 0,
                kout=int(dest["bus"]),
            )
            inst.node.map(**{pname: dest["bus"]})
        with self._lock:
            rec["dests"][(key, pname)] = dest
        rack.mapped.add((key, pname))

    def unwire(self, lid: str, key: str, pname: str) -> None:
        rec = self.instances.get(lid)
        if rec is None:
            return
        with self._lock:
            dest = rec["dests"].pop((key, pname), None)
        if dest is None:
            return
        rack = self.app.rack
        if rack is not None:
            rack.mapped.discard((key, pname))
            try:
                inst = rack.find(key)
                if dest["bus"] is not None:
                    inst.node.map(**{pname: None})
                # settle on whatever the settings dict currently says (the
                # slider kept steering it while mapped)
                p = inst.module.params[pname]
                rack.set_param(key, pname,
                               inst.settings.get(pname, p.default))
            except Exception:  # noqa: BLE001
                pass
        for obj in (dest["node"], dest["bus"]):
            try:
                if obj is not None:
                    obj.free()
            except Exception:  # noqa: BLE001
                pass

    def set_center_unit(self, key: str, pname: str, unit: float) -> bool:
        """Slider/CC on a mapped param steers THAT destination's center.
        True if some LFO owns (key, pname)."""
        lid = self._owner_of(key, pname)
        if lid is None:
            return False
        dest = self.instances[lid]["dests"].get((key, pname))
        if dest is None:
            return False
        dest["center"] = max(0.0, min(1.0, float(unit)))
        try:
            if dest["node"] is not None:
                dest["node"].set(center=dest["center"])
        except Exception:  # noqa: BLE001
            pass
        return True

    # -- resilience --------------------------------------------------------------

    def on_node_replaced(self, key: str) -> None:
        """Target node respawned (hot reload / bypass toggle): re-map."""
        rack = self.app.rack
        for rec in self.instances.values():
            for (k, pname), dest in rec["dests"].items():
                if k != key or dest["bus"] is None:
                    continue
                try:
                    rack.find(key).node.map(**{pname: dest["bus"]})
                except Exception:  # noqa: BLE001
                    pass

    def on_module_removed(self, key: str) -> None:
        """A module left the chain: its destinations go with it (no param
        restore — the node is gone)."""
        for lid, rec in self.instances.items():
            for (k, pname) in [d for d in list(rec["dests"]) if d[0] == key]:
                with self._lock:
                    dest = rec["dests"].pop((k, pname), None)
                if self.app.rack is not None:
                    self.app.rack.mapped.discard((k, pname))
                for obj in ((dest or {}).get("node"), (dest or {}).get("bus")):
                    try:
                        if obj is not None:
                            obj.free()
                    except Exception:  # noqa: BLE001
                        pass

    def on_rack_rebuilt(self) -> None:
        """Patch switch / preset load rebuilt the rack: every destination
        node is gone — free the scale synths/buses, keep the LFO nodes
        (they're spawned ctl nodes and survive, like derivers)."""
        for rec in self.instances.values():
            for (k, pname) in list(rec["dests"]):
                with self._lock:
                    dest = rec["dests"].pop((k, pname), None)
                for obj in ((dest or {}).get("node"), (dest or {}).get("bus")):
                    try:
                        if obj is not None:
                            obj.free()
                    except Exception:  # noqa: BLE001
                        pass

    def clear(self) -> None:
        for lid in list(self.instances):
            try:
                self.remove(lid)
            except KeyError:
                pass

    # -- persistence / state -----------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {"instances": [
                {"id": lid, **rec["settings"],
                 "dests": [{"key": k, "param": p} for (k, p) in rec["dests"]]}
                for lid, rec in self.instances.items()
            ]}

    def restore(self, data) -> None:
        self.clear()
        if not data:
            return
        if isinstance(data, dict) and "instances" in data:
            for e in data["instances"]:
                lid = self.spawn(want_id=e.get("id"))
                self.configure(lid, rate=e.get("rate"), depth=e.get("depth"),
                               shape=e.get("shape"))
                for d in e.get("dests", []):
                    try:
                        self.wire(lid, d["key"], d["param"])
                    except Exception as exc:  # noqa: BLE001
                        print(f"[lfo] could not wire {lid} -> "
                              f"{d.get('key')}.{d.get('param')}: {exc}")
            return
        # pre-item-7 MIGRATION: {"<key>.<param>": {rate, shape, depth,
        # center}} — one instance per old assignment; the old center becomes
        # the destination's param value (center now orbits the slider).
        for aid, cfg in data.items():
            key, _, pname = str(aid).rpartition(".")
            try:
                rack = self.app.rack
                inst = rack.find(key)
                p = inst.module.params[pname]
                if cfg.get("center") is not None:
                    rack.set_param(inst.key, pname,
                                   p.from_unit(float(cfg["center"])))
                lid = self.spawn()
                self.configure(lid, rate=cfg.get("rate"),
                               depth=cfg.get("depth"), shape=cfg.get("shape"))
                self.wire(lid, inst.key, pname)
            except Exception as exc:  # noqa: BLE001
                print(f"[lfo] could not migrate {aid}: {exc}")

    def state(self) -> list[dict]:
        with self._lock:
            return [
                {"id": lid, **rec["settings"], "shapes": list(SHAPES),
                 "dests": [{"key": k, "param": p, "center": d["center"]}
                           for (k, p), d in rec["dests"].items()]}
                for lid, rec in self.instances.items()
            ]

    @staticmethod
    def _to_unit(p, value: float) -> float:
        import math
        if p.curve == "exp":
            lo = max(p.minimum, 1e-6)
            try:
                return max(0.0, min(1.0, math.log(value / lo) / math.log(p.maximum / lo)))
            except (ValueError, ZeroDivisionError):
                return 0.5
        rng = p.maximum - p.minimum
        return max(0.0, min(1.0, (value - p.minimum) / rng)) if rng else 0.5
