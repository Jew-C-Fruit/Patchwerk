#!/bin/bash
# Relaunch SuperSynth: cleanly stop any old instance, start the GUI.
#   ./run.sh                    -> pad_space patch
#   ./run.sh demo               -> named patch
#   ./run.sh demo --no-browser  -> extra flags pass through
#
# Logs: /tmp/synth_gui.log (always; unbuffered).
cd "$(dirname "$0")"

PIDFILE=/tmp/supersynth.pid
# Stop the previous instance by pid (no pkill pattern races), then stragglers.
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    kill "$OLD" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$OLD" 2>/dev/null || break
      sleep 0.3
    done
    kill -9 "$OLD" 2>/dev/null
  fi
  rm -f "$PIDFILE"
fi
pkill -x scsynth 2>/dev/null
sleep 0.5

.venv/bin/python -u -m synthbase gui "${1:-pad_space}" "${@:2}" > /tmp/synth_gui.log 2>&1 &
NEW=$!
echo $NEW > "$PIDFILE"
sleep 5
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/ | grep -q 200; then
  echo "SuperSynth running (pid $NEW) — http://127.0.0.1:8765 — log: /tmp/synth_gui.log"
else
  echo "STARTUP PROBLEM — tail of /tmp/synth_gui.log:"
  tail -5 /tmp/synth_gui.log
  exit 1
fi
