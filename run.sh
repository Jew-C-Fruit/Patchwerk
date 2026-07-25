#!/bin/bash
# Relaunch Patchwerk: cleanly stop any old instance, start the GUI.
#   ./run.sh                    -> pad_space patch
#   ./run.sh demo               -> named patch
#   ./run.sh demo --no-browser  -> extra flags pass through
#
# Logs: /tmp/synth_gui.log (always; unbuffered).
#
# ORPHAN HARDENING (2026-07-24, the 07-16 failure): the pidfile only knows
# about instances THIS script started. An instance launched by hand (the rig
# is usually `nohup .venv/bin/python -m synthbase gui ...`), or one whose
# pidfile was lost to a crash or a reboot, is invisible to it — and the only
# symptom is the new process failing to bind 8765 five seconds later, which
# read as "STARTUP PROBLEM" with a healthy-looking log. So: kill by pidfile,
# then by process pattern, then by whoever actually HOLDS the port, and do
# not launch until the port is genuinely free.
cd "$(dirname "$0")"

PIDFILE=/tmp/patchwerk.pid
PORT=8765

# -- wait for a pid to die, escalating to -9 -----------------------------------
reap() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.3
  done
  kill -9 "$pid" 2>/dev/null
  sleep 0.2
}

# 1. the instance we started last time
if [ -f "$PIDFILE" ]; then
  reap "$(cat "$PIDFILE" 2>/dev/null)"
  rm -f "$PIDFILE"
fi

# 2. orphans we did not start (hand-launched rigs, lost pidfiles).
#    Match on "-m synthbase" — the ONE invariant in the command line. The
#    interpreter path is not: on the Mac it resolves to .../MacOS/Python
#    (capital P), so a /python/ pattern only matches by accident of the
#    "python@3.14" directory further up. The [-] bracket keeps pgrep from
#    matching itself and stops the leading dash being read as an option.
#    This script's own cmdline is "bash run.sh …", so it never self-matches.
for pid in $(pgrep -f '[-]m synthbase' 2>/dev/null); do
  [ "$pid" = "$$" ] && continue
  echo "run.sh: reaping orphaned synthbase pid $pid"
  reap "$pid"
done

# 3. whoever actually holds the port, whatever it is
if command -v lsof >/dev/null 2>&1; then
  for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    echo "run.sh: reaping pid $pid still listening on $PORT"
    reap "$pid"
  done
fi

pkill -x scsynth 2>/dev/null

# 4. do not launch into a held port — a bind failure here is unreadable later
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if command -v lsof >/dev/null 2>&1; then
    [ -z "$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null)" ] && break
  else
    break
  fi
  sleep 0.3
done
if command -v lsof >/dev/null 2>&1 && [ -n "$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null)" ]; then
  echo "STARTUP PROBLEM — port $PORT is still held by pid(s):" \
       "$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')"
  echo "  (kill it by hand, then re-run: kill -9 <pid>)"
  exit 1
fi

.venv/bin/python -u -m synthbase gui "${1:-pad_space}" "${@:2}" > /tmp/synth_gui.log 2>&1 &
NEW=$!
echo $NEW > "$PIDFILE"

# 5. poll for readiness instead of a fixed sleep — a cold scsynth boot, a
#    device fallback or a first-run mic prompt can all outlast 5 seconds,
#    and the old fixed wait reported a healthy start as a failure.
for _ in $(seq 1 40); do
  if ! kill -0 "$NEW" 2>/dev/null; then
    echo "STARTUP PROBLEM — process exited. Tail of /tmp/synth_gui.log:"
    tail -20 /tmp/synth_gui.log
    rm -f "$PIDFILE"
    exit 1
  fi
  if [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null)" = "200" ]; then
    echo "Patchwerk running (pid $NEW) — http://127.0.0.1:$PORT — log: /tmp/synth_gui.log"
    exit 0
  fi
  sleep 0.5
done

echo "STARTUP PROBLEM — no 200 from $PORT after 20s. Tail of /tmp/synth_gui.log:"
tail -20 /tmp/synth_gui.log
exit 1
