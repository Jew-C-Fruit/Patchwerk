"""Boot core for the installed Patchwerk app: find scsynth, start it, know when.

This is the SAME boot contract as `tests/rig.py` on `feat/p37-rig-driver`, and
it is deliberately a lift of that module's proven parts rather than a second,
worse boot path:

* `find_scsynth()`   — discovery WITHOUT requiring scsynth on PATH.
* two-signal readiness — device enumeration is NOT readiness (see below).
* `kill_scsynth()`   — clear stale servers by EXACT name, never by pattern.
* a bounded boot     — `Server().boot()` blocks forever on the CoreAudio
                       stall, so the caller times out and then diagnoses.

What is different here, and why this is not just an import:

1. The shipped app must not depend on `tests/`. A test harness is not a
   runtime dependency of a product.
2. rig.py is macOS-only in these paths (`pgrep`/`pkill`, `/Applications/...`).
   The installer ships on Windows too, so process control and discovery are
   branched per platform.
3. rig.py drives a rig over a websocket; this only has to start one and know
   when it answers.

⚠ CONVERGENCE DEBT: when item 37 lands, `tests/rig.py` should import
`find_scsynth`, `child_env`, `kill_scsynth`, `scsynth_alive` and
`scsynth_check` FROM HERE and delete its own copies. Two implementations of
"is scsynth ready" is exactly the drift this comment exists to stop. Until
then, any fix to one belongs in the other — the constants especially.

Stdlib only, on purpose: this runs before anything in the payload has been
imported, and it must be able to report "SuperCollider is missing" on a
machine where nothing else works.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"

#: scsynth's own readiness line. Device enumeration is NOT readiness — that is
#: exactly the trap that made a stalled scsynth read as "boots fine
#: standalone" (it prints its device list, then stalls inside CoreAudio device
#: start and never prints this). Two signals, both required: this line, and a
#: bound UDP socket on its port. Keep in step with tests/rig.py.
SCSYNTH_READY = "SuperCollider 3 server ready"
SCSYNTH_DEVICES = "Number of Devices"

#: supriya's own override, and therefore ours. Setting this in the child's
#: environment is how the installed app finds SuperCollider without the user
#: ever editing PATH — see `child_env()`.
ENVAR = "SUPRIYA_SERVER_EXECUTABLE"

#: Cold scsynth plus a device fallback. rig.py allows 40 s and run.sh 20 s;
#: an installed app is booting on unknown hardware, so take rig.py's number.
BOOT_TIMEOUT = 40.0

#: Where a discovered-by-hand SuperCollider path is remembered, so the "I
#: installed it somewhere else" case is answered once rather than every run.
CONFIG_DIR = Path.home() / ".patchwerk"
CONFIG_PATH = CONFIG_DIR / "install.json"


# -- discovery ----------------------------------------------------------------

def _mac_candidates() -> list[str]:
    return [
        "/Applications/SuperCollider.app/Contents/Resources/scsynth",
        "/Applications/SuperCollider/SuperCollider.app/Contents/Resources/scsynth",
        str(Path.home() / "Applications/SuperCollider.app/Contents/Resources/scsynth"),
        "/opt/homebrew/bin/scsynth",
        "/usr/local/bin/scsynth",
    ]


def _win_candidates() -> list[str]:
    """Every place the SuperCollider Windows installer plausibly lands.

    The installer's default is `%ProgramFiles%\\SuperCollider-<version>`, so
    the version suffix has to be globbed rather than guessed — this is the
    same probe `windows_start.bat` did, widened to the 32-bit and per-user
    install locations.
    """
    out: list[str] = []
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for root in roots:
        if not root:
            continue
        try:
            for d in sorted(Path(root).glob("SuperCollider*"), reverse=True):
                out.append(str(d / "scsynth.exe"))
        except OSError:
            pass
    return out


def saved_scsynth() -> str | None:
    """A path the user pointed us at previously, if it still exists."""
    try:
        import json
        raw = json.loads(CONFIG_PATH.read_text())
        p = raw.get("scsynth")
    except Exception:  # noqa: BLE001
        return None
    return p if p and Path(p).is_file() else None


def remember_scsynth(path: str) -> None:
    try:
        import json
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        raw = {}
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text())
            except Exception:  # noqa: BLE001
                raw = {}
        raw["scsynth"] = path
        CONFIG_PATH.write_text(json.dumps(raw, indent=1))
    except OSError:
        pass          # a config we cannot write is a re-probe, not a failure


def find_scsynth() -> Path | None:
    """Locate scsynth WITHOUT requiring it on PATH.

    Order: an explicit env override, a path the user gave us before, PATH,
    then the per-platform install locations. supriya's own finder searches
    the env var, PATH and the Mac /Applications paths; this covers the same
    ground plus Windows and the user's own answer, and — unlike supriya's —
    it returns None instead of raising, because "not installed yet" is a
    normal first-run state that the wizard has a page for.
    """
    env = os.environ.get(ENVAR)
    if env and Path(env).is_file():
        return Path(env)
    if (saved := saved_scsynth()):
        return Path(saved)
    if (found := shutil.which("scsynth")):
        return Path(found)
    for cand in (_win_candidates() if IS_WIN else _mac_candidates()):
        if Path(cand).is_file():
            return Path(cand)
    return None


def child_env(extra: dict | None = None) -> dict:
    """Environment for the spawned engine: scsynth found, PATH untouched.

    Both mechanisms are set on purpose. `SUPRIYA_SERVER_EXECUTABLE` is what
    supriya actually reads first, and it is exact. Prepending the directory
    to PATH is the belt to that braces: anything in the tree that shells out
    to a bare `scsynth` still resolves. Neither touches the user's own PATH —
    this dict is handed to a child process and dies with it.
    """
    env = dict(os.environ)
    sc = find_scsynth()
    if sc is not None:
        env[ENVAR] = str(sc)
        env["PATH"] = str(sc.parent) + os.pathsep + env.get("PATH", "")
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


# -- scsynth hygiene ----------------------------------------------------------

def scsynth_alive() -> list[int]:
    """PIDs of running scsynth processes, by EXACT name."""
    if IS_WIN:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq scsynth.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True,
        )
        pids = []
        for line in r.stdout.splitlines():
            parts = [c.strip('" ') for c in line.split('","')]
            if len(parts) > 1 and parts[0].lower() == "scsynth.exe":
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return pids
    r = subprocess.run(["pgrep", "-x", "scsynth"], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()] if r.returncode == 0 else []


def scsynth_orphans() -> list[int]:
    """Stale scsynths only: those whose parent is gone (reparented to init).

    This is the difference between housekeeping and vandalism. `run.sh` and
    `tests/rig.py` clear scsynth wholesale, which is right for a dev box the
    script owns outright — but a SHIPPED app that kills every scsynth on
    launch also kills the SuperCollider IDE the user had open, or another
    Patchwerk. (Measured, embarrassingly, on 2026-07-26: a blanket clear at
    startup took down a parallel session's live server.)

    A scsynth belonging to something alive has that something as its parent.
    A leftover from a crashed run has been reparented to pid 1. So: ppid == 1
    means stale, and nothing else gets touched.
    """
    if IS_WIN:
        return _win_orphans()
    out = []
    for pid in scsynth_alive():
        try:
            r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5)
            if int(r.stdout.strip() or -1) == 1:
                out.append(pid)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass          # cannot prove it is stale => leave it alone
    return out


def _win_orphans() -> list[int]:
    """The Windows half of `scsynth_orphans`. UNTESTED — no Windows here.

    Windows does not reparent an orphan to pid 1, so the Unix test does not
    port: a dead parent simply leaves a ParentProcessId that is no longer
    running. That is the test used here. It can false-positive if Windows has
    recycled that pid onto an unrelated process, which would mean killing a
    live scsynth — the same blunt outcome as the old behaviour, but rare
    rather than routine.

    `ps` does not exist here, and `wmic` is deprecated and absent on current
    Windows, so this goes through PowerShell's CIM provider. If that fails
    for any reason we return nothing: leaving a stale server behind is
    recoverable (Task Manager, or a reboot), killing a live one is not.
    """
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"name='scsynth.exe'\" "
             "| ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    running = set(_win_all_pids())
    if not running:
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid not in running:
            out.append(pid)
    return out


def _win_all_pids() -> list[int]:
    try:
        r = subprocess.run(["tasklist", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in r.stdout.splitlines():
        parts = [c.strip('" ') for c in line.split('","')]
        if len(parts) > 1:
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def kill_scsynth(pids: list[int] | None = None, timeout: float = 4.0) -> int:
    """Kill the given scsynth pids (default: all of them). Returns how many.

    EXACT name / explicit pids, never a pattern. scsynth's real argv[0] is
    the full `/Applications/SuperCollider.app/Contents/Resources/scsynth`, so
    a `pkill -f scsynth` matches that — and ALSO matches this launcher's own
    command line, so a pattern kill can shoot the launcher. `run.sh` gets
    this right (`pkill -x scsynth`); so does tests/rig.py; so does this.
    """
    targets = scsynth_alive() if pids is None else list(pids)
    if not targets:
        return 0
    if IS_WIN:
        for pid in targets:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True)
        return len(targets)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = set(scsynth_alive())
        if not alive & set(targets):
            return len(targets)
        time.sleep(0.2)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return len(targets)


def udp_held(port: int) -> bool:
    """Is something bound to this UDP port? The second readiness signal."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- the probe ----------------------------------------------------------------

def scsynth_check(
    timeout: float = 15.0,
    kill_first: bool = True,
    input_channels: int = 2,
    device: str | None = None,
) -> dict:
    """Can scsynth start an audio device on this machine RIGHT NOW?

    Spawns a bare scsynth — no supriya, no Patchwerk — on a fresh UDP port
    and reports BOTH readiness signals separately, because they fail apart:
    a machine where audio cannot start still enumerates devices happily.

    Returns {"ready", "devices", "udp", "exited", "stale", "seconds", "tail"}.

    THREE outcomes, not two, and conflating the last two gets the user sent
    to the wrong place:

    * `ready`                        — audio starts here.
    * `not ready, exited`            — scsynth REFUSED and said why. Its own
                                       message is the useful thing (a sample
                                       rate mismatch between the input and
                                       output devices prints a fix).
    * `not ready, devices, running`  — the CoreAudio device-start stall: it
                                       enumerated devices and then blocked
                                       forever inside coreaudiod. THIS is
                                       the permission case (see item 38).

    `exited` is what separates the last two. Without it every failure reads
    as "no microphone permission", which on a machine with mismatched sample
    rates is confidently wrong.

    `input_channels` is a parameter rather than a constant because the first-
    run wizard uses this twice for different questions: with input to ASK for
    the microphone grant, and without to prove the output-only fallback.
    """
    sc = find_scsynth()
    if sc is None:
        return {"ready": False, "devices": False, "udp": False, "stale": 0,
                "seconds": 0.0, "tail": "scsynth not found"}
    stale = kill_scsynth() if kill_first else len(scsynth_alive())
    port = free_udp_port()
    argv = [str(sc), "-u", str(port), "-i", str(input_channels), "-o", "2"]
    if device:
        argv += ["-H", device]
    t0 = time.monotonic()
    try:
        # BINARY, not text=True. The drain below is non-blocking, and a
        # non-blocking read that has nothing yet returns None — which a text
        # wrapper feeds straight into its incremental decoder and dies with
        # "can't concat NoneType to bytes". Read bytes, decode ourselves.
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    except OSError as exc:
        return {"ready": False, "devices": False, "udp": False, "stale": stale,
                "seconds": 0.0, "tail": f"could not run {sc}: {exc}"}
    lines: list[str] = []
    ready = devices = False
    try:
        # Non-blocking drain. A stalled scsynth writes NOTHING further, so a
        # plain readline() here would hang past the deadline and defeat the
        # whole point of having one.
        os.set_blocking(proc.stdout.fileno(), False)
        buf = ""
        while time.monotonic() - t0 < timeout and not ready:
            try:
                chunk = proc.stdout.read()
            except (BlockingIOError, ValueError):
                chunk = None
            if chunk:
                buf += chunk.decode("utf-8", "replace")
                *done, buf = buf.split("\n")
                for line in done:
                    lines.append(line.rstrip())
                    if SCSYNTH_DEVICES in line:
                        devices = True
                    if SCSYNTH_READY in line:
                        ready = True
            elif proc.poll() is not None:
                break            # died (bad device name) — not a stall
            else:
                time.sleep(0.05)
        # The last thing a STALLED scsynth wrote has no trailing newline, so
        # it never made it out of `buf` — and in the stall case that partial
        # line is the most diagnostic thing we have. Keep it.
        if buf.strip():
            lines.append(buf.rstrip())
    finally:
        # Sampled BEFORE we terminate it, or every probe looks like it exited.
        exited = proc.poll() is not None
        udp = udp_held(port) if ready else False
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        kill_scsynth()
    return {"ready": ready, "devices": devices, "udp": udp, "exited": exited,
            "stale": stale, "seconds": round(time.monotonic() - t0, 1),
            "tail": "\n".join(lines[-8:])}


# -- turning a failure into something a person can DO ------------------------
#
# scsynth's own failure text is written for someone holding sclang. It
# literally offers `s.options.sampleRate = <rate>;` as the fix — code the
# user cannot run, in a language this project deliberately does not use
# (CLAUDE.md, "Don'ts"). Showing that raw is how a first run ends with a
# stuck user, which is exactly what Cole hit on 2026-07-26.
#
# So failures get CLASSIFIED, and each class carries a remedy in the user's
# language: what is wrong, where to fix it, and which button opens that
# place. `open_target` names a pane the launcher can actually open.

#: macOS deep links. Verified on Sequoia 15.5 — this legacy-style URL still
#: brings System Settings to the front on the right pane.
PANE_MICROPHONE = ("x-apple.systempreferences:"
                   "com.apple.preference.security?Privacy_Microphone")
PANE_SOUND = "x-apple.systempreferences:com.apple.preference.sound"


def classify(check: dict) -> str:
    """What KIND of failure is this? Drives the remedy the user is shown.

    * "ok"          — audio starts.
    * "no-scsynth"  — SuperCollider is not installed / not runnable.
    * "permission"  — the CoreAudio stall: enumerated devices, then hung
                      rather than failed. Item 38's signature.
    * "sample-rate" — refused because input and output disagree on rate.
    * "refused"     — refused for some other reason it printed.
    * "unknown"     — no device list at all.
    """
    if check.get("ready"):
        return "ok"
    tail = (check.get("tail") or "").lower()
    if "not found" in tail and not check.get("devices"):
        return "no-scsynth"
    if check.get("exited"):
        if "sample rate" in tail:
            return "sample-rate"
        return "refused" if check.get("devices") else "unknown"
    if check.get("devices"):
        return "permission"        # still running, never got ready
    return "unknown"


def _hz(n) -> str:
    try:
        return f"{int(n):,} Hz"
    except (TypeError, ValueError):
        return "an unknown rate"


def _builtin(name: str) -> bool:
    """Does this look like the machine's own microphone?"""
    low = name.lower()
    return any(k in low for k in ("macbook", "built-in", "imac", "mac mini",
                                  "mac studio", "internal"))


def _default(entries: list) -> dict | None:
    for e in entries or []:
        if e.get("default"):
            return e
    return (entries or [None])[0]


def _sample_rate_remedy(devices: dict | None) -> dict:
    """Name the actual devices and rates, or admit we could not read them.

    A remedy that says "make your devices match" is barely better than the
    sclang it replaced — the user still has to work out WHICH devices and
    WHICH rate. When the device list is readable we name the offender, the
    two rates, and a specific input that would work. When it is not, we say
    so instead of inventing detail.

    Cole's actual case, 2026-07-26: input and output were BOTH "AirPods
    Pro" — the same device — but its microphone side runs at 24,000 Hz
    while playback runs at 48,000 Hz. A generic "your input and output
    disagree" reads as nonsense when they are visibly the same device, which
    is exactly why this names the rates.
    """
    generic = {
        "kind": "sample-rate",
        "title": "Your audio input and output are running at different "
                 "sample rates",
        "steps": [
            "Open Audio MIDI Setup (in Applications › Utilities).",
            "Click your input device and note its Format, e.g. 48,000 Hz.",
            "Click your output device and set the SAME rate.",
            "Come back here and press Try again.",
        ],
        "note": "SuperCollider will not start until they match. This is not "
                "a permissions problem — nothing needs granting.",
        "open_target": "audio-midi-setup",
        "open_label": "Open Audio MIDI Setup",
    }
    if not devices:
        return generic
    din, dout = _default(devices.get("inputs")), _default(devices.get("outputs"))
    if not din or not dout:
        return generic
    in_rate, out_rate = din.get("sample_rate"), dout.get("sample_rate")
    if not in_rate or not out_rate or in_rate == out_rate:
        return generic

    # A different input that already matches the output is the one-click fix.
    # Prefer the BUILT-IN mic: the candidate list also contains Continuity
    # devices (an iPhone advertises itself as a microphone), and telling
    # someone to route their synth through their phone is a worse answer than
    # the mic already in the machine.
    cands = [d for d in devices.get("inputs", [])
             if d.get("sample_rate") == out_rate and d is not din]
    cands.sort(key=lambda d: 0 if _builtin(d.get("name", "")) else 1)
    alt = cands[0] if cands else None
    same_device = din.get("name") == dout.get("name")
    steps = ["Open System Settings › Sound › Input."]
    if alt:
        steps.append(f"Choose {alt['name']!r} ({_hz(out_rate)}) instead of "
                     f"{din['name']!r} ({_hz(in_rate)}).")
    else:
        steps.append(f"Choose an input that runs at {_hz(out_rate)} — "
                     f"{din['name']!r} is at {_hz(in_rate)}.")
    steps.append("Come back here and press Try again.")
    note = (
        f"Your sound output {dout['name']!r} runs at {_hz(out_rate)}, but the "
        f"microphone side of {din['name']!r} runs at {_hz(in_rate)}, and "
        f"SuperCollider will not start a device pair that disagrees."
    )
    if same_device:
        note += (" They are the same device: a Bluetooth headset drops its "
                 "microphone to a low rate while its mic is in use, which is "
                 "why the two halves differ.")
    note += " This is not a permissions problem — nothing needs granting."
    # The button must open what step 1 NAMES. These steps say System Settings
    # › Sound, so sending the user to Audio MIDI Setup would be a button that
    # contradicts its own instructions.
    return {"kind": "sample-rate", "title": "Your headset's microphone and "
            "speaker run at different sample rates" if same_device else
            "Your audio input and output are running at different sample "
            "rates",
            "steps": steps, "note": note,
            "open_target": "sound-input",
            "open_label": "Open Sound settings"}


def remedy(check: dict, others_running: int = 0,
           devices: dict | None = None) -> dict:
    """A fixable description of a failure: {kind, title, steps, open_target}.

    `steps` are imperative and specific — pane names and toggle names, not
    "check your settings". `open_target` is None or a key the launcher knows
    how to open, so the remedy has a button instead of a paragraph.

    `others_running` is how many scsynth servers we did NOT start. It has to
    be checked before the sample-rate reading, because CoreAudio reports a
    device already claimed by another program as "Setting sample rate
    failed" — the same words. Sending someone to Audio MIDI Setup to fix
    rates that are already identical is the same class of wrong answer this
    whole function exists to stop.
    """
    kind = classify(check)
    if others_running and kind in ("sample-rate", "refused", "permission"):
        return {
            "kind": "device-busy",
            "title": "Another program is already using your audio device",
            "steps": [
                "Quit any other copy of Patchwerk, and the SuperCollider "
                "app if it is open.",
                "Then press Try again.",
            ],
            "note": f"{others_running} SuperCollider audio server"
                    f"{'s are' if others_running > 1 else ' is'} already "
                    "running that Patchwerk did not start. macOS reports a "
                    "device claimed by someone else as a sample-rate "
                    "failure, which is why the message below mentions rates.",
            "open_target": None, "open_label": "",
        }
    if kind == "permission":
        return {
            "kind": kind,
            "title": "macOS is blocking Patchwerk from using an audio input "
                     "device",
            "steps": [
                "Open System Settings › Privacy & Security › Microphone.",
                "Turn ON the switch next to Patchwerk.",
                "Come back here and press Try again.",
            ],
            "note": "macOS puts every audio INPUT device behind the "
                    "Microphone permission, even when you only want to play "
                    "sound. Patchwerk does not record you.",
            "open_target": "microphone",
            "open_label": "Open Microphone settings",
        }
    if kind == "sample-rate":
        return _sample_rate_remedy(devices)
    if kind == "no-scsynth":
        return {
            "kind": kind,
            "title": "SuperCollider could not be run",
            "steps": ["Reinstall SuperCollider, then press Try again."],
            "note": "", "open_target": None, "open_label": "",
        }
    if kind == "refused":
        return {
            "kind": kind,
            "title": "SuperCollider refused to start your audio device",
            "steps": [
                "Check that another program is not holding the audio device "
                "exclusively.",
                "In Audio MIDI Setup, confirm your output device is present "
                "and enabled.",
                "Press Try again.",
            ],
            "note": "SuperCollider's own message is below.",
            "open_target": "audio-midi-setup",
            "open_label": "Open Audio MIDI Setup",
        }
    return {
        "kind": kind,
        "title": "Patchwerk could not start the audio engine",
        "steps": ["Press Try again.",
                  "If it keeps failing, send the details below."],
        "note": "", "open_target": None, "open_label": "",
    }


def open_pane(target: str) -> bool:
    """Open a settings pane / utility for a remedy button. macOS only."""
    if not IS_MAC:
        return False
    if target == "microphone":
        arg = PANE_MICROPHONE
    elif target == "sound-input":
        arg = PANE_SOUND
    elif target == "audio-midi-setup":
        arg = "/System/Applications/Utilities/Audio MIDI Setup.app"
    else:
        return False
    try:
        subprocess.run(["open", arg], capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def diagnose(check: dict | None = None) -> str:
    """Why audio did not come up, in the two signals that distinguish it.

    Same three verdicts as `tests/rig.py::_diagnose`, worded for someone who
    installed an app rather than someone reading a test log.
    """
    chk = check if check is not None else scsynth_check()
    out = []
    if chk.get("stale"):
        out.append(f"Cleared {chk['stale']} stale scsynth process(es) first.")
    out.append(
        f"Bare scsynth: devices={'yes' if chk['devices'] else 'NO'} "
        f"ready={'yes' if chk['ready'] else 'NO'} "
        f"udp={'yes' if chk['udp'] else 'NO'} "
        f"exited={'yes' if chk.get('exited') else 'no'} ({chk['seconds']}s)"
    )
    if not chk["ready"] and chk.get("exited"):
        # It REFUSED rather than stalled, so it printed a reason — and that
        # reason beats anything we could guess. Blaming the microphone here
        # is how a sample-rate mismatch gets sent down a permissions rabbit
        # hole; scsynth's own text even names the fix.
        out.append(
            "SuperCollider started and then stopped on its own, with the "
            "reason below. This is NOT a permissions problem — read what it "
            "said. (Mismatched sample rates between your input and output "
            "devices are the usual cause; docs/TROUBLESHOOTING.md covers it.)"
        )
    elif chk["devices"] and not chk["ready"]:
        out.append(
            "SuperCollider can SEE your audio devices but cannot START one, "
            "and it is still hanging rather than failing. On macOS that is "
            "the microphone permission: opening a device that carries an "
            "input stream blocks forever without it."
        )
    elif not chk["devices"]:
        out.append(
            "SuperCollider produced no device list at all — check the "
            "SuperCollider installation itself."
        )
    else:
        out.append("SuperCollider itself is healthy; the fault is above it.")
    if chk.get("tail"):
        out.append("scsynth said:\n  " + chk["tail"].replace("\n", "\n  "))
    return "\n".join(out)


# -- the app ------------------------------------------------------------------

def http_ok(port: int, timeout: float = 2.0) -> bool:
    """Does the GUI answer on this port? The app's readiness signal."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/", timeout=timeout
        ) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def reap(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Stop a child, escalating to a kill. Never leave the mess behind."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
