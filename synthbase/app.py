"""SynthApp: one object that owns the whole running system.

Engine + rack + master section + MIDI router + hot reloader, with the
operations the GUI (or any other client) needs: state snapshot, set param,
set volume, notes from any controller, switch patch, switch audio devices
(full engine rebuild).

Everything here is controller-agnostic: the web GUI, hardware MIDI, and
future sensors all call the same handful of methods.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from .arp import Arpeggiator
from .drone import DroneBrain
from .drums import DrumMachine
from .lfo import LFOManager
from .scope import Scope
from .looper import Looper
from . import presets as presets_mod
from .transport import Transport, _click
from .audio_devices import list_audio_devices
from .engine import Engine
from .master import MasterSection
from .midi import MidiRouter, MonoVoice
from .midi import list_inputs as _list_midi_inputs
from .module import load_all_modules
from .rack import Rack
from .watcher import Reloader

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = REPO_ROOT / "modules"
PATCHES_DIR = REPO_ROOT / "patches"


def _read_patch(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location(f"synthpatch_{path.stem}", path)
    py = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(py)
    patch = getattr(py, "PATCH", None)
    if not isinstance(patch, dict) or "chain" not in patch:
        raise ValueError(f"{path.name} must define PATCH = {{'chain': [...]}}")
    return patch


def list_patches() -> list[str]:
    return [p.stem for p in sorted(PATCHES_DIR.glob("*.py")) if not p.name.startswith("_")]


class SynthApp:
    def __init__(
        self,
        input_device: str | None = None,
        output_device: str | None = None,
        use_midi: bool = True,
        use_reload: bool = True,
        hardware_buffer_size: int | None = 256,
    ) -> None:
        self.input_device = input_device
        self.output_device = output_device
        self.hardware_buffer_size = hardware_buffer_size
        self.use_midi = use_midi
        self.use_reload = use_reload
        self.midi_enabled = use_midi
        self.midi_port: str | None = None  # None = auto (prefer hardware)

        self.engine: Engine | None = None
        self.rack: Rack | None = None
        self.master: MasterSection | None = None
        self.router: MidiRouter | None = None
        self.reloader: Reloader | None = None
        self.voice: MonoVoice | None = None
        self.arp: Arpeggiator | None = None
        self._arp_settings: dict = {}  # persists across patch switches
        self.transport = Transport()
        self.drone = DroneBrain(self)
        self.drums = DrumMachine(self)
        self.lfos = LFOManager(self)
        self.looper = Looper(self)
        self.scope = Scope(self)
        self.on_beat_event = None  # set by GuiServer; called from the beat thread

        self.on_midi_event = None  # set by GuiServer; called from MIDI thread
        self.patch_name: str | None = None
        self.patch: dict | None = None
        # graph overlay over the linear chain: None = pure linear derivation;
        # a list of {"from": key, "to": key|"master"|None} = user rewires,
        # re-applied after every rebuild for keys that still exist.
        self.graph_wires: list[dict] | None = None
        self._voice_target_override: str | None = None  # set_voice_target survivor
        self.registry: dict = {}
        self.module_errors: dict = {}
        self._lock = threading.RLock()  # GUI thread + MIDI thread both call in

    # -- lifecycle ------------------------------------------------------------

    def start(self, patch_name: str) -> None:
        with self._lock:
            self.registry, self.module_errors = load_all_modules(MODULES_DIR)
            for fname, exc in self.module_errors.items():
                print(f"[modules] SKIPPED {fname}: {exc!r}")
            self.engine = Engine(
                input_device=self.input_device,
                output_device=self.output_device,
                hardware_buffer_size=self.hardware_buffer_size,
            ).boot()
            self.master = MasterSection(self.engine)
            self.engine.server.add_synthdefs(_click)
            self.engine.server.sync()
            self.transport.on_beat = self._handle_beat
            self.transport.start()
            self._build_patch(patch_name)
            if self.use_reload:
                self.reloader = Reloader(self.engine, self.rack, MODULES_DIR)
                self.reloader.start()

    def _build_patch(self, patch_name: str) -> None:
        """(Re)build rack + master + MIDI for a patch. Engine must be booted."""
        path = PATCHES_DIR / f"{patch_name}.py"
        self._build_from(_read_patch(path), patch_name)

    def _build_from(self, patch: dict, patch_name: str) -> None:

        if self.router:
            self.router.stop()
            self.router = None
        if self.arp:
            self.arp.shutdown()
            self.arp = None
        if self.master and self.master._master_node is not None:
            self.master.stop()
        if self.rack:
            self.rack.teardown()

        self.rack = Rack(self.engine, self.registry)
        self.rack.build(patch["chain"])
        self.master.start()
        if self.reloader:
            self.reloader.rack = self.rack  # point hot reload at the new rack

        bindings = patch.get("bindings", {})
        target = bindings.get("notes_to") or self._guess_voice_target()
        if self._voice_target_override:
            try:
                inst = self.rack.find(self._voice_target_override)
                if inst.module.kind == "source" and "gate" in inst.settings:
                    target = self._voice_target_override
            except KeyError:
                pass  # override's module left the chain — fall back
        self.voice = MonoVoice(self.rack, target) if target else None
        if self.voice:
            self.arp = Arpeggiator(self.voice, self.transport)
            self.arp.on_note = self.drone.observe
            self.arp.on_note_in = self.looper.observe_input
            self.voice.on_voiced = self._emit_voiced
            self.arp.configure(**{**self._arp_settings, **patch.get("arp", {})})
            self._arp_settings = {
                k: v for k, v in self.arp.settings().items() if k != "patterns"
            }
        self.patch_name = patch_name
        self.patch = patch
        self.rack.on_node_replaced = self._on_node_replaced
        self.lfos.assignments.clear()  # old rack's nodes are gone with it
        self.rack.mapped.clear()
        self.drone.spawn()  # re-add the drone to the fresh rack if enabled
        self._reapply_graph_wires()
        self._restart_midi()

    def _reapply_graph_wires(self) -> None:
        """After ANY rebuild the rack comes up linear; re-impose the user's
        stored graph wires for whichever keys still exist."""
        if self.graph_wires is None or not self.rack:
            return
        existing = {i.key for i in self.rack.instances if not i.service}
        for w in self.graph_wires:
            if w["from"] not in existing:
                continue
            try:
                if w["to"] is None:
                    self.rack.audio_disconnect(w["from"])
                elif w["to"] == "master" or w["to"] in existing:
                    self.rack.audio_rewire(w["from"], w["to"])
            except Exception:  # noqa: BLE001 — one bad wire must not stop the rest
                pass
        try:
            self.rack.reorder_for_wires(self.graph_wires)
        except Exception:  # noqa: BLE001
            pass

    def _on_node_replaced(self, key: str) -> None:
        self.lfos.on_node_replaced(key)

    def _emit_voiced(self, note: int, on: bool) -> None:
        try:
            self.looper.observe(note, on)
        except Exception:  # noqa: BLE001
            pass
        self._emit_midi_event({"kind": "voiced", "note": int(note), "on": bool(on)})

    def _restart_midi(self) -> None:
        """(Re)open the MIDI router against the current rack/voice/port."""
        if self.router:
            self.router.stop()
            self.router = None
        if not (self.use_midi and self.midi_enabled and self.rack):
            return
        bindings = (self.patch or {}).get("bindings", {})
        self.router = MidiRouter(
            self.rack,
            cc_bindings=bindings.get("cc"),
            port_name=self.midi_port or bindings.get("midi_in"),
            voice=self.arp or self.voice,
            verbose=False,
            on_event=self._emit_midi_event,
        )
        self.router.start()

    def _emit_midi_event(self, event: dict) -> None:
        """Forward MIDI events to whoever is listening (GUI). MIDI thread!"""
        callback = self.on_midi_event
        if callback is not None:
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                pass

    def edit_chain(self, action: str, key: str, index: int | None = None) -> None:
        """Live chain surgery: add/remove/move a stage. Auto-snaps wiring by
        rebuilding the chain in the new order with all settings, enabled
        states, and LFO assignments preserved."""
        with self._lock:
            stages = [
                (i.key, dict(i.settings), i.enabled)
                for i in self.rack.instances if not i.service
            ]
            keys = [k for k, _, _ in stages]
            if action == "add":
                if key not in self.registry:
                    raise ValueError(f"unknown module {key!r}")
                if key in keys:
                    raise ValueError(f"{key} is already in the chain")
                stages.append((key, {}, True))
            elif action == "remove":
                stages = [s for s in stages if s[0] != key]
                if self.graph_wires is not None:
                    # splice-out healing: bridge everything that fed the
                    # removed module to the removed module's own destination
                    dst = next((w["to"] for w in self.graph_wires
                                if w["from"] == key), None)
                    self.graph_wires = [
                        {**w, "to": dst} if w["to"] == key else w
                        for w in self.graph_wires if w["from"] != key
                    ]
                    if self.drums.target == key:
                        self.drums.target = dst
            elif action == "move":
                i = keys.index(key)
                j = max(0, min(len(stages) - 1, i + (index or 0)))
                stages.insert(j, stages.pop(i))
            # keep a source at the head (effects can't start a chain)
            stages.sort(key=lambda s: 0 if (self.registry[s[0]].kind == "source") else 1)
            if not stages:
                raise ValueError("chain cannot be empty")
            if self.registry[stages[0][0]].kind != "source":
                raise ValueError("chain needs at least one source")
            lfo_snap = self.lfos.snapshot()
            new_patch = dict(self.patch or {})
            new_patch["chain"] = [(k, {}) for k, _, _ in stages]
            self._build_from(new_patch, self.patch_name)
            for k, settings, enabled in stages:
                try:
                    clean = {n: v for n, v in settings.items()
                             if n in self.registry[k].params}
                    if clean:
                        self.rack.set_params(k, **clean)
                    if not enabled:
                        self.rack.set_enabled(k, False)
                except KeyError:
                    pass
            self.lfos.restore({aid: cfg for aid, cfg in lfo_snap.items()
                               if aid.split(".")[0] in [k for k, _, _ in stages]})

    def graph_wire(self, action: str, src: str, dst: str | None = None) -> None:
        """Live audio rewiring: add (src → dst|"master") or remove (park src on
        the null bus). One outgoing audio wire per source; fan-in is free
        (buses sum). Stored so rebuilds re-apply it."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            self.rack.find(src)  # raises a helpful KeyError for the GUI
            if self.graph_wires is None:
                self.graph_wires = self.rack.audio_wires()
            wires = [w for w in self.graph_wires if w["from"] != src]
            if action == "add":
                if not dst:
                    raise ValueError("graph_wire add needs a destination")
                if dst != "master":
                    self.rack.find(dst)
                    adj = {w["from"]: w["to"] for w in wires}
                    cur, hops = dst, 0
                    while cur not in (None, "master") and hops < 64:
                        if cur == src:
                            raise ValueError(f"{src} → {dst} would create an audio cycle")
                        cur = adj.get(cur)
                        hops += 1
                wires.append({"from": src, "to": dst})
                self.graph_wires = wires
                self.rack.audio_rewire(src, dst)
            elif action == "remove":
                wires.append({"from": src, "to": None})
                self.graph_wires = wires
                self.rack.audio_disconnect(src)
            else:
                raise ValueError(f"unknown graph_wire action {action!r}")
            self.rack.reorder_for_wires(self.graph_wires)

    def spawn_unconnected(self, key: str) -> None:
        """Add a module to the rack with its audio out parked on the null bus
        (palette click / empty-canvas drop). Snapshot the current wiring FIRST
        so the linear rebuild's re-tailing doesn't reroute existing modules."""
        with self._lock:
            if self.graph_wires is None and self.rack:
                self.graph_wires = self.rack.audio_wires()
            self.edit_chain("add", key)
            self.graph_wire("remove", key)

    def set_voice_target(self, key: str) -> None:
        """Re-aim the mono voice at another playable source (GUI wire re-drag)."""
        with self._lock:
            if not (self.rack and self.voice):
                raise RuntimeError("no voice to retarget")
            inst = self.rack.find(key)
            if inst.module.kind != "source" or "gate" not in inst.settings \
                    or "freq" not in inst.settings:
                raise ValueError(f"{key} is not a note-playable source")
            self.voice.all_off()  # silence the old target before switching
            self.voice.target_key = key
            self._voice_target_override = key

    def _guess_voice_target(self) -> str | None:
        """First source in the chain that looks note-playable (freq + gate)."""
        if not self.rack:
            return None
        for inst in self.rack.instances:
            if inst.module.kind == "source" and "freq" in inst.settings and "gate" in inst.settings:
                return inst.key
        return None

    def _handle_beat(self, bar: int, beat: int) -> None:
        """Runs on the transport's beat thread."""
        if self.transport.click_enabled and self.engine and self.engine.root_group:
            try:
                hi = beat == 0 and self.transport.click_accent
                self.engine.root_group.add_synth(
                    _click,
                    add_action="add_to_tail",
                    freq=2000 if hi else 1400,   # high tick on the 1 (toggleable)
                    amp=0.3 if hi else 0.18,
                )
            except Exception:  # noqa: BLE001
                pass
        callback = self.on_beat_event
        if callback is not None:
            try:
                callback(bar, beat)
            except Exception:  # noqa: BLE001
                pass

    def set_transpose(self, semitones: int) -> None:
        with self._lock:
            if self.voice:
                self.voice.transpose = max(-24, min(24, int(semitones)))

    def set_drums(self, **settings) -> None:
        with self._lock:
            self.drums.configure(**settings)

    def set_looper(self, **settings) -> None:
        with self._lock:
            self.looper.configure(**settings)

    def lfo_assign(self, key: str, name: str, **cfg) -> None:
        with self._lock:
            self.lfos.assign(key, name, **cfg)

    def lfo_unassign(self, aid: str) -> None:
        with self._lock:
            self.lfos.unassign(aid)

    def lfo_set(self, aid: str, **cfg) -> None:
        with self._lock:
            self.lfos.configure(aid, **cfg)

    def save_preset(self, name: str) -> str:
        return presets_mod.save_preset(self, name)

    def load_preset(self, name: str) -> None:
        presets_mod.load_preset(self, name)

    def delete_preset(self, name: str) -> None:
        presets_mod.delete_preset(name)

    def tonic_state(self) -> dict:
        with self.drone._lock:
            self.drone._decay(__import__("time").monotonic())
            weights = list(self.drone._weights)
        total = max(sum(weights), 1e-9)
        est = self.drone.estimate()
        from .drone import NOTE_NAMES
        return {
            "weights": [round(w / total, 4) for w in weights],
            "root": NOTE_NAMES[est] if est is not None else None,
        }

    def set_drone(self, **settings) -> None:
        with self._lock:
            self.drone.configure(**settings)

    def set_transport(self, bpm=None, beats_per_bar=None, click=None, accent=None,
                      playing=None) -> None:
        if accent is not None:
            self.transport.click_accent = bool(accent)
        if playing is not None:
            self.transport.set_running(bool(playing))
            if not playing:
                if self.arp:
                    self.arp._safe_all_off() if hasattr(self.arp, "_safe_all_off") else None
                try:
                    inst = self.rack.find("drone")
                    inst.node.pause()
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    inst = self.rack.find("drone")
                    inst.node.unpause()
                except Exception:  # noqa: BLE001
                    pass
        if bpm is not None:
            self.transport.set_bpm(bpm)
        if beats_per_bar is not None:
            self.transport.set_meter(beats_per_bar)
        if click is not None:
            self.transport.click_enabled = bool(click)

    def stop(self) -> None:
        with self._lock:
            self.looper.shutdown()
            self.drums.shutdown()
            self.lfos.clear()
            self.drone.shutdown()
            self.transport.shutdown()
            if self.reloader:
                self.reloader.stop()
                self.reloader = None
            if self.router:
                self.router.stop()
                self.router = None
            if self.arp:
                self.arp.shutdown()
                self.arp = None
            if self.master:
                self.master.stop()
            if self.rack:
                self.rack.teardown()
                self.rack = None
            if self.engine:
                self.engine.quit()
                self.engine = None

    # -- GUI-facing operations ---------------------------------------------------

    def select_patch(self, patch_name: str) -> None:
        with self._lock:
            self._build_patch(patch_name)

    def set_devices(self, input_device: str | None, output_device: str | None) -> None:
        """Switch audio I/O — requires a full engine reboot (brief silence)."""
        with self._lock:
            patch_name = self.patch_name
            volume = self.master.volume if self.master else 0.8
            self.stop()
            self.input_device = input_device
            self.output_device = output_device
            self.start(patch_name)
            self.master.set_volume(volume)

    def set_param(self, key: str, name: str, value: float) -> None:
        with self._lock:
            if self.rack:
                self.rack.set_param(key, name, float(value))

    def set_param_unit(self, key: str, name: str, unit_value: float) -> float:
        """Set a param from a normalized 0..1 value (GUI sliders, sensors).
        If the param is LFO-mapped, the value steers the LFO's center."""
        with self._lock:
            inst = self.rack.find(key)
            p = inst.module.params[name]
            value = p.from_unit(float(unit_value))
            if self.lfos.set_center_unit(key, name, float(unit_value)):
                inst.settings[name] = value  # remembered for unassign-restore
                return value
            self.rack.set_param(key, name, value)
            return value

    def set_midi(self, port_name: str | None, enabled: bool) -> None:
        """Choose the MIDI note/CC source (or turn MIDI off)."""
        with self._lock:
            self.midi_port = port_name
            self.midi_enabled = bool(enabled)
            self._restart_midi()

    def set_enabled(self, key: str, enabled: bool) -> None:
        with self._lock:
            if self.rack:
                self.rack.set_enabled(key, enabled)

    def set_volume(self, volume: float) -> None:
        with self._lock:
            if self.master:
                self.master.set_volume(volume)

    def note_on(self, note: int, velocity: int = 100) -> None:
        sink = self.arp or self.voice
        if sink:
            sink.note_on(int(note), int(velocity))

    def note_off(self, note: int) -> None:
        sink = self.arp or self.voice
        if sink:
            sink.note_off(int(note))

    def all_notes_off(self) -> None:
        sink = self.arp or self.voice
        if sink:
            sink.all_off()

    def set_arp(self, **settings) -> None:
        with self._lock:
            if self.arp:
                self.arp.configure(**settings)
                self._arp_settings = {
                    k: v for k, v in self.arp.settings().items() if k != "patterns"
                }

    def levels(self) -> dict:
        return self.master.levels() if self.master else {"out": [0, 0], "in": None}

    # -- state snapshot for clients -----------------------------------------------

    def state(self) -> dict:
        with self._lock:
            chain = []
            if self.rack:
                for inst in self.rack.instances:
                    chain.append({
                        "key": inst.key,
                        "name": inst.module.name,
                        "kind": inst.module.kind,
                        "family": inst.module.family,
                        "enabled": inst.enabled,
                        "service": inst.service,
                        "params": {
                            pname: {
                                "min": p.minimum,
                                "max": p.maximum,
                                "curve": p.curve,
                                "options": list(p.options),
                                "default": p.default,
                                "lfo": (inst.key, pname) in self.rack.mapped,
                                "value": inst.settings.get(pname, p.default),
                            }
                            for pname, p in inst.module.params.items()
                        },
                    })
            return {
                "patch": self.patch_name,
                "patches": list_patches(),
                "chain": chain,
                "volume": self.master.volume if self.master else 0.8,
                "devices": list_audio_devices(),
                "current_input": self.input_device,
                "current_output": self.output_device,
                "input_enabled": bool(
                    self.engine and self.engine.options.input_bus_channel_count > 0
                ),
                "boot_note": self.engine.boot_note if self.engine else None,
                "voice_target": self.voice.target_key if self.voice else None,
                "transpose": self.voice.transpose if self.voice else 0,
                "midi_inputs": _list_midi_inputs(),
                "midi_port": self.router.active_port if self.router else None,
                "midi_enabled": self.midi_enabled,
                "wires": self.rack.audio_wires() if self.rack else [],
                "drums_target": self.drums.target,
                "arp": self.arp.settings() if self.arp else None,
                "transport": self.transport.settings(),
                "drone": self.drone.settings(),
                "drums": self.drums.settings(),
                "looper": self.looper.settings(),
                "lfos": self.lfos.state(),
                "presets": presets_mod.list_presets(),
                "available": sorted(
                    ({"key": m.key, "name": m.name, "kind": m.kind,
                      "family": m.family}
                     for m in self.registry.values() if m.key != "drone"),
                    key=lambda d: (d["kind"] != "source", d["family"], d["key"]),
                ),
                "module_errors": {k: repr(v) for k, v in self.module_errors.items()},
            }
