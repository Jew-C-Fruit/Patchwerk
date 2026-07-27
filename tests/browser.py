"""Browser hygiene for agent-driven rigs: reuse a tab, close what you opened.

    Item 37 Phase 1, follow-up. macOS, via `osascript`.

Sessions have been leaving Patchwerk tabs behind and they stack up. Same
principle as the scsynth process management in `rig.py`: teardown has to be
reliable enough that the next session does not inherit the mess, and a
session that dies badly is exactly when tabs get orphaned.

Three rules, in the order they actually matter:

1. **Never let the SERVER open the tab.** `synthbase gui` calls
   `webbrowser.open` on every boot unless `--no-browser`, so each un-flagged
   `./run.sh` leaves a tab in the DEFAULT browser. That is the leak, it is
   browser-independent, and `rig.py` closes it by always passing
   `--no-browser`.
2. **Reuse, don't open.** `open_or_reuse()` focuses an existing tab on the
   rig's origin instead of stacking another.
3. **Close what you opened, and sweep what died.** `Rig` closes its own tabs
   at teardown; `close_stale()` sweeps orphans from sessions that could not.

    .venv/bin/python tests/rig.py tabs                # what is open
    .venv/bin/python tests/rig.py tabs --close-stale  # sweep the orphans
    .venv/bin/python tests/rig.py ui                  # reuse-or-open, focused

**Which browser, for which job.** They are not interchangeable:

| you want to | use | why |
|---|---|---|
| drive the GUI interactively | a real browser — the claude-in-chrome MCP where Chrome exists, else the in-app Claude Browser (`preview_start` + `read_page`) | DOM-aware: `read_page` / `find` / `form_input` beat coordinates |
| screenshot for the manual | **Playwright Chromium** (`docs/manual/capture.py`) | its own profile, fixed viewport and `device_scale_factor`, dies with the process — it can never leave a tab |
| assert on the GUI headlessly | **Playwright Chromium** (`check_blocks.py`) | no rig, no audio, deterministic, CI |
| anything | NOT pixel-level control | a click at (x, y) cannot read the DOM and breaks on any reflow |

Playwright's Chromium is a different browser with a different profile from
anything on your Dock: it can never see or leave a real tab, which is why
the screenshot path has no hygiene problem and the interactive path does.
That is the split — screenshots and interactive driving legitimately want
different browsers, so this module governs the second and leaves the first
alone.

**This machine, 2026-07-26: there is no Chrome.** `/Applications` has Safari
and Firefox, `mdfind` finds no `com.google.Chrome`, and no browser is
connected to the claude-in-chrome MCP. Safari is the default and is what
`webbrowser.open` has been stacking tabs in. So this module is written
browser-AGNOSTIC (`BROWSERS`) and acts on whichever scriptable browsers are
actually running, rather than assuming the one the guidance named.

**Permissions.** Scripting another app needs macOS Automation permission,
granted once per terminal per app. Denied or un-answered, it HANGS — a
dialog no one is looking at. Every call here is bounded by `OSA_TIMEOUT` and
raises `BrowserError` with the fix, the same discipline as the boot timeout:
an environment fault must be legible, never a hang. (Verified the hard way:
the first `osascript` to Safari from this session hung on exactly that
dialog.)

**Safety.** Nothing here closes a tab it cannot justify. A tab counts as
Patchwerk only if its title says so (a live rig) or it is a loopback URL on
a known Patchwerk port (a dead rig — Chrome and Safari both overwrite the
title with the bare URL once the server is gone, so the port is the only
evidence left). Everything else on loopback is somebody else's dev server
and is never touched.
"""

from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

#: name -> the AppleScript dialect it speaks. Safari calls a tab's title
#: `name` and its active tab `current tab`; the Chromium family calls them
#: `title` and `active tab index`. Everything else is identical.
BROWSERS = {
    "Google Chrome": "chromium",
    "Chromium": "chromium",
    "Brave Browser": "chromium",
    "Microsoft Edge": "chromium",
    "Safari": "safari",
}

TITLE_HINT = "Patchwerk"
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")
SEP = "<<|>>"          # multi-char: a URL or title can contain any single one
OSA_TIMEOUT = 20.0     # a permission dialog must not become a hang


class BrowserError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tab:
    app: str
    window: int
    index: int
    url: str
    title: str

    @property
    def port(self) -> int | None:
        try:
            return urlparse(self.url).port
        except ValueError:
            return None

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").strip("[]")

    def __str__(self) -> str:
        return f"{self.app} w{self.window}t{self.index}  {self.url}  {self.title!r}"


def patchwerk_ports() -> set[int]:
    """Ports a loopback tab may be a Patchwerk tab on.

    The default, the silent rig's default, and whatever `SS_PORT` says —
    deliberately NOT "any loopback port", because that is somebody else's
    dev server.
    """
    ports = {8765, 8799}
    try:
        ports.add(int(os.environ.get("SS_PORT", 8765)))
    except ValueError:
        pass
    return ports


# -- talking to the browser ---------------------------------------------------

def installed() -> list[str]:
    """Scriptable browsers present on this Mac."""
    out = []
    for name in BROWSERS:
        if Path(f"/Applications/{name}.app").exists() or \
                Path(f"/System/Applications/{name}.app").exists() or \
                (Path.home() / f"Applications/{name}.app").exists():
            out.append(name)
    return out


def running() -> list[str]:
    """Browsers that are RUNNING. Checked with pgrep, which cannot launch one.

    `tell application "Safari"` LAUNCHES Safari if it is not running — a tab
    manager that boots the user's browser in order to count tabs is a bug.
    """
    out = []
    for name in BROWSERS:
        r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
        if r.stdout.strip():
            out.append(name)
    return out


def _run_osa(script: str) -> subprocess.CompletedProcess:
    """The one place a subprocess is spawned — swapped out in tests."""
    return subprocess.run(["osascript", "-e", script], capture_output=True,
                          text=True, timeout=OSA_TIMEOUT)


def _osa(script: str) -> str:
    try:
        r = _run_osa(script)
    except subprocess.TimeoutExpired:
        raise BrowserError(
            f"osascript timed out after {OSA_TIMEOUT:.0f}s — a macOS "
            "Automation permission dialog is probably waiting for a human. "
            "Grant it once in System Settings > Privacy & Security > "
            "Automation, then re-run.") from None
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "-1743" in err or "not authorized" in err.lower():
            raise BrowserError(
                "not allowed to script the browser. Grant this terminal "
                "Automation access: System Settings > Privacy & Security > "
                f"Automation.  ({err})")
        raise BrowserError(f"osascript failed: {err}")
    return r.stdout


def _title_of(app: str) -> str:
    return "name" if BROWSERS.get(app) == "safari" else "title"


def _list_script(app: str) -> str:
    return f'''
tell application "{app}"
    set out to ""
    set wi to 0
    repeat with w in windows
        set wi to wi + 1
        set ti to 0
        repeat with t in tabs of w
            set ti to ti + 1
            set out to out & wi & "{SEP}" & ti & "{SEP}" & (URL of t) & ¬
                "{SEP}" & ({_title_of(app)} of t) & linefeed
        end repeat
    end repeat
    return out
end tell
'''


def all_tabs(apps: list[str] | None = None) -> list[Tab]:
    """Every tab of every running browser. Empty when none is running."""
    out = []
    for app in (apps if apps is not None else running()):
        for line in _osa(_list_script(app)).splitlines():
            parts = line.split(SEP)
            if len(parts) != 4:
                continue
            w, i, url, title = parts
            if w.strip().isdigit() and i.strip().isdigit():
                out.append(Tab(app, int(w), int(i), url.strip(), title.strip()))
    return out


def is_patchwerk(tab: Tab) -> bool:
    """A tab we are entitled to act on. Deliberately conservative.

    Two ways to qualify, and the second exists because the browser replaces
    the title with the bare URL once the server behind it is gone — the dead
    tab we most want to sweep is the one that no longer says what it was.
    """
    if tab.host not in LOOPBACK:
        return False
    if TITLE_HINT.lower() in tab.title.lower():
        return True
    return tab.port in patchwerk_ports()


def patchwerk_tabs(apps: list[str] | None = None) -> list[Tab]:
    return [t for t in all_tabs(apps) if is_patchwerk(t)]


def port_alive(p: int | None, timeout: float = 1.5) -> bool:
    if not p:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{p}/", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def stale_tabs(apps: list[str] | None = None) -> list[Tab]:
    """Patchwerk tabs whose rig is gone — the orphans of a killed session."""
    live: dict[int | None, bool] = {}
    out = []
    for t in patchwerk_tabs(apps):
        if t.port not in live:
            live[t.port] = port_alive(t.port)
        if not live[t.port]:
            out.append(t)
    return out


def _as_list(urls) -> str:
    quoted = ", ".join('"' + u.replace("\\", "\\\\").replace('"', '\\"') + '"'
                       for u in urls)
    return "{" + quoted + "}"


def _close_script(app: str, urls) -> str:
    """Close by URL, walking each window BACKWARDS.

    Indices shift as tabs close, so the index we listed with cannot be
    trusted; the URL can. There is no `return n` — see `close_tabs`.
    """
    return f'''
tell application "{app}"
    set targets to {_as_list(urls)}
    repeat with w in windows
        repeat with i from (count of tabs of w) to 1 by -1
            if (URL of tab i of w) is in targets then
                close tab i of w
            end if
        end repeat
    end repeat
end tell
'''


def close_tabs(tabs: list[Tab]) -> int:
    """Close exactly these tabs, matched by URL. Returns how many went.

    **The count comes from re-listing, never from the script.** Safari's
    `close` can finish the work and then take its time returning — observed
    2026-07-26, closing two tabs: both were gone and `osascript` still hit
    the 20 s timeout. A driver that trusted the return value would have
    reported a failure for an action that succeeded, and one that treated
    the timeout as fatal would have hidden the success. So: do the work,
    then LOOK. A timeout is only an error if the tabs are still there.
    """
    by_app: dict[str, set] = {}
    for t in tabs:
        by_app.setdefault(t.app, set()).add(t.url)
    pending: BrowserError | None = None
    for app, urls in by_app.items():
        try:
            _osa(_close_script(app, sorted(urls)))
        except BrowserError as exc:
            pending = exc          # verify before believing it
    left = {t.url for t in all_tabs(list(by_app))}
    gone = sum(1 for t in tabs if t.url not in left)
    if pending is not None and gone < len({t.url for t in tabs}):
        raise pending
    return gone


def close_stale(apps: list[str] | None = None) -> list[Tab]:
    """Sweep orphaned Patchwerk tabs. Returns the ones that were closed."""
    doomed = stale_tabs(apps)
    if doomed:
        close_tabs(doomed)
    return doomed


def find_tab(url: str, apps: list[str] | None = None) -> Tab | None:
    """An existing tab on the same ORIGIN — a trailing slash or the route
    (`/` vs `/blocks`) must not earn the same rig a second tab."""
    want = urlparse(url)
    for t in all_tabs(apps):
        got = urlparse(t.url)
        if (got.hostname, got.port) == (want.hostname, want.port):
            return t
    return None


def focus(tab: Tab) -> None:
    select = (f"set current tab of w to tab {tab.index} of w"
              if BROWSERS.get(tab.app) == "safari"
              else f"set active tab index of w to {tab.index}")
    _osa(f'''
tell application "{tab.app}"
    set w to window {tab.window}
    {select}
    set index of w to 1
    activate
end tell
''')


def default_browser() -> str | None:
    """Whichever browser to use: the running one, else the installed one."""
    for pool in (running(), installed()):
        if pool:
            return pool[0]
    return None


def open_tab(url: str, app: str | None = None) -> Tab:
    """Open a NEW tab and return it. Launches the browser if need be."""
    app = app or default_browser()
    if app is None:
        raise BrowserError("no scriptable browser is installed")
    # Both dialects add a tab to an existing window the same way; they differ
    # only when there is no window yet.
    fresh = ('make new document with properties {URL:"%s"}' % url
             if BROWSERS.get(app) == "safari" else
             'make new window\n        '
             'set URL of active tab of window 1 to "%s"' % url)
    _osa(f'''
tell application "{app}"
    activate
    if (count of windows) is 0 then
        {fresh}
    else
        tell window 1 to make new tab with properties {{URL:"{url}"}}
    end if
end tell
''')
    return find_tab(url, [app]) or Tab(app, 1, 1, url, "")


def open_or_reuse(url: str, tidy: bool = True,
                  app: str | None = None) -> tuple[Tab, bool]:
    """Focus the rig's existing tab, or open one. Returns (tab, reused).

    `tidy` sweeps orphans first, so the common case — an agent opening the
    UI — is also the moment the last session's leftovers go away.
    """
    if tidy and running():
        for t in close_stale():
            print(f"[browser] closed stale tab: {t.url}")
    existing = find_tab(url)
    if existing is not None:
        focus(existing)
        return existing, True
    return open_tab(url, app), False
