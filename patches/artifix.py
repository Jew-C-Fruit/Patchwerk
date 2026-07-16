"""Artifix — the full package preset ("everything on", for diagnosing).

    python -m synthbase gui artifix        # then open http://127.0.0.1:8765
    python -m synthbase play patches/artifix.py

Loads the whole Artifix group already wired, so you can hear and see it work
end to end without patching anything by hand:

  * Artifix Gen (continuous source) -> master
  * a Living Oscillator breathing on `morph`   (bounded-aperiodic drift)
  * an Allocation Intent holding a conserved balance across three dims —
    harm / bright / res move as one budget (sum of m_i^2 = r^2)
  * a slow LFO shimmering `detune`             (an obvious spectrum wiggle)

Once it's up, click **Spectrum** and **Sphere** in the palette:
  - Spectrum, left unwired, watches the global master feed — the Gen's sound.
  - Sphere auto-binds to the Living Oscillator and draws its trajectory.

Heads-up on the Note Monitor: Artifix Gen is a *continuous* generator with
no note events, so the Note Monitor stays empty on this patch — it's meant
for note-played chains (keys -> voice -> source). For Artifix, Spectrum and
Sphere are the monitors that show it moving.

Schema note: `living`, `allocations`, and `lfos` are OPTIONAL preset sections
applied once on load by SynthApp._apply_patch_mods. Ordinary chain/bindings
patches simply don't include them.
"""

PATCH = {
    # One source, straight to master. No effects — this is the Gen on its own
    # so the visualizers show its raw voice.
    "chain": [
        ("artifix_gen", {"pitch": 110, "morph": 0.5, "harm": 0.35,
                         "bright": 0.5, "res": 0.25, "detune": 0.35,
                         "stereo": 0.5, "amp": 0.22}),
    ],
    "bindings": {},

    # --- Artifix modulation preset (applied once, on load) ------------------

    # The Living Oscillator: a Thomas-attractor drift that never quite repeats.
    # Wired to `morph`, the waveform slowly breathes between triangle and saw.
    "living": [
        {"key": "artifix_gen", "param": "morph",
         "life": 0.50, "wander": 0.30, "depth": 0.40},
    ],

    # The Allocation Intent: one conserved "intensity" split across the six
    # dimensions. Three of them are wired to Gen params, so as the budget
    # favours one the others yield — a coordinated tone balance, not movement.
    # Dim slots: 0 wave, 1 harm, 2 filt, 3 stereo, 4 res, 5 det.
    "allocations": [
        {"r": 1.0, "w": [0.50, 0.55, 0.50, 0.40, 0.45, 0.30],
         "targets": [
             {"slot": 1, "key": "artifix_gen", "param": "harm"},    # harm dim
             {"slot": 2, "key": "artifix_gen", "param": "bright"},  # filt dim
             {"slot": 4, "key": "artifix_gen", "param": "res"},     # res dim
         ]},
    ],

    # A plain LFO on `detune` — the shimmer knob — so the spectrum visibly
    # wiggles even at a glance. Proves the LFO path too.
    "lfos": [
        {"key": "artifix_gen", "param": "detune",
         "rate": 0.15, "shape": 0, "depth": 0.45},
    ],
}
