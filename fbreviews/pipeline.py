"""Brand → one agent with the Facebook skill → classification → one report.

Run folder (resumable):
  output/<brand>_<timestamp>/
    workspace/                  the agent's working dir
      locations.json            locations the agent found
      results/<slug>.json       one finished location each (reviews + replies + status)
      pace.json, STOP           pacing state / safety stop
    meta.json                   brand, settings, round summaries
    report.json                 the single answer: all locations, each with its reviews and replies
    report.xlsx
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from .cdp_bridge import BridgeThread
from .classify import classify_replies
from .config import Settings
from .export import summarize, write_excel, write_json
from .fburl import normalize_page_url, slug
from .models import BrandReport, LocationPage, LocationResult, Reply, Review

logger = logging.getLogger("fbreviews.pipeline")
TRANSIENT = ("503", "502", "504", "429", "ModelUnavailable", "Service Unavailable", "overloaded", "rate limit",
             "timed out", "Connection reset")
STOP_STATES = ("login_required", "blocked", "cap_reached")


def new_run_dir(out_dir: Path, brand: str) -> Path:
    d = out_dir / f"{slug(brand)}_{datetime.now():%Y%m%d-%H%M%S}"
    (d / "workspace" / "results").mkdir(parents=True, exist_ok=True)
    return d


def to_reviews(raw: list) -> list[Review]:
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        replies = [
            Reply(author=c.get("author"), text=str(c.get("text") or ""), date_text=c.get("date_text"),
                  permalink=c.get("permalink"), depth=int(c.get("depth") or 1), truncated=bool(c.get("truncated")))
            for c in (r.get("replies") or []) if isinstance(c, dict)
        ]
        rec, rating, cc = r.get("recommends"), r.get("rating"), r.get("comment_count")
        out.append(Review(
            reviewer=r.get("reviewer"),
            recommends=rec if isinstance(rec, bool) else None,
            rating=int(rating) if isinstance(rating, (int, float)) or (isinstance(rating, str) and rating.isdigit()) else None,
            text=r.get("text"), date_text=r.get("date_text"), permalink=r.get("permalink"),
            comment_count=int(cc) if isinstance(cc, (int, float)) else None,
            replies_unavailable=bool(r.get("replies_unavailable")),
            replies=replies,
        ))
    return out


STATUSES = {"ok", "partial", "no_reviews", "reviews_disabled", "login_required", "blocked", "not_found", "error"}


def promote_partials(ws: Path) -> None:
    """results/_partial_<slug>.json (reviews read, run stopped before saving) → results/<slug>.json, status partial."""
    try:
        locs = {}
        for l in json.loads((ws / "locations.json").read_text()):
            from .fburl import page_slug
            locs[page_slug(l.get("url", ""))] = l
    except (OSError, ValueError):
        locs = {}
    for f in (ws / "results").glob("_partial_*.json"):
        sl = f.stem[len("_partial_"):]
        final = ws / "results" / f"{sl}.json"
        if final.exists():
            f.unlink()
            continue
        try:
            reviews = json.loads(f.read_text())
        except ValueError:
            continue
        loc = locs.get(sl, {"name": sl, "url": sl})
        final.write_text(json.dumps({"location": loc, "status": "partial", "reviews": reviews,
                                     "notes": "run stopped before this location was finished; reply threads not read"},
                                    ensure_ascii=False, indent=1))
        f.unlink()


def load_results(run_dir: Path) -> list[LocationResult]:
    """Agent result files → LocationResult, in the order of locations.json."""
    ws = run_dir / "workspace"
    order = {}
    try:
        for i, l in enumerate(json.loads((ws / "locations.json").read_text())):
            order[normalize_page_url(l.get("url", "")) or l.get("url")] = i
    except (OSError, ValueError):
        pass
    promote_partials(ws)
    out = []
    for f in sorted((ws / "results").glob("*.json")):
        if f.stem.startswith("_"):
            continue
        try:
            d = json.loads(f.read_text())
            loc = d.get("location") or {}
            loc = {k: v for k, v in loc.items() if k in LocationPage.model_fields}
            loc.setdefault("name", loc.get("url", f.stem))
            status = d.get("status") if d.get("status") in STATUSES else "error"
            rt = d.get("review_total")
            out.append(LocationResult(location=LocationPage(**loc), status=status, reviews_url=d.get("reviews_url"),
                                      review_total=int(rt) if isinstance(rt, (int, float)) else None,
                                      reviews=to_reviews(d.get("reviews") or []), notes=str(d.get("notes") or "")))
        except Exception as e:
            logger.warning("skipping %s: %s", f.name, e)
    out.sort(key=lambda r: order.get(r.location.url, 10**9))
    return out


def progress_of(run_dir: Path) -> dict:
    ws = run_dir / "workspace"
    try:
        locs = json.loads((ws / "locations.json").read_text())
    except (OSError, ValueError):
        locs = []
    done = len([f for f in (ws / "results").glob("*.json") if not f.stem.startswith("_")]) if (ws / "results").exists() else 0
    stop = (ws / "STOP").read_text() if (ws / "STOP").exists() else None
    return {"locations": len(locs), "done": done, "stopped": stop}


async def finalize(run_dir: Path, brand: str, s: Settings, use_llm: bool = True) -> BrandReport:
    results = load_results(run_dir)
    llm = None
    if use_llm and s.llm_base_url and s.classifier_model:
        llm = {"base_url": s.llm_base_url, "api_key": s.llm_api_key, "model": s.classifier_model}
    method = await classify_replies(results, brand, llm=llm)
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    report = BrandReport(brand=brand, model=s.llm_model, locations=results, classification_method=method,
                         brand_pages=meta.get("brand_pages", []))
    report.summary = summarize(report)
    write_json(report, run_dir / "report.json")
    write_excel(report, run_dir / "report.xlsx")
    return report


async def run(
    brand: str,
    s: Settings,
    *,
    out_dir: Path = Path("output"),
    resume: Path | None = None,
    max_locations: int = 200,
    max_reviews: int = 500,
    locations_file: Path | None = None,
    hint: str | None = None,
    use_llm_classifier: bool = True,
    progress=print,
) -> Path:
    from browser_use import Browser

    from .agents import make_llm, run_agent

    if missing := s.missing_llm():
        raise SystemExit(f"Липсват настройки в .env: {', '.join(missing)}")
    if not s.openwork_cdp_url:
        raise SystemExit("Не намирам браузъра на OpenWork. Пуснат ли е OpenWork? Или задайте OPENWORK_CDP_URL.")

    run_dir = resume or new_run_dir(out_dir, brand)
    ws = run_dir / "workspace"
    (ws / "results").mkdir(parents=True, exist_ok=True)
    stop_file = ws / "STOP"
    if resume and stop_file.exists():
        progress(f"Предишното пускане спря: {stop_file.read_text()}. Продължавам (уверете се, че Facebook е наред).")
        stop_file.unlink()
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"rounds": []}
    meta.update({"brand": brand, "max_locations": max_locations, "max_reviews": max_reviews, "model": s.llm_model})
    locations_given = False
    if locations_file:
        raw = json.loads(Path(locations_file).read_text())
        locs = [x if isinstance(x, dict) else {"name": x, "url": x} for x in raw]
        (ws / "locations.json").write_text(json.dumps(locs, ensure_ascii=False, indent=1))
        locations_given = True
    elif (ws / "locations.json").exists() and meta.get("locations_given"):
        locations_given = True
    meta["locations_given"] = locations_given
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    if locations_given:  # a given list is the whole list: the helpers will not add more
        max_locations = len(json.loads((ws / "locations.json").read_text()))
    config = {"pace_min_s": s.pace_min_s, "pace_max_s": s.pace_max_s, "daily_page_cap": s.daily_page_cap,
              "max_reviews": max_reviews, "max_locations": max_locations}
    progress(f"Папка: {run_dir}")

    bridge = BridgeThread(s.openwork_cdp_url, s.openwork_tab_match)
    bridge.start()
    progress(f"Браузър: OpenWork, таб {(bridge.lease_info or {}).get('url', '?')}")
    browser = Browser(cdp_url=bridge.url, keep_alive=True)
    llm = make_llm(s)
    try:
        for round_no in range(len(meta["rounds"]) + 1, len(meta["rounds"]) + 1 + s.max_rounds):
            before = progress_of(run_dir)
            progress(f"Рунд {round_no}: агентът работи… (локации {before['locations']}, готови {before['done']})")
            try:
                summary = await run_agent(brand, s=s, llm=llm, browser=browser, workspace=ws,
                                          max_locations=max_locations, hint=hint, round_no=round_no,
                                          locations_given=locations_given, config=config)
                sm = summary.model_dump()
            except Exception as e:
                logger.exception("agent round failed")
                sm = {"stop_reason": "error", "notes": f"{type(e).__name__}: {e}", "complete": False}
            if sm.get("brand_pages"):
                meta["brand_pages"] = sorted(set(meta.get("brand_pages", [])) | set(sm["brand_pages"]))
            after = progress_of(run_dir)
            meta["rounds"].append({"round": round_no, **sm, **{f"after_{k}": v for k, v in after.items()}})
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            progress(f"   → локации {after['locations']}, готови {after['done']}. {str(sm.get('notes', ''))[:300]}")

            if after["stopped"] or sm.get("stop_reason") in STOP_STATES:
                progress(f"Спрях заради Facebook: {after['stopped'] or sm.get('stop_reason')}. "
                         "Проверете акаунта в OpenWork и продължете по-късно с --resume.")
                break
            all_done = after["locations"] > 0 and after["done"] >= after["locations"]
            if sm.get("complete") or all_done:
                break
            no_progress = after["done"] == before["done"] and after["locations"] == before["locations"]
            if no_progress:
                if any(t in str(sm.get("notes", "")) for t in TRANSIENT):
                    progress("   моделът не отговаря; нов опит след 60 сек…")
                    await asyncio.sleep(60)
                    continue
                progress("   рундът не донесе напредък — спирам, за да не въртя напразно.")
                break
    finally:
        try:
            await browser.stop()
        finally:
            bridge.stop()

    progress("Класифицирам отговорите (генериран / ръчен)…")
    report = await finalize(run_dir, brand, s, use_llm=use_llm_classifier)
    progress(f"Готово: {report.summary}")
    return run_dir
