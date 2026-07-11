"""Vox: mic -> pitch shift -> ring mod (off) -> telephone (off) -> echo -> reverb.

Headphones strongly recommended (mic + speakers = feedback).
Toggle ring mod / telephone on for robot / radio voices; use the pitch
shifter's semitones for harmonized doubling (set mix ~0.5).
"""

PATCH = {
    "chain": [
        ("audio_in", {"gain": 1.2}),
        ("pitchshift", {"semitones": 0, "mix": 1.0}),
        ("ringmod", {"mix": 0.8}),
        ("telephone", {}),
        ("echo", {"time": 0.22, "feedback": 0.25, "mix": 0.12}),
        ("reverb", {"room": 0.5, "mix": 0.25}),
    ],
    "bindings": {
        "cc": {1: ("pitchshift", "semitones"), 4: ("pitchshift", "semitones"),
               11: ("audio_in", "gain")},
    },
}
