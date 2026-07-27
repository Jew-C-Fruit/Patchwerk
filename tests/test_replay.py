"""What the skeleton diff still refuses — the partial order, pinned.

    python tests/test_replay.py

CI-safe: pure stdlib, no rig, no browser, no engine. It compares synthetic
skeletons, so it runs in milliseconds and says exactly which property broke.

WHY THIS EXISTS. `diff_skeletons` compares an ORDERED sequence, and one pair
in it is genuinely unordered: `{"kind":"looper"}`'s `recording` transition is
set on the looper's own daemon thread (`looper.py:344-351`) while the
`deck:rec` pulse pair is emitted from the gate settle pass. Two threads, no
synchronisation, so the interleaving is a coin flip — measured at roughly 2
in 5 serial runs on `main`, and far worse under the load CI normally has.

The cheap repairs are both wrong, and this file is what stops someone
reaching for them later:

* **sorting entries within a timestamp** would reorder the PULSE PAIR, whose
  two halves carry an identical `t` to four decimal places (verified in the
  recording) — `False` sorts before `True`, so the falling half would come
  first and the one ordering the contract actually specifies would be
  destroyed;
* **comparing multisets** would stop noticing any reordering at all.

Either would be the same trade refused when the probe window was widened:
buying green by no longer testing the thing. So ordering is relaxed for
exactly the pairs the engine leaves unordered, named in
`replay.CONCURRENT_EMITTERS` with the thread boundary that earns each one,
and everything below still has to fail.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import replay as R  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("ok    " if cond else "FAIL  ") + name
          + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILURES.append(name)


# A miniature of the real thing: two marks, both lanes represented, and a
# pulse pair whose halves must stay in order.
BASE = [
    ("mark", "setup"),
    ("level", "transport:run", False, False),
    ("mark", "fire"),
    ("transport", ("bpm", "click", "running")),
    ("level", "transport:run", True, False),
    ("looper", "armed"),
    ("level", "deck:rec", True, True),
    ("level", "deck:rec", False, True),
    ("looper", "recording"),
    ("mark", "settled"),
    ("gate", "button#0", False),
]


def swap(seq, a, b):
    out = list(seq)
    out[a], out[b] = out[b], out[a]
    return out


def main():
    same = R.diff_skeletons(BASE, list(BASE))
    check("identical skeletons compare equal", same["same"], str(same))
    check("...and tolerate nothing when nothing is interleaved",
          same["tolerated"] == 0, str(same["tolerated"]))

    # ---- THE RACE: tolerated, and only this one -----------------------------
    # `looper:recording` crossing the deck:rec falling half. Contents and
    # per-lane order are untouched; only the interleaving moved.
    raced = swap(BASE, 7, 8)
    d = R.diff_skeletons(BASE, raced)
    check("the looper/settle interleaving is TOLERATED", d["same"], str(d))
    check("...and the tolerance is REPORTED, not silent",
          d["tolerated"] == 1, f"tolerated={d['tolerated']}")

    # ---- everything the diff must still refuse ------------------------------
    d = R.diff_skeletons(BASE, swap(BASE, 6, 7))
    check("a PULSE PAIR reordered (on/off swapped) still FAILS",
          not d["same"], str(d))
    check("...and it is named as a lane reorder, not as missing content",
          "lane" in (d.get("why") or ""), str(d.get("why")))

    d = R.diff_skeletons(BASE, swap(BASE, 3, 4))
    check("a transport event reordered against the level tap it caused "
          "still FAILS", not d["same"], str(d))

    # Both looper entries are in ONE lane: their own order is still checked.
    looper_swapped = list(BASE)
    looper_swapped[5], looper_swapped[8] = looper_swapped[8], looper_swapped[5]
    d = R.diff_skeletons(BASE, looper_swapped)
    check("the looper's OWN sequence (armed before recording) still FAILS "
          "when reversed", not d["same"], str(d))

    dropped = [e for i, e in enumerate(BASE) if i != 6]
    d = R.diff_skeletons(BASE, dropped)
    check("a MISSING event still FAILS", not d["same"], str(d))
    check("...and names the event that went missing",
          d.get("missing") and d["missing"][0] == ("level", "deck:rec", True, True),
          str(d.get("missing")))

    added = list(BASE)
    added.insert(6, ("level", "deck:play", True, True))
    d = R.diff_skeletons(BASE, added)
    check("an EXTRA event still FAILS", not d["same"], str(d))

    # A mark is a total-order barrier: an event may float inside its phase,
    # never across one. Without this the relaxation would be global in
    # practice, since every event would be free to drift anywhere.
    moved = list(BASE)
    moved.remove(("gate", "button#0", False))
    moved.insert(1, ("gate", "button#0", False))
    d = R.diff_skeletons(BASE, moved)
    check("an event that DRIFTS ACROSS A MARK still FAILS", not d["same"],
          str(d))

    reordered_marks = [e for e in BASE if e[0] != "mark"]
    d = R.diff_skeletons(BASE, reordered_marks)
    check("a changed MARK SEQUENCE still FAILS", not d["same"], str(d))

    # ---- the lane map itself ------------------------------------------------
    check("every concurrent emitter states the thread boundary that earns it",
          all(isinstance(v, str) and len(v) > 80
              for v in R.CONCURRENT_EMITTERS.values()),
          str(list(R.CONCURRENT_EMITTERS)))
    check("the relaxation is NARROW — the settle lane is not in it",
          R.SETTLE_LANE not in R.CONCURRENT_EMITTERS
          and R.lane_of(("level", "x", True, False)) == R.SETTLE_LANE
          and R.lane_of(("looper", "recording")) == "looper",
          str(sorted(R.CONCURRENT_EMITTERS)))

    check_atomic_write()

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


def _collide_writer(target, tag):
    """A module-level worker: `spawn` pickles the target by reference."""
    from transcript import TranscriptWriter
    with TranscriptWriter(target, scenario=tag) as tw:
        for i in range(40):
            tw.recv({"type": "midi", "event": {"kind": "level",
                                               "ep": f"{tag}:{i}",
                                               "pad": "x" * 300}})


def check_atomic_write():
    """A transcript appears at its final path COMPLETE, or not at all.

    Two sessions independently reported `--emission` failing with
    `Expecting value: line 1 column 1` and a check count that dropped from
    16 to 13. Reproduced at 2 in 6 trials by pointing two writers at one
    path: each had its own offset and its own O_TRUNC, so the file was a mix
    of both — sometimes a torn line, sometimes a plausible file that had
    silently lost half its records. The second is the worse one, and it is
    the reason this is asserted rather than assumed: a short recording reads
    as a backend regression, or worse, as a smaller passing run.
    """
    import multiprocessing as mp
    import tempfile
    from transcript import TranscriptWriter, read_transcript

    d = Path(tempfile.mkdtemp(prefix="patchwerk-atomic-"))
    target = d / "t.jsonl"

    with TranscriptWriter(target, scenario="a") as tw:
        check("while writing, the FINAL path does not exist yet",
              not target.exists(), f"{target} appeared early")
        check("...and the partial is at a per-process sidecar",
              tw.sidecar_path.exists()
              and tw.sidecar_path.name.startswith("."), str(tw.sidecar_path))
        tw.recv({"type": "state"})
    check("on close it is published, complete", target.exists()
          and len(read_transcript(target)) == 2, "")
    check("...and the sidecar is gone", not tw.sidecar_path.exists(), "")

    # An abandoned recording must not overwrite what was already there.
    before = target.read_text()
    tw2 = TranscriptWriter(target, scenario="b")
    tw2.recv({"type": "state"})
    left = tw2.abandon()
    check("an ABANDONED recording leaves the previous file intact",
          target.read_text() == before, "the committed file was clobbered")
    check("...and its partial is still readable, for diagnosis",
          left is not None and left.exists(), str(left))

    # Two writers, one path. NOT raced: a race would make this assertion
    # itself probabilistic — at ~1 in 3 per trial, four trials give a ~20%
    # chance of passing while broken, and a check that agrees with a
    # coincidence is the exact thing this suite exists to refuse. Assert the
    # deterministic INVARIANT that makes concurrency safe instead: two live
    # writers never share a file, and neither publishes until it closes.
    a = TranscriptWriter(target, scenario="A")
    b = TranscriptWriter(target, scenario="B")
    a.recv({"type": "state", "who": "A"})
    b.recv({"type": "state", "who": "B"})
    check("two concurrent writers never share a file",
          a.sidecar_path != b.sidecar_path,
          f"both wrote {a.sidecar_path}")
    check("...and neither has published while both are open",
          target.read_text() == before, "the target moved under them")
    a.close()
    first = read_transcript(target)
    b.close()
    second = read_transcript(target)
    check("each publish lands WHOLE — last writer wins, no interleaving",
          len(first) == 2 and len(second) == 2
          and first.records[0].get("scenario") == "A"
          and second.records[0].get("scenario") == "B",
          f"{len(first)}/{len(second)} records, "
          f"{first.records[0].get('scenario')}/"
          f"{second.records[0].get('scenario')}")

    import shutil
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
