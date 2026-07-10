"""FM bells in space: fm_bell -> chorus -> echo -> reverb."""

PATCH = {
    "chain": [
        ("fm_bell", {"freq": 440}),
        ("chorus", {"mix": 0.35}),
        ("echo", {"time": 0.375, "feedback": 0.45, "mix": 0.3}),
        ("reverb", {"room": 0.85, "mix": 0.4}),
    ],
    "bindings": {
        "notes_to": "fm_bell",
        "cc": {1: ("fm_bell", "index"), 4: ("fm_bell", "index"),
               11: ("fm_bell", "amp"), 74: ("fm_bell", "ratio")},
    },
    "arp": {"division": "1/8", "gate": 0.35},
}
