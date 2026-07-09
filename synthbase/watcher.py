"""Hot reload: watch modules/ and swap running nodes when a file changes.

Edit a module's DSP while sound is playing; on save, the synthdef is
recompiled, sent to the server, and every running instance is replaced
in place (same chain position, settings preserved). A broken edit prints
the error and leaves the previous version running.
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .engine import Engine
from .module import load_module_file
from .rack import Rack

DEBOUNCE_SECONDS = 0.25


class _Handler(FileSystemEventHandler):
    def __init__(self, reloader: "Reloader") -> None:
        self.reloader = reloader

    def on_modified(self, event):
        self._maybe(event)

    def on_created(self, event):
        self._maybe(event)

    def _maybe(self, event) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.suffix == ".py" and not path.name.startswith("_"):
            self.reloader.schedule(path)


class Reloader:
    def __init__(self, engine: Engine, rack: Rack, modules_dir: Path) -> None:
        self.engine = engine
        self.rack = rack
        self.modules_dir = Path(modules_dir)
        self.observer = Observer()
        self._pending: dict[Path, float] = {}
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        self.observer.schedule(_Handler(self), str(self.modules_dir), recursive=False)
        self.observer.start()
        print(f"[reload] watching {self.modules_dir}/ — edit a module and save")

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join(timeout=2)

    # -- debounced scheduling -------------------------------------------------

    def schedule(self, path: Path) -> None:
        with self._lock:
            self._pending[path] = time.monotonic()
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
        for path in paths:
            self.reload_file(path)

    # -- the actual reload ------------------------------------------------------

    def reload_file(self, path: Path) -> None:
        try:
            new_modules = load_module_file(path)
        except Exception:
            print(f"[reload] ERROR in {path.name} — previous version still running:")
            traceback.print_exc(limit=4)
            return
        try:
            self.engine.register(*new_modules)
            for mod in new_modules:
                if self.rack.respawn(mod):
                    print(f"[reload] swapped {mod.key} live")
                else:
                    self.rack.registry[mod.key] = mod
                    print(f"[reload] updated {mod.key} (not currently in rack)")
        except Exception:
            print(f"[reload] ERROR applying {path.name}:")
            traceback.print_exc(limit=4)
