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
            default_in = item.get("coreaudio_default_audio_input_device") == "spaudio_yes"
            default_out = item.get("coreaudio_default_audio_output_device") == "spaudio_yes"
            if in_ch:
                inputs.append({"name": name, "channels": in_ch, "sample_rate": rate,
                               "default": default_in})
            if out_ch:
                outputs.append({"name": name, "channels": out_ch, "sample_rate": rate,
                                "default": default_out})
    return {"inputs": inputs, "outputs": outputs}


def find_rate_matched_input(output_device: str | None) -> str | None:
    """Find an input device whose sample rate matches the output's.

    Used when the default input can't pair with the output (bluetooth
    headset mics run at 16 kHz and can never match). Prefers the built-in
    microphone. Returns a device name or None.
    """
    devices = list_audio_devices()
    outs, ins = devices["outputs"], devices["inputs"]
    if output_device:
        out = next((d for d in outs if d["name"] == output_device), None)
    else:
        out = next((d for d in outs if d.get("default")), None)
    if not out or not out.get("sample_rate"):
        return None
    rate = out["sample_rate"]
    candidates = [d for d in ins if d.get("sample_rate") == rate]
    for d in candidates:
        if "macbook" in d["name"].lower() or "built-in" in d["name"].lower():
            return d["name"]
    return candidates[0]["name"] if candidates else None
