"""MIDI input -> rack control.

Uses mido (python-rtmidi backend) over CoreMIDI. Two jobs:

1. Notes: mono, last-note priority, driving one target source module that
   exposes ``freq`` and ``gate`` params (e.g. wobble_saw).
2. CCs: a bindings dict maps CC number -> (module_key, param_name); values
   are scaled through the param's range/curve.

Sensors (pyserial) will feed the same ``handle_cc``-style path later — a
control value is a control value regardless of where it came from.
"""

from __future__ import annotations

import mido

from .rack import Rack

A4_MIDI, A4_FREQ = 69, 440.0


def midi_to_freq(note: int) -> float:
    return A4_FREQ * 2 ** ((note - A4_MIDI) / 12)


def list_inputs() -> list[str]:
    try:
        return mido.get_input_names()
    except Exception as exc:  # noqa: BLE001 — no MIDI backend is never fatal
        print(f"[midi] backend unavailable ({exc.__class__.__name__}) — no MIDI")
        return []


class MonoVoice:
    """Last-note-priority mono voice driving one source module instance."""

    def __init__(self, rack: Rack, target_key: str) -> None:
        self.rack = rack
        self.target_key = target_key
        self._held: list[int] = []  # note stack, most recent last

    def note_on(self, note: int, velocity: int) -> None:
        if note in self._held:
            self._held.remove(note)
        self._held.append(note)
        self.rack.set_params(self.target_key, freq=midi_to_freq(note), gate=1)

    def note_off(self, note: int) -> None:
        if note in self._held:
            self._held.remove(note)
        if self._held:
            self.rack.set_params(self.target_key, freq=midi_to_freq(self._held[-1]))
        else:
            self.rack.set_params(self.target_key, gate=0)

    def all_off(self) -> None:
        self._held.clear()
        self.rack.set_params(self.target_key, gate=0)


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
    ) -> None:
        self.rack = rack
        self.cc_bindings = cc_bindings or {}
        self.voice = voice or (MonoVoice(rack, notes_to) if notes_to else None)
        self.verbose = verbose
        self.port = None
        self.port_name = port_name
        self.active_port: str | None = None  # what actually got opened

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

    def _handle(self, msg: mido.Message) -> None:
        try:
            if msg.type == "note_on" and msg.velocity > 0:
                if self.voice:
                    self.voice.note_on(msg.note, msg.velocity)
            elif msg.type in ("note_off", "note_on"):  # note_on vel 0 == off
                if self.voice:
                    self.voice.note_off(msg.note)
            elif msg.type == "control_change":
                self.handle_cc(msg.control, msg.value / 127.0)
        except Exception as exc:  # noqa: BLE001 — a bad mapping must not kill the port
            print(f"[midi] error handling {msg}: {exc}")

    def handle_cc(self, control: int, unit_value: float) -> None:
        """unit_value is normalized 0..1 (shared entry point for sensors/GUI)."""
        binding = self.cc_bindings.get(control)
        if binding is None:
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
        if self.verbose:
            print(f"[midi] {key}.{param_name} = {value:.2f}")
