"""Artifix — the default preset (the "glass" voice, tuned by ear).

    python -m synthbase gui artifix        # then open http://127.0.0.1:8765
    python -m synthbase play patches/artifix.py

The default Artifix sound, dialed in over a listening session: a soft, dark,
near-unison "glass" pad whose movement is a gentle stereo chorus (a slow
moving phase, not a detuned beat), breathing on `morph` via a slow Living
Oscillator, sitting in a deep, calm reverb.

  * Artifix Gen (glass voicing, chorus-phase + level baked in)  ->  reverb  ->  master
  * a slow Living Oscillator breathing on `morph`  (also drives the Sphere)

The reverb is a normal chain effect — pull it out for the dry voice, or open
its card to change the room. The Allocation Intent and the extra modulators
aren't in the default (they pushed the voice away from this calm character);
they're still one palette click away when you want them.

Open the **Spectrum** and **Sphere** monitors to watch it: Spectrum on the
master feed, Sphere auto-bound to the Living Oscillator (two balls on a
radius-conserving sphere). The Note Monitor stays empty here — the generator
is continuous, with no note events.

Schema note: `living`, `allocations`, and `lfos` are optional preset sections
applied once on load by SynthApp._apply_patch_mods.
"""

PATCH = {
    # glass voice -> gentle reverb -> master. The generator's own defaults are
    # already the glass voicing; the chain restates the key ones for clarity.
    "chain": [
        ("artifix_gen", {"pitch": 110, "morph": 0.30, "harm": 0.10,
                         "bright": 0.36, "res": 0.13, "detune": 0.03,
                         "stereo": 0.55, "phase": 0.60, "amp": 0.40}),
        ("reverb", {"room": 0.85, "damp": 0.40, "mix": 0.22}),
    ],
    "bindings": {},

    # slow, shallow breath on the waveform — the "alive" without the harshness.
    # Also the source the Sphere visualizer draws.
    "living": [
        {"key": "artifix_gen", "param": "morph",
         "life": 0.22, "wander": 0.30, "depth": 0.20, "center": 0.30},
    ],
}
