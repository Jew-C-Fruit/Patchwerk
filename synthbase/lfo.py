"""LFOs snappable onto any module parameter.

An assignment = one control-rate LFO synth writing REAL param values onto a
control bus, plus the target node's parameter mapped to that bus
(``node.map``). Modulation runs sample-accurately inside scsynth — Python is
only involved when you turn an LFO's knobs. These control buses are the
patch cables of the future graph UI.

The LFO knows its target's range and curve (baked in at assign time), so
`center` and `depth` are normalized 0..1 like every other control in the
system: center = where in the param's range the LFO orbits, depth = how much
of the range it sweeps.

Interplay with the rest of the system:
- A mapped param's slider (or bound CC) moves the LFO's *center* instead of
  fighting the mapping (rack.set_param skips node.set for mapped params).
- Hot reload / bypass re-enable replace the target node — the rack fires
  ``on_node_replaced`` and assignments re-map automatically.
"""

from __future__ import annotations

import threading

from supriya import AddAction, synthdef
from supriya.ugens import LFNoise0, LFPulse, LFSaw, LFTri, LinExp, Out, Select, SinOsc

SHAPES = ("sine", "tri", "ramp", "square", "s&h")


@synthdef()
def _lfo(rate=1.0, shape=0, depth=0.25, center=0.5, lo=0.0, hi=1.0, is_exp=0, kout=0):
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
    unit = (center + osc * depth * 0.5).clip(0.0, 1.0)
    linear = lo + unit * (hi - lo)
    expo = LinExp.kr(source=unit, input_minimum=0, input_maximum=1,
                     output_minimum=lo.clip(1e-4, 1e9), output_maximum=hi)
    Out.kr(bus=kout, source=linear * (1 - is_exp) + expo * is_exp)


class LFOManager:
    def __init__(self, app) -> None:
        self.app = app
        self.assignments: dict[str, dict] = {}  # "key.param" -> record
        self._lock = threading.Lock()
        self._registered = False

    # -- lifecycle -----------------------------------------------------------

    def _ensure_synthdef(self) -> None:
        if not self._registered and self.app.engine and self.app.engine.server:
            self.app.engine.server.add_synthdefs(_lfo)
            self.app.engine.server.sync()
            self._registered = True

    def reset(self) -> None:
        """Engine went away (patch switch keeps engine; reboot doesn't)."""
        self._registered = False

    # -- assign / unassign ------------------------------------------------------

    def assign(self, key: str, pname: str, **cfg) -> str:
        rack = self.app.rack
        inst = rack.find(key)
        p = inst.module.params[pname]
        aid = f"{key}.{pname}"
        if aid in self.assignments:
            return aid
        self._ensure_synthdef()
        server = self.app.engine.server
        bus = server.add_bus(calculation_rate="control")
        current = inst.settings.get(pname, p.default)
        settings = {
            "rate": float(cfg.get("rate", 1.0)),
            "shape": int(cfg.get("shape", 0)),
            "depth": float(cfg.get("depth", 0.25)),
            "center": float(cfg.get("center", self._to_unit(p, current))),
        }
        node = server.add_synth(
            _lfo,
            add_action=AddAction.ADD_TO_HEAD,
            target_node=self.app.engine.root_group,
            lo=p.minimum, hi=p.maximum,
            is_exp=1 if p.curve == "exp" else 0,
            kout=int(bus),
            **settings,
        )
        inst.node.map(**{pname: bus})
        with self._lock:
            self.assignments[aid] = {
                "key": key, "param": pname, "node": node, "bus": bus,
                "settings": settings, "restore": current,
            }
        rack.mapped.add((key, pname))
        return aid

    def unassign(self, aid: str) -> None:
        with self._lock:
            rec = self.assignments.pop(aid, None)
        if rec is None:
            return
        rack = self.app.rack
        rack.mapped.discard((rec["key"], rec["param"]))
        try:
            inst = rack.find(rec["key"])
            inst.node.map(**{rec["param"]: None})
            # restore whatever the settings dict currently says
            rack.set_param(rec["key"], rec["param"],
                           inst.settings.get(rec["param"], rec["restore"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["node"].free()
            rec["bus"].free()
        except Exception:  # noqa: BLE001
            pass

    def configure(self, aid: str, **kw) -> None:
        rec = self.assignments.get(aid)
        if rec is None:
            return
        updates = {}
        for k in ("rate", "depth", "center"):
            if kw.get(k) is not None:
                updates[k] = float(kw[k])
        if kw.get("shape") is not None:
            s = kw["shape"]
            updates["shape"] = SHAPES.index(s) if isinstance(s, str) else int(s)
        if updates:
            rec["settings"].update(updates)
            try:
                rec["node"].set(**updates)
            except Exception:  # noqa: BLE001
                pass

    def set_center_unit(self, key: str, pname: str, unit: float) -> bool:
        """Slider/CC on a mapped param steers the LFO's center. True if handled."""
        aid = f"{key}.{pname}"
        if aid not in self.assignments:
            return False
        self.configure(aid, center=unit)
        return True

    # -- resilience ------------------------------------------------------------

    def on_node_replaced(self, key: str) -> None:
        """Target node respawned (hot reload / bypass toggle): re-map."""
        rack = self.app.rack
        for aid, rec in list(self.assignments.items()):
            if rec["key"] != key:
                continue
            try:
                inst = rack.find(key)
                inst.node.map(**{rec["param"]: rec["bus"]})
            except Exception:  # noqa: BLE001
                pass

    def clear(self) -> None:
        for aid in list(self.assignments):
            self.unassign(aid)

    # -- persistence / state ------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {aid: dict(rec["settings"]) for aid, rec in self.assignments.items()}

    def restore(self, data: dict) -> None:
        self.clear()
        for aid, settings in (data or {}).items():
            key, _, pname = aid.partition(".")
            try:
                self.assign(key, pname, **settings)
            except Exception as exc:  # noqa: BLE001
                print(f"[lfo] could not restore {aid}: {exc}")

    def state(self) -> list[dict]:
        with self._lock:
            return [
                {"id": aid, "key": rec["key"], "param": rec["param"],
                 **rec["settings"], "shapes": list(SHAPES)}
                for aid, rec in self.assignments.items()
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
