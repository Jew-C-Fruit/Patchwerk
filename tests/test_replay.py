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

    print(f"\n{'PASS' if not FAILURES else 'FAIL'} — {len(FAILURES)} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
