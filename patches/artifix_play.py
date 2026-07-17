"""Artifix (playable) — the glass voice with the keys wired in by default.

Same glass sound as patches/artifix.py, but note-playable: the keys route
keys -> voice -> artifix_voice, so you can play it from the on-screen keys or
a MIDI keyboard the moment it loads (and the Note Monitor lights up when you
play). It's silent until you press a key — hold a note or chord for a
sustained pad, and the Living Oscillator breathes `morph` while you play.
Reverb is on the chain for space.

    python -m synthbase gui artifix_play

The continuous, hands-off drone still lives in patches/artifix.py — this is
the sibling for performing rather than replacing it.
"""

PATCH = {
    "chain": [
        ("artifix_voice", {"morph": 0.30, "harm": 0.10, "bright": 0.36,
                           "res": 0.13, "detune": 0.03, "stereo": 0.55,
                           "phase": 0.60, "attack": 0.30, "release": 1.60,
                           "amp": 0.40}),
        ("reverb", {"room": 0.85, "damp": 0.40, "mix": 0.22}),
    ],
    # keys -> voice -> artifix_voice, wired on load (artifix_voice has a gate)
    "bindings": {"notes_to": "artifix_voice"},

    # default monitors to spawn on first load (Waveform, Spectrum, Sphere, Notes)
    "monitors": ["wave", "spectrum", "sphere", "notes"],

    # the slow morph breath rides along while you play (also drives the Sphere)
    "living": [
        {"key": "artifix_voice", "param": "morph",
         "life": 0.22, "wander": 0.30, "depth": 0.20, "center": 0.30},
    ],
}
