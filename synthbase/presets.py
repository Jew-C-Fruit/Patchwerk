"""Presets: full settings recall.

A preset is a JSON snapshot of everything performable: which patch, every
instance's params and enabled state (including service instances like the
drone), arp, drone-brain, transport, drum patterns, master volume, and any
LFO assignments. Saved to presets/<name>.json — plain data, git-friendly,
GUI-editable later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _\-]", "", str(name)).strip()
    return name[:48] or "untitled"


def list_presets() -> list[str]:
    PRESETS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json"))


def snapshot(app) -> dict:
    """Capture the app's full performable state. Call under app._lock."""
    modules = {}
    if app.rack:
        for inst in app.rack.instances:
            modules[inst.key] = {
                "settings": {
                    k: v for k, v in inst.settings.items()
                    if k in inst.module.params
                },
                "enabled": inst.enabled,
                "service": inst.service,
            }
    data = {
        "version": 1,
        "patch": app.patch_name,
        "modules": modules,
        "volume": app.master.volume if app.master else 0.8,
        "transport": {
            "bpm": app.transport.bpm,
            "beats_per_bar": app.transport.beats_per_bar,
            "click": app.transport.click_enabled,
            "accent": app.transport.click_accent,
        },
        "arp": {k: v for k, v in (app.arp.settings() if app.arp else {}).items()
                if k not in ("patterns", "divisions")},
        "drone": {k: v for k, v in app.drone.settings().items()
                  if k not in ("everies", "root")},
    }
    if getattr(app, "drums", None):
        data["drums"] = app.drums.snapshot()
    if getattr(app, "lfos", None):
        data["lfos"] = app.lfos.snapshot()
    return data


def save_preset(app, name: str) -> str:
    name = _safe_name(name)
    PRESETS_DIR.mkdir(exist_ok=True)
    with app._lock:
        data = snapshot(app)
    (PRESETS_DIR / f"{name}.json").write_text(json.dumps(data, indent=2))
    return name


def delete_preset(name: str) -> None:
    path = PRESETS_DIR / f"{_safe_name(name)}.json"
    if path.exists():
        path.unlink()


def load_preset(app, name: str) -> None:
    """Apply a preset: switch patch if needed, then restore all state."""
    path = PRESETS_DIR / f"{_safe_name(name)}.json"
    data = json.loads(path.read_text())

    with app._lock:
        # 1. Patch (rebuilds rack, arp, master, router)
        if data.get("patch") and data["patch"] != app.patch_name:
            app._build_patch(data["patch"])

        # 2. Transport
        t = data.get("transport", {})
        app.set_transport(bpm=t.get("bpm"), beats_per_bar=t.get("beats_per_bar"),
                          click=t.get("click"), accent=t.get("accent"))

        # 3. Drone brain (enable state spawns/despawns its instance)
        if app.drone and "drone" in data:
            app.drone.configure(**data["drone"])

        # 4. Arp
        if app.arp and "arp" in data:
            app.arp.configure(**data["arp"])

        # 5. Module params + enabled states
        for key, mod_state in data.get("modules", {}).items():
            try:
                inst = app.rack.find(key)
            except KeyError:
                continue  # module not present in this patch anymore
            settings = {
                k: v for k, v in mod_state.get("settings", {}).items()
                if k in inst.module.params
            }
            if settings:
                app.rack.set_params(key, **settings)
            if bool(mod_state.get("enabled", True)) != inst.enabled:
                app.rack.set_enabled(key, bool(mod_state.get("enabled", True)))

        # 6. Volume
        if app.master and "volume" in data:
            app.master.set_volume(data["volume"])

        # 7. Drums / LFOs (present once those systems exist)
        if getattr(app, "drums", None) and "drums" in data:
            app.drums.restore(data["drums"])
        if getattr(app, "lfos", None) and "lfos" in data:
            app.lfos.restore(data["lfos"])
