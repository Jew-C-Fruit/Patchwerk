"""Rig-driver + transcript tests (CI-safe: no rig, no scsynth, no MIDI, no net).

    python tests/test_rig.py

Item 37 Phase 1. Everything here is the half of `tests/rig.py` and
`tests/transcript.py` that does NOT need a machine with audio on it: the
transcript round-trip, the normaliser Phase 2 will diff through, the
stuck-note walker, the scenario grammar, port resolution, scsynth
discovery, the scsynth-hygiene rules (kill by exact name; readiness is
the ready LINE plus a bound socket, never the device list), and — the one
that will actually catch a regression — that the driver's spawn/remove
table still matches the message types `synthbase/server.py` dispatches.

The live half (boot, teardown, virtual MIDI round-trip) is
`tests/probe_rig_ws.py`, and it is Mac-only by nature.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import rig as R  # noqa: E402
import transcript as T  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _write_sample(path, suffix="2"):
    """A miniature session: state, a spawn, notes, telemetry, a mark."""
    with T.TranscriptWriter(path, port=8765, patch="pad_space",
                            scenario="sample") as tw:
        tw.recv({"type": "state", "buttons": [], "ctl_wires": []})
        tw.send({"type": "spawn_button"})
        tw.recv({"type": "state", "buttons": [{"id": f"button.{suffix}"}],
                 "ctl_wires": [{"from": f"button.{suffix}", "to": "transport:tap"}]})
        tw.recv({"type": "meters", "out": [0.11, 0.12], "in": 0.0})
        tw.mark("notes")
        tw.midi({"type": "note_on", "note": 60, "velocity": 90})
        tw.recv({"type": "midi", "event": {"kind": "tap", "src": "keys",
                                           "note": 60, "on": True}})
        tw.recv({"type": "midi", "event": {"kind": "level",
                                           "ep": "transport:click", "on": True}})
        tw.recv({"type": "midi", "event": {"kind": "tap", "src": "keys",
                                           "note": 60, "on": False}})
        tw.recv({"type": "tonic", "note": 60})
        tw.mark("done")
        tw.final({"type": "state", "buttons": [{"id": f"button.{suffix}"}]})


# -- transcript ---------------------------------------------------------------

def test_transcript_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "sub" / "t.jsonl"
        _write_sample(p)
        check("writer creates its parent directory", p.exists())
        t = T.read_transcript(p)
        check("header is record 0", t.header.get("rec") == "header"
              and t.header.get("v") == T.VERSION, str(t.header))
        check("header carries the run's identity",
              (t.header.get("patch"), t.header.get("port"),
               t.header.get("scenario")) == ("pad_space", 8765, "sample"))
        check("recv() returns server messages only", len(t.recv()) == 7,
              str(len(t.recv())))
        check("recv(type) filters", [m["type"] for m in t.recv("state")]
              == ["state", "state"])
        check("sends() are separate from recv()",
              t.sends() == [{"type": "spawn_button"}], str(t.sends()))
        check("midi() records what the driver injected",
              t.midi() == [{"type": "note_on", "note": 60, "velocity": 90}])
        check("marks() in order", t.marks() == ["notes", "done"], str(t.marks()))
        check("final_state is the closing snapshot",
              (t.final_state or {}).get("buttons") == [{"id": "button.2"}])
        check("types() counts by message type",
              dict(t.types()) == {"state": 2, "meters": 1, "midi": 3, "tonic": 1},
              str(dict(t.types())))
        check("offsets are monotonic",
              all(a["t"] <= b["t"] for a, b in zip(t.records, t.records[1:])))


def test_transcript_events_and_segments():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        _write_sample(p)
        t = T.read_transcript(p)
        check("events() unwraps the midi envelope", len(t.events()) == 3)
        check("events(kind) filters — level is where the doctrine lives",
              [e["ep"] for e in t.events("level")] == ["transport:click"])
        seg = t.segment("notes", "done")
        check("segment() is bounded by its marks",
              len(seg.events()) == 3 and not seg.recv("state"),
              str(len(seg.records)))
        check("segment() to the end works", len(t.segment("done").records) == 1)
        try:
            t.segment("nope")
            check("segment() on a missing mark raises", False)
        except KeyError:
            check("segment() on a missing mark raises", True)


def test_normalize():
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
        _write_sample(a, suffix="2")
        _write_sample(b, suffix="7")     # a different alloc_id suffix
        ra = T.normalize(T.read_transcript(a).records)
        rb = T.normalize(T.read_transcript(b).records)
        check("normalize drops the header by default",
              not any(r["rec"] == "header" for r in ra))
        kinds = {r["msg"].get("type") for r in ra if r["rec"] in ("recv", "final")}
        check("normalize drops meters/tonic/deriver",
              not (kinds & set(T.DEFAULT_DROP)), str(kinds))
        check("ids normalise to <type>#<ordinal>",
              json.dumps(ra).count("button#0") == 3
              and "button.2" not in json.dumps(ra), json.dumps(ra)[:200])
        check("two runs with different suffixes normalise IDENTICALLY",
              ra == rb, "\n" + json.dumps(ra)[:300] + "\n" + json.dumps(rb)[:300])
        raw = T.read_transcript(a).records
        check("raw records still carry the real ids (the writer filters nothing)",
              "button.2" in json.dumps(raw))
        check("timestamps quantise to the quantum",
              all(abs(r["t"] / T.DEFAULT_QUANTUM
                      - round(r["t"] / T.DEFAULT_QUANTUM)) < 1e-6 for r in ra))
        kept = T.normalize(raw, drop=(), ids=False, keep_header=True)
        check("nothing is dropped when asked to drop nothing",
              len(kept) == len(raw) and "button.2" in json.dumps(kept))
        check("kept header loses its volatile fields",
              "started" not in kept[0] and "port" not in kept[0], str(kept[0]))


def test_id_normalisation_edges():
    m = {}
    out = T.normalize_ids({"a": "keyshift.2:3", "b": ["voice.5", "voice.5"],
                           "c": "lowpass", "d": 1.5, "e": "0.5"}, m)
    check("lane grammar survives id normalisation",
          out["a"] == "keyshift#0:3", str(out))
    check("the same id maps to the same name everywhere",
          out["b"] == ["voice#0", "voice#0"], str(out))
    check("bare (deterministic) ids are left alone", out["c"] == "lowpass")
    check("numbers are not ids", out["d"] == 1.5 and out["e"] == "0.5", str(out))
    out2 = T.normalize_ids({"x": "voice.9", "y": "lowpass.3"}, m)
    check("ordinals count per type, in first-appearance order",
          out2 == {"x": "voice#1", "y": "lowpass#0"}, str(out2))


def test_unpaired_notes():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.jsonl"
        _write_sample(p)
        t = T.read_transcript(p)
        check("a closed note pair leaves nothing open",
              T.unpaired_notes(t) == [], str(T.unpaired_notes(t)))
        bad = Path(d) / "stuck.jsonl"
        with T.TranscriptWriter(bad) as tw:
            tw.recv({"type": "midi", "event": {"kind": "tap", "src": "arp",
                                               "note": 64, "on": True}})
            tw.recv({"type": "midi", "event": {"kind": "voiced", "note": 64,
                                               "on": True}})
            tw.recv({"type": "midi", "event": {"kind": "voiced", "note": 64,
                                               "on": False}})
        rows = T.unpaired_notes(T.read_transcript(bad))
        check("an unclosed tap is reported (the stuck-note invariant)",
              len(rows) == 1 and rows[0]["who"] == "arp" and rows[0]["note"] == 64,
              str(rows))


# -- driver: the parts that need no rig ---------------------------------------

def test_port_and_discovery(monkey=None):
    import os
    old = os.environ.get("SS_PORT")
    try:
        os.environ.pop("SS_PORT", None)
        check("default port is 8765", R.rig_port() == 8765)
        os.environ["SS_PORT"] = "9111"
        check("SS_PORT wins", R.rig_port() == 9111)
    finally:
        os.environ.pop("SS_PORT", None)
        if old is not None:
            os.environ["SS_PORT"] = old
    sc = R.find_scsynth()
    check("find_scsynth() answers without raising and without PATH",
          sc is None or Path(sc).exists(), str(sc))
    env = R.child_env()
    check("child_env() always yields a PATH", bool(env.get("PATH")))
    if sc is not None:
        check("child_env() puts scsynth's directory first",
              env["PATH"].split(":")[0] == str(Path(sc).parent),
              env["PATH"][:120])


def test_scsynth_hygiene():
    """Kill by EXACT name, and treat readiness as two signals, not one."""
    src = (REPO / "tests" / "rig.py").read_text()
    calls = re.findall(r'\[\s*"(?:pkill|pgrep)"[^\]]*\]', src)
    check("the driver does kill/list scsynth by process name", bool(calls),
          str(calls))
    check("NO pkill/pgrep uses -f — a loose pattern matches the driver's own "
          "command line and it kills itself",
          all('"-f"' not in c for c in calls), str(calls))
    check("every scsynth kill/list is anchored with -x",
          all('"-x"' in c for c in calls), str(calls))

    check("readiness is scsynth's own ready LINE, not its device list",
          R.SCSYNTH_READY == "SuperCollider 3 server ready"
          and R.SCSYNTH_DEVICES == "Number of Devices"
          and R.SCSYNTH_READY != R.SCSYNTH_DEVICES)
    check("scsynth_check reports both signals plus the udp socket, separately",
          {"ready", "devices", "udp"} <= set(
              re.findall(r'"(\w+)":', src[src.index("def scsynth_check"):
                                          src.index("def _diagnose")])))
    check("scsynth_alive() answers a list without raising",
          isinstance(R.scsynth_alive(), list))
    p = R.free_udp_port()
    check("free_udp_port() hands back a real, free port",
          1024 < p < 65536 and not R.udp_held(p), str(p))

    # _diagnose must name the fault, not just report a hang. Feed it each
    # verdict rather than spawning a server — CI has no audio either way.
    real = R.scsynth_check
    try:
        R.scsynth_check = lambda *a, **k: {
            "ready": False, "devices": True, "udp": False, "stale": 0,
            "seconds": 15.0, "tail": "SC_AudioDriver: sample rate = 48000"}
        stalled = R._diagnose(8765, "/tmp/x.log")
        check("devices-but-not-ready is named as the CoreAudio stall",
              "never starts one" in stalled and "NOT a stale process" in stalled
              and "silent=True" in stalled, stalled)
        R.scsynth_check = lambda *a, **k: {
            "ready": True, "devices": True, "udp": True, "stale": 2,
            "seconds": 1.0, "tail": "SuperCollider 3 server ready"}
        healthy = R._diagnose(8765, "/tmp/x.log")
        check("a healthy scsynth points the finger above it",
              "fault is above it" in healthy and "cleared" in healthy, healthy)
    finally:
        R.scsynth_check = real


def test_ids_in():
    check("ids_in reads a list of dicts",
          R.ids_in({"buttons": [{"id": "button"}, {"id": "button.2"}]},
                   "buttons") == {"button", "button.2"})
    check("ids_in reads a list of strings",
          R.ids_in({"transport_cards": ["play", "tempo"]},
                   "transport_cards") == {"play", "tempo"})
    check("ids_in reads a dict", R.ids_in({"x": {"a": 1}}, "x") == {"a"})
    check("ids_in on a missing section is empty",
          R.ids_in({}, "nope") == set())


def test_spawn_table_matches_the_protocol():
    """The driver's spawn/remove table must not drift from server.py."""
    src = (REPO / "synthbase" / "server.py").read_text()
    handled = set(re.findall(r'(?:el)?if t == "([a-z_]+)"', src))
    missing = {m for pair in R.SPAWNABLE.items()
               for m in (pair[0], pair[1][1]) if m not in handled}
    check("every spawn/remove message in SPAWNABLE is dispatched by server.py",
          not missing, str(sorted(missing)))
    spawns = {m for m in handled if m.startswith("spawn_")
              and m not in ("spawn_module", "spawn_transport_card")}
    check("SPAWNABLE covers every id-allocating spawn message",
          spawns == set(R.SPAWNABLE), str(sorted(spawns ^ set(R.SPAWNABLE))))
    check("the poke message is still a real message type",
          "set_transport" in handled)


def test_scenario_grammar():
    good = {"name": "x", "steps": [
        {"do": "spawn", "type": "spawn_button", "as": "b"},
        {"do": "send", "msg": {"type": "set_button", "id": "$b", "latch": False}},
        {"do": "wire", "from": "$b", "to": "transport:tap"},
        {"do": "mark", "label": "taps"},
        {"do": "wait", "s": 0.5},
        {"do": "midi_enable"},
        {"do": "midi", "kind": "note_on", "note": 60},
        {"do": "restore"}]}
    check("a well-formed scenario validates clean",
          R.validate_scenario(good) == [], str(R.validate_scenario(good)))
    bad = {"steps": [
        {"do": "teleport"},
        {"do": "send", "msg": "not-an-object"},
        {"do": "wire", "from": "$ghost", "to": "arp", "kind": "wat_wire"},
        {"do": "midi", "kind": "aftertouch"},
        {"do": "mark"}]}
    probs = " | ".join(R.validate_scenario(bad))
    for want in ("no name", "unknown step", "needs a msg object",
                 "not bound by an earlier spawn", "unknown wire kind",
                 "unknown midi kind", "mark needs a label"):
        check(f"validator catches: {want}", want in probs, probs)


def test_substitution():
    out = R._subst({"msg": {"id": "$b", "to": "transport:tap",
                            "list": ["$b", 3]}}, {"b": "button.2"})
    check("$vars substitute anywhere, at any depth",
          out == {"msg": {"id": "button.2", "to": "transport:tap",
                          "list": ["button.2", 3]}}, str(out))
    check("an unbound $var is left verbatim (the validator is what complains)",
          R._subst("$nope", {}) == "$nope")


def test_committed_scenarios_validate():
    d = REPO / "tests" / "scenarios"
    files = sorted(d.glob("*.json")) if d.exists() else []
    check("scenarios are committed for Phase 2 to record from", bool(files),
          str(d))
    for f in files:
        try:
            scn = json.loads(f.read_text())
        except ValueError as exc:
            check(f"{f.name} is valid JSON", False, str(exc))
            continue
        probs = R.validate_scenario(scn)
        check(f"{f.name} validates", probs == [], str(probs))


def test_recorded_fixtures():
    """The reader against transcripts a REAL backend produced, not ours.

    Every other check here writes the transcript it then reads, which is
    the same circularity item 37 exists to break — "a mock only proves the
    GUI reacts to a message we invented." These two files were recorded off
    `tests/silent_rig.py` (the real `GuiServer` over a real `SynthApp`) by
    `rig.py play`, and they are what Phase 2 replays.
    """
    d = REPO / "tests" / "fixtures"
    lv = T.read_transcript(d / "transcript_transport_levels.jsonl")
    check("the recorded level transcript loads", len(lv.records) > 50,
          str(len(lv.records)))
    eps = [(e.get("ep"), e.get("on")) for e in lv.events("level")]
    check("it carries REAL {'kind':'level'} emissions for both endpoints",
          {e for e, _ in eps} == {"transport:run", "transport:click"}, str(eps))
    check("marks segment it into the four toggles",
          lv.marks() == ["setup", "run-on", "click-on", "click-off",
                         "run-off", "settled"], str(lv.marks()))
    seg = lv.segment("click-on", "click-off")
    check("the click-on segment holds exactly its own level event",
          [(e.get("ep"), e.get("on")) for e in seg.events("level")]
          == [("transport:click", True)],
          str([(e.get("ep"), e.get("on")) for e in seg.events("level")]))
    check("a real recording has no unpaired notes",
          T.unpaired_notes(lv) == [], str(T.unpaired_notes(lv)))
    n = T.normalize(lv.records)
    check("a real recording normalises and loses its telemetry",
          0 < len(n) < len(lv.records)
          and not any(r.get("msg", {}).get("type") in T.DEFAULT_DROP for r in n),
          f"{len(n)} of {len(lv.records)}")
    check("normalising a real recording is idempotent",
          T.normalize(n, quantum=0) == n)

    tap = T.read_transcript(d / "transcript_transport_tap.jsonl")
    during = tap.segment("taps", "settled")
    check("the tap transcript records four button fires",
          len([e for e in during.events("gate") if e.get("on")]) == 4,
          str(during.events("gate")))
    check("§3.3, ON DISK: four tap pulses broadcast NO state at all",
          during.recv("state") == [], str(len(during.recv("state"))))
    bpms = [m["transport"]["bpm"] for m in tap.recv("state")]
    check("§3.3, ON DISK: bpm still moved — the rig sped up in silence",
          max(bpms) - min(bpms) > 5, str(sorted(set(bpms))))


def main():
    test_transcript_roundtrip()
    test_transcript_events_and_segments()
    test_normalize()
    test_id_normalisation_edges()
    test_unpaired_notes()
    test_port_and_discovery()
    test_scsynth_hygiene()
    test_ids_in()
    test_spawn_table_matches_the_protocol()
    test_scenario_grammar()
    test_substitution()
    test_committed_scenarios_validate()
    test_recorded_fixtures()
    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
