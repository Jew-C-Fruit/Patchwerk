"""Binary LEVEL sources on the Python control plane: Button and Clock.

Since the binary rework there is ONE binary signal kind — sources own
hi/lo LEVELS and edges DERIVE from level changes (synthbase/gate.py has
the model). What used to be a "ping" is now just a pulse: a level going
hi then lo, propagating through the graph. Wires still ride
app.ctl_wires with the kind inferred from the source endpoint.

* ButtonTrigger ("button", "button.2", ...) — a manual LEVEL source with
  two modes:
  - momentary (latch=False, default): the level is hi WHILE HELD —
    press() drives it hi, release() drops it lo. Hold-to-enable a :pwr,
    tap to fire a downstream trig-in.
  - persistent (latch=True): press() TOGGLES the level; release() is a
    no-op — the button is a latching switch.
  fire() is the click-compat path: press() then (momentary only)
  release() — a pulse. A BOUND CC follows the same modes: momentary →
  the level FOLLOWS the CC (>= 0.5 = down; press/release on crossings);
  latch → toggle on the rising crossing. Binding/arming is unchanged:
  pairing mode captures the next COMPATIBLE input — non-tonal MIDI
  controls (the router never surfaces note messages as bindable events)
  or an unassigned computer key (captured client-side).
  Emits {"kind": "gate", "id", "on"} on level change (the GUI LED) and
  keeps the {"kind": "ping", "src"} tap on RISING edges (pulse anims).

* ClockTrigger ("clock", "clock.2", ...) — the one PULSE-ONLY source:
  its persistent level is always lo; each transport-grid tick calls
  gates.pulse(id) (level hi → rising edges fire → level lo, silently).
  Phase-locked via transport.next_grid, never a free-running timer. Its
  ladder is CLOCK_DIVISIONS: the global DIVISIONS plus multi-bar periods
  past 1/1 — clock-only; arp/deriver ladders are deliberately untouched.
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
    """A spawnable manual/bindable binary LEVEL source."""

    def __init__(self, app, bid: str = "button") -> None:
        self.app = app
        self.id = bid
        self.binding: dict | None = None  # {"kind":"cc","cc":n} | {"kind":"key","code":s}
        self.armed = False                # pairing mode (transient, not persisted)
        self.latch = False                # False = momentary, True = persistent
        self.level = False                # the button's binary level
        self._last_unit = 0.0             # bound-CC crossing detector state

    # -- the level -------------------------------------------------------------

    def _set_level(self, lvl: bool) -> None:
        lvl = bool(lvl)
        if lvl == self.level:
            return
        self.level = lvl
        try:
            if lvl:   # the ping tap survives on rising edges (pulse anims)
                self.app._emit_midi_event({"kind": "ping", "src": self.id})
            self.app._emit_midi_event(
                {"kind": "gate", "id": self.id, "on": lvl})
        except Exception:  # noqa: BLE001
            pass
        try:
            self.app.gates.on_source_level(self.id)
        except Exception:  # noqa: BLE001
            pass

    def press(self) -> None:
        """Mouse-down / key-down / CC rising crossing. Momentary: level
        hi while held. Latch: toggles."""
        self._set_level(not self.level if self.latch else True)

    def release(self) -> None:
        """Mouse-up / key-up / CC falling crossing. Latch mode ignores it."""
        if not self.latch:
            self._set_level(False)

    def fire(self) -> None:
        """Click compat: a full press+release — momentary buttons pulse,
        latched buttons toggle."""
        self.press()
        if not self.latch:
            self.release()

    # -- MIDI capture / bound-CC levels (called from the MIDI thread) ----------

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
            falling = unit < 0.5 and self._last_unit >= 0.5
            self._last_unit = float(unit)
            if self.latch:
                if rising:          # toggle on the rising crossing only
                    self.press()
            elif rising:            # momentary: the level FOLLOWS the CC
                self.press()
            elif falling:
                self.release()
            return True
        return False

    # -- node contract ---------------------------------------------------------

    def configure(self, binding="__unset__", armed=None, latch=None) -> None:
        if binding != "__unset__":
            self.binding = dict(binding) if isinstance(binding, dict) else None
            self._last_unit = 0.0
        if armed is not None:
            self.armed = bool(armed)
        if latch is not None and bool(latch) != self.latch:
            self.latch = bool(latch)
            # switching modes drops the level — a momentary button must
            # never wake up stuck hi with nobody holding it
            self._set_level(False)

    def settings(self) -> dict:
        return {"id": self.id, "binding": self.binding, "armed": self.armed,
                "latch": bool(self.latch), "on": bool(self.level)}

    def shutdown(self) -> None:
        self.armed = False


class ClockTrigger:
    """A spawnable transport-locked PULSE source."""

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
        try:
            self.app.gates.pulse(self.id)   # hi → edges fire → lo, silently
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
