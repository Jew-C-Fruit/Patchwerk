"""MIDI input -> rack control.

Uses mido (python-rtmidi backend) over CoreMIDI. Jobs:

1. Notes: mono, last-note priority, with pitch bend and sustain (CC64),
   driving one target source module exposing ``freq`` and ``gate``.
2. CCs: a bindings dict maps CC number -> (module_key, param_name); values
   are scaled through the param's range/curve.
3. Events out: every incoming CC/bend/sustain is reported via ``on_event``
   so the GUI can track physical controls (virtual sliders follow real ones)
   and show a monitor of what the hardware sends.

Sensors (pyserial) will feed the same ``handle_cc``-style path later — a
control value is a control value regardless of where it came from.
"""

from __future__ import annotations

from typing import Callable

import time

import mido

from .allocation import MonoLatest
from .rack import Rack

A4_MIDI, A4_FREQ = 69, 440.0
BEND_RANGE_SEMITONES = 2.0
SUSTAIN_CC = 64


def midi_to_freq(note: int) -> float:
    return A4_FREQ * 2 ** ((note - A4_MIDI) / 12)


_ports_cache: dict = {"t": 0.0, "names": None}


def list_inputs(force: bool = False) -> list[str]:
    now = time.monotonic()
    if not force and _ports_cache["names"] is not None and now - _ports_cache["t"] < 3.0:
        return _ports_cache["names"]
    try:
        names = mido.get_input_names()
    except Exception as exc:  # noqa: BLE001 — no MIDI backend is never fatal
        print(f"[midi] backend unavailable ({exc.__class__.__name__}) — no MIDI")
        names = []
    _ports_cache["names"] = names
    _ports_cache["t"] = now
    return names


#: The mono voice is now the ``mono-latest`` policy of the allocation
#: framework (``synthbase/allocation.py``), which it shares with the poly
#: voice and — from item 29 — the drone. Re-exported here because the import
#: path ``from .midi import MonoVoice`` is what the app and patches use, and
#: because a mono voice IS what a MIDI keyboard drives by default.
MonoVoice = MonoLatest


class MidiRouter:
    """Opens a MIDI input port and routes messages to the rack."""

    def __init__(
        self,
        rack: Rack,
        cc_bindings: dict[int, tuple[str, str]] | None = None,
        notes_to: str | None = None,
        port_name: str | None = None,
        verbose: bool = True,
        voice: MonoVoice | None = None,  # share a voice with other controllers (GUI)
        on_event: Callable[[dict], None] | None = None,  # runs on the MIDI thread!
    ) -> None:
        self.rack = rack
        self.cc_bindings = cc_bindings or {}
        self.voice = voice or (MonoVoice(rack, notes_to) if notes_to else None)
        self.verbose = verbose
        self.port = None
        self.port_name = port_name
        self.active_port: str | None = None  # what actually got opened
        self.on_event = on_event

    def start(self) -> None:
        names = list_inputs()
        if not names:
            print("[midi] no MIDI inputs found — running without MIDI")
            return
        # Default: prefer real hardware over virtual IAC buses.
        hardware = [n for n in names if "iac" not in n.lower()]
        name = self.port_name or (hardware[0] if hardware else names[0])
        try:
            self.port = mido.open_input(name, callback=self._handle)
        except Exception as exc:  # noqa: BLE001
            print(f"[midi] could not open {name!r}: {exc} — running without MIDI")
            return
        self.active_port = name
        print(f"[midi] listening on {name!r}")

    def stop(self) -> None:
        if self.port is not None:
            self.port.close()
            self.port = None
        self.active_port = None

    # -- message handling ---------------------------------------------------

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 — GUI trouble must not kill MIDI
                pass

    def _handle(self, msg: mido.Message) -> None:
        try:
            if msg.type == "note_on" and msg.velocity > 0:
                if self.voice:
                    self.voice.note_on(msg.note, msg.velocity)
            elif msg.type in ("note_off", "note_on"):  # note_on vel 0 == off
                if self.voice:
                    self.voice.note_off(msg.note)
            elif msg.type == "pitchwheel":
                semis = (msg.pitch / 8192.0) * BEND_RANGE_SEMITONES
                if self.voice:
                    self.voice.set_bend(semis)
                self._emit({"kind": "bend", "semitones": round(semis, 3)})
            elif msg.type == "control_change":
                if msg.control == SUSTAIN_CC:
                    on = msg.value >= 64
                    if self.voice:
                        self.voice.set_sustain(on)
                    self._emit({"kind": "sustain", "on": on})
                else:
                    self.handle_cc(msg.control, msg.value / 127.0)
        except Exception as exc:  # noqa: BLE001 — a bad mapping must not kill the port
            print(f"[midi] error handling {msg}: {exc}")

    def handle_cc(self, control: int, unit_value: float) -> None:
        """unit_value is normalized 0..1 (shared entry point for sensors/GUI)."""
        binding = self.cc_bindings.get(control)
        if binding is None:
            self._emit({"kind": "cc", "cc": control, "unit": round(unit_value, 4)})
            if self.verbose:
                print(f"[midi] unbound CC {control} = {unit_value:.2f}")
            return
        key, param_name = binding
        inst = self.rack.find(key)
        p = inst.module.params.get(param_name)
        if p is None:
            print(f"[midi] {key} has no param {param_name!r}")
            return
        value = p.from_unit(unit_value)
        self.rack.set_param(key, param_name, value)
        self._emit({
            "kind": "cc", "cc": control, "unit": round(unit_value, 4),
            "bound": [key, param_name], "value": value,
        })
        if self.verbose:
            print(f"[midi] {key}.{param_name} = {value:.2f}")
