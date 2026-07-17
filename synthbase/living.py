"""Living Oscillator — a modulator whose motion is bounded but never repeats.

Same machinery as the LFO (control-rate synth writing REAL param values onto
a control bus; the target node's param is ``node.map``-ed to that bus), but
instead of a periodic shape it runs a *living* trajectory: a slow chaotic
flow (a Thomas attractor, integrated in the synthdef) blended with a
quasi-periodic phasor, all kept sub-audio so it reads as "alive," not
"rough." This is the modulation heart of the Artifix package — wire it to
any knob on any module and that knob drifts on its own.

Two knobs shape the motion: ``life`` = trajectory speed, ``wander`` = chaos
vs. quasi-periodic (0 = smooth orbit, 1 = restless). ``depth``/``center``
work exactly like the LFO's: where in the target's range it orbits and how
much of the range it covers. Range and curve are baked in at assign time,
so depth/center stay normalized 0..1.

Each assignment also publishes a point ON the unit sphere — the direction of
the 3-D attractor state, ``(x, y, z) / |(x, y, z)|`` — on a 3-channel control
bus so the Sphere visualizer can draw it. Radius = 1 is the conserved
invariant the Artifix sphere illustrates; as the attractor roams the octants
that vector sweeps the whole surface.

Mirrors LFOManager (assign/unassign/configure/on_node_replaced/snapshot/
restore/state) so it plugs into the same app + server + GUI seams.
"""

from __future__ import annotations

import threading

from supriya import AddAction, synthdef
from supriya.ugens import (
    ControlDur, Impulse, LinExp, LocalIn, LocalOut, Out, SinOsc,
)


@synthdef()
def _living(life=0.35, wander=0.3, depth=0.25, center=0.5,
            lo=0.0, hi=1.0, is_exp=0, kout=0, traj_bus=0):
    dt = ControlDur.ir()
    spd = 0.15 + 3.0 * life
    # Thomas cyclically-symmetric attractor, integrated at control rate.
    st = LocalIn.kr(channel_count=3)
    x, y, z = st[0], st[1], st[2]
    b = 0.19 - 0.12 * wander                      # lower b = more chaotic
    s = (1.0 + 2.4 * wander) * spd
    # Impulse.kr(frequency=0) fires once at t=0 — a SMALL seed nudges the
    # attractor off its unstable origin fixed point. The old kick of 1.0
    # slammed x into the +wall and it never crossed zero again (the trajectory
    # pinned in one octant — the Sphere dot stuck in a corner); 0.1 lets it
    # roam symmetrically. The natural span is ~±5, so clip at ±8 as a safety
    # rail it can't reach rather than a wall it lives against.
    kick = 0.1 * Impulse.kr(frequency=0)
    nx = (x + (y.sin() - b * x) * s * dt + kick).clip(-8.0, 8.0)
    ny = (y + (z.sin() - b * y) * s * dt).clip(-8.0, 8.0)
    nz = (z + (x.sin() - b * z) * s * dt).clip(-8.0, 8.0)
    LocalOut.kr(source=[nx, ny, nz])

    quasi = 0.5 + 0.5 * SinOsc.kr(frequency=0.031 * spd)   # incommensurate drift
    chaos = (0.5 + 0.5 * (x * (1.0 / 5.0))).clip(0.0, 1.0)  # x now centred on 0
    base = quasi * (1.0 - wander) + chaos * wander
    unit = (center + (base - 0.5) * 2.0 * depth).clip(0.0, 1.0)
    linear = lo + unit * (hi - lo)
    expo = LinExp.kr(source=unit, input_minimum=0, input_maximum=1,
                     output_minimum=lo.clip(1e-4, 1e9), output_maximum=hi)
    Out.kr(bus=kout, source=linear * (1 - is_exp) + expo * is_exp)

    # publish a point ON the unit sphere — the direction of the 3-D state.
    # radius = 1 is the conserved invariant the Sphere viz draws, and as the
    # attractor roams the octants this vector sweeps the whole surface, so the
    # dot rides the sphere instead of clustering in one screen-corner.
    mag = (x * x + y * y + z * z + 1e-6).sqrt()
    Out.kr(bus=traj_bus, source=[x / mag, y / mag, z / mag])


CONFIG_KEYS = ("life", "wander", "depth", "center")


class LivingManager:
    """One Living Oscillator per (module, param), just like LFOManager."""

    def __init__(self, app) -> None:
        self.app = app
        self.assignments: dict[str, dict] = {}   # "key.param" -> record
        self._lock = threading.Lock()
        self._registered = False

    # -- lifecycle -----------------------------------------------------------

    def _ensure_synthdef(self) -> None:
        if not self._registered and self.app.engine and self.app.engine.server:
            self.app.engine.server.add_synthdefs(_living)
            self.app.engine.server.sync()
            self._registered = True

    def reset(self) -> None:
        """Engine went away on a full reboot — re-register on next assign."""
        self._registered = False

    # -- assign / unassign ---------------------------------------------------

    def assign(self, key: str, pname: str, **cfg) -> str:
        rack = self.app.rack
        inst = rack.find(key)
        key = inst.key
        p = inst.module.params[pname]
        aid = f"{key}.{pname}"
        if aid in self.assignments:
            return aid
        self._ensure_synthdef()
        server = self.app.engine.server
        bus = server.add_bus(calculation_rate="control")
        traj = server.add_bus_group(calculation_rate="control", count=3)
        current = inst.settings.get(pname, p.default)
        settings = {
            "life": float(cfg.get("life", 0.35)),
            "wander": float(cfg.get("wander", 0.3)),
            "depth": float(cfg.get("depth", 0.4)),
            "center": float(cfg.get("center", self._to_unit(p, current))),
        }
        node = server.add_synth(
            _living,
            add_action=AddAction.ADD_TO_HEAD,
            target_node=self.app.engine.root_group,
            lo=p.minimum, hi=p.maximum,
            is_exp=1 if p.curve == "exp" else 0,
            kout=int(bus), traj_bus=int(traj),
            **settings,
        )
        inst.node.map(**{pname: bus})
        with self._lock:
            self.assignments[aid] = {
                "key": key, "param": pname, "node": node, "bus": bus,
                "traj": traj, "settings": settings, "restore": current,
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
            rack.set_param(rec["key"], rec["param"],
                           inst.settings.get(rec["param"], rec["restore"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            rec["node"].free()
            rec["bus"].free()
            rec["traj"].free()
        except Exception:  # noqa: BLE001
            pass

    def configure(self, aid: str, **kw) -> None:
        rec = self.assignments.get(aid)
        if rec is None:
            return
        updates = {}
        for k in CONFIG_KEYS:
            if kw.get(k) is not None:
                updates[k] = float(kw[k])
        if updates:
            rec["settings"].update(updates)
            try:
                rec["node"].set(**updates)
            except Exception:  # noqa: BLE001
                pass

    def set_center_unit(self, key: str, pname: str, unit: float) -> bool:
        """Slider/CC on a mapped param steers the center. True if handled."""
        aid = f"{key}.{pname}"
        if aid not in self.assignments:
            return False
        self.configure(aid, center=unit)
        return True

    # -- resilience ----------------------------------------------------------

    def on_node_replaced(self, key: str) -> None:
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

    # -- sphere feed ---------------------------------------------------------

    def trajectories(self) -> dict:
        """Poll each assignment's (x, y, r²) bus for the Sphere visualizer."""
        out = {}
        with self._lock:
            recs = list(self.assignments.items())
        for aid, rec in recs:
            try:
                out[aid] = [float(b.get()) for b in rec["traj"]]
            except Exception:  # noqa: BLE001
                pass
        return out

    # -- persistence / state -------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {aid: dict(rec["settings"]) for aid, rec in self.assignments.items()}

    def restore(self, data: dict) -> None:
        self.clear()
        for aid, settings in (data or {}).items():
            key, _, pname = aid.rpartition(".")
            try:
                self.assign(key, pname, **settings)
            except Exception as exc:  # noqa: BLE001
                print(f"[living] could not restore {aid}: {exc}")

    def state(self) -> list[dict]:
        with self._lock:
            return [
                {"id": aid, "key": rec["key"], "param": rec["param"], **rec["settings"]}
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
