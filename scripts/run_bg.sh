#!/bin/zsh
# Start a scrape in the background and return at once (for OpenWork's agent, whose commands have a time limit).
#   scripts/run_bg.sh "Zara" [max_locations=0] [max_reviews=0] [auto|lite-hidden] [country]   (0 = all, the default)
#   The mode is automatic: logged in to Facebook in OpenWork -> full mode; logged out -> lite. Country default: Bulgaria.
#   lite        = only public Page data (% recommend, number of reviews, address, phone), in OpenWork's visible browser
#   lite-hidden = the same in a hidden fresh browser with no Facebook account (OpenWork not needed)
#   scripts/run_bg.sh --resume            # continue the latest run
# Progress: scripts/status.sh   Stop: scripts/stop.sh
cd "$(dirname "$0")/.." || exit 1
mkdir -p output/runs
if [ -f output/runs/current.pid ] && kill -0 "$(cat output/runs/current.pid)" 2>/dev/null; then
  echo "ALREADY_RUNNING pid=$(cat output/runs/current.pid) log=$(cat output/runs/current.log)"; exit 0
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "NEEDS_ENV: fill LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in $(pwd)/.env, then run again"; exit 2
fi
ts=$(date +%Y%m%d-%H%M%S)
log="$(pwd)/output/runs/$ts.log"
if [ "$1" = "--resume" ]; then
  last=$(ls -1td output/*_*/ 2>/dev/null | grep -v '/runs/' | head -1)
  [ -n "$last" ] || { echo "NOTHING_TO_RESUME"; exit 3; }
  args=(scrape --resume "$last")
else
  [ -n "$1" ] || { echo 'USAGE: scripts/run_bg.sh "<brand>" [max_locations] [max_reviews] [lite|lite-hidden]'; exit 4; }
  args=(scrape "$1" --max-locations "${2:-0}" --max-reviews "${3:-0}")
  case "$4" in
    lite|--lite) args+=(--lite) ;;
    lite-hidden|hidden) args+=(--lite --hidden) ;;
  esac
  [ -n "$5" ] && [ "$5" != "all" ] && args+=(--country "$5")
  case "${6:-}" in noposts|--no-posts) args+=(--no-posts) ;; esac
fi
nohup zsh scripts/_job.sh "${args[@]}" > "$log" 2>&1 < /dev/null &
echo $! > output/runs/current.pid
echo "$log" > output/runs/current.log
echo "STARTED pid=$! log=$log"
