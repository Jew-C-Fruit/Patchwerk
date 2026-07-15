## What this does



## Testing

- [ ] `python tests/smoke.py` passes
- [ ] `python tests/test_graph.py` / `test_looper.py` pass (if you touched engine/rack/looper/keyshift/drone)
- [ ] `python tests/gui_check8.py` passes (if you touched the GUI)
- [ ] Confirmed on real hardware with `python -m synthbase test` / `play` / `gui` — describe what you heard, or say if you couldn't test audio

## New module? (delete this section if not applicable)

- [ ] Follows the module contract in `CLAUDE.md` (stereo out, params declared with sane ranges, appropriate `curve`)
- [ ] One module per file
- [ ] Function name is stable (it's the module's identity for patches/hot reload)
