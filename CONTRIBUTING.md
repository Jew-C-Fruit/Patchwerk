# Contributing to Patchwerk

Glad you want to poke at this. Patchwerk is a hobby project, so process is
kept light — but a few habits keep `main` trustworthy for everyone who
clones it.

## Ways to contribute

- **New modules** — the easiest and most encouraged path. A module is one
  small Python file in `modules/`.
- **Bug fixes / engine work** — `synthbase/` (the engine + control plane).
- **GUI work** — `gui/flex.html` + `synthbase/app.py` / `synthbase/server.py`.
- **Docs** — this repo's docs lag the code more often than either of us
  would like.

## Adding a module

1. Fork the repo and branch from `main`.
2. Copy the file in `modules/` closest to what you're building.
3. Follow the contract in [`CLAUDE.md`](CLAUDE.md) — it's short, read the
   whole thing once. The essentials: function name is the module's stable
   identity, always output stereo, every knob goes through `params`.
4. Run the headless tests (below) at minimum. If you have SuperCollider
   installed, confirm it actually sounds right with
   `python -m synthbase play patches/demo.py` or `python -m synthbase gui`.
5. Open a PR against `main` using the PR template.

## Tests

These run headless — no audio hardware or SuperCollider server needed —
which is also what CI runs on every PR:

```bash
python tests/smoke.py        # every module loads, synthdefs compile, params sane, patches parse
python tests/test_graph.py   # graph/wiring logic, control plane, instance ids
python tests/test_looper.py  # loop deck logic
python tests/gui_check8.py   # GUI checks via headless Playwright (pip install playwright; playwright install chromium)
```

Run whichever are relevant to what you touched; `smoke.py` is the fast
baseline and worth running for almost any change. `test_mixed_sources.py`,
`diag_*.py`, `hear_check.py`, and `probe_ws.py` need a **live** server
(`python -m synthbase gui` actually running with real audio) — they're
Mac-only manual checks, not something CI can run, so use them locally when
relevant but don't expect them in a CI log.

### What CI checks, and what it can't

GitHub Actions runs the headless suite above on every PR. No audio hardware
exists in CI, so it verifies structure — modules load, synthdefs compile,
param ranges are sane, wiring logic behaves — not whether anything sounds
good. A green check means your PR won't crash the engine or corrupt the
graph. It doesn't mean it sounds right; that's still a human ear's job.

## PR checklist

- [ ] Relevant headless tests pass (see above)
- [ ] New/changed modules follow the `CLAUDE.md` contract
- [ ] You describe what you tested, including whether you confirmed real
      sound output — CI can't hear it, so say so explicitly if you couldn't
      test on real hardware

## Review and merge

Cole reviews and merges PRs — for now, the only gatekeeper. `main` is
protected and requires the CI check to pass before merge, including for
Cole's own changes, so everything goes through a PR.

## Reporting bugs or proposing a module without writing code

Open an issue — templates are provided for both a bug report and a module
proposal. Check [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) first;
a handful of known-sharp edges are already documented there.

## Code style

- Match `CLAUDE.md`'s module rules.
- Keep the base thin — no heavyweight wrapper abstractions around supriya.
- One module per file unless variants truly belong together.
- Don't block in MIDI callbacks.

## If this grows

Right now every module lives together in `modules/`, gated only by PR review
and CI. If community contributions grow enough that quality or maturity
varies a lot, splitting into something like `modules/community/` for
less-vetted contributions is a reasonable next step — worth revisiting if
that becomes a real problem, not something to preempt now.

## Local setup

See the README's Setup section. You'll need SuperCollider installed (and
realistically a Mac, though nothing is deliberately macOS-only besides the
setup instructions) to hear anything; the headless tests don't need it.
