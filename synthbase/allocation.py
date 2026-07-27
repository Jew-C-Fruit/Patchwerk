"""Voice allocation: one policy family behind every note-driven node.

Three things in this repo turn a stream of note events into sound, and until
now each carried its own copy of the answer:

* the MONO VOICE (was ``midi.MonoVoice``) — last-note priority, gate follows
  the held set, release on empty;
* the DRONE (``app._DroneSink``) — last-note priority, **no gate**, and an
  empty held set HOLDS the last root rather than releasing;
* the POLY VOICE — didn't exist, which is item 10.

They differ in exactly one axis: the ALLOCATION POLICY — how many notes may
sound at once, which sounding note a new note displaces, and what an empty
held set means. Everything else (transpose, bend, the ``on_voiced`` viz tap,
retargeting onto another source) is shared. So the policy is the subclass and
the rest is the base, and item 29 (drone as an allocation) is the third
policy rather than a fourth implementation.

  ``MonoLatest``  1 slot, gate, release on empty   — Mono Voice
  ``Poly``        N slots, gate, steal oldest      — Poly Voice   (item 10)
  ``Hold``        1 slot, no gate, hold on empty   — Drone        (item 29)

Slots and satellites
--------------------
An allocation does not own a synth; it LEASES SLOTS from the target source's
``VoicePool``. Slot 0 is the target instance's own node, driven through
``rack.set_params`` exactly as the mono voice always drove it — so a lone
voice on a lone source behaves identically to before pools existed, and the
card's freq/gate readout keeps moving. Slots >= 1 are SATELLITES: real
scsynth synths cloned from the instance's settings, writing to the same out
bus (extra sources SUM — the bus doctrine gives polyphony for free), with no
``Instance``, no card and no state entry. One card, N voices.

That single mechanism fixes both halves of item 10. Poly leases N slots on
one target. And a SECOND mono voice aimed at a source that already has one
now leases slot 1 instead of stomping slot 0 — which is why ``voice`` and
``voice.2`` could spawn but never sound as distinct voices (both called
``set_params`` on the same node, so each note-off cut the other's held note).

Satellites are derived state: they are re-cloned whenever the target's node
identity changes (bypass, hot reload, rack rebuild) or the server object is
replaced, per the "track the server OBJECT, not a boolean" landmine.
"""

from __future__ import annotations

import threading
from typing import Any

from supriya import AddAction

A4_MIDI, A4_FREQ = 69, 440.0

#: Params that ARE the voice: never mirrored from the target onto satellites,
#: or every satellite would collapse onto slot 0's pitch and gate.
PER_VOICE_PARAMS = ("freq", "gate")

#: Gap between closing a stolen voice's gate and reopening it, in seconds.
#: A gate is level-triggered, so ``gate=1`` while already 1 does not restrike
#: the envelope — a stolen note would inherit its predecessor's envelope
#: position. Closing and reopening across a scheduled gap gives the new note
#: its own attack. Short enough to read as immediate.
STEAL_GAP = 0.012

MAX_POLY_VOICES = 16


def midi_to_freq(note: float) -> float:
    return A4_FREQ * 2 ** ((note - A4_MIDI) / 12)


# --------------------------------------------------------------------------
# slots and pools
# --------------------------------------------------------------------------


class Slot:
    """One independently gateable voice on a target source.

    ``index`` 0 is the target instance's own node; higher indices are
    satellites. The distinction lives here and nowhere else: callers just
    ``set(freq=..., gate=...)``.
    """

    __slots__ = ("pool", "index", "node", "_settings")

    def __init__(self, pool: "VoicePool", index: int, node: Any = None) -> None:
        self.pool = pool
        self.index = index
        self.node = node
        self._settings: dict[str, float] = {}

    @property
    def is_primary(self) -> bool:
        return self.index == 0

    def set(self, **values: float) -> None:
        self._settings.update(values)
        self.pool._write(self, values)

    def settings(self) -> dict[str, float]:
        return dict(self._settings)


class VoicePool:
    """Every slot available on ONE target source instance.

    Lives on the rack (``rack.voice_pools``) so its lifetime is the rack's:
    a rebuild makes a new rack and the old pools die with it, satellites and
    all. Allocations acquire and release leases; the pool owns the nodes.
    """

    def __init__(self, rack: Any, target_key: str) -> None:
        self.rack = rack
        self.target_key = target_key
        self._lock = threading.RLock()
        self._slots: list[Slot] = []
        self._leased: set[int] = set()
        # identity of what the satellites were cloned FROM; a change in
        # either means every satellite is stale (see module docstring)
        self._server: Any = None
        self._primary_node: Any = None

    # -- identity / staleness ------------------------------------------------

    def _instance(self):
        try:
            return self.rack.find(self.target_key)
        except KeyError:
            return None

    def _server_obj(self):
        engine = getattr(self.rack, "engine", None)
        return getattr(engine, "server", None) if engine else None

    def _validate(self) -> None:
        """Drop satellites whose provenance no longer holds."""
        inst = self._instance()
        server = self._server_obj()
        node = inst.node if inst is not None else None
        if server is self._server and node is self._primary_node:
            return
        self._free_satellites()
        self._server = server
        self._primary_node = node
        # leases survive: the allocations still want their slots, they just
        # need fresh nodes. Re-spawn for every index still leased.
        for index in sorted(i for i in self._leased if i > 0):
            self._ensure_satellite(index)

    # -- satellite lifecycle -------------------------------------------------

    def _satellite_settings(self, inst) -> dict[str, float]:
        settings = dict(inst.settings)
        # a satellite must come up SILENT — a fresh node inheriting the
        # target's live gate would sound a note nobody played (the
        # "playable sources must spawn gate=0" landmine)
        if "gate" in settings:
            settings["gate"] = 0
        return settings

    def _ensure_satellite(self, index: int) -> Slot | None:
        inst = self._instance()
        server = self._server_obj()
        if inst is None or server is None or inst.node is None:
            return None
        while len(self._slots) <= index:
            self._slots.append(Slot(self, len(self._slots)))
        slot = self._slots[index]
        if slot.node is not None:
            return slot
        try:
            slot.node = server.add_synth(
                inst.module.synthdef,
                add_action=AddAction.ADD_BEFORE,
                target_node=inst.node,
                **self._satellite_settings(inst),
            )
        except Exception:  # noqa: BLE001 — mid-rebuild; _validate retries
            slot.node = None
            return None
        return slot

    def _free_satellites(self) -> None:
        for slot in self._slots[1:]:
            if slot.node is not None:
                try:
                    # force=True or this does NOT free. supriya's
                    # `Node.free()` emits `/n_set gate 0` for any synth that
                    # HAS a gate control, and `/n_free` only without one — and
                    # every playable source has a gate (rule 5), which also
                    # mandates done_action=0 so the node survives release.
                    # Unforced, the satellite is merely SILENCED while the
                    # pool records it as gone: five spawn/dispose cycles took
                    # the live node count 9→13→17→21→25→29, never down.
                    slot.node.free(force=True)
                except Exception:  # noqa: BLE001 — server already gone
                    pass
                slot.node = None

    # -- leasing -------------------------------------------------------------

    def acquire(self, count: int) -> list[Slot]:
        """Lease ``count`` slots, lowest free index first.

        The first lease on a target gets slot 0, so the single-voice case is
        byte-for-byte the pre-pool behaviour.
        """
        with self._lock:
            self._validate()
            leased: list[Slot] = []
            index = 0
            while len(leased) < count:
                if index in self._leased:
                    index += 1
                    continue
                while len(self._slots) <= index:
                    self._slots.append(Slot(self, len(self._slots)))
                if index > 0:
                    self._ensure_satellite(index)
                self._leased.add(index)
                leased.append(self._slots[index])
                index += 1
            return leased

    def release(self, slots) -> None:
        with self._lock:
            for slot in slots:
                self._leased.discard(slot.index)
                if slot.index > 0 and slot.node is not None:
                    try:
                        slot.node.free(force=True)   # see _free_satellites
                    except Exception:  # noqa: BLE001
                        pass
                    slot.node = None

    def dispose(self) -> None:
        with self._lock:
            self._free_satellites()
            self._leased.clear()
            self._slots = []

    # -- writes --------------------------------------------------------------

    def _mapped(self, name: str) -> bool:
        mapped = getattr(self.rack, "mapped", None)
        return bool(mapped) and (self.target_key, name) in mapped

    def _write(self, slot: Slot, values: dict[str, float]) -> None:
        """Apply a slot's per-voice params.

        Slot 0 goes through ``rack.set_params`` so ``inst.settings`` and the
        broadcast state follow it, exactly as the mono voice always did.
        Satellites are private nodes: set them directly, but honour the same
        LFO-mapped rule the rack applies.
        """
        if slot.is_primary:
            try:
                self.rack.set_params(self.target_key, **values)
            except Exception:  # noqa: BLE001 — rack mid-rebuild; next note lands
                pass
            return
        with self._lock:
            self._validate()
            node = slot.node
        if node is None:
            return
        live = {k: v for k, v in values.items() if not self._mapped(k)}
        if not live:
            return
        try:
            node.set(**live)
        except Exception:  # noqa: BLE001
            pass

    def schedule(self, slot: Slot, delay: float, values: dict[str, float]) -> None:
        """Apply ``values`` to ``slot`` after ``delay`` seconds, on the server
        clock where possible.

        Used for the steal restrike. ``server.at()`` bundles the message with
        a timestamp so scsynth applies it at the right moment regardless of
        Python scheduling; if that isn't available the set still lands, just
        immediately (a legato steal — audible as a missing attack, never as a
        wrong note).
        """
        server = self._server_obj()
        moment = getattr(server, "at", None) if server is not None else None
        if moment is None:
            self._write(slot, values)
            return
        try:
            with server.at(server.latency + delay):
                self._write(slot, values)
        except Exception:  # noqa: BLE001 — no scheduling; fall back to now
            self._write(slot, values)

    # -- called by the rack --------------------------------------------------

    def mirror(self, values: dict[str, float]) -> None:
        """Push a target param change onto every satellite.

        The whole point of one card / N voices: turning the target's cutoff
        must move all N. ``freq``/``gate`` are excluded — those ARE the voice.
        """
        shared = {k: v for k, v in values.items() if k not in PER_VOICE_PARAMS}
        if not shared:
            return
        with self._lock:
            nodes = [s.node for s in self._slots[1:] if s.node is not None]
        for node in nodes:
            try:
                node.set(**shared)
            except Exception:  # noqa: BLE001
                pass

    def set_paused(self, paused: bool) -> None:
        """Follow the target source's bypass. Without this, bypassing a card
        while a chord is held pauses slot 0 and leaves the satellites
        sounding — the bypass would silence one voice out of N."""
        with self._lock:
            nodes = [s.node for s in self._slots[1:] if s.node is not None]
        for node in nodes:
            try:
                (node.pause if paused else node.unpause)()
            except Exception:  # noqa: BLE001
                pass

    def reposition(self) -> None:
        """Re-seat satellites just before the target node.

        ``reorder_for_wires`` moves Instance nodes to satisfy the audio
        graph; satellites aren't Instances, so they must follow their target
        or they write their bus after the effect that reads it.
        """
        with self._lock:
            inst = self._instance()
            if inst is None or inst.node is None:
                return
            for slot in self._slots[1:]:
                if slot.node is None:
                    continue
                try:
                    slot.node.move(add_action=AddAction.ADD_BEFORE,
                                   target_node=inst.node)
                except Exception:  # noqa: BLE001 — order is best-effort
                    pass


def pool_for(rack: Any, target_key: str) -> VoicePool:
    """The pool for a target, created on first use and owned by the rack."""
    pools = getattr(rack, "voice_pools", None)
    if pools is None:                      # rack predates pools (tests, mocks)
        pools = {}
        rack.voice_pools = pools
    pool = pools.get(target_key)
    if pool is None:
        pool = VoicePool(rack, target_key)
        pools[target_key] = pool
    return pool


# --------------------------------------------------------------------------
# allocations
# --------------------------------------------------------------------------


class Allocation:
    """Base note-sink: everything that is NOT the allocation policy.

    Presents the note-sink interface the control plane expects
    (``note_on``/``note_off``/``all_off``/``set_sustain``/``set_bend``) and
    keeps the shared pitch state. Subclasses implement the policy.
    """

    policy = "base"
    #: notes that may sound at once; ``Poly`` overrides per instance
    voices = 1

    def __init__(self, rack: Any, target_key: str) -> None:
        self.rack = rack
        self._target_key = target_key
        self.on_voiced = None    # viz tap: (note, on) for what actually sounds
        self.transpose = 0       # semitones, applied to every note
        self.bend = 0.0          # semitones
        self.sustain = False
        self._slots: list[Slot] = []
        self._lease()

    # -- target ---------------------------------------------------------------

    @property
    def target_key(self) -> str:
        return self._target_key

    @target_key.setter
    def target_key(self, key: str) -> None:
        if key == self._target_key:
            return
        self._release()
        self._target_key = key
        self._lease()

    def _lease(self) -> None:
        if self.rack is None:
            return
        try:
            self._slots = pool_for(self.rack, self._target_key).acquire(self.voices)
        except Exception:  # noqa: BLE001 — no server yet; lease on first note
            self._slots = []

    def _release(self) -> None:
        if not self._slots:
            return
        try:
            pool_for(self.rack, self._target_key).release(self._slots)
        except Exception:  # noqa: BLE001
            pass
        self._slots = []

    def _slot(self, index: int = 0) -> Slot | None:
        if not self._slots:
            self._lease()
        if index < len(self._slots):
            return self._slots[index]
        return None

    def dispose(self) -> None:
        """Give the slots back. Every removal path must call this or the
        satellites outlive their allocation and drone on."""
        self.all_off()
        self._release()

    # -- pitch ----------------------------------------------------------------

    def _freq(self, note: float) -> float:
        return midi_to_freq(note + self.transpose) * 2 ** (self.bend / 12)

    def _emit_voiced(self, note: int, on: bool) -> None:
        if self.on_voiced:
            try:
                self.on_voiced(note, on)
            except Exception:  # noqa: BLE001
                pass

    # -- note-sink interface (subclasses override) ----------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        raise NotImplementedError

    def note_off(self, note: int) -> None:
        raise NotImplementedError

    def all_off(self) -> None:
        raise NotImplementedError

    def set_sustain(self, on: bool) -> None:
        self.sustain = bool(on)

    def set_bend(self, semitones: float) -> None:
        self.bend = semitones


class MonoLatest(Allocation):
    """Last-note-priority mono voice with pitch bend and sustain.

    The historical ``midi.MonoVoice``, unchanged in behaviour: one slot, the
    newest note wins, releasing it falls back to the newest still-held note,
    and an empty held set closes the gate (unless the pedal is down).
    """

    policy = "mono-latest"
    voices = 1

    def __init__(self, rack: Any, target_key: str) -> None:
        self._held: list[int] = []          # note stack, most recent last
        self._sounding: int | None = None   # note currently voiced (incl. sustained)
        super().__init__(rack, target_key)

    def _aim(self, note: int, gate: bool | None = None) -> None:
        slot = self._slot()
        if slot is None:
            return
        values: dict[str, float] = {"freq": self._freq(note)}
        if gate is not None:
            values["gate"] = 1 if gate else 0
        slot.set(**values)

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        self._held.append(note)
        prev = self._sounding
        self._sounding = note
        self._aim(note, gate=True)
        # the mono voice sounds ONE note: close the old segment, open the new
        if prev is not None and prev != note:
            self._emit_voiced(prev, False)
        if prev != note:
            self._emit_voiced(note, True)

    def note_off(self, note: int) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        if note != self._sounding:
            return  # released a background-held key; sound unchanged
        if self._held:
            prev = self._sounding
            self._sounding = self._held[-1]
            self._aim(self._sounding)
            self._emit_voiced(prev, False)
            self._emit_voiced(self._sounding, True)
        elif self.sustain:
            pass  # pedal holds the last note; released on set_sustain(False)
        else:
            self._sounding = None
            self._close()
            self._emit_voiced(note, False)

    def _close(self) -> None:
        slot = self._slot()
        if slot is not None:
            slot.set(gate=0)

    def set_sustain(self, on: bool) -> None:
        self.sustain = bool(on)
        if not on and not self._held and self._sounding is not None:
            self._emit_voiced(self._sounding, False)
            self._sounding = None
            self._close()

    def set_bend(self, semitones: float) -> None:
        self.bend = semitones
        if self._sounding is not None:
            self._aim(self._sounding)

    def all_off(self) -> None:
        if self._sounding is not None:
            self._emit_voiced(self._sounding, False)
        self._held.clear()
        self._sounding = None
        self._close()


class Poly(Allocation):
    """N notes at once on one target source, stealing the OLDEST when full.

    Policy (Cole, 2026-07-26): a repeated note reuses the voice already
    sounding it, so a retrigger never costs a second slot; otherwise the
    longest-sounding voice is taken. Sustain holds released notes until the
    pedal lifts, and a held-but-stolen note does NOT come back — it is gone,
    which is what every hardware poly synth does.

    At ``voices == 1`` this reduces exactly to ``MonoLatest`` minus the
    fallback-to-held-note behaviour, which is the mono/poly delineation
    Cole framed the item around.
    """

    policy = "poly"

    def __init__(self, rack: Any, target_key: str, voices: int = 8) -> None:
        self.voices = max(1, min(int(voices), MAX_POLY_VOICES))
        # slot index -> note sounding on it; ordered oldest-first for steal
        self._sounding: dict[int, int] = {}
        self._order: list[int] = []        # slot indices, oldest first
        self._held: set[int] = set()       # notes with a key still down
        self._sustained: set[int] = set()  # notes released under the pedal
        super().__init__(rack, target_key)

    # -- slot bookkeeping -----------------------------------------------------

    def _slot_of(self, note: int) -> int | None:
        for index, sounding in self._sounding.items():
            if sounding == note:
                return index
        return None

    def _free_index(self) -> int | None:
        for index in range(len(self._slots)):
            if index not in self._sounding:
                return index
        return None

    def set_voices(self, count: int) -> None:
        """Resize the pool. Notes sounding on slots that go away are closed —
        a silencing path, so their taps close too."""
        count = max(1, min(int(count), MAX_POLY_VOICES))
        if count == self.voices:
            return
        self.all_off()
        self._release()
        self.voices = count
        self._lease()

    # -- policy ---------------------------------------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = int(note)
        self._held.add(note)
        self._sustained.discard(note)
        if not self._slots:
            self._lease()
        if not self._slots:
            return

        index = self._slot_of(note)
        if index is not None:
            # already sounding this note: reuse its own voice, restrike it
            self._restrike(index, note)
            self._touch(index)
            return

        index = self._free_index()
        if index is None:
            index = self._order[0] if self._order else 0
            stolen = self._sounding.get(index)
            if stolen is not None:
                self._sustained.discard(stolen)
                self._held.discard(stolen)
                self._emit_voiced(stolen, False)
            self._restrike(index, note)
        else:
            self._slots[index].set(freq=self._freq(note), gate=1)

        self._sounding[index] = note
        self._touch(index)
        self._emit_voiced(note, True)

    def _restrike(self, index: int, note: int) -> None:
        """Close and reopen a busy slot's gate so the incoming note gets its
        own attack instead of inheriting the envelope it displaced."""
        slot = self._slots[index]
        slot.set(gate=0)
        pool = pool_for(self.rack, self._target_key)
        pool.schedule(slot, STEAL_GAP, {"freq": self._freq(note), "gate": 1})
        self._sounding[index] = note

    def _touch(self, index: int) -> None:
        if index in self._order:
            self._order.remove(index)
        self._order.append(index)

    def note_off(self, note: int) -> None:
        note = int(note)
        self._held.discard(note)
        if self.sustain:
            if self._slot_of(note) is not None:
                self._sustained.add(note)
            return
        self._silence(note)

    def _silence(self, note: int) -> None:
        index = self._slot_of(note)
        if index is None:
            return
        self._slots[index].set(gate=0)
        self._sounding.pop(index, None)
        if index in self._order:
            self._order.remove(index)
        self._sustained.discard(note)
        self._emit_voiced(note, False)

    def set_sustain(self, on: bool) -> None:
        self.sustain = bool(on)
        if not on:
            for note in list(self._sustained):
                if note not in self._held:
                    self._silence(note)
            self._sustained.clear()

    def set_bend(self, semitones: float) -> None:
        self.bend = semitones
        for index, note in list(self._sounding.items()):
            if index < len(self._slots):
                self._slots[index].set(freq=self._freq(note))

    def all_off(self) -> None:
        # EVERY silencing path closes its notes AND their viz taps
        for index, note in list(self._sounding.items()):
            if index < len(self._slots):
                self._slots[index].set(gate=0)
            self._emit_voiced(note, False)
        self._sounding.clear()
        self._order.clear()
        self._held.clear()
        self._sustained.clear()


class Hold(Allocation):
    """Last-note priority with NO gate and no release — the drone policy.

    What makes a drone a drone (item 29): notes steer ``freq`` through the
    synthdef's own Lag/glide path, releasing the sounding root falls back to
    the newest still-held note, and an EMPTY held set HOLDS the last root.
    The on/off switch is the module's bypass, not the note stream — so this
    policy never touches ``gate``.

    Item 29 swaps ``app._DroneSink`` for this and gives it a card. One
    difference to settle there: ``_DroneSink`` aims at the raw MIDI pitch and
    ignores global transpose/bend, where this honours both like every other
    allocation. Construct with ``transpose``/``bend`` left at 0 for identical
    behaviour.
    """

    policy = "hold"
    voices = 1

    def __init__(self, rack: Any, target_key: str) -> None:
        self._held: list[int] = []       # ordered; last = the sounding root
        self._root: int | None = None
        super().__init__(rack, target_key)

    def _aim(self, note: int) -> None:
        slot = self._slot()
        if slot is None:
            return
        self._root = note
        slot.set(freq=self._freq(note))

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        self._held.append(note)
        self._aim(note)

    def note_off(self, note: int) -> None:
        note = int(note)
        was_top = bool(self._held) and self._held[-1] == note
        if note in self._held:
            self._held.remove(note)
        # releasing a note that ISN'T the sounding root must not retarget;
        # releasing the root falls back to the newest still-held note;
        # releasing the last note HOLDS (no silence)
        if was_top and self._held:
            self._aim(self._held[-1])

    def set_bend(self, semitones: float) -> None:
        self.bend = semitones
        if self._root is not None:
            self._aim(self._root)

    def all_off(self) -> None:
        self._held.clear()   # hold the current root


#: policy name -> class, for state/protocol round-tripping
POLICIES = {cls.policy: cls for cls in (MonoLatest, Poly, Hold)}
