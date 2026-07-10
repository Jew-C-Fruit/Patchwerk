"""Plucked strings through a phaser into a big room."""

PATCH = {
    "chain": [
        ("pluck", {"freq": 220}),
        ("phaser", {"rate": 0.2, "mix": 0.35}),
        ("compressor", {"threshold": 0.25, "ratio": 3.0}),
        ("reverb", {"room": 0.7, "mix": 0.3}),
    ],
    "bindings": {
        "notes_to": "pluck",
        "cc": {1: ("pluck", "damp"), 4: ("pluck", "damp"),
               11: ("pluck", "amp"), 74: ("phaser", "rate")},
    },
    "arp": {"division": "1/16", "gate": 0.9},
}
