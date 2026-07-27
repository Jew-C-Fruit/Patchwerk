"""Transcript replay: what the SERVER said, rendered by the REAL page.

Item 37 Phase 2 — the payload of the plan. This is the engine; the
assertions live in `tests/check_replay.py`.

The hole this closes, stated as the plan states it: the 9 Python suites
observe engine STATE but never engine OUTPUT, the 3 Playwright suites never
import `synthbase` at all and assert against messages they INVENTED, and
nothing anywhere joins "the backend emitted X" to "the GUI rendered Y".
Replay joins them. It records real server->client traffic through Phase 1's
rig driver, then feeds that recording — verbatim, in order — through
`check_blocks`' existing websocket stub into `gui/blocks.html`.

**No audio, no scsynth, no rig.** `tests/silent_rig.py` (Phase 1) serves the
REAL `GuiServer` over a real but engine-less `SynthApp` in ~0.8 s. Every
`{"kind":"level"}` tap, every `{"kind":"transport"}` event, the whole binary
and control plane — all of it is exercised with no audio device anywhere in
the picture. So this runs in CI today, on a Linux runner, unmodified.

Two planes, deliberately separable, because CI runs them in different jobs:

* the **emission plane** needs `synthbase` + `aiohttp` and no browser —
  re-record a scenario now and compare it against the committed fixture;
* the **DOM plane** needs Playwright and no engine — replay a committed
  fixture into the page and read the rendered surfaces.

**Why re-recording matters, and it is the whole point.** A committed
transcript is a fixture, and a fixture rots exactly like the fiction it
replaces: gut `_emit_level` and every committed `.jsonl` still contains the
events it contained yesterday. Provenance is not liveness. What makes the
emission assertable is RE-DERIVING the transcript on the tree under test and
diffing it against the recording — `skeleton()` and `diff_skeletons()` here,
`record()` to produce the fresh one. That is the check that goes red when the
backend stops talking.

**The coincidence rule.** An observation where the surface ALREADY read the
expected value before the event landed proves nothing — it agrees with a
coincidence. `Observation.informative` marks the ones that do prove
something, and the suite requires every endpoint x surface x direction to
have at least one. This is not decoration: a scenario that pokes a full
state broadcast between the fire and the probe re-syncs every surface and
turns the whole matrix into coincidences, all of them green.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from transcript import Transcript, normalize_ids, read_transcript  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
SCENARIOS = REPO / "tests" / "scenarios"

#: Message types a replay never feeds the page: 20 Hz float noise whose
#: ARRIVAL is a clock artefact. `transcript.DEFAULT_DROP` says the same for
#: diffing; this is the render-time twin.
SKIP_TYPES = ("meters",)


# -- the emission plane -------------------------------------------------------

def engine_capabilities() -> dict:
    """What contract does THIS tree's engine speak?

    A transcript recorded against a newer engine replays fine against an
    older tree's GUI — it is just data — but its EMISSIONS cannot be
    re-derived here, and reporting that as a regression would be a lie.
    So probe rather than assume, and let the caller downgrade.

    `pulse` covers the whole `feat/p3-reactive-taps` contract: the
    `pulse`-tagged trig-in taps and `{"kind":"transport"}` landed together
    (`_pulse_level` + `_is_trig_dst` in `gate.py`, `set_transport`'s emit in
    `app.py`), so one probe answers for both.
    """
    caps = {"engine": False, "pulse": False}
    try:
        from synthbase.gate import GateManager
    except Exception:  # noqa: BLE001 — no synthbase in the GUI CI job
        return caps
    caps["engine"] = True
    caps["pulse"] = hasattr(GateManager, "_pulse_level")
    return caps


def _ev_key(e: dict, sub) -> tuple:
    """One event reduced to a run-to-run STABLE tuple.

    Values that are wall-clock derived never enter the key. Tap tempo is
    the case that forces this: three taps 500 ms apart produce a bpm of
    119.87 one run and 119.54 the next, so a skeleton that carried the
    float would report drift on every rerun and be switched off within a
    day. Presence and shape are what a skeleton is for; VALUES are the
    observability check's job (it reads them off the rendered page).
    """
    k = e.get("kind")
    if k == "level":
        return ("level", sub(str(e.get("ep"))), bool(e.get("on")),
                bool(e.get("pulse")))
    if k == "gate":
        return ("gate", sub(str(e.get("id"))), bool(e.get("on")))
    if k == "ping":
        return ("ping", sub(str(e.get("src"))))
    if k == "looper":
        return ("looper", e.get("state"))
    if k == "transport":
        return ("transport", tuple(sorted(x for x in e if x != "kind")))
    if k == "tap":
        return ("tap", sub(str(e.get("src"))), e.get("note"), bool(e.get("on")))
    if k == "voiced":
        return ("voiced", bool(e.get("deck")), e.get("note"), bool(e.get("on")))
    if k == "tonic_out":
        return ("tonic_out", sub(str(e.get("id"))), e.get("root"))
    return (str(k),)


def skeleton(t: Transcript | list) -> list[tuple]:
    """The comparable spine of a transcript: marks + every inner event.

    `state` is deliberately absent. A state broadcast is a whole-board
    snapshot whose diff is dominated by things this phase does not assert
    (device lists, preset names, ids), and including it would bury the one
    signal that matters — whether the event plane still says what it said.
    Marks stay in so a difference localises to a scenario phase.
    """
    records = t.records if isinstance(t, Transcript) else t
    mapping: dict = {}
    sub = lambda s: normalize_ids(s, mapping)   # noqa: E731 — one shared map
    out = []
    for r in records:
        if r.get("rec") == "mark":
            out.append(("mark", r.get("label")))
        elif r.get("rec") == "recv" and r.get("msg", {}).get("type") == "midi":
            out.append(_ev_key(r["msg"].get("event") or {}, sub))
    return out


def diff_skeletons(want: list[tuple], got: list[tuple]) -> dict:
    """Compare two skeletons. Returns counts plus the first divergence.

    Order-sensitive on purpose — `{"kind":"transport"}` must precede the
    `level` tap that caused it, and a reordering there is a real defect.
    """
    n = min(len(want), len(got))
    first = next((i for i in range(n) if want[i] != got[i]), None)
    if first is None and len(want) != len(got):
        first = n
    return {
        "same": first is None and len(want) == len(got),
        "at": first,
        "want": want[first] if first is not None and first < len(want) else None,
        "got": got[first] if first is not None and first < len(got) else None,
        "n_want": len(want), "n_got": len(got),
        "missing": [k for k in want if k not in got],
        "extra": [k for k in got if k not in want],
    }


def record(scenario: str | Path, out: str | Path, *, port: int | None = None,
           timeout: float = 180.0) -> Transcript:
    """Re-derive a transcript NOW, against this tree, on a silent rig.

    Shells out to `tests/rig.py` rather than importing it: the driver owns
    process lifecycle, and a suite that crashed mid-scenario must not leave
    a rig behind in the CI job's process group.
    """
    scenario, out = Path(scenario), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if port:
        env["SS_PORT"] = str(port)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests" / "rig.py"), "--silent",
         "-o", str(out), "play", str(scenario)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"record({scenario.name}) failed rc={r.returncode}\n"
                           f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return read_transcript(out)


def free_port(lo: int = 8810, hi: int = 8899) -> int:
    """A port nothing is listening on — parallel sessions share this Mac."""
    import socket
    for p in range(lo, hi):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise RuntimeError(f"no free port in {lo}..{hi}")


# -- the DOM plane ------------------------------------------------------------

_CHROME_GLOB = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
                or glob.glob("/opt/pw-browsers/chromium/chrome-linux/chrome")
                or glob.glob("/opt/pw-browsers/chromium"))
CHROME = _CHROME_GLOB[0] if _CHROME_GLOB else None

#: `check_blocks`' stub, imported rather than copied. Both check_real.py and
#: gui_check8.py duplicate it; a fourth copy would be a fourth thing to
#: forget. The plan asks for replay in its OWN file importing check_blocks'
#: helpers, and this is the helper that matters.
try:
    from check_blocks import STUB  # noqa: E402
except Exception:  # noqa: BLE001 — check_blocks needs playwright installed
    STUB = None


@dataclass
class Observation:
    """One surface read across one event. The unit the matrix is built from.

    `informative` is the load-bearing field. `post == expect` alone is not
    evidence: if the surface already read `expect` before the event landed,
    a handler that does nothing scores identically to one that works. Only
    observations where `pre != expect` can tell those two apart.

    Two modes, because the two event shapes ask different questions:

    * `"level"` — a STEADY tap. The surface must READ the event's value.
      Probes return a bool.
    * `"change"` — a PULSE. There is no steady value to read; a flash is a
      transient. So the probe returns a SIGNATURE (classes + the computed
      properties a flash could plausibly move) and reacting means the
      signature moved at all. Implementation-agnostic on purpose: the GUI
      half of the pulse contract is not written yet, and a probe that
      demanded one particular class name would be asserting a fiction about
      an implementation nobody has chosen.
    """
    ep: str
    surface: str
    expect: object
    pre: object
    post: object
    mode: str = "level"          # "level" | "change"
    pulse: bool = False
    index: int = -1
    mark: str = ""

    @property
    def informative(self) -> bool:
        if self.pre is None:
            return False
        if self.mode == "change":
            return True          # a signature always CAN move
        return bool(self.pre) != bool(self.expect)

    @property
    def reacted(self) -> bool:
        if self.post is None:
            return False
        if self.mode == "change":
            return self.post != self.pre
        return bool(self.post) == bool(self.expect)

    @property
    def missing_surface(self) -> bool:
        return self.post is None


class ReplayPage:
    """A blocks.html page you feed server messages to, one at a time."""

    def __init__(self, page, errors: list) -> None:
        self.page = page
        self.errors = errors
        self.fed = 0

    def feed(self, msg: dict, settle: int = 0) -> None:
        """Inject ONE server->client message, exactly as recorded."""
        self.page.evaluate("(m) => __msg(m)", msg)
        self.fed += 1
        if settle:
            self.page.wait_for_timeout(settle)

    def probe(self, js: str):
        return self.page.evaluate(js)

    def probes(self, table: dict) -> dict:
        """Evaluate a {name: js} table in one round-trip."""
        expr = "(() => ({" + ",".join(
            f"{json.dumps(k)}: (() => {{ try {{ return ({v}); }}"
            f" catch (e) {{ return null; }} }})()"
            for k, v in table.items()) + "}))()"
        return self.page.evaluate(expr)

    def settle(self, ms: int = 40) -> None:
        self.page.wait_for_timeout(ms)


def open_replay(pw, *, viewport=(1700, 1250)):
    """A headless blocks.html with check_blocks' websocket stub installed."""
    if STUB is None:
        raise RuntimeError("check_blocks did not import — is playwright installed?")
    launch_kw = {"headless": True}
    if CHROME and os.path.exists(CHROME):
        launch_kw["executable_path"] = CHROME
    browser = pw.chromium.launch(**launch_kw)
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((REPO / "gui" / "blocks.html").as_uri())
    page.wait_for_timeout(300)
    page.evaluate("""() => {
      window.__msg = (m) => window.__wss[0].onmessage({data: JSON.stringify(m)});
    }""")
    return browser, ReplayPage(page, errors)


@dataclass
class Walk:
    """The result of walking one transcript through one page."""
    observations: list = field(default_factory=list)
    marks: list = field(default_factory=list)
    fed: int = 0
    withheld: int = 0
    seconds: float = 0.0


def walk(rp: ReplayPage, t: Transcript, *, on_event=None,
         stop_mark: str | None = None, start_mark: str | None = None,
         skip_types: tuple = SKIP_TYPES, skip_event=None) -> Walk:
    """Feed every recorded server message into the page, in file order.

    `on_event(rp, event, index, mark)` fires AFTER an inner
    `{"type":"midi"}` event is injected but BEFORE the next message — the
    only window in which a tap's effect is attributable to the tap. Feed
    the whole transcript and probe at mark boundaries instead, and every
    reading is post-`state`-broadcast: the poke at the end of a scenario
    phase re-renders the whole board and re-syncs every surface, so the
    matrix goes uniformly green and proves nothing. That is not a
    hypothetical — the `indicators` scenario pokes at the end of both
    steady phases, and the top-bar click gap is invisible if you read
    there.

    `skip_event` WITHHOLDS inner events. It exists for attribution, and it
    is only honest under one rule: **every withheld event must get its own
    pass in which its own effect is asserted.** Withholding is how you find
    out which of two adjacent causes moved a surface; it becomes a way of
    excusing a broken handler the moment some event is withheld everywhere.
    `check_replay`'s driver passes hold that line — see DRIVERS there.
    """
    w = Walk()
    t0 = time.monotonic()
    mark = ""
    live = start_mark is None
    for i, r in enumerate(t.records):
        rec = r.get("rec")
        if rec == "mark":
            mark = r.get("label") or ""
            if start_mark is not None and mark == start_mark:
                live = True
            if stop_mark is not None and mark == stop_mark:
                break
            if live:
                w.marks.append(mark)
            continue
        if rec != "recv" or not live:
            continue
        m = r["msg"]
        if m.get("type") in skip_types:
            continue
        if (skip_event is not None and m.get("type") == "midi"
                and skip_event(m.get("event") or {})):
            w.withheld += 1
            continue
        pre_hook = on_event is not None and m.get("type") == "midi"
        ev = (m.get("event") or {}) if pre_hook else None
        if pre_hook:
            on_event(rp, ev, i, mark, "before")
        rp.feed(m)
        w.fed += 1
        if pre_hook:
            on_event(rp, ev, i, mark, "after")
    w.seconds = round(time.monotonic() - t0, 2)
    return w


def unpaired_from(t: Transcript) -> list[dict]:
    """CLAUDE.md's stuck-note invariant, over a recording."""
    from transcript import unpaired_notes
    return unpaired_notes(t)
