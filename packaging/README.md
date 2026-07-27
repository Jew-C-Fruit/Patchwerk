# Packaging — the macOS disk image and the Windows setup wizard

Everything here builds a **distributable Patchwerk** for someone who will
never open a terminal: a `.dmg` on macOS and an Inno Setup wizard on Windows.

```bash
python3 packaging/build.py mac        # -> build/Patchwerk-0.1.0-macOS-arm64.dmg
python3 packaging/build.py windows    # -> build/windows/  (compile with Inno Setup)
python3 packaging/build.py all
```

Nothing in `synthbase/` is touched. The only file outside `packaging/` that
this branch changes is `.gitignore` (three lines: `build/`,
`packaging/.cache/`).

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

## First run, step by step

1. **Already running?** If something answers on the app port, this is a
   second double-click: hand the browser to the running instance and exit.
2. **A browser opens on a local wizard page.** Patchwerk's UI is already a
   browser at `127.0.0.1:8765`, so setup lives where the product does. It is
   `http.server` from the stdlib — no Tk, which is the dependency most likely
   to be missing or broken on the machines this has to work on.
3. **SuperCollider** — detect, or the guide page above.
4. **Microphone permission (macOS only, first run)** — explained *before* it
   happens, then triggered, then verified. See the next section.
5. **The engine starts** — `python -m synthbase gui pad_space --port 8765
   --no-browser`, with a bounded wait for HTTP 200.
6. **The page redirects to the app.** Quitting Patchwerk stops the engine and
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

### NOT verified

* **The microphone dialog was never accepted.** Granting a permission on
  Cole's machine is Cole's call, so the run stops at "macOS is now willing to
  prompt". **Cole should do one first launch and click Allow**, then confirm
  the wizard reports input as available.
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
