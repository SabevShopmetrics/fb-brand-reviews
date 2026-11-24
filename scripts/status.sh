#!/bin/zsh
# Progress of the background run: RUNNING / FINISHED / NONE, the key log lines, and the report when done.
cd "$(dirname "$0")/.." || exit 1
[ -f output/runs/current.log ] || { echo "NONE: no run started yet"; exit 0; }
log=$(cat output/runs/current.log); pid=$(cat output/runs/current.pid 2>/dev/null)
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then echo "RUNNING pid=$pid"; else echo "FINISHED"; fi
echo "log: $log"
grep -E "Папка|Браузър|Рунд|→|Спрях|спирам|Готово|Отчет|Excel|EXIT=|✗|Липсват|Не намирам|NEEDS|ERROR|Traceback" "$log" | tail -15
dir=$(grep -o "Папка: .*" "$log" | head -1 | sed 's/Папка: //')
if [ -n "$dir" ] && [ -f "$dir/report.json" ]; then
  echo "report: $(pwd)/$dir/report.json"
  echo "excel:  $(pwd)/$dir/report.xlsx"
fi
