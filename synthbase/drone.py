"""Derivers: ctl-plane nodes that listen to notes and derive ONE note out.

v5 split of the old DroneBrain; reworked 2026-07 (drone rework, item 3;
deriver split, item 6). The bespoke "tonic" signal is RETIRED — a deriver
is an ordinary ctl-plane node: notes in, ONE mono note stream out, fanned
over its outgoing ctl wires. A drone is just a ctl note-sink
(app._DroneSink) — so is a voice, the arp, or a Key Shifter lane.

TWO deriver nodes share one timing model (_DeriverBase):

* TonicDeriver (the ESTIMATOR, ids "tonic", "tonic.2", ...) — TWO-LAYER
  scale-aware instant derivation (2026-07 redesign; the old settle-and-land
  hysteresis is gone):

  LAYER 1 (continuous, never gates): RootEstimator accumulates
  DURATION-WEIGHTED pitch-class evidence — a held note keeps gaining
  weight while it is down (capped) and only starts decaying after
  release; a grace note contributes almost nothing. The evidence is
  matched against SCALE TEMPLATES (7 diatonic modes + harmonic/melodic
  minor + major/minor pentatonic + blues) rotated across all 12 tonics,
  Krumhansl-style, giving a running best (tonic, mode) + confidence,
  with a chromatic fallback when nothing fits. Near-tie rule: prefer the
  SUPERSET mode over its pentatonic subset until the evidence clearly
  avoids the missing degrees.

  LAYER 2 (instant, on trigger): NO settling. Read the CURRENT HELD SET
  (per-note refcounted union across inputs; a short release-grace window
  catches staccato playing) and snap to the most probable root GIVEN the
  inferred scale — held chord → its root, single note → its function in
  the scale. Commit immediately. Knobs: memory (evidence window) and
  bass (emphasis) stay; stickiness DIED; listening now names the LAYER-2
  chord-tone interpretation profile.

  DECK SUPERPOWER (both halves, toggleable): with a Loop Deck wired into
  the notes-in, (1) deck_feed reads the deck's FULL recorded phrase
  statically — onset-clustered into chord groups, duration-weighted —
  and feeds it to the scale layer as context evidence + a per-position
  harmonic map that sharpens Layer-2 picks while the loop rolls;
  (2) every="deck" phase-locks commits to the loop's chord-group
  boundaries (anticipatory: each boundary commits the group STARTING
  there). Ping still overrides, per the shared timing model.

  analysis() (broadcast on a steady tick for the card viz) carries the
  evidence histogram, per-root scores, leading candidate, confidence and
  the inferred scale {tonic, mode, conf, label}.

* LiteralDeriver (ids "literal", "literal.2", ...) — deterministic,
  zero-lag (drop). At commit time it reads the live held-set/last-played
  and emits ONE note: extract = lowest-held / highest-held / last-played /
  first-played; place = absolute (keep the real octave) / fold (re-voice
  into a fixed octave) / transpose (±N semitones); hold-on-empty either
  holds the last note or releases it.

SHARED TIMING: a settable `every` grid timer drives commit(); if a ping
source is wired into the node's trigger-in the internal timer stands down
and each ping commits instead (unwire → timer resumes). The Literal adds
"immediate": commit on every input note event. Defaults: Estimator
"1 bar", Literal "immediate".

Emits {"kind": "tap", "src": "<id>", ...} viz taps for the OUTPUT stream
and {"kind": "tonic_out", "id": "<id>", "root": "Eb"} on root changes.
"""

from __future__ import annotations

import math
import threading
import time

from .transport import Transport

NOTE_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
EVERY = {"1 beat": 1.0, "2 beats": 2.0, "1 bar": "bar", "2 bars": "2bar", "4 bars": "4bar"}
DECAY_TAU = 6.0          # default memory: seconds for the histogram to fade by 1/e
BASS_COEF = 0.06         # default bass emphasis per semitone below 55

# -- duration-weighted evidence ------------------------------------------------
ONSET_W = 0.35           # instant credit at note_on (a grace note is ~only this)
HOLD_RATE = 1.0          # evidence weight gained per held second
HOLD_CAP = 6.0           # max seconds of hold credit (a 30 s pedal can't drown it)
RELEASE_GRACE = 0.3      # commit-time window: just-released notes count as held

# Listening profiles: how much a held pitch class (at interval i above a
# candidate root) argues FOR that root at COMMIT time (Layer 2).
PROFILES = {
    "triadic":    {0: 1.0, 7: 0.75, 4: 0.60, 3: 0.60, 10: 0.35, 11: 0.35},
    "root+fifth": {0: 1.0, 7: 0.6},
    "chromatic":  {0: 1.0},
}
PROFILE = PROFILES["triadic"]   # legacy alias

# -- scale vocabulary (Layer 1) ------------------------------------------------
# Ordered: modes first so the near-tie rule (prefer SUPERSET over pentatonic
# subset) falls out of iteration order on exact ties.
SCALES = {
    "ionian":     (0, 2, 4, 5, 7, 9, 11),
    "dorian":     (0, 2, 3, 5, 7, 9, 10),
    "phrygian":   (0, 1, 3, 5, 7, 8, 10),
    "lydian":     (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "aeolian":    (0, 2, 3, 5, 7, 8, 10),
    "locrian":    (0, 1, 3, 5, 6, 8, 10),
    "harm minor": (0, 2, 3, 5, 7, 8, 11),
    "mel minor":  (0, 2, 3, 5, 7, 9, 11),
    "maj pent":   (0, 2, 4, 7, 9),
    "min pent":   (0, 3, 5, 7, 10),
    "blues":      (0, 3, 5, 6, 7, 10),
}
SCALE_TIE_EPS = 0.015    # near-tie band for the superset preference
SCALE_FLOOR = 0.30       # best fit below this → chromatic (scale = None)
SCALE_CONF_GAIN = 4.0    # contrast gain on the winner-vs-runner gap (the raw
                         # gap is intrinsically small: templates overlap)
OUT_OF_SCALE_W = -0.7    # penalty weight for evidence outside a template


def _template_weights(pcs: tuple) -> tuple[list[float], float]:
    """Krumhansl-style weight vector for one template + its energy norm
    (so 5-note pentatonics compare fairly against 7-note modes)."""
    w = [OUT_OF_SCALE_W] * 12
    for pc in pcs:
        if pc == 0:
            w[pc] = 1.0
        elif pc == 7:
            w[pc] = 0.8
        elif pc in (3, 4):
            w[pc] = 0.65
        else:
            w[pc] = 0.5
    norm = math.sqrt(sum(w[pc] * w[pc] for pc in pcs))
    return w, norm


_SCALE_W = {mode: _template_weights(pcs) for mode, pcs in SCALES.items()}


def scale_scores(evidence: list[float]) -> list[tuple[float, int, str]]:
    """Score every (tonic, mode) candidate against a 12-bin evidence
    histogram. Returns [(score, tonic, mode)] unsorted; empty if no
    evidence."""
    total = sum(evidence)
    if total <= 0:
        return []
    evn = [e / total for e in evidence]
    out = []
    for mode, (w, norm) in _SCALE_W.items():
        for tonic in range(12):
            s = sum(evn[(tonic + i) % 12] * w[i] for i in range(12)) / norm
            out.append((s, tonic, mode))
    return out


def best_scale(evidence: list[float]) -> tuple[int, str, float] | None:
    """Running best (tonic, mode, confidence) — or None (chromatic) when
    nothing fits. Near-ties prefer the larger template (superset rule);
    confidence = winner vs the best DIFFERENT-KEY-SIGNATURE runner-up
    (relative modes share the absolute pitch-class set — C ionian vs
    A aeolian is a naming choice, not uncertainty; the tonic weighting
    picks the better name)."""
    cands = scale_scores(evidence)
    if not cands:
        return None
    top = max(s for s, _, _ in cands)
    if top < SCALE_FLOOR:
        return None
    near = [c for c in cands if c[0] >= top * (1.0 - SCALE_TIE_EPS)]
    # superset rule: most template notes wins the near-tie band;
    # then score; then vocabulary order (stable via SCALES insertion order)
    order = {m: i for i, m in enumerate(SCALES)}
    near.sort(key=lambda c: (-len(SCALES[c[2]]), -c[0], order[c[2]]))
    score, tonic, mode = near[0]
    winner_set = frozenset((tonic + i) % 12 for i in SCALES[mode])
    runner = max((s for s, t, m in cands
                  if frozenset((t + i) % 12 for i in SCALES[m]) != winner_set),
                 default=0.0)
    conf = 0.0 if score <= 0 else max(
        0.0, min(1.0, (score - runner) / score * SCALE_CONF_GAIN))
    return tonic, mode, round(conf, 3)


def pick_root(held: list[int], scale: tuple[int, str, float] | None,
              profile: str = "triadic", bass: float = BASS_COEF,
              prior_pc: int | None = None) -> int | None:
    """LAYER 2: snap the current held set to its most probable root pitch
    class GIVEN the inferred scale. Held chord → its root (chord-tone
    interpretation per the listening profile); single note → its function
    in the scale decides. prior_pc (deck harmonic map) adds a nudge."""
    if not held:
        return None
    prof = PROFILES.get(profile, PROFILES["triadic"])
    low = min(held)
    held_pcs = {n % 12 for n in held}
    if scale is not None:
        tonic = scale[0]
        cands = {(tonic + i) % 12 for i in SCALES[scale[1]]} | held_pcs
    else:
        tonic = None
        cands = set(range(12))
    best_r, best_s = None, 0.0
    for r in sorted(cands):
        s = sum(prof.get((n % 12 - r) % 12, 0.0) * (1.5 if n == low else 1.0)
                for n in held)
        if tonic is not None:
            if r == tonic:
                s += 0.10          # tonal prior: tonic …
            elif r == (tonic + 7) % 12:
                s += 0.05          # … and dominant get a nudge
        if prior_pc is not None and r == prior_pc:
            s += 0.4               # deck harmonic-map agreement
        if s > best_s + 1e-9:
            best_r, best_s = r, s
    return best_r


# -- deck superpower: static phrase → chord groups -----------------------------
DECK_ONSET_WINDOW = 0.25   # beats: onsets this close cluster into one group
DECK_MELODY_W = 0.4        # singleton (melody) evidence weight vs chord 1.0
DECK_HOLD_CAP_BEATS = 4.0  # duration-weight cap for loop notes, in beats
DECK_GAIN = 6.0            # total evidence mass the deck context contributes


def deck_groups(events: list, loop_beats: float,
                window: float = DECK_ONSET_WINDOW) -> list[dict]:
    """Cluster a Loop Deck phrase ((beat, note, on) events) into CHORD
    GROUPS by onset adjacency. Each group: {start, end, notes:[(beat,
    dur, note)], chord:bool}. Open notes run to the loop end."""
    if not events or loop_beats <= 0:
        return []
    open_: dict[int, list[float]] = {}
    spans: list[tuple[float, float, int]] = []
    for beat, note, on in sorted(events, key=lambda e: e[0]):
        if on:
            open_.setdefault(int(note), []).append(float(beat))
        else:
            starts = open_.get(int(note))
            if starts:
                b0 = starts.pop(0)
                spans.append((b0, max(0.05, float(beat) - b0), int(note)))
    for note, starts in open_.items():
        for b0 in starts:
            spans.append((b0, max(0.05, loop_beats - b0), note))
    if not spans:
        return []
    spans.sort()
    groups: list[dict] = []
    for b0, dur, note in spans:
        if groups and b0 - groups[-1]["start"] <= window:
            groups[-1]["notes"].append((b0, dur, note))
        else:
            groups.append({"start": b0, "notes": [(b0, dur, note)]})
    for i, g in enumerate(groups):
        g["end"] = groups[i + 1]["start"] if i + 1 < len(groups) else loop_beats
        g["chord"] = len(g["notes"]) >= 2
    return groups


def deck_evidence(groups: list[dict], bass: float = BASS_COEF) -> list[float]:
    """Duration-weighted 12-bin evidence from a deck harmonic map,
    normalized to DECK_GAIN total mass (chords count full, singletons as
    melody evidence)."""
    ev = [0.0] * 12
    for g in groups:
        wgt = 1.0 if g["chord"] else DECK_MELODY_W
        for _, dur, note in g["notes"]:
            emph = 1.0 + max(0.0, (55 - note)) * bass
            ev[note % 12] += (ONSET_W + min(dur, DECK_HOLD_CAP_BEATS)) * wgt * emph
    total = sum(ev)
    if total <= 0:
        return ev
    return [e / total * DECK_GAIN for e in ev]

EXTRACTS = ("lowest-held", "highest-held", "last-played", "first-played")
PLACES = ("absolute", "fold", "transpose")


def midi_to_freq(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


class RootEstimator:
    """The Layer-1 brain: DURATION-WEIGHTED pitch-class evidence (held
    notes keep gaining weight, capped; released evidence decays) matched
    against scale templates for a running (tonic, mode, confidence).
    Also tracks the refcounted held set Layer 2 reads at commit time."""

    def __init__(self) -> None:
        self._released = [0.0] * 12   # decayed evidence (post-release)
        self._last_decay = time.monotonic()
        self._lock = threading.Lock()
        self._held: dict[int, list] = {}   # note -> [refcount, onset time]
        self._recent: list[tuple[float, int]] = []  # (release time, note)
        self.context: list[float] | None = None     # deck evidence (12) or None
        self.tau = DECAY_TAU          # "memory": decay seconds (1..30)
        self.bass = BASS_COEF         # bass emphasis per semitone below 55
        self.profile = "triadic"      # "listening": Layer-2 chord-tone profile

    # -- evidence in ----------------------------------------------------------

    def _emph(self, note: int) -> float:
        # Bass emphasis: low notes are stronger root evidence.
        return 1.0 + max(0.0, (55 - note)) * self.bass

    def note_on(self, note: int) -> None:
        note = int(note)
        now = time.monotonic()
        with self._lock:
            self._decay(now)
            ent = self._held.get(note)
            if ent is not None:
                ent[0] += 1           # refcount: fan-in may repeat a note
            else:
                self._held[note] = [1, now]

    def note_off(self, note: int) -> None:
        note = int(note)
        now = time.monotonic()
        with self._lock:
            ent = self._held.get(note)
            if ent is None:
                return
            ent[0] -= 1
            if ent[0] > 0:
                return
            del self._held[note]
            # credit: onset + capped hold duration, into the decaying pool
            dur = min(max(0.0, now - ent[1]), HOLD_CAP)
            self._decay(now)
            self._released[note % 12] += (ONSET_W + dur * HOLD_RATE) * self._emph(note)
            self._recent.append((now, note))
            if len(self._recent) > 32:
                del self._recent[:-32]

    def observe(self, note: int) -> None:
        """Legacy/stream evidence (no hold tracking): one onset credit."""
        now = time.monotonic()
        with self._lock:
            self._decay(now)
            self._released[int(note) % 12] += ONSET_W * self._emph(int(note))

    def clear_held(self) -> None:
        with self._lock:
            self._held.clear()
            self._recent.clear()

    # -- evidence out ----------------------------------------------------------

    def _decay(self, now: float) -> None:
        dt = now - self._last_decay
        if dt > 0:
            factor = math.exp(-dt / max(0.25, self.tau))
            self._released = [w * factor for w in self._released]
            self._last_decay = now

    def weights(self) -> list[float]:
        """Combined evidence: decayed released pool + live PINNED held
        contributions (weight grows with hold time, capped) + deck
        context when set."""
        now = time.monotonic()
        with self._lock:
            self._decay(now)
            w = list(self._released)
            for note, (_, onset) in self._held.items():
                dur = min(max(0.0, now - onset), HOLD_CAP)
                w[note % 12] += (ONSET_W + dur * HOLD_RATE) * self._emph(note)
            if self.context is not None:
                w = [a + b for a, b in zip(w, self.context)]
            return w

    def held_notes(self) -> list[int]:
        with self._lock:
            return sorted(self._held)

    def recent_release_notes(self, window: float = RELEASE_GRACE) -> list[int]:
        """Notes released within the grace window (staccato-at-trigger)."""
        now = time.monotonic()
        with self._lock:
            return sorted({n for t, n in self._recent if now - t <= window})

    # -- Layer 1: scale inference ---------------------------------------------

    def scale(self) -> tuple[int, str, float] | None:
        """(tonic, mode, confidence) or None (chromatic / no evidence)."""
        w = self.weights()
        if sum(w) < 0.1:
            return None
        return best_scale(w)

    # -- legacy root scoring (viz + empty-held fallback) -----------------------

    def _support(self) -> dict:
        return PROFILES.get(self.profile, PROFILES["triadic"])

    def _score(self, weights: list[float], candidate: int) -> float:
        return sum(
            weights[(candidate + interval) % 12] * support
            for interval, support in self._support().items()
        )

    def analysis(self, incumbent: int | None) -> dict:
        """Steady-tick snapshot for the card viz: evidence histogram
        (weights), per-root scores, leading candidate, confidence AND the
        inferred scale {tonic, mode, conf, label} (None = chromatic)."""
        weights = self.weights()
        total = sum(weights)
        if total < 0.1:
            return {"weights": [0.0] * 12, "scores": [0.0] * 12,
                    "leading": None, "confidence": 0.0, "scale": None}
        scores = [self._score(weights, r) for r in range(12)]
        order = sorted(range(12), key=lambda r: scores[r], reverse=True)
        top, runner = scores[order[0]], scores[order[1]]
        conf = 0.0 if top <= 0 else max(0.0, min(1.0, (top - runner) / top))
        wpeak = max(weights) or 1.0
        speak = max(scores) or 1.0
        sc = best_scale(weights)
        return {
            "weights": [round(w / wpeak, 4) for w in weights],
            "scores": [round(s / speak, 4) for s in scores],
            "leading": order[0],
            "confidence": round(conf, 3),
            "scale": None if sc is None else {
                "tonic": sc[0], "mode": sc[1], "conf": sc[2],
                "label": f"{NOTE_NAMES[sc[0]]} {sc[1]}",
            },
        }


class _DeriverBase:
    """Shared deriver chassis: mono note out over ctl wires, the `every`
    grid timer, and the ping trigger-in override."""

    DEFAULT_EVERY = "1 bar"

    def __init__(self, app, nid: str) -> None:
        self.app = app  # needs .transport, .ctl_wires, ._ctl_sinks, ._emit_midi_event
        self.id = nid
        self.every = self.DEFAULT_EVERY
        self._out_note: int | None = None  # the emitted note (mono out)
        self._thread: threading.Thread | None = None
        self._quit = threading.Event()
        self._ensure_thread()

    # -- mono note out ---------------------------------------------------------

    def _tap(self, note: int, on: bool) -> None:
        try:
            self.app._emit_midi_event(
                {"kind": "tap", "src": self.id, "note": int(note), "on": bool(on)})
        except Exception:  # noqa: BLE001
            pass

    def _thru(self, fn) -> None:
        for s in self.app._ctl_sinks(self.id):
            try:
                fn(s)
            except Exception:  # noqa: BLE001 — one dead target must not stop the rest
                pass

    def _emit_out(self, note: int | None) -> None:
        """Move the emitted note (mono: off the old, on the new).
        note=None releases without a replacement."""
        prev = self._out_note
        if note == prev:
            return
        if prev is not None:
            self._tap(prev, False)
            self._thru(lambda s: s.note_off(prev))
        self._out_note = note
        if note is not None:
            self._tap(note, True)
            self._thru(lambda s: s.note_on(note, 100))

    def current_note(self) -> int | None:
        """The note this deriver is currently emitting (None = none yet)."""
        return self._out_note

    # -- ping trigger-in (node-scoped) -----------------------------------------

    def trigger(self) -> None:
        """Commit NOW. While any ping source is wired into this node its
        internal grid timer stands down (see _run) — unwire to resume."""
        self.commit()

    def _ping_driven(self) -> bool:
        app = self.app
        try:
            return any(w.get("to") == self.id and app._is_ping_src(w.get("from"))
                       for w in (getattr(app, "ctl_wires", None) or []))
        except Exception:  # noqa: BLE001
            return False

    # -- the commit (subclasses implement) -------------------------------------

    def commit(self) -> None:  # pragma: no cover — abstract
        raise NotImplementedError

    # -- lifecycle -------------------------------------------------------------

    def shutdown(self) -> None:
        # release the emitted note downstream first — a removed deriver must
        # not leave its note ringing (drones HOLD by design; voices release)
        if self._out_note is not None:
            note = self._out_note
            self._tap(note, False)
            self._thru(lambda s: s.note_off(note))
            self._out_note = None
        self._quit.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    # -- the decision thread (grid-quantized) ----------------------------------

    def _interval_beats(self, transport: Transport) -> float | None:
        v = EVERY.get(self.every)
        if v is None:
            return None                     # "immediate" — no grid timer
        if v == "bar":
            return float(transport.beats_per_bar)
        if v == "2bar":
            return 2.0 * transport.beats_per_bar
        if v == "4bar":
            return 4.0 * transport.beats_per_bar
        return float(v)

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._quit.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _sleep_until(self, t: float) -> bool:
        while not self._quit.is_set():
            dt = t - time.monotonic()
            if dt <= 0:
                return True
            time.sleep(min(dt, 0.05))
        return False

    def _custom_next(self, transport: Transport):
        """Hook: (wall_time, payload) for a non-grid timing source (the
        estimator's every="deck"), or None to use the grid timer."""
        return None

    def _custom_commit(self, payload) -> None:  # pragma: no cover — hook
        pass

    def _run(self) -> None:
        transport = self.app.transport
        while not self._quit.is_set():
            custom = self._custom_next(transport)
            if custom is not None:          # non-grid timing (deck-synced)
                t, payload = custom
                if not self._sleep_until(t):
                    return
                if not transport.running:
                    time.sleep(0.1)
                    continue
                if self._ping_driven():     # wired ping owns commit timing
                    continue
                if payload is not None:
                    self._custom_commit(payload)
                continue
            beats = self._interval_beats(transport)
            if beats is None:               # event-driven ("immediate")
                if not self._sleep_until(time.monotonic() + 0.2):
                    return
                continue
            _, t = transport.next_grid(beats)
            if not self._sleep_until(t):
                return
            # a STOPPED transport freezes beats_now — next_grid then returns
            # a constant past time forever; don't busy-spin decisions
            if not transport.running:
                time.sleep(0.1)
                continue
            # a wired ping source OWNS the commit timing (timer override)
            if self._ping_driven():
                continue
            self.commit()


class TonicDeriver(_DeriverBase):
    """The ESTIMATOR deriver (ids "tonic", "tonic.2", ...): two-layer
    scale-aware INSTANT derivation. Input notes are evidence + the held
    set Layer 2 reads at trigger time."""

    DEFAULT_EVERY = "1 bar"

    def __init__(self, app, tid: str = "tonic") -> None:
        self.octave = 2               # root lands at C{octave}..B{octave}
        self.root: int | None = None  # pitch class 0-11
        self.est = RootEstimator()
        self.deck_feed = False        # deck superpower half 1 (context feed)
        self._deck_sig = None         # phrase signature the map was built from
        self._deck_map: list[dict] = []
        super().__init__(app, tid)

    # -- note-sink interface (evidence + held set) ------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        self.est.note_on(note)

    def note_off(self, note: int) -> None:
        self.est.note_off(note)

    def all_off(self) -> None:
        # panic: clear input state, release the emitted root downstream and
        # close its tap — silencing paths must never strand an "on"
        self.est.clear_held()
        if self._out_note is not None:
            self._tap(self._out_note, False)
            self._out_note = None
        self._thru(lambda s: s.all_off())

    def set_sustain(self, on: bool) -> None:
        self._thru(lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        self._thru(lambda s: s.set_bend(semitones))

    # -- configuration ---------------------------------------------------------

    def configure(self, **kw) -> None:
        if kw.get("every") in EVERY or kw.get("every") == "deck":
            self.every = kw["every"]
        if kw.get("octave") is not None:
            self.octave = min(4, max(0, int(kw["octave"])))
            self._emit_root()  # re-voice the emitted root at the new octave
        if kw.get("memory") is not None:
            self.est.tau = min(30.0, max(1.0, float(kw["memory"])))
        if kw.get("bass") is not None:
            self.est.bass = min(0.2, max(0.0, float(kw["bass"])))
        if kw.get("listening") in PROFILES:
            self.est.profile = kw["listening"]
        if kw.get("deck_feed") is not None:
            self.deck_feed = bool(kw["deck_feed"])
            if not self.deck_feed:
                self.est.context = None
                self._deck_sig = None

    def settings(self) -> dict:
        sc = self.est.scale() if self._has_evidence() else None
        return {
            "id": self.id,
            "every": self.every,
            "everies": [*EVERY, "deck"],
            "octave": self.octave,
            "root": NOTE_NAMES[self.root] if self.root is not None else None,
            "memory": round(self.est.tau, 2),
            "bass": round(self.est.bass, 3),
            "listening": self.est.profile,
            "listenings": list(PROFILES),
            "deck_feed": self.deck_feed,
            "scale": None if sc is None else f"{NOTE_NAMES[sc[0]]} {sc[1]}",
        }

    def _has_evidence(self) -> bool:
        try:
            return sum(self.est.weights()) >= 0.1
        except Exception:  # noqa: BLE001
            return False

    def analysis(self) -> dict:
        """Live snapshot for the card's histogram viz (server steady tick).
        Also the deck-context refresh tick (cheap when the phrase is
        unchanged)."""
        self._deck_refresh()
        a = self.est.analysis(self.root)
        a["id"] = self.id
        a["root"] = self.root
        a["deck"] = bool(self._deck_map)
        return a

    # -- deck superpower --------------------------------------------------------

    def _deck_wired(self):
        """The Loop Deck, when one is wired into this deriver's notes-in."""
        app = self.app
        looper = getattr(app, "looper", None)
        if looper is None:
            return None
        try:
            wired = any(w.get("from") == "deck" and w.get("to") == self.id
                        for w in (getattr(app, "ctl_wires", None) or []))
        except Exception:  # noqa: BLE001
            wired = False
        return looper if wired else None

    def _deck_refresh(self) -> None:
        """(Re)build the harmonic map + context evidence when the deck
        phrase changed; drop both when the deck unwires or feed is off."""
        deck = self._deck_wired()
        want_map = deck is not None and (self.deck_feed or self.every == "deck")
        if not want_map:
            if self._deck_map:
                self._deck_map = []
            self._deck_sig = None
            if self.est.context is not None:
                self.est.context = None
            return
        try:
            s = deck.settings()
            notes = [tuple(e) for e in s.get("notes") or []]
            sig = (round(float(s.get("loop_beats") or 0.0), 4), len(notes),
                   hash(tuple(notes)))
        except Exception:  # noqa: BLE001
            return
        if sig != self._deck_sig:
            self._deck_sig = sig
            self._deck_map = deck_groups(notes, sig[0])
        # context evidence follows the deck_feed toggle specifically
        if self.deck_feed and self._deck_map:
            self.est.context = deck_evidence(self._deck_map, self.est.bass)
        elif self.est.context is not None:
            self.est.context = None

    def _group_root(self, group: dict) -> int | None:
        notes = [n for _, _, n in group["notes"]]
        return pick_root(notes, self.est.scale(), self.est.profile,
                         self.est.bass)

    def _current_group_pc(self, deck) -> int | None:
        """The harmonic map's root under the playhead (Layer-2 prior)."""
        if not self._deck_map:
            return None
        try:
            ph = deck.phase() if deck is not None else None
        except Exception:  # noqa: BLE001
            ph = None
        if ph is None:
            return None
        for g in self._deck_map:
            if g["start"] <= ph < g["end"]:
                return self._group_root(g)
        return self._group_root(self._deck_map[-1])

    # -- deck-synced timing (every="deck") --------------------------------------

    def _custom_next(self, transport: Transport):
        if self.every != "deck":
            return None
        self._deck_refresh()
        deck = self._deck_wired()
        idle = (time.monotonic() + 0.25, None)
        if deck is None or not self._deck_map:
            return idle
        try:
            ph = deck.phase()
        except Exception:  # noqa: BLE001
            ph = None
        if ph is None:                      # deck not rolling — stand by
            return idle
        loop_beats = self._deck_sig[0] if self._deck_sig else 0.0
        if loop_beats <= 0:
            return idle
        best_d, best_i = None, None
        for i, g in enumerate(self._deck_map):
            d = (g["start"] - ph) % loop_beats
            if d < 0.02:                    # just passed / on it — next lap
                d += loop_beats
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        t = transport.time_of_beat(transport.beats_now() + best_d)
        return (t, best_i)

    def _custom_commit(self, payload) -> None:
        """Deck-synced commit: the boundary we slept to commits the group
        STARTING there (anticipatory, not reactive)."""
        if not isinstance(payload, int) or payload >= len(self._deck_map):
            return
        new_root = self._group_root(self._deck_map[payload])
        self._commit_root(new_root)

    # -- the commit ------------------------------------------------------------

    def _root_note(self) -> int | None:
        if self.root is None:
            return None
        return 12 * (self.octave + 1) + self.root

    def _emit_root(self) -> None:
        self._emit_out(self._root_note())

    def _commit_root(self, new_root: int | None) -> None:
        if new_root is None or new_root == self.root:
            return
        self.root = new_root
        self._emit_root()
        try:
            self.app._emit_midi_event(
                {"kind": "tonic_out", "id": self.id, "root": NOTE_NAMES[new_root]})
        except Exception:  # noqa: BLE001
            pass

    def commit(self) -> None:
        """LAYER 2, instant: read the current held set (grace window for
        just-released notes) and snap to the most probable root given the
        inferred scale. No settling, no hysteresis."""
        self._deck_refresh()
        held = self.est.held_notes() or self.est.recent_release_notes()
        scale = self.est.scale()
        if held:
            prior = self._current_group_pc(self._deck_wired()) \
                if self.deck_feed else None
            new_root = pick_root(held, scale, self.est.profile,
                                 self.est.bass, prior_pc=prior)
        elif self.root is None and scale is not None:
            new_root = scale[0]  # first-ever commit: the scale tonic
        else:
            new_root = None      # nothing held — hold the current root
        self._commit_root(new_root)

    # legacy name (older tests/callers): a grid decision
    def decide(self) -> None:
        self.commit()


class LiteralDeriver(_DeriverBase):
    """The LITERAL deriver (ids "literal", "literal.2", ...): deterministic,
    zero-lag. At commit time it reads the live held-set/last-played and
    emits ONE note through extract × place."""

    DEFAULT_EVERY = "immediate"

    def __init__(self, app, lid: str = "literal") -> None:
        self.extract = "lowest-held"
        self.place = "absolute"
        self.fold_octave = 3          # "fold": re-voice to C{fold_octave}..B
        self.transpose = 0            # "transpose": ±N semitones
        self.hold_on_empty = True
        self._held: list[int] = []    # ordered by note_on time
        self._last_played: int | None = None
        self._first_played: int | None = None  # first of the current phrase
        super().__init__(app, lid)
        self.every = "immediate"

    # -- note-sink interface ---------------------------------------------------

    def note_on(self, note: int, velocity: int = 100) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        if not self._held:
            self._first_played = note      # a fresh phrase starts
        self._held.append(note)
        self._last_played = note
        if self.every == "immediate" and not self._ping_driven():
            self.commit()

    def note_off(self, note: int) -> None:
        note = int(note)
        if note in self._held:
            self._held.remove(note)
        if self.every == "immediate" and not self._ping_driven():
            self.commit()

    def all_off(self) -> None:
        self._held.clear()
        if self._out_note is not None:
            self._tap(self._out_note, False)
            self._out_note = None
        self._thru(lambda s: s.all_off())

    def set_sustain(self, on: bool) -> None:
        self._thru(lambda s: s.set_sustain(on))

    def set_bend(self, semitones: float) -> None:
        self._thru(lambda s: s.set_bend(semitones))

    # -- configuration ---------------------------------------------------------

    def configure(self, **kw) -> None:
        if kw.get("every") == "immediate" or kw.get("every") in EVERY:
            self.every = kw["every"]
        if kw.get("extract") in EXTRACTS:
            self.extract = kw["extract"]
        if kw.get("place") in PLACES:
            self.place = kw["place"]
        if kw.get("fold_octave") is not None:
            self.fold_octave = min(7, max(0, int(kw["fold_octave"])))
        if kw.get("transpose") is not None:
            self.transpose = min(24, max(-24, int(kw["transpose"])))
        if kw.get("hold_on_empty") is not None:
            self.hold_on_empty = bool(kw["hold_on_empty"])

    def settings(self) -> dict:
        return {
            "id": self.id,
            "every": self.every,
            "everies": ["immediate", *EVERY],
            "extract": self.extract,
            "extracts": list(EXTRACTS),
            "place": self.place,
            "places": list(PLACES),
            "fold_octave": self.fold_octave,
            "transpose": self.transpose,
            "hold_on_empty": self.hold_on_empty,
            "note": self._out_note,
        }

    # -- the commit ------------------------------------------------------------

    def _pick(self) -> int | None:
        if self.extract == "lowest-held":
            return min(self._held) if self._held else None
        if self.extract == "highest-held":
            return max(self._held) if self._held else None
        if self.extract == "last-played":
            return self._last_played
        if self.extract == "first-played":
            return self._held[0] if self._held else self._first_played
        return None

    def _place(self, note: int) -> int:
        if self.place == "fold":
            return 12 * (self.fold_octave + 1) + (note % 12)
        if self.place == "transpose":
            return min(127, max(0, note + self.transpose))
        return note

    def commit(self) -> None:
        """Sample the current notes and move the mono out accordingly."""
        picked = self._pick()
        if picked is None:
            if not self.hold_on_empty:
                self._emit_out(None)       # release; nothing replaces it
            return                          # hold: leave the out where it is
        self._emit_out(self._place(int(picked)))
