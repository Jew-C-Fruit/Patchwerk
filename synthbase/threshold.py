"""Threshold: CV edge → ping (item 8a) — a comparator that watches an
analog signal and fires a PING when it crosses a limit.

The whole point is the bridge rule: continuous stays server-side, discrete
stays Python-side, and the only crossing is EDGE-NOTIFY — a ``_threshold_watch``
synth reads the source LFO's normalized bus and a ``SendTrig`` fires exactly
one ``/tr`` OSC message per threshold crossing. Python never polls a bus;
the ``/tr`` handler turns each message into an ordinary ping fan-out
(``app._ping_sinks``), identical to a Button or Clock fire.

Comparator semantics: ``Schmidt.kr(sig, level - hyst, level + hyst)`` — the
hysteresis window IS the debounce, so a noisy CV can't machine-gun pings.
Edge direction is selectable (rising / falling / both) via two spawn-set
gate params (``r_on``/``f_on``) multiplying the edge triggers, so a mode
change is a plain ``node.set`` with no respawn. The falling-edge trigger
(``1 - state``) starts POSITIVE when the signal spawns below the window,
which SendTrig counts as a transition — so fires inside a short arm-delay
after (re)spawn are swallowed Python-side rather than heard as a phantom
ping.

Level is in the LFO's NORMALIZED terms (bipolar −1..1, post-depth): a depth
0.25 LFO swings ±0.25, so a level of 0.0 ticks twice a cycle and ±0.3 never
fires. That's deliberate — the threshold listens to the CV itself, not to
any destination's param mapping.

Feed (b), sensors (M4): serial values are already Python-side, so the same
node exposes ``feed(value)`` — a pure-Python Schmitt with identical
level/hysteresis/mode semantics. When the pyserial bridge lands, a sensor
channel calls ``feed`` and the ping path is already there. Until then it's
exercised only by tests.

Wire model: the CV-in is SINGLE-INPUT (like a param's quiet handle) —
``threshold_wire add`` steals nothing (an LFO fans out freely); re-wiring
replaces the source. Ping-out wires ride ``app.ctl_wires`` with the kind
inferred from the source endpoint, exactly like button/clock.
"""

from __future__ import annotations

import threading
import time

from supriya import AddAction, synthdef
from supriya.ugens import In, Schmidt, SendTrig

from .rack import alloc_id

MODES = ("rising", "falling", "both")
ARM_DELAY = 0.15   # s: swallow the spurious initial falling-edge after spawn


@synthdef()
def _threshold_watch(kin=0, lo=-0.02, hi=0.02, r_on=1, f_on=0, tag=0):
    sig = In.kr(bus=kin)
    state = Schmidt.kr(source=sig, minimum=lo, maximum=hi)
    # SendTrig fires on its trigger's nonpositive→positive transition:
    # `state` makes that a rising crossing, `1 - state` a falling one.
    # The r_on/f_on gates hold an off-mode trigger at 0 so it can't fire.
    SendTrig.kr(trigger=state * r_on, id_=tag, value=1)
    SendTrig.kr(trigger=(1 - state) * f_on, id_=tag, value=0)


class ThresholdNode:
    """One comparator instance. Server watch synth is owned by the manager;
    this object owns the settings + the Python-side (sensor) Schmitt."""

    def __init__(self, app, tid: str) -> None:
        self.app = app
        self.id = tid
        self.level = 0.0          # in normalized CV terms (bipolar -1..1)
        self.hysteresis = 0.02
        self.mode = "rising"
        self.source: str | None = None   # LFO id wired into the CV-in
        self._armed_at = 0.0      # monotonic ts of last (re)spawn
        self._feed_state: bool | None = None   # pure-Python Schmitt (feed b)

    # -- firing (shared by /tr and feed) ---------------------------------------

    def fire(self, rising: bool | None = None) -> None:
        try:
            self.app._emit_midi_event({"kind": "ping", "src": self.id})
        except Exception:  # noqa: BLE001
            pass
        for s in self.app._ping_sinks(self.id):
            try:
                s.trigger()
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    def on_tr(self, value: float) -> None:
        """A /tr arrived for this node's tag (OSC thread — keep it light).
        Swallows fires inside the arm window: the falling-edge trigger sits
        positive at spawn when the CV starts below the window."""
        if time.monotonic() - self._armed_at < ARM_DELAY:
            return
        self.fire(rising=value >= 0.5)

    # -- feed (b): Python-side values (sensors, M4) ----------------------------

    def feed(self, value: float) -> None:
        """Same Schmitt semantics for values that are ALREADY Python-side.
        No server round-trip: a serial sensor value crossing the limit is
        a pure-Python ping."""
        lo, hi = self.level - self.hysteresis, self.level + self.hysteresis
        prev = self._feed_state
        state = prev if prev is not None else value > lo
        if value >= hi:
            state = True
        elif value <= lo:
            state = False
        self._feed_state = state
        if prev is None or state == prev:
            return
        if state and self.mode in ("rising", "both"):
            self.fire(rising=True)
        elif not state and self.mode in ("falling", "both"):
            self.fire(rising=False)

    # -- node contract ---------------------------------------------------------

    def settings(self) -> dict:
        return {"id": self.id, "level": self.level,
                "hysteresis": self.hysteresis, "mode": self.mode,
                "modes": list(MODES), "source": self.source}


class ThresholdManager:
    """All threshold instances + their server watch synths.

    Instance record: {"node": ThresholdNode, "synth": watch synth | None,
    "tag": int}. The /tr callback is registered ONCE per live server and
    dispatches on the trigger id (tag). Server objects are None when no
    engine is up (headless tests) — every server touch is guarded.
    """

    def __init__(self, app) -> None:
        self.app = app
        self.instances: dict[str, dict] = {}
        self._lock = threading.Lock()
        # PER-SERVER registration (same live-found bug class as
        # LFOManager): a device-switch engine reboot must re-send the
        # synthdef AND re-register the /tr callback on the NEW server.
        self._registered_server = None
        self._callback = None          # the /tr OscCallback
        self._callback_server = None   # ...and which server owns it
        self._next_tag = 700           # SendTrig ids: distinct, arbitrary base

    # -- server plumbing ---------------------------------------------------------

    def _server(self):
        eng = self.app.engine
        return eng.server if eng and getattr(eng, "server", None) else None

    def _ensure_server_side(self, server) -> None:
        if self._registered_server is not server:
            server.add_synthdefs(_threshold_watch)
            server.sync()
            self._registered_server = server
        if self._callback is None or self._callback_server is not server:
            self._callback = server.register_osc_callback(
                pattern=["/tr"], procedure=self._on_tr)
            self._callback_server = server

    def _on_tr(self, message) -> None:
        """/tr handler (OSC thread): [node_id, trig_id, value] → the
        matching threshold's ping. Unknown tags (scopes, other SendTrigs)
        fall through silently."""
        try:
            _, trig_id, value = message.contents
        except Exception:  # noqa: BLE001
            return
        for rec in list(self.instances.values()):
            if rec["tag"] == int(trig_id):
                rec["node"].on_tr(float(value))
                return

    def reset(self) -> None:
        """Engine went away — server-side objects are already gone."""
        self._registered_server = None
        self._callback = None
        self._callback_server = None
        for rec in self.instances.values():
            rec["synth"] = None

    # -- instances ---------------------------------------------------------------

    def spawn(self, want_id: str | None = None) -> str:
        with self._lock:
            tid = want_id or alloc_id("threshold", self.instances.keys())
            if tid not in self.instances:
                self._next_tag += 1
                self.instances[tid] = {"node": ThresholdNode(self.app, tid),
                                       "synth": None, "tag": self._next_tag}
            return tid

    def remove(self, tid: str) -> None:
        with self._lock:
            rec = self.instances.pop(tid, None)
        if rec is None:
            raise KeyError(f"no threshold {tid!r}")
        self._free_synth(rec)

    def configure(self, tid: str, level=None, hysteresis=None, mode=None,
                  source="__unset__") -> None:
        rec = self.instances.get(tid)
        if rec is None:
            raise KeyError(f"no threshold {tid!r}")
        node = rec["node"]
        if level is not None:
            node.level = max(-1.0, min(1.0, float(level)))
        if hysteresis is not None:
            node.hysteresis = max(0.0, min(0.5, float(hysteresis)))
        if mode is not None:
            if mode not in MODES:
                raise ValueError(f"unknown threshold mode {mode!r}")
            node.mode = mode
        node._feed_state = None       # window moved: re-latch on next feed
        if source != "__unset__" and source != node.source:
            self._set_source(rec, source)
        elif rec["synth"] is not None:
            # moving the window/gates can flip the comparator or lift an
            # edge gate mid-high (r_on 0→1 with state=1 IS a 0→1 trigger
            # transition) — re-arm so that phantom edge is swallowed
            node._armed_at = time.monotonic()
            try:
                rec["synth"].set(**self._synth_params(node))
            except Exception:  # noqa: BLE001
                pass

    # -- the CV wire (LFO → threshold, single-input) ------------------------------

    def wire(self, action: str, tid: str, lfo_id: str | None) -> None:
        rec = self.instances.get(tid)
        if rec is None:
            raise KeyError(f"no threshold {tid!r}")
        if action == "add":
            if lfo_id not in self.app.lfos.instances:
                raise KeyError(f"no LFO {lfo_id!r}")
            self._set_source(rec, lfo_id)
        elif action == "remove":
            if rec["node"].source == lfo_id or lfo_id is None:
                self._set_source(rec, None)
        else:
            raise ValueError(f"unknown threshold_wire action {action!r}")

    def _set_source(self, rec, lfo_id: str | None) -> None:
        node = rec["node"]
        if lfo_id == node.source and rec["synth"] is not None:
            return
        self._free_synth(rec)
        node.source = lfo_id
        if lfo_id is None:
            return
        lrec = self.app.lfos.instances.get(lfo_id)
        server = self._server()
        if server is None or lrec is None or lrec.get("node") is None:
            return   # headless: the data model still carries the wire
        self._ensure_server_side(server)
        node._armed_at = time.monotonic()
        rec["synth"] = server.add_synth(
            _threshold_watch,
            add_action=AddAction.ADD_AFTER,   # always downstream of its norm
            target_node=lrec["node"],
            kin=int(lrec["bus"]), tag=rec["tag"],
            **self._synth_params(node),
        )

    def _free_synth(self, rec) -> None:
        s = rec.get("synth")
        rec["synth"] = None
        try:
            if s is not None:
                s.free()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _synth_params(node: ThresholdNode) -> dict:
        return {"lo": node.level - node.hysteresis,
                "hi": node.level + node.hysteresis,
                "r_on": 1 if node.mode in ("rising", "both") else 0,
                "f_on": 1 if node.mode in ("falling", "both") else 0}

    # -- resilience --------------------------------------------------------------

    def on_lfo_removed(self, lfo_id: str) -> None:
        """The source LFO is gone: the CV-in unwires (watch synth freed)."""
        for rec in self.instances.values():
            if rec["node"].source == lfo_id:
                self._set_source(rec, None)

    def on_engine_up(self) -> None:
        """Engine (re)booted: respawn watch synths for wired sources."""
        for rec in self.instances.values():
            src = rec["node"].source
            if src is not None:
                rec["node"].source = None   # force a fresh spawn
                try:
                    self._set_source(rec, src)
                except Exception:  # noqa: BLE001
                    rec["node"].source = src

    def clear(self) -> None:
        for tid in list(self.instances):
            try:
                self.remove(tid)
            except KeyError:
                pass

    # -- persistence / state -----------------------------------------------------

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [{k: v for k, v in rec["node"].settings().items()
                     if k in ("id", "level", "hysteresis", "mode", "source")}
                    for rec in self.instances.values()]

    def restore(self, data) -> None:
        self.clear()
        for e in data or []:
            tid = self.spawn(want_id=e.get("id"))
            try:
                self.configure(tid, level=e.get("level"),
                               hysteresis=e.get("hysteresis"),
                               mode=e.get("mode"),
                               source=e.get("source"))
            except Exception as exc:  # noqa: BLE001
                print(f"[threshold] could not restore {tid}: {exc}")

    def state(self) -> list[dict]:
        with self._lock:
            return [rec["node"].settings()
                    for rec in self.instances.values()]
