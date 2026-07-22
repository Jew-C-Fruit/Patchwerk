"""Ping: a discrete EDGE signal on the Python control plane + its sources.

A ping is an instantaneous edge — trigger(), not a sustained gate. Ping
wires live in app.ctl_wires alongside note wires; the KIND is inferred
from the source endpoint (button/clock ids only ever emit pings, so a wire
from one is a ping wire). Consumers expose trigger() through
app._ping_sinks(src); derivers commit on it (their internal grid timer is
suppressed while a ping source is wired in — unwire and the timer resumes).

Two source nodes:

* ButtonTrigger ("button", "button.2", ...) — a manual trigger that can be
  BOUND to a physical control, key-binding style. Clicking the card fires
  directly. Arming pairing mode captures the next COMPATIBLE input:
  non-tonal MIDI controls (CCs; the router never surfaces note messages as
  bindable events, so a note-on can never bind) or an unassigned computer
  key (captured client-side; the GUI stores {"kind": "key", "code": ...}
  here so it survives presets/resume). A bound CC fires on the RISING edge
  (unit crosses 0.5 upward) so continuous controllers don't machine-gun.

* ClockTrigger ("clock", "clock.2", ...) — fires on a selectable transport
  grid division, phase-locked via transport.next_grid (never a free-running
  timer, so ticks stay aligned across tempo changes). Its ladder is
  CLOCK_DIVISIONS: the global DIVISIONS plus multi-bar periods past 1/1
  (item 6) — clock-only; arp/deriver ladders are deliberately untouched.

Both emit {"kind": "ping", "src": "<id>"} viz taps on fire.
"""

from __future__ import annotations

import threading
import time

from .transport import DIVISIONS

# The clock's own division ladder (item 6): multi-bar periods above 1/1,
# then the full global ladder. Entries are WHOLE-NOTE multiples (2/1 = 8
# beats...) — meter-independent, consistent with the existing 1/1 = 4-beats
# convention — and phase-lock to multiples from beat 0 via next_grid, so a
# 4/1 clock always fires on the same downbeats no matter when it spawned.
# The global DIVISIONS stays untouched on purpose: arp and deriver ladders
# must not grow multi-bar entries (Cole, 07-22).
CLOCK_DIVISIONS = {"8/1": 32.0, "4/1": 16.0, "2/1": 8.0, **DIVISIONS}


class ButtonTrigger:
    """A spawnable manual/bindable trigger node."""

    def __init__(self, app, bid: str = "button") -> None:
        self.app = app
        self.id = bid
        self.binding: dict | None = None  # {"kind":"cc","cc":n} | {"kind":"key","code":s}
        self.armed = False                # pairing mode (transient, not persisted)
        self._last_unit = 0.0             # bound-CC edge detector state

    # -- firing ----------------------------------------------------------------

    def fire(self) -> None:
        try:
            self.app._emit_midi_event({"kind": "ping", "src": self.id})
        except Exception:  # noqa: BLE001
            pass
        for s in self.app._ping_sinks(self.id):
            try:
                s.trigger()
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    # -- MIDI capture / bound-CC firing (called from the MIDI thread) ----------

    def on_cc(self, cc: int, unit: float) -> bool:
        """Feed a CC event through this button. Returns True if it was
        captured (arm) or consumed (bound to this CC)."""
        if self.armed:
            # capture: ONLY non-tonal controls ever reach here (the router
            # never surfaces note messages as events — the tone filter)
            self.binding = {"kind": "cc", "cc": int(cc)}
            self.armed = False
            self._last_unit = float(unit)
            try:
                self.app._emit_midi_event(
                    {"kind": "ping_bound", "id": self.id, "binding": self.binding})
            except Exception:  # noqa: BLE001
                pass
            return True
        b = self.binding
        if b and b.get("kind") == "cc" and int(b.get("cc", -1)) == int(cc):
            rising = unit >= 0.5 and self._last_unit < 0.5
            self._last_unit = float(unit)
            if rising:
                self.fire()
            return True
        return False

    # -- node contract ---------------------------------------------------------

    def configure(self, binding="__unset__", armed=None) -> None:
        if binding != "__unset__":
            self.binding = dict(binding) if isinstance(binding, dict) else None
            self._last_unit = 0.0
        if armed is not None:
            self.armed = bool(armed)

    def settings(self) -> dict:
        return {"id": self.id, "binding": self.binding, "armed": self.armed}

    def shutdown(self) -> None:
        self.armed = False


class ClockTrigger:
    """A spawnable transport-locked trigger node."""

    def __init__(self, app, cid: str = "clock") -> None:
        self.app = app
        self.id = cid
        self.division = "1/4"
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()
        self._kick = threading.Event()   # re-grid NOW (division changed)
        self._ensure_thread()

    def fire(self) -> None:
        try:
            self.app._emit_midi_event({"kind": "ping", "src": self.id})
        except Exception:  # noqa: BLE001
            pass
        for s in self.app._ping_sinks(self.id):
            try:
                s.trigger()
            except Exception:  # noqa: BLE001
                pass

    def configure(self, division=None) -> None:
        if division in CLOCK_DIVISIONS and division != self.division:
            self.division = division
            self._kick.set()   # interrupt the current sleep, re-grid at once

    def settings(self) -> dict:
        return {"id": self.id, "division": self.division,
                "divisions": list(CLOCK_DIVISIONS)}

    def shutdown(self) -> None:
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the tick thread (phase-locked to the transport) -----------------------

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _sleep_until(self, t: float) -> str:
        while not self._quit.is_set():
            if self._kick.is_set():
                self._kick.clear()
                return "rearm"        # division changed — recompute the grid
            dt = t - time.monotonic()
            if dt <= 0:
                return "fire"
            time.sleep(min(dt, 0.05))
        return "quit"

    def _run(self) -> None:
        transport = self.app.transport
        while not self._quit.is_set():
            beats = CLOCK_DIVISIONS.get(self.division, 1.0)
            gb, t = transport.next_grid(beats)
            r = self._sleep_until(t)
            if r == "quit":
                return
            if r == "rearm":
                continue
            # a STOPPED transport freezes beats_now — next_grid then returns
            # a constant past time forever; don't spin, don't fire
            if not transport.running:
                time.sleep(0.1)
                continue
            self.fire()
