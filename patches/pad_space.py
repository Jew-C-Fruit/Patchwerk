"""Spacey pad: detuned pulses -> drive -> filter -> autopan -> echo -> reverb.

    python -m synthbase gui pad_space
Toggle modules on/off in the GUI to hear what each stage adds.
"""

PATCH = {
    "chain": [
        ("pulse_pad", {"freq": 110}),
        ("drive", {"gain": 2.5, "mix": 0.6}),
        ("lowpass", {"cutoff": 2200}),
        ("autopan", {"rate": 0.25, "depth": 0.6}),
        ("echo", {"time": 0.45, "feedback": 0.35, "mix": 0.25}),
        ("reverb", {"room": 0.8, "mix": 0.35}),
    ],
    "bindings": {
        "notes_to": "pulse_pad",
        "cc": {
            1: ("lowpass", "cutoff"),
            74: ("lowpass", "cutoff"),
            71: ("drive", "gain"),
        },
    },
}
