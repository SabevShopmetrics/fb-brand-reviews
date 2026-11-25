#!/bin/zsh
# Двойно щракване във Finder: пита за бранд и пуска агента. Изисква OpenWork с отворен Facebook.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Първо пускане: инсталирам (2–3 минути)…"
  PY=""
  for c in python3.13 python3.12 python3.11 python3; do
    if command -v $c >/dev/null 2>&1 && $c -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)'; then PY=$c; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Нужен е Python 3.11–3.13. Инсталирайте от https://www.python.org/downloads/ (или: brew install python@3.12) и пуснете отново."
    read; exit 1
  fi
  $PY -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt || { echo "Инсталацията не мина."; read; exit 1; }
fi
[ -f .env ] || { cp .env.example .env; echo "Попълнете .env (модел и ключ) и пуснете отново."; open -e .env; read; exit 1; }
echo "Последни изпълнения (за продължаване):"; ls -1t output 2>/dev/null | grep -v '\.log$' | head -5
echo
read "brand?Бранд (напр. Zara) или Enter за продължаване на последното: "
if [ -z "$brand" ]; then
  last=$(ls -1td output/*/ 2>/dev/null | head -1)
  [ -n "$last" ] || { echo "Няма какво да продължа."; read; exit 1; }
  .venv/bin/python -m fbreviews scrape --resume "$last"
else
  read "maxloc?Колко локации максимум [200]: "
  read "lite?Без профил във Facebook (само % препоръка, брой ревюта, адрес)? [y/N]: "
  if [[ "$lite" == [yYдД]* ]]; then
    .venv/bin/python -m fbreviews scrape "$brand" --max-locations "${maxloc:-200}" --lite
  else
    .venv/bin/python -m fbreviews scrape "$brand" --max-locations "${maxloc:-200}"
  fi
fi
echo; echo "Готово. Натиснете Enter за затваряне."; read
