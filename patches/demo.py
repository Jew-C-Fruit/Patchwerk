"""Demo patch: MIDI-playable saw voice -> low-pass -> echo.

Play it:            python -m synthbase play patches/demo.py
CP88 mod wheel      -> filter cutoff (CC 1)
CC 74 (if mapped)   -> filter cutoff too (common assignable knob default)
"""

PATCH = {
    # Which modules, in which order. First must be a source; audio flows
    # top-to-bottom, last stage goes to the hardware output.
    "chain": [
        ("wobble_saw", {"freq": 110, "amp": 0.25}),
        ("lowpass", {"cutoff": 1400}),
        ("echo", {"time": 0.375, "feedback": 0.45, "mix": 0.3}),
    ],
    "bindings": {
        "midi_in": None,          # None = first available MIDI input (the CP88)
        "notes_to": "wobble_saw", # mono, last-note priority
        "cc": {
            1: ("lowpass", "cutoff"),      # CP88 mod lever
            4: ("lowpass", "cutoff"),      # CP88 FC2 jack (wah pedal)
            11: ("wobble_saw", "amp"),     # CP88 FC1 jack (expression)
            74: ("lowpass", "cutoff"),
            71: ("lowpass", "resonance"),
        },
    },
}
