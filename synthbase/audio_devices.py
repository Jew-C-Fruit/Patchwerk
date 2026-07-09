"""Enumerate CoreAudio devices (macOS) for the GUI's I/O selectors.

Uses `system_profiler SPAudioDataType -json` — no extra dependencies.
On non-mac platforms this degrades to empty lists (the GUI hides the
selectors), so headless testing elsewhere still works.
"""

from __future__ import annotations

import json
import subprocess
import sys


def list_audio_devices() -> dict:
    """Returns {"inputs": [...], "outputs": [...]} of
    {"name", "channels", "sample_rate"} dicts."""
    if sys.platform != "darwin":
        return {"inputs": [], "outputs": []}
    try:
        raw = subprocess.run(
            ["system_profiler", "SPAudioDataType", "-json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        data = json.loads(raw)
    except Exception:
        return {"inputs": [], "outputs": []}

    inputs, outputs = [], []
    for section in data.get("SPAudioDataType", []):
        for item in section.get("_items", []):
            name = item.get("_name")
            if not name:
                continue
            rate = item.get("coreaudio_device_srate")
            in_ch = item.get("coreaudio_device_input") or 0
            out_ch = item.get("coreaudio_device_output") or 0
            if in_ch:
                inputs.append({"name": name, "channels": in_ch, "sample_rate": rate})
            if out_ch:
                outputs.append({"name": name, "channels": out_ch, "sample_rate": rate})
    return {"inputs": inputs, "outputs": outputs}
