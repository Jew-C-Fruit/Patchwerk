#!/bin/bash
# Relaunch SuperSynth: kill any old instance, start the GUI.
#   ./run.sh              -> pad_space patch
#   ./run.sh demo         -> named patch
#   ./run.sh demo --no-browser --hw-buffer 128   -> extra flags pass through
cd "$(dirname "$0")"
pkill -f 'synthbase gui' 2>/dev/null
pkill -x scsynth 2>/dev/null
sleep 1
exec .venv/bin/python -m synthbase gui "${1:-pad_space}" "${@:2}"
