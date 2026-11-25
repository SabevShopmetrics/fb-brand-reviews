"""Command line.

  python -m fbreviews check                       # OpenWork browser + our model reachable?
  python -m fbreviews scrape "Zara"               # all locations (up to --max-locations)
  python -m fbreviews scrape "Zara" --resume output/zara_20261124-150000
  python -m fbreviews finalize output/zara_...    # re-run classification + report only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .config import Settings


def cmd_check(s: Settings, quiet: bool = False, lite: bool = False, hidden: bool = False) -> int:
    ok = True
    if lite and hidden:
        print("Лек режим, невидим браузър без профил във Facebook (OpenWork не е нужен)")
    else:
        print(f"OpenWork CDP: {s.openwork_cdp_url or 'НЕ Е НАМЕРЕН'}")
    if lite and hidden:
        pass
    elif s.openwork_cdp_url:
        from .cdp_bridge import BridgeThread

        try:
            with BridgeThread(s.openwork_cdp_url, s.openwork_tab_match) as b:
                url = (b.lease_info or {}).get("url", "")
                print(f"  ✓ мостът работи; агентът ще ползва таба: {url}")
                if lite:
                    print("  лек режим: Facebook профил не е нужен (ако сте логнати там, пак работи)")
                else:
                    from .cdp_bridge import facebook_logged_in

                    li = facebook_logged_in(s.openwork_cdp_url, (b.lease_info or {}).get("targetId"))
                    if li is True:
                        print("  ✓ Facebook е логнат: пълен режим (ревюта и отговори)")
                    elif li is False:
                        print("  ! Facebook не е логнат: ще мине автоматично на лек режим (публичните данни)")
        except Exception as e:
            ok = False
            print(f"  ✗ {e}")
    else:
        ok = False
    print(f"Модел: {s.llm_model or '—'} @ {s.llm_base_url or '—'}")
    if missing := s.missing_llm():
        ok = False
        print(f"  ✗ липсва в .env: {', '.join(missing)}")
    else:
        import time
        from openai import OpenAI

        client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "not-needed")
        for attempt in range(3):
            try:
                r = client.chat.completions.create(
                    model=s.llm_model, messages=[{"role": "user", "content": "Reply with: ok"}], max_tokens=5
                )
                print(f"  ✓ моделът отговаря: {(r.choices[0].message.content or '').strip()[:40]!r}")
                break
            except Exception as e:
                if attempt == 2:
                    ok = False
                    print(f"  ✗ моделът не отговаря: {e}")
                else:
                    time.sleep(5 * (attempt + 1))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fbreviews", description="Facebook ревюта и отговори по локации на бранд")
    p.add_argument("--env", help="път до .env (по подразбиране ./.env)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="проверка на OpenWork браузъра и модела")

    sc = sub.add_parser("scrape", help="намери локациите и извлечи ревютата")
    sc.add_argument("brand", nargs="?", help='бранд / магазин, напр. "Zara" (без него програмата пита)')
    sc.add_argument("--max-locations", type=int, default=0, help="0 (по подразбиране) = всички локации")
    sc.add_argument("--max-reviews", type=int, default=0, help="0 (по подразбиране) = всички ревюта на локация")
    sc.add_argument("--out", type=Path, default=Path("output"))
    sc.add_argument("--resume", type=Path, help="продължи прекъснато изпълнение (папка в output/)")
    sc.add_argument("--locations-file", type=Path, help="JSON списък с локации [{name,url}] — без търсене")
    sc.add_argument("--hint", help="подсказка за агента при търсенето, напр. 'официалната страница е ZARA'")
    sc.add_argument("--no-llm-classifier", action="store_true", help="само евристики при класификацията")
    sc.add_argument("--lite", action="store_true",
                    help="лек режим: само публичните данни на страниците (%% препоръка, брой ревюта, адрес); "
                         "във вградения браузър на OpenWork, профил не е нужен")
    sc.add_argument("--hidden", action="store_true",
                    help="с --lite: невидим отделен браузър без профил, вместо вградения на OpenWork")

    fn = sub.add_parser("finalize", help="само класификация + отчет за готова папка")
    fn.add_argument("run_dir", type=Path)
    fn.add_argument("--brand")
    fn.add_argument("--no-llm-classifier", action="store_true")

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.load(a.env)

    if a.cmd == "check":
        return cmd_check(s)

    from . import pipeline

    if a.cmd == "scrape":
        if not a.brand and not a.resume:
            a.brand = input("Бранд / магазин (напр. Zara): ").strip()
        if not a.brand and a.resume:
            meta_f = a.resume / "meta.json"
            a.brand = json.loads(meta_f.read_text()).get("brand") if meta_f.exists() else None
        if not a.brand:
            print("Няма бранд.", file=sys.stderr)
            return 2
        if cmd_check(s, quiet=True, lite=a.lite, hidden=a.hidden) != 0:
            print("Проверката не мина — вижте горе какво липсва.", file=sys.stderr)
            return 1
        run_dir = asyncio.run(pipeline.run(
            a.brand, s, out_dir=a.out, resume=a.resume, max_locations=a.max_locations, max_reviews=a.max_reviews,
            locations_file=a.locations_file, hint=a.hint, use_llm_classifier=not a.no_llm_classifier, lite=a.lite, hidden=a.hidden,
        ))
        print(f"\nОтчет: {run_dir / 'report.json'}\nExcel: {run_dir / 'report.xlsx'}")
        return 0

    if a.cmd == "finalize":
        meta_f = a.run_dir / "meta.json"
        brand = a.brand or (json.loads(meta_f.read_text()).get("brand") if meta_f.exists() else None)
        if not brand:
            print("Подайте --brand", file=sys.stderr)
            return 2
        report = asyncio.run(pipeline.finalize(a.run_dir, brand, s, use_llm=not a.no_llm_classifier))
        print(json.dumps(report.summary, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
