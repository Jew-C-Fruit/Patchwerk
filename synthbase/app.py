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
from .drone import EVERY as TONIC_EVERY
from .drone import NOTE_NAMES, TonicDeriver, midi_to_freq
from .drums import DrumMachine
from .keyshift import KeyShifter
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
from .rack import Rack, alloc_id, type_of
from .watcher import Reloader

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = REPO_ROOT / "modules"
PATCHES_DIR = REPO_ROOT / "patches"

# -- the control plane: wires among control nodes --------------------------------
# Node ids (v5): "keys" (all controllers: GUI keys, hardware MIDI, CP88),
# "arp", "deck" (the loop deck), voice ids ("voice", "voice.2", ...), tonic
# deriver ids ("tonic", "tonic.2", ...), and drone INSTANCE ids ("drone",
# "drone.2", ... — tonic-in only). Control FLOW is defined by wires —
# keys→(arp?)→(deck?)→voice, any topology. keys is never a destination
# (that would re-enter the controllers); self-wires are forbidden.
# deck→arp→deck is legal: the deck's _self_fire guard prevents replayed
# notes from re-recording. Wire type rules: tonic outs (deriver TONIC out)
# only connect to tonic ins (drone instances).
CTL_SOURCES = ("keys", "arp", "deck")
CTL_TARGETS = ("arp", "deck")


def default_ctl_wires() -> list[dict]:
    """Today's fixed flow, expressed as wires (preserves v2 behavior):
    keys feed the arp, the arp drives voice + deck-record, and the deck
    replays through its private voice node."""
    return [
        {"from": "keys", "to": "arp"},
        {"from": "arp", "to": "voice"},
        {"from": "arp", "to": "deck"},
        {"from": "deck", "to": "voice"},
    ]


class _NullSink:
    """MonoVoice-shaped no-op base for control-wire adapters."""

    def note_on(self, note: int, velocity: int = 100) -> None: ...
    def note_off(self, note: int) -> None: ...
    def all_off(self) -> None: ...
    def set_sustain(self, on: bool) -> None: ...
    def set_bend(self, semitones: float) -> None: ...


class _DeckRecordTap(_NullSink):
    """Adapts the looper's record methods to the note-sink interface."""

    def __init__(self, looper, voiced: bool) -> None:
        self.looper = looper
        self.voiced = voiced
        self._open: set[int] = set()  # notes on'd while a record pass is live

    def _rec(self, note: int, on: bool) -> None:
        (self.looper.record_voiced if self.voiced
         else self.looper.record_raw)(note, on)

    def note_on(self, note: int, velocity: int = 100) -> None:
        self._rec(note, True)
        if self.looper.state in ("armed", "recording", "overdubbing"):
            self._open.add(int(note))

    def note_off(self, note: int) -> None:
        self._rec(note, False)
        self._open.discard(int(note))

    def all_off(self) -> None:
        # panic/arp-stop while recording: close every open note in the take,
        # else the phrase keeps unmatched ons (full-width deck bars + rings)
        for n in list(self._open):
            self._rec(n, False)
        self._open.clear()


class _FanOut(_NullSink):
    """Fan a note event to every sink a node is wired to (resolved LIVE, so
    wire edits take effect on the very next event)."""

    def __init__(self, app, src: str) -> None:
        self.app = app
        self.src = src
        self._open: set[int] = set()  # notes on'd but not yet off'd

    def _each(self, fn) -> None:
        for s in self.app._ctl_sinks(self.src):
            try:
                fn(s)
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    def _tap(self, note: int, on: bool) -> None:
        # ONE viz tap per source-fire (not per edge): monitors riding this
        # node's outgoing wires filter client-side by src. Emitted even when
        # unwired — a GLOBAL monitor still shows the fire.
        self.app._emit_midi_event(
            {"kind": "tap", "src": self.src, "note": int(note), "on": bool(on)})

    def _close_taps(self) -> None:
        """Every silencing path must CLOSE its open taps — an on with no off
        pins a full-width bar on every note monitor forever."""
        for n in list(self._open):
            self._tap(n, False)
        self._open.clear()

    def note_on(self, note: int, velocity: int = 100) -> None:
        self._open.add(int(note))
        self._tap(note, True)
        self._each(lambda s: s.note_on(note, velocity))

    def note_off(self, note: int) -> None:
        self._open.discard(int(note))
        self._tap(note, False)
        self._each(lambda s: s.note_off(note))

    def all_off(self) -> None:
        self._close_taps()
        self._each(lambda s: s.all_off())

    def set_sustain(self, on: bool) -> None:
        self._each(lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        self._each(lambda s: s.set_bend(semitones))


class _KeysNode(_FanOut):
    """The controllers' node: GUI keys, hardware MIDI, sensors — all enter
    the graph here. Sustain/bend stay GLOBAL (pedal and wheel are physical
    gestures on the instrument, not events in a note path); with multiple
    mono voices they apply to ALL of them."""

    def __init__(self, app) -> None:
        super().__init__(app, "keys")

    def set_sustain(self, on: bool) -> None:
        self.app._global_sustain(on)

    def set_bend(self, semitones: float) -> None:
        for v in list(self.app.voices.values()):
            try:
                v.set_bend(semitones)
            except Exception:  # noqa: BLE001
                pass

    def all_off(self) -> None:
        # panic is global too: silence the arp pool AND every voice/tonic/
        # keyshift directly, whatever the wiring says — and close this
        # node's own open taps so monitors don't pin stuck bars
        self._close_taps()
        for s in (self.app.arp, *self.app.voices.values(),
                  *self.app.tonics.values(), *self.app.keyshifts.values()):
            if s is None:
                continue
            try:
                s.all_off()
            except Exception:  # noqa: BLE001
                pass


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
        # v5: multiple mono voices, id -> MonoVoice. "voice" is the primary;
        # spawned ones are "voice.2", "voice.3", ... (self.voice = primary).
        self.voices: dict[str, MonoVoice] = {}
        self._voice_targets: dict[str, str | None] = {"voice": None}  # id -> override
        self.arp: Arpeggiator | None = None
        self._arp_settings: dict = {}  # persists across patch switches
        self.transport = Transport()
        # v5: tonic derivers (spawnable ctl nodes) replace the DroneBrain.
        self.tonics: dict[str, TonicDeriver] = {}
        # v6: key shifters (spawnable 4-lane ctl modifiers)
        self.keyshifts: dict[str, KeyShifter] = {}
        self.drone_follow: dict[str, bool] = {}  # drone instance id -> follow tonic
        self._legacy_drone = False               # set_drone compat pair active
        self._legacy_drone_id: str | None = None
        self.drums = DrumMachine(self)
        self.lfos = LFOManager(self)
        self.looper = Looper(self)
        self.scope = Scope(self)
        # control plane: wires among {keys, arp, deck, voice ids, tonic ids,
        # drone ids}. Survive rebuilds (like graph_wires); reset to default
        # on select_patch.
        self.ctl_wires: list[dict] = default_ctl_wires()
        self._keys = _KeysNode(self)                    # every controller enters here
        self._arp_out = _FanOut(self, "arp")            # the arp fires into this
        self._deck_raw_tap = _DeckRecordTap(self.looper, voiced=False)
        self._deck_voiced_tap = _DeckRecordTap(self.looper, voiced=True)
        self.on_beat_event = None  # set by GuiServer; called from the beat thread

        self.on_midi_event = None  # set by GuiServer; called from MIDI thread
        self.patch_name: str | None = None
        self.patch: dict | None = None
        # graph overlay over the linear chain: None = pure linear derivation;
        # a list of {"from": id, "to": id|"master"|None} = user rewires,
        # re-applied after every rebuild for ids that still exist.
        self.graph_wires: list[dict] | None = None
        self._transpose = 0
        self.registry: dict = {}
        self.module_errors: dict = {}
        self._lock = threading.RLock()  # GUI thread + MIDI thread both call in

    # primary-voice accessor (lots of code — and tests — talk to "the voice")
    @property
    def voice(self) -> MonoVoice | None:
        return self.voices.get("voice")

    @voice.setter
    def voice(self, v) -> None:
        if v is None:
            self.voices.pop("voice", None)
        else:
            self.voices["voice"] = v

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

        self._make_voices(patch)
        if self.voice:
            # the arp fires into a live fan-out over the arp→X wires — no
            # hardwired voice/deck taps anymore
            self.arp = Arpeggiator(self._arp_out, self.transport)
            self.arp.configure(**{**self._arp_settings, **patch.get("arp", {})})
            self._arp_settings = {
                k: v for k, v in self.arp.settings().items() if k != "patterns"
            }
        self.patch_name = patch_name
        self.patch = patch
        self.rack.on_node_replaced = self._on_node_replaced
        self.lfos.assignments.clear()  # old rack's nodes are gone with it
        self.rack.mapped.clear()
        if self._legacy_drone:  # re-add the compat deriver+drone pair
            self._ensure_legacy_drone()
        self._reapply_graph_wires()
        self._restart_midi()

    def _make_voices(self, patch: dict) -> None:
        """(Re)create every mono voice against the fresh rack, keeping ids
        and stored target overrides where the target module still exists."""
        bindings = patch.get("bindings", {})
        if "voice" not in self._voice_targets:
            self._voice_targets["voice"] = None
        for v in self.voices.values():
            # a rebuild silences the old rack — close each old voice's open
            # "voiced" segment so note rolls don't pin a stuck bar
            if getattr(v, "_sounding", None) is not None:
                self._emit_voiced(v._sounding, False)
        self.voices = {}
        guess = self._guess_voice_target()
        for vid, override in self._voice_targets.items():
            target = None
            for cand in (override,
                         bindings.get("notes_to") if vid == "voice" else None):
                if not cand:
                    continue
                try:
                    inst = self.rack.find(cand)
                    if inst.module.kind == "source" and "gate" in inst.settings:
                        target = inst.key
                        break
                except KeyError:
                    pass  # override's module left the chain — fall back
            target = target or guess
            if not target:
                continue
            v = MonoVoice(self.rack, target)
            v.transpose = self._transpose
            v.on_voiced = self._emit_voiced
            self.voices[vid] = v

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
        # viz only — the deck records via its wires, not this tap
        self._emit_midi_event({"kind": "voiced", "note": int(note), "on": bool(on)})

    # -- control-plane wiring --------------------------------------------------

    def _is_drone_id(self, nid) -> bool:
        """Is nid a drone INSTANCE id (the only tonic-in nodes)?"""
        if not isinstance(nid, str) or not self.rack:
            return False
        return any(i.key == nid and i.type == "drone"
                   for i in self.rack.instances)

    @staticmethod
    def _split_ep(ep) -> tuple[str, int | None]:
        """Ctl endpoint grammar: "keyshift.2:3" = node "keyshift.2", lane 3.
        Nodes without lanes are plain ids ("arp" → ("arp", None))."""
        s = str(ep)
        if ":" not in s:
            return s, None
        base, _, lane = s.partition(":")
        try:
            return base, int(lane)
        except ValueError:
            return base, -1  # malformed lane — never validates

    def _ctl_src_ok(self, src) -> bool:
        base, lane = self._split_ep(src)
        if base in self.keyshifts:
            return lane is not None and 1 <= lane <= 4
        return lane is None and (base in CTL_SOURCES or base in self.tonics)

    def _ctl_dst_ok(self, dst) -> bool:
        base, lane = self._split_ep(dst)
        if base in self.keyshifts:
            return lane is not None and 1 <= lane <= 4
        return lane is None and (
            base in CTL_TARGETS or base == "voice"  # primary id is reserved
            or base in self.voices or base in self.tonics
            or self._is_drone_id(base))

    def _ctl_sinks(self, src: str) -> list:
        """Resolve a node's outgoing wires to note-sink objects, live.
        (Deck REPLAY resolution lives in looper._sink(), which reads the same
        ctl_wires — this handles keys/arp/tonic/keyshift dispatch. Wires INTO
        drone instances are tonic wires: root updates, not note events —
        skipped. Keyshift lanes are addressed "id:lane": lane k in → lane k
        out only, so multiple signals ride one shifter without merging.)"""
        sinks = []
        for w in self.ctl_wires:
            if w["from"] != src:
                continue
            t = w["to"]
            base, lane = self._split_ep(t)
            if t == "arp" and self.arp is not None:
                sinks.append(self.arp)
            elif t in self.voices:
                sinks.append(self.voices[t])
            elif t == "deck":
                # keys→deck records the raw input; arp→deck records arp output
                sinks.append(self._deck_voiced_tap if src == "arp"
                             else self._deck_raw_tap)
            elif t in self.tonics:
                sinks.append(self.tonics[t])
            elif base in self.keyshifts and lane is not None:
                try:
                    sinks.append(self.keyshifts[base].lane_in(lane))
                except ValueError:
                    pass  # stale wire with a bad lane — skip
        return sinks

    def set_ctl_wire(self, action: str, src: str, dst: str | None = None) -> None:
        """Add/remove a control wire. The graph IS the router: an unwired
        node's events dead-end silently."""
        with self._lock:
            # legacy vocabulary: "drone" the brain → the first tonic deriver
            if src == "drone" and src not in self.tonics and \
                    not self._is_drone_id("drone"):
                src = "tonic"
            if not self._ctl_src_ok(src):
                raise ValueError(f"{src!r} has no control output")
            if action == "add":
                if not self._ctl_dst_ok(dst):
                    raise ValueError(f"cannot wire control into {dst!r}")
                # self-wires are forbidden at the NODE level: for lane nodes
                # (keyshift) even cross-lane self-wires would recurse
                # synchronously through the shifter
                if self._split_ep(src)[0] == self._split_ep(dst)[0]:
                    raise ValueError(f"{src} → {dst} would loop on itself")
                if self._is_drone_id(dst) and src not in self.tonics:
                    raise ValueError(
                        f"{dst!r} takes only a tonic input (wire a Tonic Deriver)")
                w = {"from": src, "to": dst}
                if w not in self.ctl_wires:
                    self.ctl_wires.append(w)
                    if src in self.tonics and self._is_drone_id(dst):
                        # fresh tonic wire: push the current root immediately
                        self.tonics[src].drive_drones(only=dst)
            elif action == "remove":
                n0 = len(self.ctl_wires)
                self.ctl_wires = [w for w in self.ctl_wires
                                  if not (w["from"] == src and w["to"] == dst)]
                if len(self.ctl_wires) != n0:
                    # unhooking a node's LAST input silences it — a stuck
                    # note is worse live than a dropped one
                    if dst == "arp" and self.arp and \
                            not any(w["to"] == "arp" for w in self.ctl_wires):
                        self.arp.all_off()
                    if dst in self.voices and \
                            not any(w["to"] == dst for w in self.ctl_wires):
                        self.voices[dst].all_off()
                    # unhooking the deck's replay must not leave notes ringing
                    if src == "deck" and dst in self.voices:
                        if dst == "voice":
                            self.looper._deck_teardown()
                        else:
                            self.voices[dst].all_off()
                    elif src == "deck" and dst == "arp" and self.arp:
                        for n in list(self.looper._sounding):
                            try:
                                self.arp.note_off(n)
                            except Exception:  # noqa: BLE001
                                pass
            else:
                raise ValueError(f"unknown ctl_wire action {action!r}")

    def _global_sustain(self, on: bool) -> None:
        """The pedal is a physical gesture — one pedal, ALL voices. The arp
        latches its pool; a voice fed exclusively by the ENABLED arp skips
        the direct latch (a latched voice would defeat the arp's gating)."""
        if self.arp:
            self.arp.set_sustain(on)  # latch the pool
        arp_gating = bool(self.arp and self.arp.enabled)
        for vid, v in self.voices.items():
            feeds = {w["from"] for w in self.ctl_wires if w["to"] == vid}
            if arp_gating and "arp" in feeds:
                continue  # the arp's latch carries this voice's stream
            try:
                v.set_sustain(on)
            except Exception:  # noqa: BLE001
                pass

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
            voice=self._keys,   # hardware notes enter the ctl graph at "keys"
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

    def edit_chain(self, action: str, key: str, index: int | None = None) -> str | None:
        """Live chain surgery: add/remove/move a stage IN PLACE — spawn/free/
        rewire only the affected module, never a whole-rack rebuild. Modules
        already running are untouched, so a failed spawn can only affect the one
        module being added (see docs/INCREMENTAL_EDIT_PLAN.md). A full
        `_build_from` is now reserved for patch LOAD / boot only.

        `key` is an instance id for remove/move; for add it's a module TYPE
        (duplicates allowed — the new instance auto-suffixes to a fresh id,
        which is returned). Audio topology is wire-defined (`graph_wires`), so
        add arrives parked on the null bus and the GUI splices it with ordinary
        graph_wire messages."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            keys = [i.key for i in self.rack.instances if not i.service]
            if self.graph_wires is None:
                # first structural edit: adopt the current wiring as the
                # authoritative overlay before we start mutating in place
                self.graph_wires = self.rack.audio_wires()
            result: str | None = None

            if action == "add":
                base = type_of(key)
                if base not in self.registry:
                    raise ValueError(f"unknown module {key!r}")
                new_id = key if ("." in key and key not in keys) \
                    else alloc_id(base, keys)
                self.rack.add_module(new_id)          # spawn ONE parked node
                self.graph_wires = [w for w in self.graph_wires
                                    if w["from"] != new_id]
                self.graph_wires.append({"from": new_id, "to": None})
                result = new_id
                # a parked module is wired to nothing and silent — NO reorder,
                # NO wire reapply, NO voice rebuild. This is the whole point:
                # adding a module must not touch the running rack.

            elif action == "remove":
                if key not in keys:
                    raise KeyError(f"no module {key!r} to remove")
                if len(keys) <= 1:
                    raise ValueError("chain cannot be empty")
                # splice-out healing: bridge everything that fed the removed
                # module to the removed module's own destination
                dst = next((w["to"] for w in self.graph_wires
                            if w["from"] == key), None)
                feeders = [w["from"] for w in self.graph_wires
                           if w["to"] == key]
                self.graph_wires = [
                    {**w, "to": dst} if w["to"] == key else w
                    for w in self.graph_wires if w["from"] != key
                ]
                if self.drums.target == key:
                    self.drums.target = dst
                # the removed instance's control-plane presence goes with it
                self.ctl_wires = [w for w in self.ctl_wires
                                  if key not in (w.get("from"), w.get("to"))]
                self.drone_follow.pop(key, None)
                if self._legacy_drone_id == key:
                    self._legacy_drone_id = None
                    self._legacy_drone = False
                # drop any LFO assignments on the departing module + its map guards
                for aid in [a for a in list(self.lfos.assignments)
                            if a.rsplit(".", 1)[0] == key]:
                    self.lfos.unassign(aid)
                self.rack.mapped = {(k, p) for (k, p) in self.rack.mapped
                                    if k != key}
                voice_touched = (key in self._voice_targets.values()
                                 or any(getattr(v, "target_key", None) == key
                                        for v in self.voices.values()))
                self.rack.detach_instance(key)        # free ONE node
                # re-aim ONLY the wires that fed the removed module at its dst
                for f in feeders:
                    try:
                        if dst is None:
                            self.rack.audio_disconnect(f)
                        else:
                            self.rack.audio_rewire(f, dst)
                    except Exception:  # noqa: BLE001
                        pass
                if voice_touched:
                    self._make_voices(self.patch or {})

            elif action == "move":
                # audio order is wire-defined; a move is a pure list reorder.
                insts = [i for i in self.rack.instances if not i.service]
                svc = [i for i in self.rack.instances if i.service]
                i = next(n for n, ins in enumerate(insts) if ins.key == key)
                j = max(0, min(len(insts) - 1, i + (index or 0)))
                insts.insert(j, insts.pop(i))
                self.rack.instances = insts + svc
                self.rack.reorder_for_wires(self.graph_wires)
            else:
                raise ValueError(f"unknown edit_chain action {action!r}")

            if self.patch is not None:
                self.patch["chain"] = [
                    (i.key, {}) for i in self.rack.instances if not i.service
                ]
            return result

    def graph_wire(self, action: str, src: str, dst: str | None = None) -> None:
        """Live audio rewiring: add (src → dst|"master") or remove (park src on
        the null bus). One outgoing audio wire per source; fan-in is free
        (buses sum). Stored so rebuilds re-apply it."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            # normalize legacy type keys to instance ids (raises for the GUI)
            src = self.rack.find(src).key
            if self.graph_wires is None:
                self.graph_wires = self.rack.audio_wires()
            wires = [w for w in self.graph_wires if w["from"] != src]
            if action == "add":
                if not dst:
                    raise ValueError("graph_wire add needs a destination")
                if dst != "master":
                    dst = self.rack.find(dst).key
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

    def spawn_unconnected(self, key: str) -> str:
        """Add a module to the rack with its audio out parked on the null bus
        (palette click / empty-canvas drop). Snapshot the current wiring FIRST
        so the linear rebuild's re-tailing doesn't reroute existing modules.
        `key` is a module TYPE; the fresh instance id is returned."""
        with self._lock:
            if self.graph_wires is None and self.rack:
                self.graph_wires = self.rack.audio_wires()
            # edit_chain("add") already spawns the module parked on the null bus;
            # the old extra graph_wire("remove") here was redundant and triggered
            # a full node reorder per add — dropped.
            return self.edit_chain("add", key)

    def set_voice_target(self, key: str, voice: str = "voice") -> None:
        """Re-aim a mono voice at another playable source (GUI wire re-drag)."""
        with self._lock:
            v = self.voices.get(voice)
            if not (self.rack and v):
                raise RuntimeError(f"no voice {voice!r} to retarget")
            inst = self.rack.find(key)
            if inst.module.kind != "source" or "gate" not in inst.settings \
                    or "freq" not in inst.settings:
                raise ValueError(f"{key} is not a note-playable source")
            v.all_off()  # silence the old target before switching
            v.target_key = inst.key
            self._voice_targets[voice] = inst.key

    # -- multiple mono voices ----------------------------------------------------

    def spawn_voice(self) -> str:
        """Add another mono voice ("voice.2", ...). It arrives unwired —
        patch keys/arp/deck into it — aimed at the first playable source."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            target = self._guess_voice_target()
            if not target:
                raise ValueError("no note-playable source to aim a voice at")
            vid = alloc_id("voice", self.voices.keys() | self._voice_targets.keys())
            v = MonoVoice(self.rack, target)
            v.transpose = self._transpose
            v.on_voiced = self._emit_voiced
            self.voices[vid] = v
            self._voice_targets[vid] = None
            return vid

    def remove_voice(self, vid: str) -> None:
        with self._lock:
            if vid == "voice":
                raise ValueError("the primary voice cannot be removed")
            v = self.voices.pop(vid, None)
            self._voice_targets.pop(vid, None)
            if v is None:
                raise KeyError(f"no voice {vid!r}")
            try:
                v.all_off()
            except Exception:  # noqa: BLE001
                pass
            self.ctl_wires = [w for w in self.ctl_wires
                              if vid not in (w.get("from"), w.get("to"))]

    # -- tonic derivers ------------------------------------------------------------

    def spawn_tonic(self, want_id: str | None = None) -> str:
        with self._lock:
            tid = want_id or alloc_id("tonic", self.tonics.keys())
            if tid not in self.tonics:
                self.tonics[tid] = TonicDeriver(self, tid)
            return tid

    def _heal_ctl_snip(self, ins: list, outs: list) -> None:
        """SNIP-HEAL: removing a node that sat A→X→B on the ctl plane
        auto-reconnects A→B — but ONLY when unambiguous (exactly 1 upstream
        and 1 downstream); multi-in/multi-out just drops (pairwise N×M
        healing would invent wires the user never patched). Call AFTER the
        removed node's wires are gone."""
        if len(ins) == 1 and len(outs) == 1:
            try:
                self.set_ctl_wire("add", ins[0], outs[0])
            except (ValueError, KeyError):
                pass  # e.g. A→B invalid (self-wire, keys-as-dst) — drop

    def remove_tonic(self, tid: str) -> None:
        with self._lock:
            d = self.tonics.pop(tid, None)
            if d is None:
                raise KeyError(f"no tonic deriver {tid!r}")
            d.shutdown()
            # snip-heal candidates: note streams IN, thru wires OUT (the
            # amber tonic→drone wires are a different signal kind — dropped)
            ins = [w["from"] for w in self.ctl_wires if w.get("to") == tid]
            outs = [w["to"] for w in self.ctl_wires
                    if w.get("from") == tid and not self._is_drone_id(w.get("to"))]
            self.ctl_wires = [w for w in self.ctl_wires
                              if tid not in (w.get("from"), w.get("to"))]
            self._heal_ctl_snip(ins, outs)
            if self._legacy_drone and tid == "tonic":
                self._legacy_drone = False

    def set_tonic(self, tid: str, **settings) -> None:
        with self._lock:
            d = self.tonics.get(tid)
            if d is None:
                raise KeyError(f"no tonic deriver {tid!r}")
            d.configure(**settings)

    # -- key shifters -----------------------------------------------------------

    def spawn_keyshift(self, want_id: str | None = None) -> str:
        with self._lock:
            kid = want_id or alloc_id("keyshift", self.keyshifts.keys())
            if kid not in self.keyshifts:
                self.keyshifts[kid] = KeyShifter(self, kid)
            return kid

    def remove_keyshift(self, kid: str) -> None:
        with self._lock:
            ks = self.keyshifts.pop(kid, None)
            if ks is None:
                raise KeyError(f"no key shifter {kid!r}")
            try:
                ks.shutdown()  # closes open notes downstream + their taps
            except Exception:  # noqa: BLE001
                pass
            # snip-heal candidates PER LANE: each lane is its own A→X→B path
            lane_pairs = []
            for lane in range(1, 5):
                ep = f"{kid}:{lane}"
                lane_pairs.append((
                    [w["from"] for w in self.ctl_wires if w.get("to") == ep],
                    [w["to"] for w in self.ctl_wires if w.get("from") == ep]))
            # its control-plane presence goes with it (lane endpoints too)
            self.ctl_wires = [
                w for w in self.ctl_wires
                if kid not in (self._split_ep(w.get("from"))[0],
                               self._split_ep(w.get("to"))[0])]
            for ins, outs in lane_pairs:
                self._heal_ctl_snip(ins, outs)

    def set_keyshift(self, kid: str, **settings) -> None:
        with self._lock:
            ks = self.keyshifts.get(kid)
            if ks is None:
                raise KeyError(f"no key shifter {kid!r}")
            ks.configure(**settings)

    def set_drone_follow(self, iid: str, on: bool) -> None:
        """The drone card's tonic toggle: root updates drive freq or don't."""
        with self._lock:
            if not self._is_drone_id(iid):
                raise KeyError(f"no drone instance {iid!r}")
            self.drone_follow[iid] = bool(on)
            if on:
                for d in self.tonics.values():
                    d.drive_drones(only=iid)

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
        # key shifters ride the transport: progression steps land on beat 0
        for ks in list(self.keyshifts.values()):
            try:
                ks.on_beat(bar, beat)
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
            self._transpose = max(-24, min(24, int(semitones)))
            for v in self.voices.values():  # transpose is GLOBAL
                v.transpose = self._transpose

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
        """Header strip: the FIRST deriver's histogram + root (legacy shape)."""
        d = self.tonics.get("tonic") or next(iter(self.tonics.values()), None)
        if d is None:
            return {"weights": [0.0] * 12, "root": None}
        weights = d.est.weights()
        total = max(sum(weights), 1e-9)
        est = d.est.estimate(d.root)
        return {
            "weights": [round(w / total, 4) for w in weights],
            "root": NOTE_NAMES[est] if est is not None else None,
        }

    def set_drone(self, enabled=None, every=None, octave=None, **_ignored) -> None:
        """LEGACY compat (/legacy GUI, old presets): the monolithic drone
        maps onto a deriver+drone pair — ensure a "tonic" deriver exists
        (configured with every/octave), and on enable spawn a drone instance
        riding the chain head, wired arp→tonic→drone with follow on."""
        with self._lock:
            if enabled is True or every is not None or octave is not None:
                tid = self.spawn_tonic(want_id="tonic")
                self.tonics[tid].configure(every=every, octave=octave)
            if enabled is True:
                self._legacy_drone = True
                self._ensure_legacy_drone()
            elif enabled is False and self._legacy_drone:
                self._legacy_drone = False
                did = self._legacy_drone_id
                self._legacy_drone_id = None
                if did and self.rack:
                    try:
                        self.rack.remove_instance(did)
                    except Exception:  # noqa: BLE001
                        pass
                    self.ctl_wires = [w for w in self.ctl_wires
                                      if w.get("to") != did]

    def _ensure_legacy_drone(self) -> None:
        """Idempotent: (re)spawn the compat drone instance in the current
        rack and (re)impose the default deriver wiring."""
        rack, mod = self.rack, self.registry.get("drone")
        if not (self._legacy_drone and rack and mod):
            return
        d = self.tonics.get("tonic")
        inst = None
        if self._legacy_drone_id:
            try:
                inst = rack.find(self._legacy_drone_id)
            except KeyError:
                inst = None
        if inst is None:
            overrides = {}
            if d and d.root is not None:
                overrides["freq"] = midi_to_freq(12 * (d.octave + 1) + d.root)
            try:
                inst = rack.add_service_source(mod, overrides)
            except Exception as exc:  # noqa: BLE001
                print(f"[drone] could not spawn: {exc}")
                return
            self._legacy_drone_id = inst.key
        did = self._legacy_drone_id
        self.drone_follow.setdefault(did, True)
        for w in ({"from": "arp", "to": "tonic"}, {"from": "tonic", "to": did}):
            if w not in self.ctl_wires:
                self.ctl_wires.append(w)

    def set_transport(self, bpm=None, beats_per_bar=None, click=None, accent=None,
                      playing=None) -> None:
        if accent is not None:
            self.transport.click_accent = bool(accent)
        if playing is not None:
            self.transport.set_running(bool(playing))
            if not playing and self.arp:
                self.arp._safe_all_off() if hasattr(self.arp, "_safe_all_off") else None
            # transport stop/start pauses every drone instance
            for inst in (self.rack.instances if self.rack else []):
                if inst.type != "drone" or inst.node is None or not inst.enabled:
                    continue
                try:
                    (inst.node.unpause if playing else inst.node.pause)()
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
            for d in self.tonics.values():
                d.shutdown()
            for ks in self.keyshifts.values():
                try:
                    ks.shutdown()
                except Exception:  # noqa: BLE001
                    pass
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
            self.ctl_wires = default_ctl_wires()  # fresh patch, fresh control plane
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
            key = inst.key  # normalize a legacy type key to the instance id
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
        # graph walk from "keys": wire keys→voice directly and no arp is in
        # the path; no outgoing wire and the note dead-ends silently
        self._keys.note_on(int(note), int(velocity))

    def note_off(self, note: int) -> None:
        self._keys.note_off(int(note))

    def all_notes_off(self) -> None:
        self._keys.all_off()

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

    def _legacy_drone_settings(self) -> dict:
        """state.drone kept for /legacy clients (the old brain's shape)."""
        d = self.tonics.get("tonic")
        return {
            "enabled": bool(self._legacy_drone),
            "every": d.every if d else "1 bar",
            "everies": list(TONIC_EVERY),
            "octave": d.octave if d else 2,
            "root": (NOTE_NAMES[d.root] if d and d.root is not None else None),
        }

    def state(self) -> dict:
        with self._lock:
            chain = []
            if self.rack:
                for inst in self.rack.instances:
                    suffix = inst.key.split(".", 1)[1] if "." in inst.key else ""
                    entry = {
                        "key": inst.key,      # UNIQUE instance id
                        "type": inst.type,    # module key (registry/LIB lookups)
                        "name": inst.module.name + (f" {suffix}" if suffix else ""),
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
                    }
                    if inst.type == "drone":
                        entry["tonic_follow"] = self.drone_follow.get(inst.key, True)
                    chain.append(entry)
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
                "voices": [{"id": vid, "target": v.target_key}
                           for vid, v in self.voices.items()],
                "tonics": [d.settings() for d in self.tonics.values()],
                "keyshifts": [k.settings() for k in self.keyshifts.values()],
                "transpose": self._transpose,
                "midi_inputs": _list_midi_inputs(),
                "midi_port": self.router.active_port if self.router else None,
                "midi_enabled": self.midi_enabled,
                "wires": self.rack.audio_wires() if self.rack else [],
                "ctl_wires": [dict(w) for w in self.ctl_wires],
                "drums_target": self.drums.target,
                "arp": self.arp.settings() if self.arp else None,
                "transport": self.transport.settings(),
                "drone": self._legacy_drone_settings(),
                "drums": self.drums.settings(),
                "looper": self.looper.settings(),
                "lfos": self.lfos.state(),
                "presets": presets_mod.list_presets(),
                "available": sorted(
                    ({"key": m.key, "name": m.name, "kind": m.kind,
                      "family": m.family}
                     for m in self.registry.values()),
                    key=lambda d: (d["kind"] != "source", d["family"], d["key"]),
                ),
                "module_errors": {k: repr(v) for k, v in self.module_errors.items()},
            }
