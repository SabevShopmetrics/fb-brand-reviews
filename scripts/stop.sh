#!/bin/zsh
# Stop the background run (progress is kept; continue later with scripts/run_bg.sh --resume).
cd "$(dirname "$0")/.." || exit 1
pid=$(cat output/runs/current.pid 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid"; echo "STOPPED pid=$pid"; else echo "NOT_RUNNING"; fi
