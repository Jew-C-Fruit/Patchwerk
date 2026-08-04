"""Build the Patchwerk installers.

    python3 packaging/build.py mac        -> build/Patchwerk-<v>-<arch>.dmg
    python3 packaging/build.py windows    -> build/windows/  (feed to Inno Setup)
    python3 packaging/build.py all

WHY A SELF-CONTAINED BUNDLE. The alternative — the `windows_start.bat`
approach of creating a venv on first run — needs the user to already have
Python 3.10+, needs the internet at the worst possible moment, and can fail
in a dozen ways we cannot see. macOS makes it worse: Sequoia still ships
/usr/bin/python3 as 3.9, which supriya cannot use, and touching it can
trigger the Command Line Tools dialog. So the interpreter and every
dependency go INSIDE the bundle at build time, from
`python-build-standalone`. The user installs one thing and it works offline.

The one thing NOT bundled is SuperCollider — see packaging/README.md.

CROSS-BUILDING. The Windows payload is built from macOS by unpacking
`win_amd64` wheels with `pip --target`; every Patchwerk dependency publishes
one (verified — python-rtmidi and aiohttp are the only compiled ones). What
this CANNOT do from a Mac is run Inno Setup, so `windows` produces the
staged tree and the .iss, and Setup.exe is compiled on Windows or in CI.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BUILD = REPO / "build"
CACHE = HERE / ".cache"

#: WHERE THE .app IS ASSEMBLED, and why it is not `build/`.
#:
#: This repo lives under ~/Documents, which is backed by a file-provider sync
#: daemon. That daemon re-adds `com.apple.FinderInfo` and
#: `com.apple.fileprovider.fpfs#P` to directories asynchronously — including
#: to a bundle we just signed. `codesign` refuses to sign, and refuses to
#: verify, anything carrying those ("resource fork, Finder information, or
#: similar detritus not allowed"), so a bundle assembled in the repo signs
#: cleanly and then FAILS verification seconds later, with nothing in the
#: build having changed. Assemble and sign outside the synced tree; only the
#: finished .dmg — one file, whose contents are a frozen filesystem image and
#: so immune to later xattrs — comes back into `build/`.
WORK = Path(os.environ.get("PATCHWERK_BUILD")
            or (tempfile.gettempdir() + "/patchwerk-build"))

VERSION = "2.2.0"
BUNDLE_ID = "com.patchwerk.app"

#: python-build-standalone. Pinned: an installer that builds differently next
#: week is not an installer. 3.12 rather than 3.13/3.14 because it is the
#: version every dependency currently ships a wheel for.
PY_TAG = "20260718"
PY_VER = "3.12.13"
PY_BASE = ("https://github.com/astral-sh/python-build-standalone/releases/"
           f"download/{PY_TAG}/cpython-{PY_VER}%2B{PY_TAG}-")
PY_BUILDS = {
    "mac-arm64":  "aarch64-apple-darwin-install_only.tar.gz",
    "mac-x86_64": "x86_64-apple-darwin-install_only.tar.gz",
    "win-x86_64": "x86_64-pc-windows-msvc-install_only.tar.gz",
}

#: What the app actually needs at runtime. `tests/` is dev tooling and does
#: not ship; `docs/` does, because TROUBLESHOOTING.md is the thing a stuck
#: user wants and it costs 156 KB.
PAYLOAD_DIRS = ["synthbase", "modules", "patches", "gui", "presets", "docs"]
PAYLOAD_FILES = ["requirements.txt", "README.md", "LICENSE", "CLAUDE.md"]

#: `gui/legacy` is archived, unserved and speaks a dead protocol (CLAUDE.md).
#: It stays in the repo for `tests/gui_check8.py`, which loads it by path —
#: but a product should not ship dead code, and tests do not ship either.
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "legacy")


def sh(*argv, **kw) -> None:
    print("  $", " ".join(str(a) for a in argv))
    subprocess.run([str(a) for a in argv], check=True, **kw)


def step(msg: str) -> None:
    print(f"\n== {msg}")


# -- the bundled interpreter --------------------------------------------------

def fetch_python(key: str) -> Path:
    """Download and unpack a standalone CPython. Cached by key."""
    dest = CACHE / key
    if (dest / "python").is_dir():
        return dest / "python"
    CACHE.mkdir(parents=True, exist_ok=True)
    url = PY_BASE + PY_BUILDS[key]
    tgz = CACHE / f"{key}.tar.gz"
    if not tgz.exists():
        print(f"  downloading {url}")
        urllib.request.urlretrieve(url, tgz)
    print(f"  unpacking {tgz.name}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tgz) as tf:
        # The archive's top-level dir is always "python/".
        tf.extractall(dest, filter="data")
    return dest / "python"


# -- payload ------------------------------------------------------------------

def build_manual() -> None:
    """Rebuild `gui/manual.html` before it is copied into the payload.

    The manual is a RELEASE DELIVERABLE under the docs policy, and `/manual`
    is served from the instrument itself — so a DMG whose `gui/manual.html`
    is missing or stale ships a product with a dead menu item. The built
    file is tracked at the release commit as well, so a clean clone has one
    too; this rebuild is the belt to that braces, and it keeps a build from
    packaging a manual older than the sources beside it.
    """
    src = REPO / "docs" / "manual" / "build.py"
    if not src.is_file():
        step("manual builder absent — skipping (gui/manual.html as committed)")
        return
    step("building the user manual")
    subprocess.run([sys.executable, str(src)], cwd=REPO, check=True)


def stage_payload(root: Path, key: str) -> None:
    """Lay out python/ + app/ + the launcher under `root`."""
    step(f"staging payload for {key}")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    build_manual()

    src_py = fetch_python(key)
    shutil.copytree(src_py, root / "python", symlinks=True)

    app = root / "app"
    app.mkdir()
    for d in PAYLOAD_DIRS:
        if (REPO / d).is_dir():
            shutil.copytree(REPO / d, app / d, ignore=IGNORE, symlinks=True)
    for f in PAYLOAD_FILES:
        if (REPO / f).is_file():
            shutil.copy2(REPO / f, app / f)

    for f in ("launcher.py", "boot_core.py"):
        shutil.copy2(HERE / "payload" / f, root / f)

    (root / "VERSION").write_text(f"{VERSION}\n")


def install_deps(root: Path, key: str) -> None:
    """Put the dependencies inside the bundled interpreter."""
    step(f"installing dependencies for {key}")
    req = REPO / "requirements.txt"
    if key.startswith("mac"):
        py = root / "python" / "bin" / "python3"
        sh(py, "-m", "pip", "install", "--no-warn-script-location",
           "--disable-pip-version-check", "-r", req)
    else:
        # Cross-install: unpack win_amd64 wheels with the HOST pip. No code
        # from those wheels runs here, so the host interpreter's version is
        # irrelevant — but --python-version/--abi must match the bundled one
        # or pip resolves wheels the target cannot import.
        target = root / "python" / "Lib" / "site-packages"
        target.mkdir(parents=True, exist_ok=True)
        sh(sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--target", target,
           "--platform", "win_amd64", "--only-binary=:all:",
           "--implementation", "cp", "--python-version", "3.12",
           "-r", req, "--upgrade")


def make_icons() -> Path:
    step("drawing the placeholder icon")
    out = WORK / "icon"
    sh(sys.executable, str(HERE / "icon" / "make_icon.py"), str(out))
    return out


# -- macOS --------------------------------------------------------------------

def make_icns(icondir: Path) -> Path:
    """`iconutil` is part of macOS, so no icon library is needed."""
    iconset = WORK / "Patchwerk.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    # iconutil demands these exact names.
    for size in (16, 32, 128, 256, 512):
        shutil.copy2(icondir / f"icon_{size}.png",
                     iconset / f"icon_{size}x{size}.png")
        shutil.copy2(icondir / f"icon_{size * 2}.png",
                     iconset / f"icon_{size}x{size}@2x.png")
    icns = WORK / "Patchwerk.icns"
    sh("iconutil", "-c", "icns", str(iconset), "-o", str(icns))
    return icns


def build_mac(arch: str | None = None) -> Path:
    arch = arch or ("arm64" if platform.machine() == "arm64" else "x86_64")
    key = f"mac-{arch}"
    WORK.mkdir(parents=True, exist_ok=True)
    appdir = WORK / "Patchwerk.app"
    contents = appdir / "Contents"

    if appdir.exists():
        shutil.rmtree(appdir)
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir(parents=True)

    stage_payload(contents / "Resources", key)
    install_deps(contents / "Resources", key)

    icondir = make_icons()
    shutil.copy2(make_icns(icondir), contents / "Resources" / "Patchwerk.icns")

    step("writing Info.plist")
    plist = plistlib.loads((HERE / "macos" / "Info.plist.in").read_bytes())
    plist["CFBundleShortVersionString"] = VERSION
    plist["CFBundleVersion"] = VERSION
    plist["CFBundleIdentifier"] = BUNDLE_ID
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))

    step("compiling the bundle executable")
    # MUST be a compiled Mach-O at Contents/MacOS, not a script — otherwise
    # TCC resolves the responsible process to the interpreter instead of to
    # this bundle, no Info.plist is consulted, and the microphone prompt
    # never appears. The full measurement is in the header of stub.c.
    stub = contents / "MacOS" / "Patchwerk"
    sh("clang", "-O2", "-arch", arch, "-mmacosx-version-min=11.0",
       "-o", str(stub), str(HERE / "macos" / "stub.c"))
    stub.chmod(0o755)

    step("ad-hoc code signing")
    # `codesign` refuses a bundle carrying extended attributes: "resource
    # fork, Finder information, or similar detritus not allowed". shutil.copy2
    # preserves xattrs, so anything the repo picked up (quarantine flags,
    # com.apple.provenance) rides into the bundle and stops the signature.
    # Strip them first — nothing in the payload wants an xattr.
    sh("xattr", "-cr", str(appdir))
    # No Developer ID here, so this is an ad-hoc signature. It is still worth
    # doing: TCC keys a permission grant to the code identity, and an
    # UNSIGNED bundle has none — the microphone grant would not stick across
    # launches. See README for the notarisation gap this leaves.
    # ENTITLEMENTS — why the bundle needs them, since the file itself cannot
    # say so (AMFI's parser rejects XML comments: "Failed to parse
    # entitlements: AMFIUnserializeXML: syntax error"; keep that plist bare).
    #
    # `--options runtime` opts into the hardened runtime, which changes TCC's
    # prompting policy: a hardened process is NOT prompted for a protected
    # service unless it holds the matching entitlement. Signed without
    # `com.apple.security.device.audio-input`, tccd logs
    #
    #   Prompting policy for hardened runtime; service:
    #   kTCCServiceMicrophone requires entitlement
    #   com.apple.security.device.audio-input but it is missing
    #
    # and shows NO DIALOG — scsynth then blocks forever on device start,
    # which looks exactly like item 38's stall. An Info.plist usage string is
    # necessary and not sufficient; this is the other half.
    #
    # `disable-library-validation` is for the bundled CPython: hardened
    # processes may only load libraries sharing their Team ID, and an ad-hoc
    # signature has none, so every compiled extension (python-rtmidi,
    # aiohttp, multidict) would fail to load.
    ents = HERE / "macos" / "entitlements.plist"
    try:
        sh("codesign", "--force", "--deep", "--sign", "-",
           "--options", "runtime", "--entitlements", str(ents), str(appdir))
    except subprocess.CalledProcessError:
        print("  ! hardened-runtime signing failed; retrying plain ad-hoc")
        sh("codesign", "--force", "--deep", "--sign", "-",
           "--entitlements", str(ents), str(appdir))

    # The entitlement is not decoration: without audio-input, a hardened
    # process is never PROMPTED for the microphone, and the failure is
    # silent. Assert it landed rather than discovering it on a user's Mac.
    got = subprocess.run(["codesign", "-d", "--entitlements", "-",
                          "--xml", str(appdir)],
                         capture_output=True, text=True).stdout
    if "com.apple.security.device.audio-input" not in got:
        raise SystemExit("! signed bundle lacks the audio-input entitlement "
                         "— the microphone prompt would never appear")

    # Verify HERE, in the build, rather than trusting that signing succeeded.
    # A signature that codesign accepted and then stopped verifying is exactly
    # the failure the WORK directory exists to prevent, and it is silent: the
    # user meets it as an unexplained Gatekeeper refusal, not as a build error.
    sh("codesign", "--verify", "--deep", "--strict", str(appdir))

    return make_dmg(appdir, arch)


def make_dmg(appdir: Path, arch: str) -> Path:
    step("building the disk image")
    stage = WORK / "dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copytree(appdir, stage / "Patchwerk.app", symlinks=True)
    os.symlink("/Applications", stage / "Applications")
    (stage / "READ ME FIRST.txt").write_text(READ_ME)
    # The copy is a fresh one; strip again so the image itself is clean.
    sh("xattr", "-cr", str(stage / "Patchwerk.app"))
    sh("codesign", "--verify", "--deep", "--strict", str(stage / "Patchwerk.app"))

    tmp_dmg = WORK / f"Patchwerk-{VERSION}-macOS-{arch}.dmg"
    tmp_dmg.unlink(missing_ok=True)
    sh("hdiutil", "create", "-volname", "Patchwerk", "-srcfolder", str(stage),
       "-ov", "-format", "UDZO", "-quiet", str(tmp_dmg))

    BUILD.mkdir(parents=True, exist_ok=True)
    dmg = BUILD / tmp_dmg.name
    dmg.unlink(missing_ok=True)
    shutil.move(str(tmp_dmg), str(dmg))
    return dmg


READ_ME = """Patchwerk
=========

1. Drag Patchwerk to the Applications folder on the right.
2. Because this build is not notarised by Apple, the FIRST launch must be:
   right-click Patchwerk in Applications -> Open -> Open.
   (Double-clicking works normally from then on.)
3. Patchwerk needs SuperCollider, a separate free download. If it is not
   installed, Patchwerk says so on first launch and links you to it:
   https://supercollider.github.io/downloads
4. On first launch macOS asks for microphone access. That permission is how
   macOS gates ALL audio-device input. Allowing it enables the Audio In
   module and the input meter; declining still leaves you a working synth,
   output only.

Patchwerk's interface opens in your browser at http://127.0.0.1:8765 —
it runs entirely on your own machine and is not reachable from the network.
"""


# -- Windows ------------------------------------------------------------------

def build_windows() -> Path:
    key = "win-x86_64"
    out = BUILD / "windows"
    root = out / "Patchwerk"

    stage_payload(root, key)
    install_deps(root, key)

    icondir = make_icons()
    shutil.copy2(icondir / "Patchwerk.ico", root / "Patchwerk.ico")

    step("copying the Windows launchers and the Inno Setup script")
    for f in ("Patchwerk.bat", "Patchwerk-console.bat"):
        shutil.copy2(HERE / "windows" / f, root / f)
    iss = out / "patchwerk.iss"
    shutil.copy2(HERE / "windows" / "patchwerk.iss", iss)
    iss.write_text(iss.read_text().replace("@VERSION@", VERSION))

    # Inno Setup is Windows-only. If we happen to be on Windows with it
    # installed, finish the job; otherwise stop with the tree ready.
    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if iscc:
        step("compiling Setup.exe with Inno Setup")
        sh(iscc, f"/O{BUILD}", str(iss))
        return BUILD / f"Patchwerk-{VERSION}-Setup.exe"
    print("\n  Inno Setup (ISCC) not found — the payload is staged but the")
    print("  wizard is not compiled. On a Windows machine, run:")
    print(f"     iscc {iss.relative_to(REPO)}")
    return out


# -- main ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", choices=["mac", "windows", "all"])
    ap.add_argument("--arch", help="macOS arch (default: this machine's)")
    args = ap.parse_args()

    BUILD.mkdir(exist_ok=True)
    made = []
    if args.target in ("mac", "all"):
        if platform.system() != "Darwin":
            print("! the macOS bundle needs macOS (codesign, iconutil, hdiutil)")
        else:
            made.append(build_mac(args.arch))
    if args.target in ("windows", "all"):
        made.append(build_windows())

    print("\n== done")
    for m in made:
        size = ""
        if Path(m).is_file():
            size = f"  ({Path(m).stat().st_size / 1e6:.0f} MB)"
        print(f"  {m}{size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
