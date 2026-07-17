"""Artifix (live) — the drone and the keys, layered into one patch.

The combined mode: the continuous glass drone (`artifix_gen`) sits underneath
as an always-on bed, and the playable glass voice (`artifix_voice`) blooms
your notes on top — both **sum** into the reverb. Play over the drone; release
and the bed is still there. So it's layered: the drone holds the space, the
keys add motion on top and relax back into it.

    python -m synthbase gui artifix_live

keys -> voice -> artifix_voice; the Living Oscillator breathes the drone's
`morph` (and drives the Sphere). This is the "try it" combined patch — if it
feels right it can become the one Artifix, retiring the drone/play pair.
"""

PATCH = {
    "chain": [
        # drone bed (continuous) + played voice (silent until keys). Two sources
        # SUM into the running bus; the reverb then wraps the pair.
        ("artifix_gen", {"pitch": 110, "morph": 0.30, "harm": 0.10,
                         "bright": 0.36, "res": 0.13, "detune": 0.03,
                         "stereo": 0.55, "phase": 0.60, "amp": 0.30}),
        ("artifix_voice", {"morph": 0.30, "harm": 0.10, "bright": 0.36,
                           "res": 0.13, "detune": 0.03, "stereo": 0.55,
                           "phase": 0.60, "attack": 0.30, "release": 1.60,
                           "amp": 0.34}),
        ("reverb", {"room": 0.85, "damp": 0.40, "mix": 0.22}),
    ],
    "bindings": {"notes_to": "artifix_voice"},   # keys play the voice layer

    "monitors": ["wave", "spectrum", "sphere", "notes"],

    # the drone's morph breathes; played notes layer over that ambient bed
    "living": [
        {"key": "artifix_gen", "param": "morph",
         "life": 0.22, "wander": 0.30, "depth": 0.20, "center": 0.30},
    ],
}
