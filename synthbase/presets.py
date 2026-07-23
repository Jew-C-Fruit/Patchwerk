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
    """Capture the app's full performable state. Call under app._lock.

    v2 (v5 code): modules are keyed by INSTANCE id and carry their module
    "type"; loading a pre-v2 preset (no type) treats the key as the type."""
    modules = {}
    if app.rack:
        for inst in app.rack.instances:
            modules[inst.key] = {
                "type": inst.type,
                "settings": {
                    k: v for k, v in inst.settings.items()
                    if k in inst.module.params
                },
                "enabled": inst.enabled,
                "service": inst.service,
            }
    data = {
        "version": 2,
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
        "drone": {k: v for k, v in app._legacy_drone_settings().items()
                  if k not in ("everies", "root")},
        "tonics": [{k: v for k, v in d.settings().items()
                    if k in ("id", "every", "octave", "memory",
                             "bass", "listening", "deck_feed")}
                   for d in app.tonics.values()],
        "literals": [{k: v for k, v in d.settings().items()
                      if k in ("id", "every", "extract", "place",
                               "fold_octave", "transpose", "hold_on_empty")}
                     for d in getattr(app, "literals", {}).values()],
        "keyshifts": [{k: v for k, v in ks.settings().items()
                       if k in ("id", "key", "length", "steps")}
                      for ks in getattr(app, "keyshifts", {}).values()],
        "buttons": [{k: v for k, v in b.settings().items()
                     if k in ("id", "binding", "latch")}
                    for b in getattr(app, "buttons", {}).values()],
        "clocks": [{k: v for k, v in c.settings().items()
                    if k in ("id", "division")}
                   for c in getattr(app, "clocks", {}).values()],
        "relays": [{"id": r.id, "closed": bool(r.closed)}
                   for r in getattr(app, "relays", {}).values()],
    }
    if getattr(app, "drums", None):
        data["drums"] = app.drums.snapshot()
    if getattr(app, "lfos", None):
        data["lfos"] = app.lfos.snapshot()
    if getattr(app, "thresholds", None):
        data["thresholds"] = app.thresholds.snapshot()
    if getattr(app, "gates", None):
        data["gates"] = app.gates.snapshot()
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
    _apply(app, data)


def _apply(app, data: dict) -> None:
    with app._lock:
        # 1. Patch (rebuilds rack, arp, master, router)
        if data.get("patch") and data["patch"] != app.patch_name:
            app._build_patch(data["patch"])

        # 2. Transport
        t = data.get("transport", {})
        app.set_transport(bpm=t.get("bpm"), beats_per_bar=t.get("beats_per_bar"),
                          click=t.get("click"), accent=t.get("accent"))

        # 3. Tonic derivers, then the legacy drone pair (enable state
        #    spawns/despawns its compat instance)
        for t in data.get("tonics", []):
            tid = t.get("id") or "tonic"
            app.spawn_tonic(want_id=tid)
            # old presets may still carry "stickiness" — silently ignored
            app.set_tonic(tid, every=t.get("every"), octave=t.get("octave"),
                          memory=t.get("memory"), bass=t.get("bass"),
                          listening=t.get("listening"),
                          deck_feed=t.get("deck_feed"))
        for t in data.get("literals", []):
            lid = t.get("id") or "literal"
            app.spawn_literal(want_id=lid)
            app.set_literal(lid, every=t.get("every"),
                            extract=t.get("extract"), place=t.get("place"),
                            fold_octave=t.get("fold_octave"),
                            transpose=t.get("transpose"),
                            hold_on_empty=t.get("hold_on_empty"))
        if "drone" in data:
            app.set_drone(**data["drone"])
        for k in data.get("keyshifts", []):
            kid = k.get("id") or "keyshift"
            app.spawn_keyshift(want_id=kid)
            app.set_keyshift(kid, key=k.get("key"), length=k.get("length"),
                             steps=k.get("steps"))
        for b in data.get("buttons", []):
            bid = b.get("id") or "button"
            app.spawn_button(want_id=bid)
            app.set_button(bid, binding=b.get("binding"),
                           latch=b.get("latch"))
        for c in data.get("clocks", []):
            cid = c.get("id") or "clock"
            app.spawn_clock(want_id=cid)
            app.set_clock(cid, division=c.get("division"))

        # 4. Arp
        if app.arp and "arp" in data:
            app.arp.configure(**data["arp"])

        # 5. Module params + enabled states. Preset keys are instance ids
        #    (old presets: type keys — rack.find falls back to the first
        #    instance of that type).
        for key, mod_state in data.get("modules", {}).items():
            try:
                inst = app.rack.find(key)
            except KeyError:
                continue  # module not present in this patch anymore
            # a swapped Instrument: the saved type differs from what's
            # running under this id — swap in place BEFORE applying settings
            want_type = mod_state.get("type")
            if (want_type and want_type != inst.type
                    and want_type in app.registry
                    and app.registry[want_type].kind == inst.module.kind):
                try:
                    app.swap_synth(inst.key, want_type)
                    inst = app.rack.find(key)
                except Exception:  # noqa: BLE001
                    pass
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

        # 7. Drums / LFOs / thresholds (present once those systems exist).
        #    Thresholds AFTER lfos: a restored CV-in needs its source LFO live.
        if getattr(app, "drums", None) and "drums" in data:
            app.drums.restore(data["drums"])
        if getattr(app, "lfos", None) and "lfos" in data:
            app.lfos.restore(data["lfos"])
        if getattr(app, "thresholds", None) and "thresholds" in data:
            app.thresholds.restore(data["thresholds"])
        if getattr(app, "gates", None) and "gates" in data:
            app.gates.restore(data["gates"])   # legacy "switches" ignored
        # relays BEFORE the resume ctl/graph wire replay (apply_resume runs
        # after _apply): circuit endpoints must exist for wires to re-add
        if hasattr(app, "spawn_relay"):
            for r in data.get("relays", []):
                rid = r.get("id") or "relay"
                app.spawn_relay(want_id=rid)
                app.set_relay(rid, closed=bool(r.get("closed")))


# -- restart resume: snapshot + wiring, restored automatically on boot --------
# (the GUI's ⟳ posts /restart; the server writes this file and re-execs)

RESUME_PATH = Path(__file__).resolve().parent.parent / ".resume.json"


def write_resume(app) -> None:
    """Preset snapshot PLUS the graph: audio/ctl wires, voice targets, drums
    routing — everything a preset alone doesn't carry back across a restart."""
    with app._lock:
        data = snapshot(app)
        data["resume"] = {
            "graph_wires": (app.graph_wires if app.graph_wires is not None
                            else (app.rack.audio_wires() if app.rack else [])),
            "ctl_wires": [dict(w) for w in app.ctl_wires],
            "voice_targets": {vid: getattr(v, "target_key", None)
                              for vid, v in app.voices.items()},
            "drums_target": (app.drums.target
                             if getattr(app, "drums", None) else None),
        }
    RESUME_PATH.write_text(json.dumps(data, indent=2))


def apply_resume(app) -> bool:
    """Boot hook: if a restart left a resume file, restore the module
    population, params, wiring and voice targets, then delete the file."""
    if not RESUME_PATH.exists():
        return False
    try:
        data = json.loads(RESUME_PATH.read_text())
    except Exception:  # noqa: BLE001
        RESUME_PATH.unlink(missing_ok=True)
        return False
    RESUME_PATH.unlink(missing_ok=True)

    # 1. respawn instances the patch itself didn't bring (spawned modules);
    #    ids are free on a fresh boot, so alloc gives back the same names
    for key, ms in data.get("modules", {}).items():
        if ms.get("service"):
            continue
        try:
            app.rack.find(key)
        except KeyError:
            try:
                # respawn by ID whenever the id's base type still exists, so
                # wires keep resolving; a swapped Instrument (stored type !=
                # id's base) gets its type corrected by _apply's swap pass
                base = str(key).split(".", 1)[0]
                app.edit_chain("add",
                               key if base in app.registry
                               else ms.get("type", key))
            except Exception:  # noqa: BLE001
                pass

    # 2. params / enabled / transport / arp / drums / lfos / volume
    try:
        _apply(app, data)
    except Exception:  # noqa: BLE001
        pass

    # 3. the graph itself
    r = data.get("resume", {})
    for w in r.get("graph_wires", []):
        try:
            if w.get("to") is None:
                app.graph_wire("remove", w["from"], None)
            else:
                app.graph_wire("add", w["from"], w["to"])
        except Exception:  # noqa: BLE001
            pass
    for w in r.get("ctl_wires", []):
        try:
            app.set_ctl_wire("add", w.get("from"), w.get("to"))
        except Exception:  # noqa: BLE001
            pass
    for vid, tgt in (r.get("voice_targets") or {}).items():
        if not tgt:
            continue
        if vid != "voice" and vid not in app.voices:
            try:
                app.spawn_voice()
            except Exception:  # noqa: BLE001
                pass
        try:
            app.set_voice_target(tgt, vid)
        except Exception:  # noqa: BLE001
            pass
    if r.get("drums_target") and getattr(app, "drums", None):
        try:
            app.set_drums(target=r["drums_target"])
        except Exception:  # noqa: BLE001
            pass
    return True
