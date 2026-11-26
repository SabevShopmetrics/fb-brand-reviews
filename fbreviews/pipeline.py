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
import re
import logging
from datetime import datetime
from pathlib import Path

from .cdp_bridge import BridgeThread, facebook_logged_in
from .classify import classify_replies
from .config import Settings
from .export import summarize, write_excel, write_json
from .fburl import normalize_page_url, page_slug as fburl_slug, slug
from .models import Ad, BrandReport, LocationPage, LocationResult, Post, PostComment, Reply, Review

logger = logging.getLogger("fbreviews.pipeline")
TRANSIENT = ("503", "502", "504", "429", "ModelUnavailable", "Service Unavailable", "overloaded", "rate limit",
             "timed out", "Connection reset")
STOP_STATES = ("login_required", "blocked", "cap_reached")
UNLIMITED = 100000  # "all": no cap on locations or reviews


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
            text=re.sub(r"\s*(Вижте по-малко|Виж по-малко|See less)\s*$", "", r.get("text") or "", flags=re.I) or r.get("text"),
            date_text=r.get("date_text"), permalink=r.get("permalink"),
            comment_count=int(cc) if isinstance(cc, (int, float)) else None,
            date_full=r.get("date_full"),
            tags=[str(t) for t in (r.get("tags") or []) if t][:30],
            reactions=int(r["reactions"]) if isinstance(r.get("reactions"), (int, float)) else None,
            photos=int(r.get("photos") or 0) if isinstance(r.get("photos"), (int, float)) else 0,
            replies_unavailable=bool(r.get("replies_unavailable")),
            replies=replies,
        ))
    return out


STATUSES = {"ok", "partial", "summary_only", "no_own_page", "no_reviews", "reviews_disabled", "login_required", "blocked", "not_found", "error"}


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


def load_results(run_dir: Path, folder: str = "results") -> list[LocationResult]:
    """Agent result files → LocationResult, in the order of locations.json (folder "brand": the brand's own Pages)."""
    ws = run_dir / "workspace"
    order = {}
    try:
        for i, l in enumerate(json.loads((ws / "locations.json").read_text())):
            order[normalize_page_url(l.get("url", "")) or l.get("url")] = i
    except (OSError, ValueError):
        pass
    if folder == "results":
        promote_partials(ws)
    out = []
    for f in sorted((ws / folder).glob("*.json")) if (ws / folder).exists() else []:
        if f.stem.startswith("_"):
            continue
        try:
            d = json.loads(f.read_text())
            loc = d.get("location") or {}
            loc = {k: v for k, v in loc.items() if k in LocationPage.model_fields}
            loc.setdefault("name", loc.get("url", f.stem))
            status = d.get("status") if d.get("status") in STATUSES else "error"
            rt, rp = d.get("review_total"), d.get("recommend_pct")
            out.append(LocationResult(location=LocationPage(**loc), status=status, reviews_url=d.get("reviews_url"),
                                      review_total=int(rt) if isinstance(rt, (int, float)) else None,
                                      recommend_pct=int(rp) if isinstance(rp, (int, float)) else None,
                                      reviews=to_reviews(d.get("reviews") or []), notes=str(d.get("notes") or ""),
                                      is_brand=folder == "brand", posts=load_posts(ws, loc.get("url", "")),
                                      posts_collected=(ws / "posts" / f"{fburl_slug(loc.get('url', ''))}.json").exists(),
                                      ads=[Ad(**{k: v for k, v in a.items() if k in Ad.model_fields})
                                           for a in d.get("ads") or [] if isinstance(a, dict)]))
        except Exception as e:
            logger.warning("skipping %s: %s", f.name, e)
    out.sort(key=lambda r: order.get(r.location.url, 10**9))
    return out


def load_posts(ws: Path, url: str) -> list[Post]:
    """posts/<slug>.json of one Page (Phase 3 / 4) → Post list; [] when the Page has none yet."""
    f = ws / "posts" / f"{fburl_slug(url)}.json" if url else None
    if not f or not f.exists():
        return []
    try:
        raw = json.loads(f.read_text()).get("posts") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        cl = p.get("comments_list")
        comments = None if cl is None else [PostComment(**{k: v for k, v in c.items() if k in PostComment.model_fields})
                                            for c in cl if isinstance(c, dict)]
        fields = {k: v for k, v in p.items() if k in Post.model_fields and k != "comments_list"}
        for k in ("comments", "shares", "photos"):
            fields[k] = int(fields.get(k) or 0)
        out.append(Post(**fields, comments_list=comments))
    return out


def unfinished_run(out_dir: Path, brand: str, max_age_days: float = 7) -> Path | None:
    """The brand's latest run if it is recent and not finished: locations left, posts or comments not collected
    (unless the run was lite / without posts), or no report yet. None = start a new run."""
    import time as _t

    runs = sorted((d for d in Path(out_dir).glob(f"{slug(brand)}_*") if d.is_dir() and (d / "meta.json").exists()),
                  key=lambda d: d.name, reverse=True)
    if not runs:
        return None
    d = runs[0]
    if _t.time() - (d / "meta.json").stat().st_mtime > max_age_days * 86400:
        return None
    try:
        meta = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return None
    p = progress_of(d)
    content = not meta.get("lite") and meta.get("posts", True) is not False
    left = (p["locations"] and p["done"] < p["locations"] and not locations_stuck(meta, p)) or (content and (p["posts_todo"] or p["comments_todo"])) \
        or not (d / "report.json").exists() or (p["locations"] == 0 and not meta.get("rounds"))
    return d if left else None


def locations_stuck(meta: dict, p: dict) -> bool:
    """The last 3 agent rounds saved no new location (and the model was not down): the locations left cannot be
    collected (duplicates, no reviews tab, gone). Running the brand again would only repeat those rounds."""
    rounds = [r for r in meta.get("rounds", []) if isinstance(r, dict)][-3:]
    if len(rounds) < 3:
        return False
    if any(any(t in str(r.get("notes", "")) for t in TRANSIENT) for r in rounds):
        return False
    return all(r.get("after_done") == p["done"] and r.get("after_locations") == p["locations"] for r in rounds)


def progress_of(run_dir: Path) -> dict:
    ws = run_dir / "workspace"
    try:
        locs = json.loads((ws / "locations.json").read_text())
    except (OSError, ValueError):
        locs = []
    done = len([f for f in (ws / "results").glob("*.json") if not f.stem.startswith("_")]) if (ws / "results").exists() else 0
    stop = (ws / "STOP").read_text() if (ws / "STOP").exists() else None
    return {"locations": len(locs), "done": done, "stopped": stop, **content_todo(ws)}


CONTENT_STATUSES = ("ok", "partial", "no_reviews", "reviews_disabled", "summary_only")


def content_todo(ws: Path) -> dict:
    """Phase 3 / 4 work left: saved Pages without a posts file, posts with comments not read yet."""
    pages = set()
    for folder in ("brand", "results"):
        for f in (ws / folder).glob("*.json") if (ws / folder).exists() else []:
            if f.stem.startswith("_"):
                continue
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("status") in CONTENT_STATUSES:
                url = normalize_page_url((d.get("location") or {}).get("url", "")) or ""
                if url:
                    pages.add(fburl_slug(url))
    have = {f.stem for f in (ws / "posts").glob("*.json")} if (ws / "posts").exists() else set()
    comments = 0
    for f in (ws / "posts").glob("*.json") if (ws / "posts").exists() else []:
        try:
            for p in json.loads(f.read_text()).get("posts", []):
                if (p.get("comments") or 0) > 0 and p.get("permalink") and p.get("comments_list") is None:
                    comments += 1
        except (OSError, ValueError, AttributeError):
            continue
    return {"posts_todo": len(pages - have), "comments_todo": comments}


def dedupe_pages(brand_results: list[LocationResult], results: list[LocationResult]) -> list[LocationResult]:
    """One Facebook Page = one set of reviews. A store without a Page of its own (the agent gave it the brand Page's
    URL, or another store's) is kept as "no_own_page" with no reviews, so nothing is counted twice; a "location"
    that IS a brand Page (same URL and name) is dropped — it is already in the brand section."""
    brand = {normalize_page_url(b.location.url) or b.location.url: b for b in brand_results}
    seen: dict[str, str] = {}
    out = []
    for r in results:
        url = normalize_page_url(r.location.url) or r.location.url
        owner = brand.get(url)
        if owner and r.location.name.strip().lower() == owner.location.name.strip().lower():
            continue
        if owner or url in seen:
            who = f"на бранда „{owner.location.name}“" if owner else f"на „{seen[url]}“"
            r = r.model_copy(update={
                "status": "no_own_page", "reviews": [], "review_total": None, "recommend_pct": None, "posts": [], "ads": [],
                "notes": f"няма собствена Facebook страница — ревютата са в страницата {who}"})
        else:
            seen[url] = r.location.name
        out.append(r)
    return out


async def finalize(run_dir: Path, brand: str, s: Settings, use_llm: bool = True) -> BrandReport:
    brand_results = load_results(run_dir, "brand")
    results = dedupe_pages(brand_results, load_results(run_dir))
    llm = None
    if use_llm and s.llm_base_url and s.classifier_model:
        llm = {"base_url": s.llm_base_url, "api_key": s.llm_api_key, "model": s.classifier_model}
    # brand Pages and locations are classified together: a template reply shared by both is one template
    method = await classify_replies(brand_results + results, brand, llm=llm)
    from .insights import analyze_posts, analyze_reviews, build_insights, business_summary

    try:  # what the reviews and posts say: from the text only; never let it break the report
        imethod = await analyze_reviews(brand_results + results, llm=llm, cache_path=run_dir / "insights_cache.json")
        cmethod = await analyze_posts(brand_results + results, llm=llm, cache_path=run_dir / "posts_cache.json")
        insights = build_insights(brand_results, results, imethod, cmethod)
    except Exception as e:  # noqa: BLE001
        logger.warning("insights failed: %s", e)
        insights = {}
    meta = json.loads((run_dir / "meta.json").read_text()) if (run_dir / "meta.json").exists() else {}
    brand_urls = [r.location.url for r in brand_results]
    report = BrandReport(brand=brand, model=s.llm_model, locations=results, brand_sections=brand_results,
                         classification_method=method, insights=insights,
                         brand_pages=sorted(set(meta.get("brand_pages", [])) | set(brand_urls)))
    report.summary = summarize(report)
    write_json(report, run_dir / "report.json")
    if insights and llm:
        try:  # what the numbers say about the business (model, cached by its input)
            biz = await business_summary(json.loads((run_dir / "report.json").read_text()), llm=llm,
                                         cache_path=run_dir / "summary_cache.json")
            if biz:
                report.insights = {**report.insights, "business": biz}
                write_json(report, run_dir / "report.json")
        except Exception as e:  # noqa: BLE001
            logger.warning("business summary failed: %s", e)
    write_excel(report, run_dir / "report.xlsx")
    from .html_report import write_html

    write_html(json.loads((run_dir / "report.json").read_text()), run_dir / "report.html")
    try:  # output/index.html — every brand so far, one tab each; never let it break the run
        from .html_report import write_index

        write_index(run_dir.parent)
    except Exception as e:  # noqa: BLE001
        logger.warning("dashboard not rebuilt: %s", e)
    return report


async def _content_rounds(run_dir, brand, s, llm, browser, ws, config, country, progress) -> None:
    """Phase 3 / 4 after every location's reviews: the posts of each saved Page, then the comments under them.
    Separate agent rounds with their own short task, so a long content phase never delays the reviews."""
    from .agents import run_agent

    idle = 0
    for k in range(1, s.max_rounds + 1):
        before = progress_of(run_dir)
        if before["stopped"] or (not before["posts_todo"] and not before["comments_todo"]):
            return
        progress(f"Съдържание, рунд {k}: постове за {before['posts_todo']} страници, "
                 f"коментари под {before['comments_todo']} поста…")
        tail_stop = asyncio.Event()
        tail = asyncio.create_task(_tail_progress(ws / "progress.log", tail_stop, progress))
        try:
            await run_agent(brand, s=s, llm=llm, browser=browser, workspace=ws, country=country, max_locations=0,
                            hint=None, round_no=k, locations_given=True, config=config, phase="content")
        except Exception as e:  # noqa: BLE001
            logger.exception("content round failed")
            progress(f"   рундът за съдържание спря: {type(e).__name__}: {e}")
        finally:
            tail_stop.set()
            await tail
        after = progress_of(run_dir)
        try:
            await finalize(run_dir, brand, s, use_llm=False)
            progress(f"   междинен отчет: {run_dir / 'report.html'}")
        except Exception as e:  # noqa: BLE001
            logger.warning("interim report failed: %s", e)
        if (after["posts_todo"], after["comments_todo"]) == (before["posts_todo"], before["comments_todo"]):
            idle += 1
            if idle >= 3:
                progress("   3 рунда без напредък в съдържанието — спирам и правя отчет.")
                return
        else:
            idle = 0


async def _tail_progress(path: Path, stop: asyncio.Event, progress) -> None:
    """Show the agent's progress lines (progress.log, written by the skill helpers) live in the run log."""
    pos = path.stat().st_size if path.exists() else 0
    while True:
        done = stop.is_set()
        if path.exists():
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                if line.strip():
                    progress(f"   • {line}")
        if done:
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass


async def run(
    brand: str,
    s: Settings,
    *,
    out_dir: Path = Path("output"),
    resume: Path | None = None,
    max_locations: int = 0,
    max_reviews: int = 0,
    locations_file: Path | None = None,
    hint: str | None = None,
    use_llm_classifier: bool = True,
    lite: bool = False,
    hidden: bool = False,
    country: str | None = None,
    progress=print,
    posts: bool = True,
) -> Path:
    from browser_use import Browser

    from .agents import make_llm, run_agent

    max_locations = max_locations if max_locations and max_locations > 0 else UNLIMITED  # 0 = everything
    max_reviews = max_reviews if max_reviews and max_reviews > 0 else UNLIMITED
    if missing := s.missing_llm():
        raise SystemExit(f"Липсват настройки в .env: {', '.join(missing)}")
    if not (lite and hidden) and not s.openwork_cdp_url:
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
    if resume and meta.get("lite"):
        lite = True  # a lite run continues as lite
        hidden = hidden or bool(meta.get("hidden"))
    country = country or meta.get("country") or s.country
    if resume and meta.get("posts") is False:
        posts = False  # a run started without posts continues without them
    meta.update({"brand": brand, "max_locations": max_locations, "max_reviews": max_reviews, "model": s.llm_model,
                 "country": country, "posts": posts,
                 "lite": lite, "hidden": hidden})
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
              "scroll_max_s": s.location_max_s,
              "max_reviews": max_reviews, "max_locations": max_locations, "lite": lite,
              "posts": posts, "post_comments": posts}
    progress(f"Папка: {run_dir}")

    bridge = None
    if lite and hidden:
        # Hidden lite mode: a fresh local Chrome/Chromium, temporary profile — no Facebook login, no cookies, no window.
        browser = Browser("chromium", headless=True, keep_alive=True)  # local: no CAPTCHA solver exists here
        progress("Браузър: невидим, нов, без профил във Facebook (лек режим: само публичните данни на страниците)")
    else:
        # OpenWork's built-in browser, visible in its panel (full mode, and lite mode by default).
        bridge = BridgeThread(s.openwork_cdp_url, s.openwork_tab_match)
        bridge.start()
        logged_in = await asyncio.to_thread(
            facebook_logged_in, s.openwork_cdp_url, (bridge.lease_info or {}).get("targetId"))
        if lite and logged_in is True:
            # Logged in: always take everything (reviews, replies) — whoever asked for lite.
            lite = False
            config["lite"] = False
            meta["lite"] = False
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            progress("Facebook в браузъра на OpenWork е логнат → пълен режим (всички ревюта и отговорите на страницата)")
        if not lite and logged_in is False:
            # Full mode needs a logged-in Facebook; without one, take what a visitor can see instead of stopping.
            lite = True
            config["lite"] = True
            meta["lite"] = True
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            progress("Facebook в браузъра на OpenWork не е логнат → минавам автоматично на лек режим "
                     "(публичните данни на страниците: % препоръка, брой ревюта, адрес, телефон)")
        mode = "лек режим: само публичните данни на страниците" if lite else "пълен режим"
        progress(f"Браузър: вграденият на OpenWork ({mode}), таб {(bridge.lease_info or {}).get('url', '?')}")
        browser = Browser(cdp_url=bridge.url, keep_alive=True)
    llm = make_llm(s)
    try:
        idle_rounds = 0
        for round_no in range(len(meta["rounds"]) + 1, len(meta["rounds"]) + 1 + s.max_rounds):
            before = progress_of(run_dir)
            if resume and before["locations"] and before["done"] >= before["locations"] and not before["stopped"]:
                progress(f"Всички {before['locations']} локации вече са готови — минавам направо към съдържанието.")
                break
            progress(f"Рунд {round_no}: агентът работи… (локации {before['locations']}, готови {before['done']})")
            tail_stop = asyncio.Event()
            tail = asyncio.create_task(_tail_progress(ws / "progress.log", tail_stop, progress))
            try:
                summary = await run_agent(brand, s=s, llm=llm, browser=browser, workspace=ws, country=country,
                                          max_locations=max_locations, hint=hint, round_no=round_no,
                                          locations_given=locations_given, config=config)
                sm = summary.model_dump()
            except Exception as e:
                logger.exception("agent round failed")
                sm = {"stop_reason": "error", "notes": f"{type(e).__name__}: {e}", "complete": False}
            finally:
                tail_stop.set()
                await tail
            if sm.get("brand_pages"):
                meta["brand_pages"] = sorted(set(meta.get("brand_pages", [])) | set(sm["brand_pages"]))
            after = progress_of(run_dir)
            meta["rounds"].append({"round": round_no, **sm, **{f"after_{k}": v for k, v in after.items()}})
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            progress(f"   → локации {after['locations']}, готови {after['done']}. {str(sm.get('notes', ''))[:300]}")
            if after["done"]:
                try:  # interim report from what is saved so far (the model classification runs at the end)
                    await finalize(run_dir, brand, s, use_llm=False)
                    progress(f"   междинен отчет: {run_dir / 'report.html'}")
                except Exception as e:
                    logger.warning("interim report failed: %s", e)

            if after["stopped"] or sm.get("stop_reason") in STOP_STATES:
                progress(f"Спрях заради Facebook: {after['stopped'] or sm.get('stop_reason')}. "
                         "Проверете акаунта в OpenWork и продължете по-късно с --resume.")
                break
            all_done = after["locations"] > 0 and after["done"] >= after["locations"]
            if sm.get("complete") or all_done:
                break
            no_progress = after["done"] == before["done"] and after["locations"] == before["locations"]
            if no_progress:
                idle_rounds += 1
                if any(t in str(sm.get("notes", "")) for t in TRANSIENT):
                    progress("   моделът не отговаря; нов опит след 60 сек…")
                    await asyncio.sleep(60)
                    continue
                if idle_rounds >= 3:
                    progress("   3 рунда подред без напредък — спирам и правя отчет с това, което е събрано.")
                    break
                progress(f"   рундът не донесе напредък — опитвам отново ({idle_rounds}/3)…")
            else:
                idle_rounds = 0
        if not lite and not progress_of(run_dir)["stopped"] and posts:
            await _content_rounds(run_dir, brand, s, llm, browser, ws, config, country, progress)
    finally:
        try:
            await browser.stop()
        finally:
            if bridge is not None:
                bridge.stop()

    progress("Класифицирам отговорите (генериран / ръчен)…")
    report = await finalize(run_dir, brand, s, use_llm=use_llm_classifier)
    progress(f"Готово: {report.summary}")
    return run_dir
