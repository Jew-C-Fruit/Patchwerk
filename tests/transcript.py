"""Rig transcript: the on-disk record of what the SERVER said.

    PURE STDLIB, ON PURPOSE. No aiohttp, no mido, no supriya, no rig.

Item 37 Phase 1. `tests/rig.py` WRITES these files while driving a live
rig; Phase 2's replay suite READS them in CI, on a machine with no
SuperCollider, no audio and no engine, and injects them through
`check_blocks`' existing websocket stub. That is the whole point: the
messages a GUI check asserts against stop being a fiction the check
invented and become a recording of what the backend actually emitted.
So this module must stay importable with nothing installed — keep it
stdlib, and keep the writer out of the reader's way.

Format: JSON Lines. One record per line, each a dict with a "rec" tag
and a monotonic offset "t" (seconds, from the writer's open):

    {"rec":"header","v":1,"t":0.0,"port":8765,"patch":"pad_space", ...}
    {"rec":"recv",  "t":0.012,"msg":{"type":"state", ...}}   server -> us
    {"rec":"send",  "t":0.51, "msg":{"type":"fire_button", ...}}  us -> server
    {"rec":"midi",  "t":0.62, "msg":{"kind":"note_on","note":60,...}}
    {"rec":"mark",  "t":1.00, "label":"after the four taps"}
    {"rec":"final", "t":2.00, "msg":{"type":"state", ...}}

`recv` is the payload Phase 2 replays; `send`/`midi`/`mark` are there so
a failing replay can be read back as a story rather than a wall of JSON.
`final` is the closing state snapshot, written by the driver at teardown
BEFORE it restores anything.

**Normalising, and why the writer does none of it.** A raw transcript
does not diff: wall-clock offsets jitter, `meters` arrives 20x a second
carrying float noise, `tonic`/`deriver` ride the same clock, and
instance ids depend on which suffix `alloc_id` happened to hand out.
The writer records RAW — a recording that has already been filtered
cannot be re-filtered differently later — and `normalize()` here does
the filtering at read time. Every knob has a default so two calls agree
by construction.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

VERSION = 1

#: Message types dropped by `normalize()` — high-rate telemetry whose
#: content is float noise and whose ARRIVAL is a clock artefact.
DEFAULT_DROP = ("meters", "tonic", "deriver")

#: Timestamp quantum, seconds. Coarse enough to absorb scheduling jitter,
#: fine enough that ordering-in-time still reads.
DEFAULT_QUANTUM = 0.05

#: An allocated instance id: "lowpass.2", "keyshift.2:3" (lane grammar
#: survives — only the SUFFIX is nondeterministic). A bare "lowpass" is
#: deterministic and is deliberately left alone.
_ID_RE = re.compile(r"\b([a-z][a-z_]*)\.(\d+)\b")


# -- writing -----------------------------------------------------------------

class TranscriptWriter:
    """Append-only .jsonl writer. Offsets are monotonic from open()."""

    def __init__(self, path, **header) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._t0 = time.monotonic()
        self.count = Counter()
        self._write("header", v=VERSION,
                    started=time.strftime("%Y-%m-%dT%H:%M:%S"), **header)

    # -- record kinds --------------------------------------------------------

    def recv(self, msg: dict) -> None:
        self._write("recv", msg=msg)

    def send(self, msg: dict) -> None:
        self._write("send", msg=msg)

    def midi(self, msg: dict) -> None:
        self._write("midi", msg=msg)

    def mark(self, label: str) -> None:
        self._write("mark", label=str(label))

    def final(self, state: dict) -> None:
        self._write("final", msg=state)

    # -- plumbing ------------------------------------------------------------

    def _write(self, rec: str, **fields) -> None:
        if self._fh is None:
            raise ValueError("transcript is closed")
        row = {"rec": rec, "t": round(time.monotonic() - self._t0, 4), **fields}
        self._fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._fh.flush()      # a killed rig must still leave a readable file
        self.count[rec] += 1
        if rec in ("recv", "final"):
            self.count["type:" + str(fields.get("msg", {}).get("type"))] += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# -- reading -----------------------------------------------------------------

class Transcript:
    """A loaded transcript. Records stay in file order, always."""

    def __init__(self, records: list[dict], path=None) -> None:
        self.records = records
        self.path = Path(path) if path else None
        self.header = records[0] if records and records[0].get("rec") == "header" else {}

    def __len__(self) -> int:
        return len(self.records)

    def of(self, rec: str) -> list[dict]:
        return [r for r in self.records if r.get("rec") == rec]

    def recv(self, *types: str) -> list[dict]:
        """Server -> client messages, optionally filtered by "type"."""
        out = [r["msg"] for r in self.records if r.get("rec") == "recv"]
        return [m for m in out if m.get("type") in types] if types else out

    def sends(self) -> list[dict]:
        return [r["msg"] for r in self.of("send")]

    def midi(self) -> list[dict]:
        return [r["msg"] for r in self.of("midi")]

    def marks(self) -> list[str]:
        return [r["label"] for r in self.of("mark")]

    @property
    def final_state(self) -> dict | None:
        rows = self.of("final")
        return rows[-1]["msg"] if rows else None

    def types(self) -> Counter:
        """How many of each server message type — the quick sanity read."""
        return Counter(m.get("type") for m in self.recv())

    def events(self, *kinds: str) -> list[dict]:
        """The `{"type":"midi","event":{...}}` inner events.

        This is where `level`, `gate`, `tap`, `voiced`, `cc`, `bend` and
        `sustain` all live — the reactive-indicator doctrine's traffic
        rides the midi message, not a channel of its own.
        """
        evs = [m.get("event", {}) for m in self.recv("midi")]
        return [e for e in evs if e.get("kind") in kinds] if kinds else evs

    def segment(self, start: str | None = None, end: str | None = None) -> "Transcript":
        """Records between two `mark` labels (inclusive of neither mark).

        A scenario marks its phases; an assertion about "the four taps"
        should read only the four taps.
        """
        lo, hi = 0, len(self.records)
        if start is not None:
            lo = next((i for i, r in enumerate(self.records)
                       if r.get("rec") == "mark" and r.get("label") == start), -1) + 1
            if lo == 0:
                raise KeyError(f"no mark {start!r} — have {self.marks()}")
        if end is not None:
            hi = next((i for i, r in enumerate(self.records)
                       if i >= lo and r.get("rec") == "mark"
                       and r.get("label") == end), None)
            if hi is None:
                raise KeyError(f"no mark {end!r} after {start!r} — have {self.marks()}")
        return Transcript(self.records[lo:hi], self.path)


def read_transcript(path) -> Transcript:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: {exc}") from None
    return Transcript(rows, path)


# -- normalising -------------------------------------------------------------

def normalize_ids(obj, mapping: dict | None = None):
    """Rewrite allocated instance ids to `<type>#<ordinal>`, in place-order.

    `mapping` is shared across a whole record list so "voice.3" is the
    same node everywhere it appears. Ordinals count first appearance per
    TYPE, so a rerun that allocates .2/.5 instead of .3/.4 normalises
    identically.
    """
    if mapping is None:
        mapping = {}

    def sub(s: str) -> str:
        def one(m):
            raw = m.group(0)
            if raw not in mapping:
                typ = m.group(1)
                n = sum(1 for v in mapping.values() if v.startswith(typ + "#"))
                mapping[raw] = f"{typ}#{n}"
            return mapping[raw]
        return _ID_RE.sub(one, s)

    def walk(o):
        if isinstance(o, str):
            return sub(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {sub(k) if isinstance(k, str) else k: walk(v) for k, v in o.items()}
        return o

    return walk(obj)


def normalize(records, *, drop=DEFAULT_DROP, quantum=DEFAULT_QUANTUM,
              ids=True, keep_header=False):
    """Make a record list diffable. Raw in, comparable out.

    - drops high-rate telemetry by message type (`DEFAULT_DROP`);
    - quantises `t` (the header's volatile fields go entirely);
    - maps allocated ids to `<type>#<ordinal>` with ONE shared mapping.
    """
    drop = set(drop or ())
    mapping: dict = {}
    out = []
    for r in records:
        rec = r.get("rec")
        if rec == "header":
            if not keep_header:
                continue
            r = {k: v for k, v in r.items() if k not in ("started", "port", "t")}
            out.append(r)
            continue
        if rec in ("recv", "final") and r.get("msg", {}).get("type") in drop:
            continue
        r = dict(r)
        if quantum:
            r["t"] = round(round(r.get("t", 0.0) / quantum) * quantum, 6)
        out.append(normalize_ids(r, mapping) if ids else r)
    return out


def unpaired_notes(records) -> list[dict]:
    """Open note-ons with no matching off — CLAUDE.md's stuck-note invariant.

    Walks the `tap` (keyed by src) and `voiced` (keyed by deck-ness)
    viz events in a transcript. Every silencing path — panic, arp stop,
    deck stop, rebuild, record-window exit — must close what it opened,
    and this is the cheap way to check all of them at once.

    Returns one row per still-open note, in the order they opened.
    """
    if isinstance(records, Transcript):
        records = records.records
    open_: dict[tuple, dict] = {}
    for r in records:
        if r.get("rec") not in ("recv",):
            continue
        m = r["msg"]
        if m.get("type") != "midi":
            continue
        e = m.get("event", {})
        kind = e.get("kind")
        if kind == "tap":
            key = ("tap", e.get("src"), e.get("note"))
        elif kind == "voiced":
            key = ("voiced", bool(e.get("deck")), e.get("note"))
        else:
            continue
        if e.get("on"):
            open_.setdefault(key, {"kind": key[0], "who": key[1],
                                   "note": key[2], "t": r.get("t")})
        else:
            open_.pop(key, None)
    return list(open_.values())
