"""The installed Patchwerk app: first-run wizard, then engine supervisor.

This is `CFBundleExecutable`'s payload on macOS and what the Start Menu
shortcut runs on Windows. It is what someone who will never open a terminal
double-clicks.

WHY THE WIZARD IS A WEB PAGE. Patchwerk's UI is already a browser at
127.0.0.1:8765, so the setup steps live in the same place the product does:
the browser opens once, shows progress, and becomes the app. The alternative
was Tk, which is exactly the dependency most likely to be missing or broken
on the machines this has to work on, and which would have given us a second
look-and-feel for no gain. `http.server` is stdlib and always there.

WHAT IT DOES, in order:

1. **Already running?** If something answers on the app port, this is a
   second double-click — hand the browser to the running instance and exit.
   No stale-instance killing behind the user's back.
2. **SuperCollider.** Discovered, never assumed (`boot_core.find_scsynth`).
   Missing means a wizard page with the download link and a re-check button,
   not a traceback. The found path is passed to the engine via
   `SUPRIYA_SERVER_EXECUTABLE` — the user never edits PATH.
3. **Audio permission (macOS, first run).** Explained, then triggered, then
   VERIFIED. See the block comment on `_grant_microphone`.
4. **Engine.** `python -m synthbase gui <patch> --port <n> --no-browser`,
   with a bounded wait on an HTTP 200 and a real diagnosis if it never comes.
5. **Supervise.** Hold the child, reap it and any scsynth it leaves behind.

Stdlib only. The bundled interpreter has supriya and friends installed, but
this file must be able to render "SuperCollider is missing" on a machine
where importing the engine would fail, so it imports nothing from the app.
"""

from __future__ import annotations

import html
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import boot_core as bc  # noqa: E402

APP_PORT = int(os.environ.get("PATCHWERK_PORT", "8765"))
DEFAULT_PATCH = os.environ.get("PATCHWERK_PATCH", "pad_space")
SC_DOWNLOAD = "https://supercollider.github.io/downloads"

#: The app tree (synthbase/, modules/, gui/, ...). The build stages it beside
#: this file; a dev checkout runs from the repo root two levels up.
APP_ROOT = HERE / "app"
if not (APP_ROOT / "synthbase").is_dir():
    APP_ROOT = HERE.parent.parent



#: Launched from Finder or a Start Menu shortcut, this process has nowhere to
#: print: stdout is discarded. So everything also goes to a file, and that
#: file is the first thing to ask for when someone says "it won't start".
#: The ENGINE's log is separate (`patchwerk.log`) — this one is the launcher's
#: own decisions: which scsynth, which permission verdict, how long the boot
#: took.
LOG_PATH = bc.CONFIG_DIR / "launcher.log"


def _log(msg: str) -> None:
    line = f"[patchwerk] {msg}"
    print(line, flush=True)
    try:
        bc.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        pass          # a log we cannot write must never stop the launch


# -- state --------------------------------------------------------------------

class State:
    """What the wizard page renders, and the only thing it renders.

    One lock, one dict. The worker thread mutates; the HTTP handler reads.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.step = "start"
        self.detail = ""
        self.error = ""
        self.scsynth = ""
        #: {kind, title, steps, note, open_target, open_label} from
        #: bc.remedy() — what the user should DO about a failure.
        self.remedy: dict = {}
        self.app_url = ""
        self.done = False
        #: set by the page to unblock a step that is waiting on the human
        self.gate = threading.Event()
        self.gate_answer = ""

    def set(self, **kw) -> None:
        with self.lock:
            changed = "step" in kw and kw["step"] != self.step
            for k, v in kw.items():
                setattr(self, k, v)
        if changed:
            _log(f"step -> {kw['step']}")

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "step": self.step, "detail": self.detail, "error": self.error,
                "scsynth": self.scsynth, "remedy": self.remedy,
                "app_url": self.app_url, "done": self.done,
            }

    def wait_for_human(self) -> str:
        """Block the worker until the page answers. Returns the answer."""
        self.gate.clear()
        self.gate.wait()
        return self.gate_answer

    def answer(self, what: str) -> None:
        self.gate_answer = what
        self.gate.set()


STATE = State()
ENGINE: subprocess.Popen | None = None

#: scsynth pids that were already running before we started. They belong to
#: something else, so we never kill them — see `shutdown`.
FOREIGN: set[int] = set()

#: Set when the app should end. The app no longer exits merely because the
#: engine did — it stays up to explain itself — so quitting is now explicit.
QUIT = threading.Event()


# -- config -------------------------------------------------------------------

def _config() -> dict:
    try:
        return json.loads(bc.CONFIG_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_config(**kw) -> None:
    cfg = _config()
    cfg.update(kw)
    try:
        bc.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        bc.CONFIG_PATH.write_text(json.dumps(cfg, indent=1))
    except OSError:
        pass


# -- step 2: SuperCollider ----------------------------------------------------

def _ensure_supercollider() -> Path:
    """Loop until SuperCollider is found or the user gives up.

    DETECT-AND-GUIDE, not bundle-and-ship. The reasoning is in
    packaging/README.md; the consequence here is that "not installed" is a
    first-class wizard page with a re-check, so the user installs SC in
    another window and clicks a button — no reinstall, no restart.
    """
    while True:
        sc = bc.find_scsynth()
        if sc is not None:
            STATE.set(scsynth=str(sc))
            _log(f"SuperCollider: {sc}")
            return sc
        STATE.set(step="need-supercollider", detail="", error="")
        answer = STATE.wait_for_human()
        if answer.startswith("path:"):
            given = answer[5:].strip()
            cand = Path(given)
            # A user handed a folder or a .app: dig for the binary inside it.
            for probe in (cand,
                          cand / "Contents/Resources/scsynth",
                          cand / "scsynth",
                          cand / "scsynth.exe"):
                if probe.is_file():
                    bc.remember_scsynth(str(probe))
                    break
            else:
                STATE.set(error=f"No scsynth at {given}")


# -- there is no permission step, and that is the fix -------------------------
#
# This used to gate first run behind a microphone grant: explain, trigger,
# verify. It was wrong twice over, and Cole hit both on 2026-07-26 —
# "throws a permission error, but doesn't indicate how to grant permission".
#
# 1. PATCHWERK DOES NOT NEED THE PERMISSION TO MAKE SOUND. Item 38 proved the
#    working configuration is `-H "<output-only device>" -i 0`, which needs
#    "no permission, no prompt and no user interaction at all". Demanding a
#    grant before the app would start was asking for something the product
#    does not require — so the cleanest fix is not a better error message,
#    it is deleting the gate.
#
# 2. THE GATE MISREPORTED UNRELATED FAILURES AS PERMISSION PROBLEMS. On
#    Cole's Mac the probe died on a sample-rate mismatch, and the screen
#    still said "Audio input is not available" over scsynth's own advice to
#    run `s.options.sampleRate = <rate>;` — sclang the user cannot run. The
#    one actionable remedy on screen pointed at the Microphone pane, which
#    would have fixed nothing.
#
# So: first run is SuperCollider, then the engine. Nothing else.
#
# The permission still gets requested — by the thing that actually wants it,
# when it wants it. The engine's own probe opens an input-bearing device, and
# because the bundle now carries NSMicrophoneUsageDescription, a compiled
# stub and the audio-input entitlement (see packaging/README.md), macOS shows
# a real dialog naming Patchwerk at that moment. If the user declines,
# item 38 falls back to output-only and says so through `boot_note` in the
# GUI. One surface, owned by the code that needs it.
#
# What this file owns instead: when the engine genuinely will not start, say
# WHY in the user's language and give them the button — see `bc.remedy`.


# -- the engine ---------------------------------------------------------------

def _start_engine(sc: Path) -> None:
    global ENGINE
    STATE.set(step="starting", detail="Starting the audio engine…", error="")

    # Clear ONLY orphaned servers — the leftovers of a Patchwerk that
    # crashed, which still hold the audio device. A scsynth with a live
    # parent belongs to something else (the SuperCollider IDE, another
    # Patchwerk) and is not ours to kill.
    global FOREIGN
    stale = bc.kill_scsynth(bc.scsynth_orphans())
    if stale:
        _log(f"cleared {stale} orphaned scsynth process(es)")
    # Everything still standing predates us, so it survives our shutdown too.
    FOREIGN = set(bc.scsynth_alive())
    if FOREIGN:
        _log(f"leaving {len(FOREIGN)} scsynth process(es) alone — not ours")

    python = sys.executable
    argv = [python, "-u", "-m", "synthbase", "gui", DEFAULT_PATCH,
            "--port", str(APP_PORT), "--no-browser"]
    logdir = bc.CONFIG_DIR
    logdir.mkdir(parents=True, exist_ok=True)
    logpath = logdir / "patchwerk.log"
    _log(f"engine: {' '.join(argv)}  (cwd={APP_ROOT}, log={logpath})")

    logfile = open(logpath, "w")
    ENGINE = subprocess.Popen(
        argv, cwd=str(APP_ROOT), env=bc.child_env({"PYTHONPATH": str(APP_ROOT)}),
        stdout=logfile, stderr=subprocess.STDOUT,
    )

    t0 = time.monotonic()
    while time.monotonic() - t0 < bc.BOOT_TIMEOUT:
        if ENGINE.poll() is not None:
            # shutdown() FIRST, and not only for tidiness: it clears ENGINE,
            # and the supervisor loop in main() treats "ENGINE exists and has
            # exited" as "the app is over". Without this the whole app quits
            # a second after the failure page appears, so the user watches
            # Patchwerk vanish instead of reading why it could not start.
            detail = _tail(logpath)
            _log("engine stopped while starting")
            shutdown()
            _fail("The audio engine stopped while starting.", detail)
            return
        if bc.http_ok(APP_PORT):
            url = f"http://127.0.0.1:{APP_PORT}/"
            STATE.set(step="ready", app_url=url, done=True, detail="")
            _log(f"ready at {url} in {time.monotonic() - t0:.1f}s")
            return
        time.sleep(0.4)

    # Bounded, then diagnosed — the whole point of not calling boot() blind.
    #
    # This timeout is load-bearing until item 38 merges: on `main` today,
    # `Server().boot()` has no timeout of its own and the CoreAudio stall
    # raises nothing, so without this the app would sit on a spinner forever.
    # Here it becomes a readable failure with the reason attached.
    err = (f"No response from the engine after {bc.BOOT_TIMEOUT:.0f} seconds.")
    _log(err)
    # Do not leave a hung engine and its scsynth spinning behind a failed
    # page. Clean state, then let the user retry deliberately.
    shutdown()
    _fail(err, _tail(logpath))


#: Engine-log markers that mean the failure was AUDIO, and so that an audio
#: probe can explain it. Anything else — a bad patch name, an import error, a
#: port clash — is not diagnosed by asking scsynth how it feels.
_AUDIO_MARKERS = (
    "servercannotboot", "could not initialize audio", "sample rate",
    "scsynth", "coreaudio", "supriya.exceptions",
)


def _devices() -> dict | None:
    """The audio device list, for naming names in a remedy.

    Reuses `synthbase.audio_devices` — which is already on main and already
    caches its `system_profiler` call (that call takes seconds; CLAUDE.md is
    explicit about never making it per-snapshot). This is the ONE place the
    launcher imports from the app, and only to describe a failure, never to
    choose a device: device SELECTION is `synthbase/audio_session.py`'s job
    (item 38).

    Returns None on any failure — a remedy that cannot name devices falls
    back to generic wording rather than inventing detail.
    """
    try:
        if str(APP_ROOT) not in sys.path:
            sys.path.insert(0, str(APP_ROOT))
        from synthbase.audio_devices import list_audio_devices
        return list_audio_devices(force=False)
    except Exception as exc:  # noqa: BLE001
        _log(f"could not read the audio device list: {exc}")
        return None


def _fail(error: str, log_text: str) -> None:
    """Present a failure the user can ACT on.

    The old version put `error` and raw scsynth output on screen and stopped
    there. A user reading "could not initialize audio" followed by sclang
    they cannot run has no next step — which is the whole of Cole's report.
    So an audio failure is probed, classified and turned into named steps
    plus a button that opens the exact place to fix it.

    But ONLY an audio failure. Running the audio probe for every failure and
    printing its verdict would re-commit the original sin in a new place:
    a bad patch name would come back as "your sample rates disagree", which
    is confident, tidy and wrong. If the engine log does not look like audio,
    say plainly that we do not know and show the log.
    """
    blob = (log_text or "").strip()
    # A COMPLETELY EMPTY engine log is not "no information" — it is the
    # signature of a HANG rather than a crash. The engine prints nothing
    # until scsynth answers, so zero bytes means it blocked inside CoreAudio
    # and never came back (`tests/rig.py::_boot_hint` reads it the same way).
    # That is emphatically an audio failure, so probe it.
    hung = not blob
    if hung or any(m in blob.lower() for m in _AUDIO_MARKERS):
        chk = bc.scsynth_check(timeout=12.0, input_channels=2,
                               kill_first=False)
        # kill_first=False above matters: clearing servers before probing
        # would erase the very evidence that someone else holds the device.
        others = len([p for p in bc.scsynth_alive() if p in FOREIGN])
        fix = bc.remedy(chk, others_running=others, devices=_devices())
        head = ("The engine printed nothing at all before it stopped "
                "responding, which means it hung rather than crashed.\n"
                if hung else "Engine log:\n" + blob + "\n")
        detail = (head + "\n" + bc.diagnose(chk)).strip()
    else:
        detail = "Engine log:\n" + blob
        fix = {
            "kind": "engine",
            "title": "Patchwerk started, but the audio engine stopped",
            "steps": ["Press Try again.",
                      "If it keeps failing, open Technical details below and "
                      "send that text — the cause is in there."],
            "note": "This is not an audio-device or permissions problem: "
                    "SuperCollider was never the thing that failed.",
            "open_target": None, "open_label": "",
        }
    _log(f"failure classified as '{fix['kind']}': {fix['title']}")
    STATE.set(step="failed", error=error, remedy=fix, detail=detail)


def _tail(path: Path, n: int = 25) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


# -- the worker ---------------------------------------------------------------

def _run() -> None:
    """Drive the steps, and let a failure be retried rather than final.

    A first launch fails for reasons the user can go and fix in another
    window — SuperCollider not installed yet, a permission not granted, a
    device in use by something else. Making them quit and relaunch the app to
    re-test their fix is a bad answer when the retry is one loop away.
    """
    while True:
        try:
            sc = _ensure_supercollider()
            _start_engine(sc)
            if STATE.snapshot()["step"] == "ready":
                _watch_engine()
        except Exception as exc:  # noqa: BLE001
            import traceback
            _log(f"failed: {exc}")
            shutdown()
            _fail(str(exc), traceback.format_exc())
        if STATE.snapshot()["step"] != "failed":
            return
        if STATE.wait_for_human() != "restart":
            QUIT.set()
            return


def _watch_engine() -> None:
    """Hold while the engine runs; turn its death into a page, not a vanishing.

    Previously the supervisor simply exited when the engine exited, so an
    engine that died AFTER startup — a Bluetooth headset reconnecting at a
    different sample rate is enough, and it happened during testing — took
    the whole app down with no window, no message and nothing to click. From
    the user's side Patchwerk just disappears, which is the same dead end as
    an error with no remedy.
    """
    while not QUIT.is_set():
        if ENGINE is None or ENGINE.poll() is not None:
            log = _tail(bc.CONFIG_DIR / "patchwerk.log")
            _log("the engine stopped after startup")
            shutdown()
            _fail("The audio engine stopped while Patchwerk was running.",
                  log)
            return
        time.sleep(0.5)


# -- shutdown -----------------------------------------------------------------

def shutdown(*_a) -> None:
    """Take the engine and OUR scsynth down together. Never orphan audio.

    "Ours" is deliberate: anything that was already running when we started
    (`FOREIGN`) is somebody else's server and stays up. Quitting Patchwerk
    must not silence a SuperCollider session the user had open beside it.
    """
    global ENGINE
    if ENGINE is not None and ENGINE.poll() is None:
        _log("stopping the engine")
        bc.reap(ENGINE)
    mine = [p for p in bc.scsynth_alive() if p not in FOREIGN]
    if mine and bc.kill_scsynth(mine):
        _log(f"scsynth outlived its engine — cleared {len(mine)}")
    ENGINE = None


# -- the wizard page ----------------------------------------------------------

PAGE = """<!doctype html><meta charset=utf-8>
<title>Patchwerk</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#111318;color:#e7e9ee;
      font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      display:flex;align-items:center;justify-content:center;min-height:100vh}
 .card{width:min(620px,92vw);background:#181b22;border:1px solid #262b36;
       border-radius:14px;padding:30px 34px}
 h1{margin:0 0 2px;font-size:19px;letter-spacing:.02em}
 .sub{color:#7f8797;font-size:13px;margin-bottom:22px}
 h2{font-size:16px;margin:0 0 10px}
 p{margin:0 0 14px;color:#c3c9d6}
 .muted{color:#8b93a3;font-size:13px}
 pre{background:#0e1016;border:1px solid #232833;border-radius:8px;
     padding:11px 13px;font-size:11.5px;line-height:1.45;color:#9aa3b4;
     white-space:pre-wrap;max-height:230px;overflow:auto}
 button{font:inherit;font-size:14px;padding:9px 17px;border-radius:8px;
        border:1px solid #333a49;cursor:pointer;margin:5px 8px 0 0}
 .go{background:#4d7cfe;border:1px solid #4d7cfe;color:#fff}
 .alt{background:#222734;border:1px solid #333a49;color:#dfe3ec}
 a{color:#7fa4ff}
 input{font:inherit;font-size:13px;padding:8px 10px;border-radius:7px;
       border:1px solid #333a49;background:#0e1016;color:#e7e9ee;width:100%;
       box-sizing:border-box;margin:6px 0 2px}
 .spin{display:inline-block;width:13px;height:13px;margin-right:9px;
       border:2px solid #39415400;border-top-color:#4d7cfe;
       border-right-color:#4d7cfe;border-radius:50%;
       animation:s .8s linear infinite;vertical-align:-2px}
 @keyframes s{to{transform:rotate(360deg)}}
 .warn{border-left:3px solid #d9a13b;padding-left:12px}
 ol.steps{margin:0 0 18px;padding-left:22px;color:#dfe3ec}
 ol.steps li{margin-bottom:7px}
 summary{cursor:pointer;margin-bottom:8px}
</style>
<div class=card><h1>Patchwerk</h1><div class=sub id=sub>Starting up…</div>
<div id=body></div></div>
<script>
const $=document.getElementById.bind(document);
let last="";
function say(w,extra){fetch("/answer",{method:"POST",
  headers:{"Content-Type":"application/x-www-form-urlencoded"},
  body:"a="+encodeURIComponent(extra?w+":"+extra:w)});}
// Opens the exact System Settings pane / utility named by the remedy. The
// browser cannot follow an x-apple.systempreferences: URL from a page, so
// the launcher does it with `open` on our behalf.
async function openPane(t){
  const r=await fetch("/open?t="+encodeURIComponent(t),{method:"POST"});
  const hint=$("hint");
  if(hint&&!r.ok) hint.textContent=
    "Could not open that automatically — open it from Applications instead.";
}
function esc(s){const d=document.createElement("div");d.textContent=s||"";
  return d.innerHTML;}
function render(s){
  const key=JSON.stringify(s); if(key===last) return; last=key;
  if(s.step==="ready"&&s.app_url){location.href=s.app_url;return;}
  const b=$("body"); let h="";
  const det=s.detail?"<pre>"+esc(s.detail)+"</pre>":"";
  const err=s.error?"<p class=warn>"+esc(s.error)+"</p>":"";
  switch(s.step){
   case "need-supercollider":
    $("sub").textContent="One thing is missing";
    h=`<h2>SuperCollider is not installed</h2>
       <p>Patchwerk's audio engine is SuperCollider's <code>scsynth</code>.
       It is a free, separate download — install it, then come back here and
       press <b>Check again</b>. Nothing else needs reinstalling.</p>
       <p><a href="${esc(s.scdl)}" target=_blank rel=noopener>Download
       SuperCollider →</a></p>${err}
       <button class=go onclick='say("recheck")'>Check again</button>
       <details style="margin-top:14px"><summary class=muted>Already
       installed somewhere unusual?</summary>
       <input id=p placeholder="/Applications/SuperCollider.app">
       <button class=alt onclick='say("path",$("p").value)'>Use this
       path</button></details>`;
    break;
   case "failed": {
    // The remedy IS the page. `error` is what went wrong; the steps are what
    // to do about it, and they lead the layout — a user who is stuck needs
    // the next action above the fold, not a log.
    const r=s.remedy||{};
    $("sub").textContent="Patchwerk needs one thing fixed";
    const steps=(r.steps||[]).map(x=>"<li>"+esc(x)+"</li>").join("");
    const btn=r.open_target
      ? `<button class=go onclick='openPane("${esc(r.open_target)}")'>
           ${esc(r.open_label||"Open settings")}</button>` : "";
    h=`<h2>${esc(r.title||"Patchwerk could not start")}</h2>
       ${r.note?"<p>"+esc(r.note)+"</p>":""}
       <ol class=steps>${steps}</ol>
       ${btn}<button class="${btn?"alt":"go"}" onclick='say("restart")'>Try
       again</button>
       <button class=alt onclick='say("quit")'>Quit Patchwerk</button>
       <p class=muted id=hint style="margin-top:14px">Fix it in the other
       window, then press <b>Try again</b> — you do not need to quit
       Patchwerk or start setup over.</p>
       <details style="margin-top:12px"><summary class=muted>Technical
       details</summary>${err}${det}</details>`;
    break;
   }
   default:
    $("sub").textContent="Starting up…";
    h=`<h2><span class=spin></span>${esc(s.detail||"Getting ready…")}</h2>`;
  }
  b.innerHTML=h;
}
async function tick(){
  try{const r=await fetch("/status");render(await r.json());}catch(e){}
  setTimeout(tick,450);
}
tick();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):      # keep stdout for our own messages
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            snap = STATE.snapshot()
            snap["scdl"] = SC_DOWNLOAD
            self._send(200, json.dumps(snap).encode(), "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        else:
            self._send(404, b"no", "text/plain")

    def do_POST(self):
        if self.path.startswith("/open"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = q.get("t", [""])[0]
            ok = bc.open_pane(target)
            _log(f"open pane {target!r}: {'ok' if ok else 'FAILED'}")
            self._send(200 if ok else 500, b"{}", "application/json")
            return
        if not self.path.startswith("/answer"):
            self._send(404, b"no", "text/plain")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        answer = urllib.parse.parse_qs(raw).get("a", [""])[0]
        STATE.answer(answer)
        self._send(200, b"{}", "application/json")


# -- main ---------------------------------------------------------------------

def main() -> int:
    _log(f"Patchwerk launcher — {platform.platform()}")
    _log(f"app root: {APP_ROOT}")

    # A second double-click is not a reason to restart the first one.
    if bc.http_ok(APP_PORT, timeout=1.5):
        url = f"http://127.0.0.1:{APP_PORT}/"
        _log(f"already running — opening {url}")
        webbrowser.open(url)
        return 0

    port = bc.free_tcp_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    wizard = f"http://127.0.0.1:{port}/"
    _log(f"wizard at {wizard}")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: (shutdown(), sys.exit(0)))
        except (ValueError, OSError):
            pass

    threading.Thread(target=_run, daemon=True).start()
    webbrowser.open(wizard)

    try:
        # Hold the process open. The worker owns the engine's whole life —
        # including turning its death into a retryable page — so this only
        # waits to be told the app is over.
        while not QUIT.is_set():
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
