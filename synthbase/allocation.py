"""Allocation Intent — one conserved modulation *budget* across many knobs.

The Artifix idea: you don't add modulation, you *steer* a fixed budget. Six
intent weights are normalized so ``Σ mᵢ² = r²`` stays constant — push one
dimension up and the others give way. Wire the six outputs to six knobs (on
one module or spread across several) and they trade against each other on a
sphere of constant radius r.

Mechanism (one-to-many, unlike the LFO/Living 1:1 model):
- one ``_alloc`` synth normalizes the weights and writes the six conserved
  magnitudes onto an internal 6-channel control bus;
- per wired target, one small ``_alloc_tap`` reads its channel, scales it to
  that target's range/curve, and writes the target's param bus, which the
  target node is ``node.map``-ed to.

So the conservation is enforced once, sample-accurately, in ``_alloc``; each
tap just re-ranges one channel for its own knob. Targets are independent —
wire one or all six.

State/rebuild plumbing mirrors LFOManager/LivingManager so it drops into the
same app + server + GUI seams (its GUI is a 1→N fan of mod wires).
"""

from __future__ import annotations

import threading

from supriya import AddAction, synthdef
from supriya.ugens import In, LinExp, Out

NDIM = 6
DIMS = ("wave", "harm", "filt", "stereo", "res", "det")


@synthdef()
def _alloc(w0=0.5, w1=0.35, w2=0.45, w3=0.4, w4=0.25, w5=0.3, r=1.0, kout=0):
    ws = [w0, w1, w2, w3, w4, w5]
    ss = w0 * w0 + w1 * w1 + w2 * w2 + w3 * w3 + w4 * w4 + w5 * w5
    norm = (ss + 1e-9).sqrt()
    # conserved magnitudes: Σ mᵢ² = r²
    mags = [(w / norm) * r for w in ws]
    Out.kr(bus=kout, source=mags)


@synthdef()
def _alloc_tap(kin=0, slot=0, lo=0.0, hi=1.0, is_exp=0, gain=1.0, kout=0):
    # read this dimension's conserved magnitude off the shared bus
    m = In.kr(bus=kin + slot, channel_count=1)
    unit = (m * gain).clip(0.0, 1.0)
    linear = lo + unit * (hi - lo)
    expo = LinExp.kr(source=unit, input_minimum=0, input_maximum=1,
                     output_minimum=lo.clip(1e-4, 1e9), output_maximum=hi)
    Out.kr(bus=kout, source=linear * (1 - is_exp) + expo * is_exp)


CONFIG_KEYS = ("r",) + tuple(f"w{i}" for i in range(NDIM))


class AllocationManager:
    """Conserved-budget modulators. One instance drives up to 6 params.

    An *instance* id is "alloc", "alloc.2", ...; each owns an `_alloc` synth,
    a 6-channel internal bus, and a dict of targets keyed by slot 0..5.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.instances: dict[str, dict] = {}   # alloc id -> record
        self._lock = threading.Lock()
        self._registered = False
        self._counter = 0

    def _ensure_synthdef(self) -> None:
        if not self._registered and self.app.engine and self.app.engine.server:
            self.app.engine.server.add_synthdefs(_alloc, _alloc_tap)
            self.app.engine.server.sync()
            self._registered = True

    def reset(self) -> None:
        self._registered = False

    def _alloc_id(self) -> str:
        existing = set(self.instances)
        if "alloc" not in existing:
            return "alloc"
        n = 2
        while f"alloc.{n}" in existing:
            n += 1
        return f"alloc.{n}"

    # -- instance lifecycle --------------------------------------------------

    def spawn(self, **cfg) -> str:
        self._ensure_synthdef()
        server = self.app.engine.server
        aid = self._alloc_id()
        internal = server.add_bus_group(calculation_rate="control", count=NDIM)
        settings = {"r": float(cfg.get("r", 1.0))}
        for i in range(NDIM):
            settings[f"w{i}"] = float(cfg.get(f"w{i}", (0.5, 0.35, 0.45, 0.4, 0.25, 0.3)[i]))
        node = server.add_synth(
            _alloc,
            add_action=AddAction.ADD_TO_HEAD,
            target_node=self.app.engine.root_group,
            kout=int(internal),
            **settings,
        )
        with self._lock:
            self.instances[aid] = {
                "node": node, "internal": internal, "settings": settings,
                "targets": {},  # slot -> {key, param, node, bus, restore}
            }
        return aid

    def remove(self, aid: str) -> None:
        with self._lock:
            rec = self.instances.pop(aid, None)
        if rec is None:
            return
        for slot in list(rec["targets"]):
            self._unwire(rec, slot)
        try:
            rec["node"].free()
            rec["internal"].free()
        except Exception:  # noqa: BLE001
            pass

    def configure(self, aid: str, **kw) -> None:
        rec = self.instances.get(aid)
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

    # -- target wiring (one slot at a time) ----------------------------------

    def wire(self, aid: str, slot: int, key: str, pname: str, gain: float = 1.0) -> None:
        rec = self.instances.get(aid)
        if rec is None:
            return
        slot = int(slot) % NDIM
        if slot in rec["targets"]:
            self._unwire(rec, slot)
        rack = self.app.rack
        inst = rack.find(key)
        key = inst.key
        p = inst.module.params[pname]
        server = self.app.engine.server
        bus = server.add_bus(calculation_rate="control")
        current = inst.settings.get(pname, p.default)
        tap = server.add_synth(
            _alloc_tap,
            add_action=AddAction.ADD_AFTER,
            target_node=rec["node"],
            kin=int(rec["internal"]), slot=slot,
            lo=p.minimum, hi=p.maximum,
            is_exp=1 if p.curve == "exp" else 0,
            gain=float(gain), kout=int(bus),
        )
        inst.node.map(**{pname: bus})
        rec["targets"][slot] = {
            "key": key, "param": pname, "node": tap, "bus": bus, "restore": current,
        }
        rack.mapped.add((key, pname))

    def unwire(self, aid: str, slot: int) -> None:
        rec = self.instances.get(aid)
        if rec is not None:
            self._unwire(rec, int(slot) % NDIM)

    def _unwire(self, rec: dict, slot: int) -> None:
        t = rec["targets"].pop(slot, None)
        if t is None:
            return
        rack = self.app.rack
        rack.mapped.discard((t["key"], t["param"]))
        try:
            inst = rack.find(t["key"])
            inst.node.map(**{t["param"]: None})
            rack.set_param(t["key"], t["param"],
                           inst.settings.get(t["param"], t["restore"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            t["node"].free()
            t["bus"].free()
        except Exception:  # noqa: BLE001
            pass

    def on_node_replaced(self, key: str) -> None:
        rack = self.app.rack
        for rec in list(self.instances.values()):
            for t in rec["targets"].values():
                if t["key"] != key:
                    continue
                try:
                    inst = rack.find(key)
                    inst.node.map(**{t["param"]: t["bus"]})
                except Exception:  # noqa: BLE001
                    pass

    def clear(self) -> None:
        for aid in list(self.instances):
            self.remove(aid)

    # -- persistence / state -------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                aid: {
                    "settings": dict(rec["settings"]),
                    "targets": {slot: {"key": t["key"], "param": t["param"]}
                                for slot, t in rec["targets"].items()},
                }
                for aid, rec in self.instances.items()
            }

    def restore(self, data: dict) -> None:
        self.clear()
        for aid, blob in (data or {}).items():
            try:
                new_id = self.spawn(**blob.get("settings", {}))
                for slot, t in blob.get("targets", {}).items():
                    self.wire(new_id, int(slot), t["key"], t["param"])
            except Exception as exc:  # noqa: BLE001
                print(f"[alloc] could not restore {aid}: {exc}")

    def state(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": aid,
                    "dims": list(DIMS),
                    **rec["settings"],
                    "targets": {str(slot): {"key": t["key"], "param": t["param"]}
                                for slot, t in rec["targets"].items()},
                }
                for aid, rec in self.instances.items()
            ]
