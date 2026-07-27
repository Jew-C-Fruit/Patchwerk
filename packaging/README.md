# Packaging — the macOS disk image and the Windows setup wizard

Everything here builds a **distributable Patchwerk** for someone who will
never open a terminal: a `.dmg` on macOS and an Inno Setup wizard on Windows.

```bash
python3 packaging/build.py mac        # -> build/Patchwerk-0.1.0-macOS-arm64.dmg
python3 packaging/build.py windows    # -> build/windows/  (compile with Inno Setup)
python3 packaging/build.py all
```

Nothing in `synthbase/` is touched. The only file outside `packaging/` that
this branch changes is `.gitignore` (two lines: `build/` and
`packaging/.cache/`).

**Base: `main` @ `1b40248`.** This branch carries only its own two commits —
it does not depend on items 10, 29, reactive-taps or the Phase 1 rig work,
none of which are on `main`. See "Dependencies for the merge captain" at the
end.

---

## What is in the box

Both platforms ship the **same self-contained payload**:

```
python/     a complete CPython 3.12.13 (python-build-standalone, pinned)
            with supriya, mido, python-rtmidi, watchdog, pyserial and
            aiohttp already installed
app/        synthbase/, modules/, patches/, gui/, presets/, docs/
launcher.py first-run wizard + engine supervisor
boot_core.py scsynth discovery, readiness, process hygiene
```

**Why bundle the interpreter.** The alternative is `windows_start.bat`'s
approach — make a venv on first run — which needs the user to already have
Python 3.10+, needs the internet at the worst possible moment, and fails in
ways we cannot see. macOS makes it worse: Sequoia still ships
`/usr/bin/python3` as 3.9, which supriya cannot use. Bundling costs ~100 MB
compressed and removes that entire class of failure. Install one thing; it
works offline.

`tests/` is dev tooling and does not ship. `gui/legacy/` does not ship —
it is archived, unserved and speaks a dead protocol.

---

## The SuperCollider decision: **detect and guide**

SuperCollider is **not** bundled and **not** downloaded by the installer. It
is detected, and if missing the first-run wizard says so, links the download,
and offers a **Check again** button — install SC in another window, click the
button, carry on. No reinstall, no restart. A path found by hand is
remembered in `~/.patchwerk/install.json`.

Rejected alternatives, and why:

| option | why not |
| --- | --- |
| **Bundle it** | SuperCollider is **GPL-3**; Patchwerk is MIT. Redistributing GPL binaries carries obligations (written offer of source) that a hobby project should not take on casually. It also adds ~100 MB, and on macOS we would have to re-sign someone else's binary inside our bundle. |
| **Download during setup** | Needs the network during install, needs admin rights on macOS to place an `.app`, and means tracking SC's release URLs and versions. It converts a clean "one missing thing" state into a long fragile step that can half-succeed. |
| **Detect and guide** ✅ | No licensing question, no size, no network. Costs the user one extra download **once**, and the wizard makes that step legible instead of a traceback. |

**The installed app never needs PATH edited.** `boot_core.find_scsynth()`
checks, in order: `SUPRIYA_SERVER_EXECUTABLE`, the remembered path, `PATH`,
then the real install locations (`/Applications/SuperCollider.app/Contents/
Resources/scsynth`, `%ProgramFiles%\SuperCollider*\scsynth.exe`, Homebrew).
The result is handed to the engine as `SUPRIYA_SERVER_EXECUTABLE` in its
child environment — which is the variable supriya's own finder reads first.

---

## There is no permission step — and that is the fix

**Cole, 2026-07-26:** *"Seeing that first run setup throws a permission error,
but doesn't indicate how to grant permission."*

Reproduced, and the first run was wrong in two ways at once.

**It asked for a permission Patchwerk does not need.** Item 38 established
that the working configuration is `-H "<output-only device>" -i 0`, which
needs "no permission, no prompt and no user interaction at all". Measured
again here on Cole's Mac:

| configuration | result |
| --- | --- |
| `-i 2` (default devices) | fails in 0.4 s |
| `-i 0` alone | fails in 10.5 s |
| `-H "MacBook Pro Speakers" -i 0` | **ready in 0.2 s** |

So gating first run behind a microphone grant was demanding something the
product never required. The fix is not a better error message — it is
**deleting the gate**. First run is now SuperCollider, then the engine.

The permission is still obtainable; it is simply requested by the code that
wants it, when it wants it. The engine's own probe opens an input-bearing
device, and because the bundle carries the three things above, macOS shows a
real dialog naming Patchwerk at that moment. Decline it and item 38 falls
back to output-only and says so via `boot_note`. One surface, owned by the
code that needs it.

**And it blamed the wrong thing.** The error Cole hit was never a permission
problem. The real cause: the default input and output were BOTH
"Cole's AirPods Pro" — but the microphone side runs at **24,000 Hz** while
playback runs at **48,000 Hz**, and scsynth refuses a pair that disagrees.
The screen nonetheless said *"Audio input is not available"* above
SuperCollider's own advice to run `s.options.sampleRate = <rate>;` — sclang
the user cannot run, in a language this project deliberately does not use —
with a "grant Microphone access" hint underneath that would have fixed
nothing.

### Failures now carry a remedy, not a log

`boot_core.classify()` / `remedy()` turn a failure into named steps plus a
button that opens the exact place to fix it:

| kind | what the user is told |
| --- | --- |
| `permission` | the stall signature — Privacy & Security › Microphone, turn on Patchwerk |
| `sample-rate` | **names the devices and both rates**, and which specific input to switch to |
| `device-busy` | another scsynth we did not start holds the device — quit it |
| `refused` / `unknown` | SuperCollider's message, with Audio MIDI Setup |
| `engine` | not an audio fault at all — the log is the answer |

What Cole's machine produces now, verbatim:

> **Your headset's microphone and speaker run at different sample rates**
> Your sound output 'Cole's AirPods Pro' runs at 48,000 Hz, but the
> microphone side of 'Cole's AirPods Pro' runs at 24,000 Hz… They are the
> same device: a Bluetooth headset drops its microphone to a low rate while
> its mic is in use… This is not a permissions problem — nothing needs
> granting.
> 1. Open System Settings › Sound › Input.
> 2. Choose 'MacBook Pro Microphone' (48,000 Hz) instead of 'Cole's AirPods
>    Pro' (24,000 Hz).
> 3. Come back here and press Try again.
> **[Open Sound settings] [Try again] [Quit Patchwerk]**

Four rules this had to learn, each from getting it wrong first:

* **Never assert a cause you have not verified.** The first version claimed
  "sample rates disagree" on a machine where the two visible devices were
  both at 48 kHz. Rates are now read from `synthbase.audio_devices` and
  named; when they cannot be read, the wording goes generic instead of
  inventing detail.
* **Only diagnose audio when the failure was audio.** Probing scsynth after a
  bad patch name and printing its verdict would re-commit the original sin
  somewhere new. Non-audio failures get the log and say so.
* **An empty engine log is evidence, not an absence.** Zero bytes means the
  engine hung rather than crashed — `tests/rig.py::_boot_hint` reads it the
  same way — so it triggers a probe rather than a shrug.
* **The button must open what the steps name.** Steps that say Sound settings
  with a button that opens Audio MIDI Setup is a remedy arguing with itself.

### Recovery, verified end to end

`Try again` re-runs discovery and the engine in place. Proven in one
continuous run: **ready → engine killed underneath the app → remedy page →
Try again → ready + HTTP 200.** No quitting, no rerunning setup, no guessing
whether it took. The app also **no longer vanishes when the engine dies after
startup** — a Bluetooth headset reconnecting was enough to do that during
testing, and the app simply disappeared. It now stays up and explains itself,
which is why the failure page carries an explicit **Quit Patchwerk**.

---

## First run, step by step

1. **Already running?** If something answers on the app port, this is a
   second double-click: hand the browser to the running instance and exit.
2. **A browser opens on a local wizard page.** Patchwerk's UI is already a
   browser at `127.0.0.1:8765`, so setup lives where the product does. It is
   `http.server` from the stdlib — no Tk, which is the dependency most likely
   to be missing or broken on the machines this has to work on.
3. **SuperCollider** — detect, or the guide page above.
4. **The engine starts** — `python -m synthbase gui pad_space --port 8765
   --no-browser`, with a bounded wait for HTTP 200. No permission gate.
5. **The page redirects to the app.** Quitting Patchwerk stops the engine and
   the scsynth it started.

Logs, which are the first thing to ask for when someone is stuck:

```
~/.patchwerk/launcher.log     the launcher's decisions
~/.patchwerk/patchwerk.log    the engine's own output
```

---

## macOS audio permission — the part that actually needed solving

Cole flagged a known unsolved problem: scsynth spawned from some process
trees enumerates CoreAudio devices and never starts one. The
`feat/p38-audio-session` session diagnosed the cause (a TCC-disclaimed
process has no microphone grant, so opening a device that carries an input
stream blocks forever in `coreaudiod`) and owns the **runtime** fix in
`synthbase/audio_session.py`: probe, and fall back to an output-only device
pinned with `-H`.

**This branch does not duplicate any of that.** A fallback is a consolation
prize — it silently costs `modules/audio_in.py` and the input meter. The
installer's job is the other half: making the grant *obtainable*. That took
three things, and **all three are required** — each one alone fails silently,
with no dialog and a synth that never makes sound:

1. **`NSMicrophoneUsageDescription` in `Info.plist`.** Without a usage
   string macOS will not prompt at all.
2. **A compiled stub as `CFBundleExecutable`** (`macos/stub.c`). This was
   first written as a shell script that `exec`'d the bundled Python — and
   tccd's own log showed the consequence:

   ```
   AUTHREQ_ATTRIBUTION: responsible={identifier=python3.12,
     responsible_path=.../Patchwerk.app/Contents/Resources/python/bin/python3.12}
   ```

   TCC blamed the **interpreter**, not `com.patchwerk.app`, so no bundle was
   resolved and the Info.plist was never read. `exec` replaces the process
   image, and a `#!` script cannot dodge it (the image would be `/bin/sh`).
   The stub is a real Mach-O that **forks and waits**, so it stays the
   process LaunchServices started. After the change:

   ```
   AUTHREQ_ATTRIBUTION: responsible={identifier=com.patchwerk.app, ...}
   ```

3. **The `com.apple.security.device.audio-input` entitlement.** With the
   hardened runtime (`--options runtime`), TCC refuses to prompt a process
   that lacks the matching entitlement, and says so:

   ```
   Prompting policy for hardened runtime; service: kTCCServiceMicrophone
   requires entitlement com.apple.security.device.audio-input but it is
   missing for responsible={identifier=com.patchwerk.app ...}
   ```

   With `macos/entitlements.plist` in the signature, the same log line
   becomes `Prompting policy for hardened runtime; allow prompt: Allow`.
   `build.py` **asserts** the entitlement survived signing, because its
   absence is invisible until a user hits it.

Two traps for whoever touches this next:

* **Keep `entitlements.plist` comment-free.** AMFI's parser rejects XML
  comments outright (`AMFIUnserializeXML: syntax error`). The reasoning lives
  in `build.py` next to the signing step instead.
* **Do not put the bundle back on a shell stub** to "simplify" it. It works,
  and it silently un-fixes the permission.

---

## What is verified, and what is not

### Verified on this Mac (Darwin 24.5, arm64, 2026-07-26)

* The DMG builds, mounts, and the app inside it passes
  `codesign --verify --deep --strict` and satisfies its designated requirement.
* Launched from the mounted image via LaunchServices, the app: found
  SuperCollider, served the wizard, started the engine, reached **HTTP 200 in
  6–8 s**, served the real `Patchwerk — Blocks` UI, and had a live scsynth.
* TCC attributes scsynth's audio access to `com.patchwerk.app`, and the
  hardened-runtime prompting policy now returns **allow prompt: Allow**
  (both read from tccd's log, quoted above).
* `SIGTERM` to the app reaps the engine and its scsynth and leaves nothing.
* Second-launch handoff: with a server already on the port, the launcher
  detects it and opens the browser instead of starting a second engine.
* The bundled interpreter is 3.12.13 and imports supriya 26.3b0, mido,
  aiohttp, rtmidi and watchdog.

Added 2026-07-26 after Cole's report:

* First run reaches **ready** with no permission step at all.
* Every remedy class was exercised against a real failure, not constructed:
  `sample-rate` (named the AirPods and both rates), `device-busy` (a parallel
  session's server held the device), `engine` (non-audio failure).
* The **Open Sound settings** and **Open Microphone settings** buttons both
  bring System Settings to the front (Sequoia 15.5).
* Killing the engine under a running app leaves the app up with a remedy.
* `Try again` recovers to a working app in one continuous run.
* The remedy's promise was checked rather than assumed: scsynth with the
  built-in mic and speakers (both 48 kHz) reaches "SuperCollider 3 server
  ready", so switching the input as instructed does resolve it.

### NOT verified

* **The microphone dialog was never accepted.** Granting a permission on
  Cole's machine is Cole's call. This now matters much less — nothing in
  first run needs it — but audio INPUT (`modules/audio_in.py`, the input
  meter) still does.

### ⚠ Known blocker on `main`, owned by item 38

On a Mac whose default device cannot start, `synthbase/engine.py`'s last
resort is `input_bus_channel_count=0, input_device=None` — i.e. `-i 0` with
no `-H`, **the exact variant item 38 proved still fails** (10.5 s, measured
above). So on `main` today the engine can fail to boot where
`synthbase/audio_session.py` on `feat/p38-audio-session` would succeed.

This branch deliberately does **not** fork that fix: device selection is item
38's, and the CLI cannot reach the working configuration anyway (`--in-device`
/`--out-device` exist, but input CHANNELS are not exposed, and
`-H <device> -i 2` fails — only `-i 0` works). What this branch does instead
is make the failure legible and recoverable. **When item 38 merges, rebuild
the DMG and this class of failure should disappear.**
* **Everything Windows.** The payload is staged and correct by inspection
  (`pythonw.exe` present, `cp312-win_amd64` `.pyd` extensions, no macOS
  `.so`), but no Windows machine exists here: `patchwerk.iss` has never been
  compiled, the `.bat` launchers have never run, and the Windows branches of
  `boot_core` (`tasklist`/`taskkill` parsing, `_win_orphans` via PowerShell)
  are **written and untested**. `_win_orphans` in particular is a best-effort
  port of a Unix test that does not exist on Windows.
* **Intel macOS.** Only `arm64` was built. `--arch x86_64` should work
  (build.py fetches that CPython and passes `-arch` to clang) but was not run.
  There is no universal binary; that needs two builds and `lipo`.
* **Notarisation.** The bundle is **ad-hoc signed**, so `spctl` rejects it and
  a downloaded copy needs right-click → Open on first launch (the DMG's
  `READ ME FIRST.txt` says so). Real distribution needs an Apple Developer ID
  plus notarisation — at which point the ad-hoc TCC identity also becomes
  stable across rebuilds, which it is not today.

### Two environmental things worth knowing

* **This Mac currently has mismatched input/output sample rates.** A bare
  scsynth aborts with `Setting sample rate failed`, and an engine boot hit the
  same. It is machine state, not packaging (`docs/TROUBLESHOOTING.md` covers
  it) — a later run booted fine. `boot_core.diagnose()` now distinguishes
  *exited-with-a-reason* from *stalled-forever* precisely so this stops being
  misreported as a permission problem.
* **The repo lives under a file-provider-synced `~/Documents`.** That daemon
  re-adds `com.apple.FinderInfo` to a bundle *after* it is signed, and
  codesign then refuses to verify it. The app is therefore assembled and
  signed in `$TMPDIR/patchwerk-build` (override with `PATCHWERK_BUILD`), and
  only the finished `.dmg` comes back into `build/`.

---

## Building the Windows Setup.exe

`build.py windows` stages `build/windows/Patchwerk/` (254 MB) and writes
`build/windows/patchwerk.iss`. Compiling the wizard needs **Inno Setup 6**,
which is Windows-only. Two ways:

**On a Windows machine** — copy `build/windows/` across, install Inno Setup,
then:

```
iscc build\windows\patchwerk.iss
```

**On a GitHub Actions `windows-latest` runner** — run `python packaging/build.py
windows`, `choco install innosetup -y`, then the same `iscc` line. No workflow
file is added here on purpose: `.github/workflows/` belongs to another live
session this round.

The wizard installs per-machine (or per-user without admin), creates Start
Menu and optional desktop shortcuts pointing at `pythonw.exe launcher.py` (no
console window), adds a **Patchwerk (show log)** shortcut running the console
`.bat` for diagnostics, detects SuperCollider on the Ready page, and offers
the download link on the finish page only when it is genuinely missing.

---

## Coordination with the other live branches

* **`feat/p38-audio-session`** owns runtime device selection in
  `synthbase/audio_session.py`. This branch owns bundle identity and the
  permission prompt. The launcher deliberately does **not** decide which
  output-only device to pin — that needs device enumeration it has no
  business duplicating (`-i 0` alone is famously not enough). When the mic
  is refused, the wizard says only what it knows and lets the engine start be
  the arbiter.
* **`feat/p37-rig-driver`** has `tests/rig.py`. `boot_core.py` is a
  deliberate lift of its proven parts — `find_scsynth`, `child_env`,
  two-signal readiness, exact-name process control, bounded boot + diagnosis
  — adapted for a shipped app (no `tests/` dependency, cross-platform).
  **Convergence debt:** when item 37 lands, `rig.py` should import those from
  here and delete its copies. Two implementations of "is scsynth ready" is
  exactly the drift worth avoiding; until then, a fix to one belongs in the
  other, the constants especially.
* `boot_core.BOOT_TIMEOUT` is load-bearing until item 38 merges: on `main`
  today `Server().boot()` has no timeout and the CoreAudio stall raises
  nothing, so without it a stalled first launch would spin forever instead of
  showing a diagnosis.

## One behaviour that differs from `run.sh` on purpose

`run.sh` and `tests/rig.py` clear scsynth **wholesale**, which is right for a
dev box the script owns. A shipped app must not: a blanket clear at startup
also kills the SuperCollider IDE the user had open, or another Patchwerk.
(Measured the hard way on 2026-07-26 — a blanket clear here took down a
parallel session's live server.) So:

* **startup** clears only **orphans** — scsynth whose parent is gone
  (`ppid == 1`), i.e. the leftovers of a Patchwerk that crashed;
* **shutdown** kills only servers that appeared **after we started**;
  anything pre-existing is somebody else's and stays up.

---

## Dependencies for the merge captain

**Base: `main` @ `1b40248`.** This branch was originally cut when `main` was
at `009600d`; that was rewound, so a rebase carried seven commits belonging to
items 10, 29 and reactive-taps as if they were mine. Rebased onto current
`main` with `--onto`; it applied without conflict, and the branch is now
honestly two commits touching `packaging/` plus two lines of `.gitignore`.

**It needs nothing unmerged.** Verified against `1b40248`:

| what the launcher uses | on `main`? |
| --- | --- |
| `synthbase.audio_devices.list_audio_devices` (naming devices in a remedy) | yes |
| `python -m synthbase gui <patch> --port N --no-browser` | yes |
| `synthbase/ modules/ patches/ gui/ presets/ docs/` (payload) | yes |

`packaging/payload/boot_core.py` is **stdlib-only** and imports nothing from
the app. `launcher.py` has exactly one app import, the one in the table, and
it is wrapped so a failure degrades to generic wording.

**Explicitly NOT dependencies, despite being discussed in this file:**

* **Item 37 / `tests/rig.py`** — `boot_core.py` is a LIFT of its proven parts
  (discovery, two-signal readiness, exact-name process control, bounded boot),
  not an import; a shipped app must not depend on `tests/`. The convergence
  debt runs the other way: when item 37 lands, `rig.py` should import from
  `boot_core` and delete its copies.
* **Item 38 / `synthbase/audio_session.py`** — deliberately not imported.
  Device selection stays theirs.

**One thing the captain should know, because it is cross-branch.** On `main`,
`engine.py`'s last-resort fallback is `input_bus_channel_count=0,
input_device=None` — `-i 0` with no `-H`, the exact variant item 38 measured
as still failing (10.5 s on this Mac). So a packaged Patchwerk built from
`main` alone can fail to boot audio on a machine whose default device will not
start, and the installer surfaces that as a remedy page rather than fixing it.
**Sequencing item 38 before or with this branch removes that class of
failure**; this branch is correct either way and needs no change when it
lands, beyond rebuilding the DMG.

Packaging is otherwise **agnostic to what else merges**: `build.py` copies
whatever `synthbase/`, `modules/` and `gui/` are present, so items 10, 29,
reactive-taps and dual-mode need no packaging changes to ship.
