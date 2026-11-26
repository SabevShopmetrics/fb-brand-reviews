#!/bin/zsh
# Run brands one after another, unattended. One brand per line in the queue file (default output/queue.txt);
# finished lines get "# done <time>" in front. Safe by design:
#   - waits while another run is going (never two at once)
#   - a brand left unfinished (posts / comments left) is run again, at most 3 times in a row
#   - daily page cap reached  -> sleeps until 00:15 and continues the same brand
#   - Facebook asks for a login / security check -> the queue STOPS (never gets around it)
# Start:  nohup zsh scripts/queue.sh > output/runs/queue.log 2>&1 &      Stop: touch output/QUEUE_STOP
cd "$(dirname "$0")/.." || exit 1
Q="${1:-output/queue.txt}"
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
busy() { [ -f output/runs/current.pid ] && kill -0 "$(cat output/runs/current.pid)" 2>/dev/null; }
wait_free() { while busy; do sleep 30; [ -f output/QUEUE_STOP ] && { say "QUEUE_STOP — спирам опашката"; exit 0; }; done; }

say "опашка: $Q"
while true; do
  [ -f output/QUEUE_STOP ] && { say "QUEUE_STOP — спирам опашката"; rm -f output/QUEUE_STOP; exit 0; }
  brand=$(grep -v '^\s*#' "$Q" | grep -v '^\s*$' | head -1)
  [ -z "$brand" ] && { say "опашката е празна — край"; exit 0; }
  tries=0
  wait_free
  # a brand finished in the last 3 days (nothing left to collect) is not run again
  fresh=$(.venv/bin/python - "$brand" <<'PY'
import sys, time
from pathlib import Path
from fbreviews.fburl import slug
from fbreviews.pipeline import unfinished_run
b = sys.argv[1]
runs = sorted(Path("output").glob(f"{slug(b)}_*/report.json"), key=lambda f: f.parent.name, reverse=True)
print("yes" if runs and time.time() - runs[0].stat().st_mtime < 3 * 86400 and not unfinished_run(Path("output"), b) else "")
PY
)
  if [ "$fresh" = "yes" ]; then
    say "• $brand вече е готов (последните 3 дни) — пропускам"
  else
  while true; do
    wait_free
    tries=$((tries + 1))
    say "▶ $brand (опит $tries)"
    out=$(scripts/run_bg.sh "$brand")
    say "  $out"
    sleep 5
    wait_free
    log=$(cat output/runs/current.log)
    if grep -qE "Спрях заради Facebook|login_required|blocked at|⛔" "$log"; then
      say "⛔ Facebook поиска вход/проверка при $brand — опашката спира (провери акаунта в OpenWork)"
      exit 2
    fi
    if grep -q "Дневният лимит" "$log"; then
      secs=$(( $(date -j -f '%Y-%m-%d %H:%M' "$(date -v+1d '+%Y-%m-%d') 00:15" +%s) - $(date +%s) ))
      say "⏸ дневният лимит е достигнат — чакам до 00:15 (${secs}s) и продължавам $brand"
      sleep "$secs"
      tries=0
      continue
    fi
    .venv/bin/python -m fbreviews dashboard >/dev/null 2>&1
    left=$(.venv/bin/python -m fbreviews resume-target "$brand" 2>/dev/null)
    if [ -n "$left" ] && [ $tries -lt 3 ]; then
      say "  $brand не е довършен ($left) — пускам пак"
      continue
    fi
    break
  done
  fi
  # mark the first pending line with this brand as done
  python3 - "$Q" "$brand" <<'PY'
import sys, datetime
path, brand = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines()
for i, l in enumerate(lines):
    if l.strip() == brand:
        lines[i] = f"# done {datetime.datetime.now():%Y-%m-%d %H:%M} {brand}"
        break
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
  say "✓ $brand — готово; табло: output/index.html"
done
