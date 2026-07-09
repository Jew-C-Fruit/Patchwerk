"""Mic through effects: built-in mic -> low-pass -> echo.

Wear headphones — mic -> speakers will feed back.

    python -m synthbase play patches/mic_fx.py
"""

PATCH = {
    "chain": [
        ("audio_in", {"gain": 1.0}),
        ("lowpass", {"cutoff": 2500}),
        ("echo", {"time": 0.5, "feedback": 0.5, "mix": 0.5}),
    ],
    "bindings": {
        "cc": {
            1: ("echo", "feedback"),
            74: ("lowpass", "cutoff"),
        },
    },
}
