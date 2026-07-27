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
from .drone import LiteralDeriver, NOTE_NAMES, TonicDeriver, midi_to_freq
from .drums import DrumMachine
from .keyshift import KeyShifter
from .gate import GateManager
from .lfo import LFOManager
from .ping import ButtonTrigger, ClockTrigger
from . import relay as relay_mod
from .relay import RelayNode
from .threshold import ThresholdManager
from .scope import Scope
from .looper import Looper
from . import presets as presets_mod
from .transport import TapTempo, Transport, _click
from .audio_devices import list_audio_devices
from .engine import Engine
from .master import MasterSection
from .allocation import MAX_POLY_VOICES, Allocation, Hold, MonoLatest, Poly
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
# "drone.2", ... — MONO ctl note-sinks since the drone rework). Control
# FLOW is defined by wires — keys→(arp?)→(deck?)→voice, any topology.
# keys is never a destination (that would re-enter the controllers);
# self-wires are forbidden. deck→arp→deck is legal: the deck's _self_fire
# guard prevents replayed notes from re-recording.
CTL_SOURCES = ("keys", "arp", "deck")
CTL_TARGETS = ("arp", "deck")

# item 9: the transport CARDS — canvas views of the ONE global transport
# ("play" = stop/play, "tempo" = tempo/click). Presence only: the wire
# endpoints (transport:run/click/accent/tap) live on the GLOBAL transport.
TRANSPORT_CARDS = ("play", "tempo")


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


# item 29: `_DroneSink` is GONE. What made a drone a drone was never the
# DSP, it was the allocation policy — last-note priority, no gate, hold on
# empty — and that policy now lives once, in allocation.Hold, beside
# MonoLatest and Poly. A drone MODULE instance's ctl-plane presence is a
# Hold aimed at that instance (see App._drone_sink); the drone CARD (ids
# "hold", "hold.2", ...) is a Hold in App.voices aimed at any playable
# source, with POWER holding that source's gate open.


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
                  *self.app.tonics.values(), *self.app.literals.values(),
                  *self.app.keyshifts.values()):
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
        # item 10: POLY voices ("poly", "poly.2", ...) live in the same dict.
        # item 29: DRONE voices ("hold", "hold.2", ...) too — the id type is
        # "hold" and not "drone" because "drone" is already a MODULE type,
        # and a ctl node sharing an id with a rack instance would shadow it
        # in _ctl_sinks.
        # All three are Allocations with one note-sink interface, so every
        # global fan-out below (sustain, transpose, bend, panic, ctl wiring,
        # state) treats them alike; the POLICY is read off the id's type, per
        # the instance-id doctrine — type_of("poly.2") == "poly".
        self.voices: dict[str, Allocation] = {}
        self._voice_targets: dict[str, str | None] = {"voice": None}  # id -> override
        self._poly_sizes: dict[str, int] = {}   # poly id -> voice count
        self._drone_powers: dict[str, bool] = {}  # hold id -> POWER (user intent)
        self.arp: Arpeggiator | None = None
        self._arp_settings: dict = {}  # persists across patch switches
        self.transport = Transport()
        self._tap_tempo = TapTempo()   # "transport:tap" trig-in state
        # item 9: transport CARDS on the canvas — a subset of
        # TRANSPORT_CARDS. Card and top-bar are two views of the one
        # global transport, kept in lockstep by the state broadcast;
        # removing a card never unwires the transport's endpoints.
        self.transport_cards: set[str] = set()
        # v5: tonic derivers (spawnable ctl nodes) replace the DroneBrain.
        self.tonics: dict[str, TonicDeriver] = {}
        # v6 deriver split: literal derivers (deterministic extract/place)
        self.literals: dict[str, LiteralDeriver] = {}
        # v6: key shifters (spawnable 4-lane ctl modifiers)
        self.keyshifts: dict[str, KeyShifter] = {}
        # drone MODULE instance id -> its Hold (item 29). Kept across
        # rebuilds so the held root survives; rebound to the fresh rack on
        # access, since an Allocation binds a rack where _DroneSink read
        # app.rack live.
        self._drone_sinks: dict[str, Hold] = {}
        # binary sources (hi/lo levels; wires ride ctl_wires, kind inferred
        # from the source endpoint — see synthbase/gate.py for the model)
        self.buttons: dict[str, ButtonTrigger] = {}
        self.clocks: dict[str, ClockTrigger] = {}
        # binary rework: relays (type-agnostic switched junctions),
        # keyshift-style dict of nodes
        self.relays: dict[str, RelayNode] = {}
        # item 25: the relay AUDIO plane — one permanent lagged-gate synth
        # per claimed audio circuit (LFOManager lifecycle pattern; replaces
        # the retired resolve_audio/resolved_wires resolution layer)
        self.relay_audio = relay_mod.RelayAudioManager(self)
        # the MOD plane's stored wires (07-24): LFO out → relay circuit →
        # "<key>:<param>". Which params an LFO really drives is RESOLVED
        # from these through the closed circuits (relay.resolve_mod);
        # _mod_managed is the dest set this layer owns, so a direct
        # lfo_wire destination is never yanked out from under the user.
        self.mod_wires: list[dict] = []
        self._mod_managed: set[tuple[str, str]] = set()
        # item 8: thresholds (CV edge → ping; watch synths + /tr edge-notify)
        self.thresholds = ThresholdManager(self)
        self._legacy_drone = False               # set_drone compat pair active
        self._legacy_drone_id: str | None = None
        self.drums = DrumMachine(self)
        self.lfos = LFOManager(self)
        self.gates = GateManager(self)   # the binary plane (logic + effects)
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
    def voice(self) -> Allocation | None:
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
        self.scope.reset()   # ring probes/buffers die with the old rack

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
        self.lfos.on_rack_rebuilt()  # dests die with the old rack; LFOs stay
        # the mod plane's PARAM ends died with the rack too (its circuits
        # survive, like every other spawned node)
        self.mod_wires = [w for w in self.mod_wires
                          if relay_mod.relay_ep(self, w.get("to")) is not None]
        self._mod_managed.clear()
        self._relay_refresh_kinds()
        self.rack.mapped.clear()
        if self._legacy_drone:  # re-add the compat deriver+drone pair
            self._ensure_legacy_drone()
        self._reapply_graph_wires()
        self._restart_midi()
        # a rebuild spawns every node UNPAUSED — a stopped transport has to
        # re-silence the drones (item 32)
        self._sync_drone_run_state()

    def _new_allocation(self, vid: str, target: str) -> Allocation:
        """Build the allocation a voice id calls for. The id's TYPE is the
        policy: "voice[.n]" -> mono-latest, "poly[.n]" -> poly,
        "hold[.n]" -> hold (item 29's drone)."""
        kind = type_of(vid)
        if kind == "poly":
            v = Poly(self.rack, target, voices=self._poly_sizes.get(vid, 8))
        elif kind == "hold":
            v = Hold(self.rack, target)
        else:
            v = MonoLatest(self.rack, target)
        v.transpose = self._transpose
        # a drone has no note-on/off segments to visualise; on_voiced would
        # emit an "on" that nothing ever closes
        if not isinstance(v, Hold):
            v.on_voiced = self._emit_voiced
        return v

    def _make_voices(self, patch: dict) -> None:
        """(Re)create every voice against the fresh rack, keeping ids and
        stored target overrides where the target module still exists."""
        bindings = patch.get("bindings", {})
        if "voice" not in self._voice_targets:
            self._voice_targets["voice"] = None
        # a drone SURVIVES a rebuild — that is the whole policy. Carry each
        # hold's root over so the rebuilt allocation re-aims at it (power is
        # already carried, in _drone_powers).
        roots = {vid: v._root for vid, v in self.voices.items()
                 if isinstance(v, Hold)}
        for v in self.voices.values():
            # a rebuild silences the old rack — close each old voice's open
            # "voiced" segments so note rolls don't pin a stuck bar. all_off()
            # is the policy's own closure path, so a poly closes all N.
            try:
                v.all_off()
            except Exception:  # noqa: BLE001 — old rack is already gone
                pass
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
            v = self.voices[vid] = self._new_allocation(vid, target)
            if isinstance(v, Hold):
                v._root = roots.get(vid)
        # a rebuilt drone is silent until it is told again — the rack came up
        # fresh and every satellite spawned gate=0. Re-push here rather than
        # at each caller, so EVERY path that rebuilds voices keeps item 32's
        # invariant (patch build, and edit_chain removing a voice's target).
        self._sync_drone_run_state()

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
        # ordering + the relay audio plane in one pass: apply_audio degrades
        # to a plain (cheap-pathed) reorder_for_wires when no audio circuit
        # is claimed; with circuits, sources wired into relay endpoints —
        # skipped above — re-point at their circuit in-buses against the
        # FRESH rack (the gate synths themselves are engine-level and
        # survive a rack rebuild)
        try:
            relay_mod.apply_audio(self)
        except Exception:  # noqa: BLE001
            pass

    def _on_node_replaced(self, key: str) -> None:
        self.lfos.on_node_replaced(key)

    def _emit_voiced(self, note: int, on: bool) -> None:
        # viz only — the deck records via its wires, not this tap
        self._emit_midi_event({"kind": "voiced", "note": int(note), "on": bool(on)})

    # -- control-plane wiring --------------------------------------------------

    def _is_drone_id(self, nid) -> bool:
        """Is nid a drone INSTANCE id (a mono ctl note-sink)?"""
        if not isinstance(nid, str) or not self.rack:
            return False
        return any(i.key == nid and i.type == "drone"
                   for i in self.rack.instances)

    def _drone_sink(self, key: str) -> Hold:
        """The per-instance drone sink: an allocation.Hold aimed at the drone
        MODULE instance (item 29 — the policy is shared with the drone CARD
        and with mono/poly, rather than reimplemented here).

        Held-note state persists across events, and the sink is rebound to
        the live rack on access: pools die with their rack, where the old
        `_DroneSink` read `app.rack` on every note and never noticed. The
        drone module is GATELESS, so Hold's power axis is inert here and
        bypass remains its only off switch, exactly as before."""
        s = self._drone_sinks.get(key)
        if s is None:
            s = self._drone_sinks[key] = Hold(self.rack, key)
        elif s.rack is not self.rack:
            s.rebind(self.rack)
        s.transpose = self._transpose
        return s

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

    def _is_ping_src(self, nid) -> bool:
        """BINARY sources (the name survives the binary rework — plenty of
        call sites): button/clock/threshold/logic ids, or a relay circuit
        endpoint whose circuit kind is binary. Their outgoing wires ARE
        binary wires — the wire kind is inferred from the source endpoint.
        A logic out wired into a deriver suppresses its grid timer exactly
        like a button used to (_ping_driven reads this predicate)."""
        if (nid in self.buttons or nid in self.clocks
                or nid in self.thresholds.instances
                or nid in self.gates.logics):
            return True
        rk = relay_mod.relay_ep(self, nid)
        return rk is not None and rk[0].kinds.get(rk[1]) == "binary"

    def _relay_refresh_kinds(self) -> None:
        """After any wire removal: circuits no wire touches forget their
        kind (the next wire re-infers it)."""
        for r in self.relays.values():
            r.refresh_kinds()

    def _deriver(self, nid):
        """Any deriver node (estimator or literal) by id, else None."""
        return self.tonics.get(nid) or self.literals.get(nid)

    def _ctl_src_ok(self, src) -> bool:
        base, lane = self._split_ep(src)
        if base in self.keyshifts:
            return lane is not None and 1 <= lane <= 4
        if base in self.relays:   # a circuit endpoint sources its note out
            return lane is not None and 1 <= lane <= relay_mod.MAX_CIRCUITS
        return lane is None and (base in CTL_SOURCES or base in self.tonics
                                 or base in self.literals)

    def _ctl_dst_ok(self, dst) -> bool:
        base, lane = self._split_ep(dst)
        if base in self.keyshifts:
            return lane is not None and 1 <= lane <= 4
        if base in self.relays:   # a circuit endpoint sinks its note in
            return lane is not None and 1 <= lane <= relay_mod.MAX_CIRCUITS
        return lane is None and (
            base in CTL_TARGETS or base == "voice"  # primary id is reserved
            or base in self.voices or base in self.tonics
            or base in self.literals or self._is_drone_id(base))

    def _ctl_sinks(self, src: str) -> list:
        """Resolve a node's outgoing wires to note-sink objects, live.
        (Deck REPLAY resolution lives in looper._sink(), which reads the same
        ctl_wires — this handles keys/arp/tonic/keyshift/drone dispatch.
        Keyshift lanes are addressed "id:lane": lane k in → lane k out only,
        so multiple signals ride one shifter without merging.)"""
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
            elif t in self.literals:
                sinks.append(self.literals[t])
            elif self._is_drone_id(t):
                sinks.append(self._drone_sink(t))  # mono last-note freq sink
            elif base in self.relays and lane is not None:
                try:                     # relay circuit note-in adapter
                    sinks.append(self.relays[base].circuit_in(lane))
                except ValueError:
                    pass  # stale wire with a bad circuit — skip
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
            # BINARY wires (ONE kind since the binary rework, inferred from
            # the source endpoint): a binary source's level lands on level-
            # ins (:pwr, logic named ins, relay circuit ins, relay:ctl) or
            # trig-ins (deriver ids, deck buttons — rising edge fires).
            # Cross-node feedback loops are legal (the settle pass freezes
            # them); direct self-wires are not. Single-input endpoints
            # STEAL: the new wire replaces the old (GUI steal-on-drop).
            if self._is_ping_src(src):
                if action == "add":
                    if not self.gates.is_toggle_dst(dst):
                        raise ValueError(
                            f"{src!r} is a binary source — {dst!r} has no"
                            " binary input")
                    if self.gates._base(src) == self.gates._base(dst):
                        raise ValueError(f"{src} → {dst} would loop on itself")
                    rk = relay_mod.relay_ep(self, dst)
                    if rk is not None:      # circuit in: claim/verify kind
                        rk[0].claim(rk[1], "binary")
                    if self.gates.is_single_input(dst):
                        self.gates.steal_input(dst)
                    if relay_mod.relay_ep(self, src) is not None:
                        # a circuit OUT is 1:1 as well (Cole, 07-24)
                        self.ctl_wires = [x for x in self.ctl_wires
                                          if x.get("from") != src]
                    w = {"from": src, "to": dst}
                    if w not in self.ctl_wires:
                        self.ctl_wires.append(w)
                    self.gates.on_wire_change(src, dst)
                elif action == "remove":
                    self.ctl_wires = [w for w in self.ctl_wires
                                      if not (w["from"] == src and w["to"] == dst)]
                    self._relay_refresh_kinds()
                    self.gates.on_wire_change(src, dst, removed=True)
                else:
                    raise ValueError(f"unknown ctl_wire action {action!r}")
                return
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
                for ep in (src, dst):       # relay circuits: claim/verify NOTES
                    rk = relay_mod.relay_ep(self, ep)
                    if rk is not None:
                        rk[0].claim(rk[1], "notes")
                # relay circuits are 1:1 CONTACT SETS (Cole, 07-24): one
                # wire per side, adding steals. A stolen note-in silences
                # downstream first — the departing controller's held notes
                # must not ring on under the new one.
                out_rk = relay_mod.relay_ep(self, src)
                if out_rk is not None:
                    self.ctl_wires = [x for x in self.ctl_wires
                                      if x.get("from") != src]
                in_rk = relay_mod.relay_ep(self, dst)
                if in_rk is not None and any(x.get("to") == dst
                                             for x in self.ctl_wires):
                    try:
                        in_rk[0].circuit_in(in_rk[1]).all_off()
                    except Exception:  # noqa: BLE001
                        pass
                    self.ctl_wires = [x for x in self.ctl_wires
                                      if x.get("to") != dst]
                w = {"from": src, "to": dst}
                if w not in self.ctl_wires:
                    self.ctl_wires.append(w)
                    if self._is_drone_id(dst):
                        # the play-in is single-input: a new wire means a new
                        # controller — stale held notes from the previous one
                        # must not resurface on later note_offs (hold freq)
                        self._drone_sink(dst).all_off()
                        d = self._deriver(src)
                        if d is not None:
                            # fresh deriver→drone wire: aim the drone at the
                            # current note immediately (don't wait for the grid)
                            n = d.current_note()
                            if n is not None:
                                self._drone_sink(dst).note_on(n)
            elif action == "remove":
                n0 = len(self.ctl_wires)
                self.ctl_wires = [w for w in self.ctl_wires
                                  if not (w["from"] == src and w["to"] == dst)]
                if len(self.ctl_wires) != n0:
                    # unhooking a relay circuit's note-in: silence downstream
                    # (the adapter forwards all_off even while open)
                    rk = relay_mod.relay_ep(self, dst)
                    if rk is not None and rk[0].kinds.get(rk[1]) == "notes":
                        rk[0].circuit_in(rk[1]).all_off()
                    self._relay_refresh_kinds()
                    # unhooking a node's LAST input silences it — a stuck
                    # note is worse live than a dropped one
                    if dst == "arp" and self.arp and \
                            not any(w["to"] == "arp" for w in self.ctl_wires):
                        self.arp.all_off()
                    if dst in self.voices and \
                            not any(w["to"] == dst for w in self.ctl_wires):
                        self.voices[dst].all_off()
                    # unhooking a drone's input drops the source's held
                    # notes (hold the sounding root; no stale fallback if a
                    # later controller releases over old state)
                    if self._is_drone_id(dst) and dst in self._drone_sinks:
                        self._drone_sinks[dst].all_off()
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
        # button triggers ride the CC stream: an ARMED button captures the
        # next CC (non-tonal by construction — the router never surfaces
        # note messages as events); a BOUND button fires on its CC's rising
        # edge. Note-ons can never reach this path, so they can never bind.
        if event.get("kind") == "cc":
            for b in list(self.buttons.values()):
                try:
                    if b.on_cc(event.get("cc"), float(event.get("unit", 0.0))):
                        break
                except Exception:  # noqa: BLE001
                    pass
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
                # ONE exception: voices that died for lack of a target (their
                # last source was removed) come back aimed at this new source —
                # otherwise remove-source → add-source leaves the rack mute.
                missing = [vid for vid in self._voice_targets
                           if vid not in self.voices]
                if missing:
                    inst = self.rack.find(new_id)
                    if inst.module.kind == "source" and "gate" in inst.settings \
                            and "freq" in inst.settings:
                        for vid in missing:
                            self.voices[vid] = self._new_allocation(vid, inst.key)
                            self._voice_targets[vid] = inst.key

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
                # (incl. gate wires into its "<key>:pwr" toggle-in)
                self.ctl_wires = [w for w in self.ctl_wires
                                  if key not in (w.get("from"), w.get("to"))
                                  and w.get("to") != f"{key}:pwr"]
                self._relay_refresh_kinds()
                self._drone_sinks.pop(key, None)
                if self._legacy_drone_id == key:
                    self._legacy_drone_id = None
                    self._legacy_drone = False
                # drop any LFO destinations on the departing module + map guards
                self.lfos.on_module_removed(key)
                # ... and the relay-routed mod wires that aimed at them
                self.mod_wires = [
                    w for w in self.mod_wires
                    if str(w.get("to", "")).rpartition(":")[0] != key]
                self._mod_managed = {kp for kp in self._mod_managed
                                     if kp[0] != key}
                self._relay_refresh_kinds()
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
                if self.relays:
                    # circuits that fed (or were fed by) the removed module
                    # re-point via the healed wires; unclaimed circuits
                    # (kind forgotten above) release their gate synths
                    try:
                        relay_mod.apply_audio(self)
                    except Exception:  # noqa: BLE001
                        pass

            elif action == "move":
                # audio order is wire-defined; a move is a pure list reorder.
                insts = [i for i in self.rack.instances if not i.service]
                svc = [i for i in self.rack.instances if i.service]
                i = next(n for n, ins in enumerate(insts) if ins.key == key)
                j = max(0, min(len(insts) - 1, i + (index or 0)))
                insts.insert(j, insts.pop(i))
                self.rack.instances = insts + svc
                relay_mod.apply_audio(self)
            else:
                raise ValueError(f"unknown edit_chain action {action!r}")

            if self.patch is not None:
                self.patch["chain"] = [
                    (i.key, {}) for i in self.rack.instances if not i.service
                ]
            # a drone ADDED while the transport is stopped spawns silent
            # (item 32); remove/move are no-ops for this sync
            self._sync_drone_run_state()
            return result

    def graph_wire(self, action: str, src: str, dst: str | None = None) -> None:
        """Live audio rewiring: add (src → dst|"master") or remove (park src on
        the null bus). One outgoing audio wire per source; fan-in is free
        (buses sum). Stored so rebuilds re-apply it.

        Binary rework: relay CIRCUIT endpoints ("relay:3") are legal on
        either end — they bypass rack.find and are stored VERBATIM. Item
        25: a claimed audio circuit is a permanent lagged-gate synth, so
        these wires are REAL whether the relay is open or closed
        (relay.apply_audio); open/close moves only the gate param."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            src_rk = relay_mod.relay_ep(self, src)
            if src_rk is None:
                # normalize legacy type keys to instance ids (raises for GUI)
                src = self.rack.find(src).key
            if self.graph_wires is None:
                self.graph_wires = self.rack.audio_wires()
            wires = [w for w in self.graph_wires if w["from"] != src]
            if action == "add":
                if not dst:
                    raise ValueError("graph_wire add needs a destination")
                dst_rk = relay_mod.relay_ep(self, dst)
                if dst != "master" and dst_rk is None:
                    dst = self.rack.find(dst).key
                if dst != "master":
                    # cycle guard over the STORED wires — relay endpoints
                    # walk like nodes, so a would-be loop through a relay
                    # is rejected regardless of its closed state
                    adj = {w["from"]: w["to"] for w in wires}
                    cur, hops = dst, 0
                    while cur not in (None, "master") and hops < 64:
                        if cur == src:
                            raise ValueError(f"{src} → {dst} would create an audio cycle")
                        cur = adj.get(cur)
                        hops += 1
                # relay circuits carry ONE kind: claim/verify audio
                for rk in (src_rk, dst_rk):
                    if rk is not None:
                        rk[0].claim(rk[1], "audio")
                if dst_rk is not None:
                    # a circuit IN is a 1:1 CONTACT, not a summing bus
                    # (Cole, 07-24): the incoming wire steals it, and the
                    # displaced source parks on the null bus
                    for old in [w for w in wires if w.get("to") == dst]:
                        if relay_mod.relay_ep(self, old["from"]) is None:
                            try:
                                self.rack.audio_disconnect(old["from"])
                            except Exception:  # noqa: BLE001
                                pass
                        old["to"] = None
                wires.append({"from": src, "to": dst})
                self.graph_wires = wires
                if src_rk is None and dst_rk is None:
                    self.rack.audio_rewire(src, dst)   # plain wire: as before
            elif action == "remove":
                if src_rk is None:
                    wires.append({"from": src, "to": None})
                    self.graph_wires = wires
                    self.rack.audio_disconnect(src)
                else:
                    # a circuit with no out-wire is parked by definition —
                    # no "to": None placeholder (it would pin the claim
                    # alive after the last real wire leaves)
                    self.graph_wires = wires
                self._relay_refresh_kinds()
            else:
                raise ValueError(f"unknown graph_wire action {action!r}")
            # item 25: relay endpoints are REAL nodes now (lagged-gate
            # synths) — apply_audio syncs them, wires them, and orders
            # every wire's src before its dst; with no claimed circuits
            # it degrades to the plain (cheap-pathed) reorder
            relay_mod.apply_audio(self)

    def swap_synth(self, key: str, new_type: str) -> None:
        """Swap a running instance's module type IN PLACE (the Instrument
        card's dropdown): same instance id, buses, wires, and node order —
        rack.swap_module REPLACEs the node on the server. Control-plane
        hygiene here: LFO destinations (and map guards) on params the new
        module doesn't have are dropped; surviving mapped params get re-mapped
        onto the fresh node. Voices keep targeting the id and simply re-gate
        the new synth on the next note."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            inst = self.rack.find(key)
            key = inst.key
            if new_type not in self.registry:
                raise ValueError(f"unknown module type {new_type!r}")
            new_params = set(self.registry[new_type].params)
            # dests on params that don't survive the swap go first (their
            # scale synths would otherwise steer a param the node lacks)
            for lid, rec in list(self.lfos.instances.items()):
                for (k, pname) in [d for d in list(rec["dests"])
                                   if d[0] == key and d[1] not in new_params]:
                    try:
                        self.lfos.unwire(lid, k, pname)
                    except Exception:  # noqa: BLE001
                        pass
            self.rack.mapped = {(k, p) for (k, p) in self.rack.mapped
                                if k != key or p in new_params}
            self.rack.swap_module(key, new_type)
            self.lfos.on_node_replaced(key)      # re-map surviving dests
            if self.graph_wires is not None:
                relay_mod.apply_audio(self)
            # a swap TO drone while the transport is stopped lands paused
            # (item 32)
            self._sync_drone_run_state()

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
        """Re-aim a mono voice at another playable source (GUI wire re-drag).

        A voice whose last target was removed no longer EXISTS (_make_voices
        skips targetless ids) — retargeting must resurrect it, not refuse,
        or the rack is unrecoverably silent until a patch reload."""
        with self._lock:
            if not self.rack:
                raise RuntimeError("no rack running")
            inst = self.rack.find(key)
            if inst.module.kind != "source" or "gate" not in inst.settings \
                    or "freq" not in inst.settings:
                raise ValueError(f"{key} is not a note-playable source")
            v = self.voices.get(voice)
            if v is None:
                if voice != "voice" and voice not in self._voice_targets:
                    raise RuntimeError(f"no voice {voice!r} to retarget")
                v = self._new_allocation(voice, inst.key)  # revive the dead voice
                self.voices[voice] = v
            else:
                v.all_off()  # silence the old target before switching
                v.target_key = inst.key
            self._voice_targets[voice] = inst.key

    # -- multiple voices: mono, poly and drone ------------------------------------

    def _spawn_allocation(self, kind: str, size: int | None = None) -> str:
        if not self.rack:
            raise RuntimeError("no rack running")
        target = self._guess_voice_target()
        if not target:
            raise ValueError("no note-playable source to aim a voice at")
        vid = alloc_id(kind, self.voices.keys() | self._voice_targets.keys())
        if size is not None:
            self._poly_sizes[vid] = max(1, min(int(size), MAX_POLY_VOICES))
        self.voices[vid] = self._new_allocation(vid, target)
        self._voice_targets[vid] = None
        return vid

    def spawn_voice(self) -> str:
        """Add another mono voice ("voice.2", ...). It arrives unwired —
        patch keys/arp/deck into it — aimed at the first playable source.

        Two mono voices on the SAME target used to stomp each other (both
        set freq/gate on the one node, so either one's note-off cut the
        other's held note). Each now leases its own slot from the target's
        voice pool, so they sound as genuinely distinct voices whether or
        not you give them separate sources."""
        with self._lock:
            return self._spawn_allocation("voice")

    def spawn_poly(self, voices: int = 8) -> str:
        """Add a POLY voice ("poly", "poly.2", ...): N notes at once on ONE
        target source, stealing the oldest when full."""
        with self._lock:
            return self._spawn_allocation("poly", size=voices)

    def set_poly_voices(self, vid: str, voices: int) -> None:
        """Resize a poly voice. Notes on slots that go away are closed."""
        with self._lock:
            v = self.voices.get(vid)
            if not isinstance(v, Poly):
                raise KeyError(f"no poly voice {vid!r}")
            v.set_voices(voices)
            self._poly_sizes[vid] = v.voices

    def spawn_drone_voice(self) -> str:
        """Add a DRONE voice ("hold", "hold.2", ...): item 29.

        Aimed at any playable source, it steers that source's freq from its
        TONE input and holds its gate open from its POWER input. It leases
        its own slot, so a drone and a poly voice can share one source
        without fighting over it."""
        with self._lock:
            return self._spawn_allocation("hold")

    def set_drone_power(self, vid: str, on: bool) -> None:
        """The drone card's POWER: hold the target's envelope open.

        The drone module's "bypass is the only off switch" becomes LEVEL
        semantics here — a binary wire into "<id>:pwr" drives this, and so
        does a click on the card. Both routes announce themselves so the
        indicator reacts to LOGIC input (reactive-indicator doctrine)."""
        with self._lock:
            v = self.voices.get(vid)
            if not isinstance(v, Hold):
                raise KeyError(f"no drone voice {vid!r}")
            self._drone_powers[vid] = bool(on)
            self._apply_drone_power(vid)
        self._emit_midi_event(
            {"kind": "level", "ep": f"{vid}:pwr", "on": bool(on)})

    def _apply_drone_power(self, vid: str) -> None:
        """Effective gate = POWER **and** transport.running.

        Item 32's invariant, in allocation terms: a drone is gateless from
        the note stream, so a stopped transport is the only other thing that
        silences it. Drone MODULE nodes get paused; a drone CARD closes the
        gate it is holding on its target instead — pausing the target would
        silence the poly voice sharing it."""
        v = self.voices.get(vid)
        if isinstance(v, Hold):
            v.set_gate_open(self._drone_powers.get(vid, False)
                            and self.transport.running)

    def remove_voice(self, vid: str) -> None:
        with self._lock:
            if vid == "voice":
                raise ValueError("the primary voice cannot be removed")
            v = self.voices.pop(vid, None)
            self._voice_targets.pop(vid, None)
            self._poly_sizes.pop(vid, None)
            self._drone_powers.pop(vid, None)
            if v is None:
                raise KeyError(f"no voice {vid!r}")
            try:
                v.dispose()  # close its notes AND hand the slots back, or the
                             # satellites outlive the card and drone on
            except Exception:  # noqa: BLE001
                pass
            self.ctl_wires = [w for w in self.ctl_wires
                              if vid not in (w.get("from"), w.get("to"))
                              # ...and the binary wire into its POWER in
                              and w.get("to") != f"{vid}:pwr"]
            self._relay_refresh_kinds()

    # -- the binary plane: logic gates + relays ------------------------------------

    def spawn_relay(self, want_id: str | None = None) -> str:
        with self._lock:
            rid = want_id or alloc_id("relay", self.relays.keys())
            if rid not in self.relays:
                self.relays[rid] = RelayNode(self, rid)
            return rid

    def remove_relay(self, rid: str) -> None:
        with self._lock:
            r = self.relays.get(rid)
            if r is None:
                raise KeyError(f"no relay {rid!r}")
            # silence downstream note sinks FIRST (their wires must still
            # resolve for the all_off to reach them)
            for k, kind in list(r.kinds.items()):
                if kind == "notes":
                    try:
                        r.circuit_in(k).all_off()
                    except Exception:  # noqa: BLE001
                        pass
            eps = {f"{rid}:{k}"
                   for k in range(1, relay_mod.MAX_CIRCUITS + 1)}
            eps.add(f"{rid}:ctl")
            # park every source that fed its audio circuits
            if self.rack is not None and self.graph_wires is not None:
                for w in self.graph_wires:
                    if w.get("to") in eps and \
                            relay_mod.relay_ep(self, w.get("from")) is None:
                        try:
                            self.rack.audio_disconnect(w["from"])
                        except Exception:  # noqa: BLE001
                            pass
                self.graph_wires = [
                    {**w, "to": None} if w.get("to") in eps else w
                    for w in self.graph_wires if w.get("from") not in eps]
            self.ctl_wires = [w for w in self.ctl_wires
                              if w.get("from") not in eps
                              and w.get("to") not in eps]
            self.mod_wires = [w for w in self.mod_wires
                              if w.get("from") not in eps
                              and w.get("to") not in eps]
            del self.relays[rid]
            relay_mod.resolve_mod(self)   # orphaned params fall back to knob
            for ep in eps:                  # edge state dies with the node
                self.gates._edge.pop(ep, None)
            self.gates.recompute()
            # gate synths of the departed relay's audio circuits die here
            # (sync releases them — the relay is gone from app.relays)
            try:
                relay_mod.apply_audio(self)
            except Exception:  # noqa: BLE001
                pass

    def set_relay(self, rid: str, closed=None) -> None:
        """The manual click. Last writer wins — a wired relay:ctl level
        simply overwrites this on its next change, and vice versa."""
        with self._lock:
            r = self.relays.get(rid)
            if r is None:
                raise KeyError(f"no relay {rid!r}")
            if closed is not None:
                r.set_closed(closed)

    def spawn_logic(self, want_id: str | None = None) -> str:
        with self._lock:
            return self.gates.spawn_logic(want_id)

    def remove_logic(self, lid: str) -> None:
        with self._lock:
            self.gates.remove_logic(lid)

    def set_logic(self, lid: str, op=None) -> None:
        with self._lock:
            self.gates.set_logic(lid, op=op)

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
            # snip-heal candidates: note streams IN, note streams OUT (since
            # the drone rework EVERY out is an ordinary ctl wire — a healed
            # A→drone feeds the drone A's notes, which is exactly the model).
            # Ping wires (trigger-ins) are a different kind: never healed.
            ins = [w["from"] for w in self.ctl_wires
                   if w.get("to") == tid and not self._is_ping_src(w.get("from"))]
            outs = [w["to"] for w in self.ctl_wires if w.get("from") == tid]
            self.ctl_wires = [w for w in self.ctl_wires
                              if tid not in (w.get("from"), w.get("to"))]
            self._relay_refresh_kinds()
            self._heal_ctl_snip(ins, outs)
            if self._legacy_drone and tid == "tonic":
                self._legacy_drone = False

    def set_tonic(self, tid: str, **settings) -> None:
        with self._lock:
            d = self.tonics.get(tid)
            if d is None:
                raise KeyError(f"no tonic deriver {tid!r}")
            d.configure(**settings)

    # -- literal derivers (deterministic extract/place) ---------------------------

    def spawn_literal(self, want_id: str | None = None) -> str:
        with self._lock:
            lid = want_id or alloc_id("literal", self.literals.keys())
            if lid not in self.literals:
                self.literals[lid] = LiteralDeriver(self, lid)
            return lid

    def remove_literal(self, lid: str) -> None:
        with self._lock:
            d = self.literals.pop(lid, None)
            if d is None:
                raise KeyError(f"no literal deriver {lid!r}")
            d.shutdown()
            ins = [w["from"] for w in self.ctl_wires
                   if w.get("to") == lid and not self._is_ping_src(w.get("from"))]
            outs = [w["to"] for w in self.ctl_wires if w.get("from") == lid]
            self.ctl_wires = [w for w in self.ctl_wires
                              if lid not in (w.get("from"), w.get("to"))]
            self._relay_refresh_kinds()
            self._heal_ctl_snip(ins, outs)

    def set_literal(self, lid: str, **settings) -> None:
        with self._lock:
            d = self.literals.get(lid)
            if d is None:
                raise KeyError(f"no literal deriver {lid!r}")
            d.configure(**settings)

    # -- binary trigger sources (button / clock) ----------------------------------

    def _drop_binary_src_wires(self, nid: str) -> None:
        """A binary source is gone: its wires go too, their targets start
        edge-fresh, and orphaned relay circuits forget their kind."""
        dropped = [w.get("to") for w in self.ctl_wires
                   if nid in (w.get("from"), w.get("to"))]
        self.ctl_wires = [w for w in self.ctl_wires
                          if nid not in (w.get("from"), w.get("to"))]
        for d in dropped:
            self.gates._edge.pop(d, None)
        self._relay_refresh_kinds()
        self.gates.recompute()

    def spawn_button(self, want_id: str | None = None) -> str:
        with self._lock:
            bid = want_id or alloc_id("button", self.buttons.keys())
            if bid not in self.buttons:
                self.buttons[bid] = ButtonTrigger(self, bid)
            return bid

    def remove_button(self, bid: str) -> None:
        with self._lock:
            b = self.buttons.pop(bid, None)
            if b is None:
                raise KeyError(f"no button trigger {bid!r}")
            b.shutdown()
            # a binary source's wires go with it (no heal: levels have no thru)
            self._drop_binary_src_wires(bid)

    def set_button(self, bid: str, **settings) -> None:
        with self._lock:
            b = self.buttons.get(bid)
            if b is None:
                raise KeyError(f"no button trigger {bid!r}")
            # arming one button disarms the others — ONE pairing at a time
            if settings.get("armed"):
                for other in self.buttons.values():
                    if other is not b:
                        other.armed = False
            b.configure(**settings)

    def fire_button(self, bid: str) -> None:
        """Click compat: momentary → a pulse; latch → toggle."""
        b = self.buttons.get(bid)
        if b is None:
            raise KeyError(f"no button trigger {bid!r}")
        b.fire()

    def button_down(self, bid: str) -> None:
        """Mouse/key DOWN: momentary level hi while held; latch toggles."""
        b = self.buttons.get(bid)
        if b is None:
            raise KeyError(f"no button trigger {bid!r}")
        b.press()

    def button_up(self, bid: str) -> None:
        """Mouse/key UP: momentary level back lo; latch ignores."""
        b = self.buttons.get(bid)
        if b is None:
            raise KeyError(f"no button trigger {bid!r}")
        b.release()

    def spawn_clock(self, want_id: str | None = None) -> str:
        with self._lock:
            cid = want_id or alloc_id("clock", self.clocks.keys())
            if cid not in self.clocks:
                self.clocks[cid] = ClockTrigger(self, cid)
            return cid

    def remove_clock(self, cid: str) -> None:
        with self._lock:
            c = self.clocks.pop(cid, None)
            if c is None:
                raise KeyError(f"no clock trigger {cid!r}")
            c.shutdown()
            self._drop_binary_src_wires(cid)

    def set_clock(self, cid: str, **settings) -> None:
        with self._lock:
            c = self.clocks.get(cid)
            if c is None:
                raise KeyError(f"no clock trigger {cid!r}")
            c.configure(**settings)

    # -- thresholds (item 8: CV edge → ping) --------------------------------------

    def spawn_threshold(self, want_id: str | None = None) -> str:
        with self._lock:
            return self.thresholds.spawn(want_id=want_id)

    def remove_threshold(self, tid: str) -> None:
        with self._lock:
            self.thresholds.remove(tid)
            # a binary source's wires go with it (no heal: levels have no thru)
            self._drop_binary_src_wires(tid)

    def set_threshold(self, tid: str, **settings) -> None:
        with self._lock:
            self.thresholds.configure(tid, **settings)

    def threshold_wire(self, action: str, tid: str, lfo_id: str | None) -> None:
        """Add/remove the CV wire: LFO out → a threshold's CV-in
        (single-input; ping-out wires ride ctl_wires like button/clock)."""
        with self._lock:
            self.thresholds.wire(action, tid, lfo_id)

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
            self._relay_refresh_kinds()
            for ins, outs in lane_pairs:
                self._heal_ctl_snip(ins, outs)

    def set_keyshift(self, kid: str, **settings) -> None:
        with self._lock:
            ks = self.keyshifts.get(kid)
            if ks is None:
                raise KeyError(f"no key shifter {kid!r}")
            ks.configure(**settings)

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
                hi = self.transport.accent_on(beat)
                self.engine.root_group.add_synth(
                    _click,
                    add_action="add_to_tail",
                    freq=2000 if hi else 1400,   # high tick on the downbeat
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

    # -- routable LFOs (standalone modulation nodes, item 7) ----------------------

    def spawn_lfo(self, want_id: str | None = None) -> str:
        with self._lock:
            return self.lfos.spawn(want_id=want_id)

    def remove_lfo(self, lid: str) -> None:
        with self._lock:
            self.thresholds.on_lfo_removed(lid)  # CV-ins unwire first
            self.mod_wires = [w for w in self.mod_wires
                              if w.get("from") != lid]
            self.lfos.remove(lid)
            self._relay_refresh_kinds()
            relay_mod.resolve_mod(self)

    def lfo_set(self, lid: str, **cfg) -> None:
        with self._lock:
            self.lfos.configure(lid, **cfg)

    def lfo_wire(self, action: str, lid: str, key: str, name: str) -> None:
        """Add/remove a DIRECT modulation wire: LFO out → a param's quiet
        handle. A param is single-input, so a direct wire also evicts any
        relay-routed mod wire that was driving it (and the relay layer
        stops managing that dest)."""
        with self._lock:
            if action == "add":
                self.lfos.wire(lid, key, name)
                try:
                    ikey = self.rack.find(key).key
                except Exception:  # noqa: BLE001
                    ikey = key
                self.mod_wires = [w for w in self.mod_wires
                                  if w.get("to") != f"{ikey}:{name}"]
                self._mod_managed.discard((ikey, name))
                self._relay_refresh_kinds()
            elif action == "remove":
                self.lfos.unwire(lid, key, name)
                # a dest the RELAY layer owns re-asserts itself: its stored
                # mod wires still exist and its circuits are still closed,
                # so an unwire here would otherwise leave those hops drawn
                # but silent (Cole's "the relay wires are dummies", 07-24)
                relay_mod.resolve_mod(self)
            else:
                raise ValueError(f"unknown lfo_wire action {action!r}")

    # -- the mod plane through relays (Cole, 07-24) --------------------------------

    def _mod_src_ok(self, src) -> bool:
        """Mod OUTPUTS: an LFO instance, or a relay circuit endpoint."""
        if relay_mod.relay_ep(self, src) is not None:
            return True
        return src in self.lfos.instances

    def _mod_dst_ok(self, dst) -> bool:
        """Mod INPUTS: a relay circuit endpoint, or a live "<key>:<param>"."""
        if relay_mod.relay_ep(self, dst) is not None:
            return True
        return relay_mod.param_ep(self, dst) is not None

    def mod_wire(self, action: str, src: str, dst: str | None = None) -> None:
        """Add/remove a mod wire through the relay plane. Endpoints: an LFO
        id ("lfo.2"), a relay CIRCUIT ("relay:3" — in or out, direction
        disambiguates) or a param ("lowpass.2:cutoff").

        A circuit is a 1:1 CONTACT SET: adding steals whatever held that
        side. A param in is single-input as ever (LFO fan-OUT stays free).
        The wires are STORED; which params actually move is resolved
        through the closed circuits by relay.resolve_mod."""
        with self._lock:
            if action == "add":
                if not self._mod_src_ok(src):
                    raise ValueError(f"{src!r} has no mod output")
                if not self._mod_dst_ok(dst):
                    raise ValueError(f"cannot wire mod into {dst!r}")
                if str(src).partition(":")[0] == str(dst).partition(":")[0]:
                    raise ValueError(f"{src} → {dst} would loop on itself")
                # cycle guard over the STORED wires — circuits walk like
                # nodes, so a loop through a relay is rejected whatever its
                # closed state
                adj = {w["from"]: w.get("to") for w in self.mod_wires
                       if relay_mod.relay_ep(self, w.get("from")) is not None}
                cur, hops = dst, 0
                while cur is not None and hops < 64:
                    if cur == src:
                        raise ValueError(
                            f"{src} → {dst} would create a mod cycle")
                    cur = adj.get(cur)
                    hops += 1
                for ep in (src, dst):        # circuits carry ONE kind
                    rk = relay_mod.relay_ep(self, ep)
                    if rk is not None:
                        rk[0].claim(rk[1], "mod")
                src_is_circuit = relay_mod.relay_ep(self, src) is not None
                self.mod_wires = [
                    w for w in self.mod_wires
                    if w.get("to") != dst
                    and not (src_is_circuit and w.get("from") == src)]
                self.mod_wires.append({"from": src, "to": dst})
            elif action == "remove":
                self.mod_wires = [
                    w for w in self.mod_wires
                    if not (w.get("from") == src
                            and (dst is None or w.get("to") == dst))]
                self._relay_refresh_kinds()
            else:
                raise ValueError(f"unknown mod_wire action {action!r}")
            relay_mod.resolve_mod(self)

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
        # instant model: the committed root, else the current leading pc
        pc = d.root if d.root is not None else d.est.analysis(d.root)["leading"]
        return {
            "weights": [round(w / total, 4) for w in weights],
            "root": NOTE_NAMES[pc] if pc is not None else None,
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
        for w in ({"from": "arp", "to": "tonic"}, {"from": "tonic", "to": did}):
            if w not in self.ctl_wires:
                self.ctl_wires.append(w)
        # enabling the drone while the transport is STOPPED must not make a
        # sound — it comes up paused and unpauses on play (item 32)
        self._sync_drone_run_state()

    def _sync_drone_run_state(self) -> None:
        """Pause/unpause every ENABLED drone node to match transport.running.

        Drones are gateless — they sound the moment their node runs — so a
        stopped transport is the only thing that silences them. Item 32
        (fresh boots come up STOPPED) turns that into a standing invariant
        rather than a set_transport side effect: every path that creates,
        replaces or re-enables a drone node while the transport is stopped
        must land it PAUSED (patch build/reload, edit_chain add, swap_synth,
        set_enabled, the legacy compat pair). DISABLED instances are skipped
        — bypass pausing belongs to rack.set_enabled and must not be undone
        here.

        ITEM 29 extends the same invariant to the drone CARD. A hold
        allocation is not a node, so there is nothing to pause — its POWER
        is re-pushed with `transport.running` ANDed in, which also re-aims
        its root. That re-push is what makes a drone survive a rebuild at
        all: the rack comes up fresh and every satellite spawns `gate=0`."""
        running = self.transport.running
        for inst in (self.rack.instances if self.rack else []):
            if inst.type != "drone" or inst.node is None or not inst.enabled:
                continue
            try:
                (inst.node.unpause if running else inst.node.pause)()
            except Exception:  # noqa: BLE001
                pass
        for vid, v in self.voices.items():
            if not isinstance(v, Hold):
                continue
            try:
                v.refresh()             # re-aim the held root first...
                self._apply_drone_power(vid)   # ...then the effective gate
            except Exception:  # noqa: BLE001
                pass

    def set_transport(self, bpm=None, beats_per_bar=None, click=None, accent=None,
                      playing=None, downbeat=None) -> None:
        if accent is not None:
            self.transport.click_accent = bool(accent)
        if playing is not None:
            self.transport.set_running(bool(playing))
            if not playing and self.arp:
                self.arp._safe_all_off() if hasattr(self.arp, "_safe_all_off") else None
            # transport stop/start pauses every drone instance
            self._sync_drone_run_state()
        if bpm is not None:
            self.transport.set_bpm(bpm)
        if beats_per_bar is not None:
            self.transport.set_meter(beats_per_bar)   # re-clamps downbeat
        if downbeat is not None:
            # AFTER set_meter so a combined meter+downbeat message clamps
            # against the new bar length
            self.transport.set_downbeat(downbeat)
        if click is not None:
            self.transport.click_enabled = bool(click)

    # -- transport cards (item 9: canvas views of the GLOBAL transport) -----------

    def spawn_transport_card(self, which: str) -> str:
        """Put a transport card on the canvas ("play" = stop/play,
        "tempo" = tempo/click). Idempotent — a card is PRESENCE, not
        state: the transport stays global and single."""
        with self._lock:
            if which not in TRANSPORT_CARDS:
                raise ValueError(f"unknown transport card {which!r}")
            self.transport_cards.add(which)
            return which

    def remove_transport_card(self, which: str) -> None:
        """Take a transport card off the canvas. Idempotent. The wire
        endpoints (transport:run/click/accent/tap) belong to the GLOBAL
        transport, not the card — existing wires persist and keep
        applying after the card is gone."""
        with self._lock:
            if which not in TRANSPORT_CARDS:
                raise ValueError(f"unknown transport card {which!r}")
            self.transport_cards.discard(which)

    def _transport_tap(self, now: float | None = None) -> None:
        """One rising edge on "transport:tap". TEMPO ONLY — never touches
        running or the beat phase beyond set_bpm's continuous re-anchor.
        `now` is injectable for tests (defaults to time.monotonic())."""
        bpm = self._tap_tempo.tap(now)
        if bpm is not None:
            self.set_transport(bpm=bpm)

    def stop(self) -> None:
        with self._lock:
            self.looper.shutdown()
            self.drums.shutdown()
            self.thresholds.clear()   # watch synths before their LFO norms
            self.lfos.clear()
            self.relay_audio.clear()  # relay gate synths are engine-level too
            # the NEXT engine (device switch reboots via stop→start) must
            # re-receive synthdefs + re-register the /tr callback
            self.thresholds.reset()
            self.lfos.reset()
            self.relay_audio.reset()
            for d in (*self.tonics.values(), *self.literals.values()):
                d.shutdown()
            for ks in self.keyshifts.values():
                try:
                    ks.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            for trg in (*self.buttons.values(), *self.clocks.values()):
                try:
                    trg.shutdown()
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
            self._relay_refresh_kinds()           # note/binary circuits re-infer
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
                # rack enable UNPAUSES the node — a drone re-enabled while
                # the transport is stopped must land back paused (item 32)
                self._sync_drone_run_state()

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

    def _wires_state(self) -> list[dict]:
        """The "wires" broadcast (item 25): the STORED graph wires, relay
        endpoints verbatim, open or closed — every client renders relay
        hops from server truth (the old rack derivation could not see the
        manager-owned gate synths, so relay hops misrendered on any second
        client: an open hop vanished, a closed one drew as a shortcut
        bypassing the relay). Parked ("to": None) wires are omitted,
        matching the old derived shape. Pre-overlay (graph_wires None = no
        structural edit yet) still falls back to the rack derivation."""
        if self.graph_wires is not None:
            return [{"from": w["from"], "to": w["to"]}
                    for w in self.graph_wires if w.get("to") is not None]
        return self.rack.audio_wires() if self.rack else []

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
                # one entry per ALLOCATION — mono, poly or drone. `policy`
                # tells the GUI which card to draw; `slots` is how many notes
                # it can sound at once (always 1 for mono-latest and hold).
                # `power` is the drone card's POWER (item 29), null for the
                # gated policies — it is USER INTENT, not the effective gate,
                # which also depends on transport.running.
                "voices": [{"id": vid, "target": v.target_key,
                            "policy": v.policy, "slots": v.voices,
                            "power": (self._drone_powers.get(vid, False)
                                      if isinstance(v, Hold) else None)}
                           for vid, v in self.voices.items()],
                "tonics": [d.settings() for d in self.tonics.values()],
                "literals": [d.settings() for d in self.literals.values()],
                "keyshifts": [k.settings() for k in self.keyshifts.values()],
                "buttons": [b.settings() for b in self.buttons.values()],
                "clocks": [c.settings() for c in self.clocks.values()],
                "transpose": self._transpose,
                "midi_inputs": _list_midi_inputs(),
                "midi_port": self.router.active_port if self.router else None,
                "midi_enabled": self.midi_enabled,
                "wires": self._wires_state(),
                "ctl_wires": [dict(w) for w in self.ctl_wires],
                "drums_target": self.drums.target,
                "arp": self.arp.settings() if self.arp else None,
                "transport": self.transport.settings(),
                "transport_cards": sorted(self.transport_cards),
                "drone": self._legacy_drone_settings(),
                "drums": self.drums.settings(),
                "looper": self.looper.settings(),
                "lfos": self.lfos.state(),
                "thresholds": self.thresholds.state(),
                **self.gates.state(),   # "logics" (binary rework: no switches)
                "relays": [r.settings() for r in self.relays.values()],
                "mod_wires": [dict(w) for w in self.mod_wires],
                "presets": presets_mod.list_presets(),
                "available": sorted(
                    ({"key": m.key, "name": m.name, "kind": m.kind,
                      "family": m.family}
                     for m in self.registry.values()),
                    key=lambda d: (d["kind"] != "source", d["family"], d["key"]),
                ),
                "module_errors": {k: repr(v) for k, v in self.module_errors.items()},
            }
