"""What the reviews say and how the brand answers — computed from the extracted data only (no new Facebook visits).

* analyze_reviews(): per review — overall tone, aspects (topic + polarity: "food +", "wait −") and a one-line
  summary. The model (the same OpenAI-compatible endpoint as the reply classifier) reads reviews in batches;
  without it, keyword + Facebook-tag heuristics. Results are cached per review in the run folder, so the interim
  report after every round only pays for new reviews.
* build_insights(): per Page — tone counts, top topics in plus / minus, reply rate (overall and for negative
  reviews), template share, approximate reply delay; and signals (a location getting worse lately, a topic that
  spikes, negative reviews left without an answer).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .models import LocationResult, Review

logger = logging.getLogger("fbreviews.insights")

TOPICS = {
    "food": "храна", "service": "обслужване", "wait": "чакане", "price": "цена", "cleanliness": "чистота",
    "staff": "персонал", "atmosphere": "атмосфера", "delivery": "доставка", "availability": "наличност", "other": "друго",
}
SENTIMENTS = ("positive", "neutral", "negative")

# heuristics (no model): words in the review text or in Facebook's review tags
KEYWORDS = {
    "food": r"храна|ястие|ястия|вкус|вкусн|пица|бургер|салат|десерт|сладолед|кафе|меню|порци|месо|food|taste|tasty|dish|pizza|burger|dessert|menu|portion|delicious|meal",
    "service": r"обслужв|сервиз|сервитьор|келнер|service|served|waiter|waitress|server",
    "wait": r"чака|бавн|минути|час и|опашк|забав|wait|slow|queue|minutes|delay|took forever",
    "price": r"цен[аи]|скъп|евтин|сметка|лв\.?|лева|евро|price|expensive|cheap|overpriced|bill|value for money",
    "cleanliness": r"чист|мръсн|хигиен|тоалетн|clean|dirty|hygien|toilet|restroom",
    "staff": r"персонал|служител|управител|мениджър|отношение|любезн|груб|staff|employee|manager|rude|friendly|polite|attitude",
    "atmosphere": r"атмосфер|обстановк|интериор|музик|уют|шум|градина|тераса|atmosphere|ambien|interior|music|cozy|noisy|vibe|terrace",
    "delivery": r"доставк|достав|куриер|поръчах онлайн|delivery|delivered|courier|takeaway|take away",
    "availability": r"нямаше|свърши|наличн|липсва|out of stock|sold out|unavailable|not available|didn't have",
}
POS_WORDS = r"страхотн|супер|прекрасн|отличн|вкусн|препоръчвам|любезн|бърз|great|excellent|amazing|delicious|friendly|recommend|perfect|love"
NEG_WORDS = r"ужас|отвратит|груб|бавн|мръсн|студен|разочаров|никога повече|не препоръчвам|лош|terrible|awful|rude|slow|dirty|cold|disappoint|never again|worst|bad"

SYSTEM = """You analyse customer reviews of a brand's Facebook Pages (restaurants, shops, services).
For every item return: "sentiment": positive | neutral | negative (the review as a whole; "recommends" is the
customer's own yes/no and usually agrees); "aspects": the topics the review talks about, each with its own polarity
— topics only from: food, service, wait, price, cleanliness, staff, atmosphere, delivery, availability, other;
polarity positive | negative | neutral; at most 4, only what the text (or the tags) really says; and "summary":
what the customer says, in Bulgarian, at most 12 words, no names.
Return only JSON: {"items": [{"id": 0, "sentiment": "...", "aspects": [{"topic": "...", "sentiment": "..."}], "summary": "..."}]}"""


def _key(rv: Review) -> str:
    raw = json.dumps([rv.text or "", rv.recommends, rv.tags], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def heuristic(rv: Review) -> dict:
    text = " ".join([rv.text or "", " ".join(rv.tags or [])]).lower()
    pos, neg = len(re.findall(POS_WORDS, text)), len(re.findall(NEG_WORDS, text))
    if rv.recommends is True:
        sent = "positive" if neg <= pos + 1 else "neutral"
    elif rv.recommends is False:
        sent = "negative"
    else:
        sent = "positive" if pos > neg else "negative" if neg > pos else "neutral"
    aspects = [{"topic": t, "sentiment": sent} for t, rx in KEYWORDS.items() if re.search(rx, text)][:4]
    return {"sentiment": sent, "aspects": aspects, "summary": None}


def _clean(item: dict, rv: Review) -> dict:
    sent = item.get("sentiment") if item.get("sentiment") in SENTIMENTS else heuristic(rv)["sentiment"]
    aspects, seen = [], set()
    for a in item.get("aspects") or []:
        t = (a or {}).get("topic")
        if t in TOPICS and t not in seen:
            seen.add(t)
            aspects.append({"topic": t, "sentiment": a.get("sentiment") if a.get("sentiment") in SENTIMENTS else sent})
    summary = item.get("summary")
    return {"sentiment": sent, "aspects": aspects[:4], "summary": str(summary)[:160] if summary else None}


async def _llm_batch(batch: list[dict], *, base_url: str, api_key: str, model: str) -> dict[int, dict]:
    from openai import AsyncOpenAI

    from .classify import _parse_json

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")
    msg = json.dumps({"items": batch}, ensure_ascii=False)
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=model, temperature=0,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}])
            data = _parse_json(resp.choices[0].message.content or "")
            return {int(x["id"]): x for x in data.get("items", []) if isinstance(x, dict) and "id" in x}
        except Exception as e:  # malformed JSON, transient server error
            logger.warning("insights batch failed (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(2 * (attempt + 1))
    return {}


async def analyze_reviews(results: list[LocationResult], *, llm: dict | None = None, cache_path: Path | None = None,
                          batch_size: int = 20, concurrency: int = 4) -> str:
    """Fill review.sentiment / aspects / summary in place. Returns the method used."""
    cache: dict[str, dict] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    reviews = [rv for res in results for rv in res.reviews]
    todo = []
    for rv in reviews:
        k = _key(rv)
        if k in cache:
            continue
        if llm and (rv.text or "").strip():
            todo.append((k, rv))
        else:  # no text (only a yes/no and maybe tags): nothing for a model to read
            cache[k] = {**heuristic(rv), "by": "heuristic"}
    if todo:
        sem = asyncio.Semaphore(concurrency)

        async def run(chunk):
            payload = [{"id": i, "recommends": rv.recommends, "tags": rv.tags, "text": (rv.text or "")[:1500]}
                       for i, (_, rv) in enumerate(chunk)]
            async with sem:
                got = await _llm_batch(payload, **llm)
            for i, (k, rv) in enumerate(chunk):
                cache[k] = {**_clean(got[i], rv), "by": "model"} if i in got else {**heuristic(rv), "by": "heuristic"}

        await asyncio.gather(*(run(todo[j:j + batch_size]) for j in range(0, len(todo), batch_size)))
    for rv in reviews:
        c = cache.get(_key(rv)) or heuristic(rv)
        rv.sentiment, rv.aspects, rv.summary = c["sentiment"], c["aspects"], c.get("summary")
    if cache_path:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    by_model = sum(1 for rv in reviews if (cache.get(_key(rv)) or {}).get("by") == "model")
    if not reviews:
        return ""
    return (f"model ({llm['model']}) for {by_model} of {len(reviews)} reviews, keywords for the rest" if llm
            else "keywords and Facebook tags (no model)")


# ------------------------------------------------------------------ dates
MONTHS = {m: i for i, m in enumerate(["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември",
                                      "октомври", "ноември", "декември"], 1)}
MONTHS.update({m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"], 1)})
REL = {"мин": 1 / 1440, "ч": 1 / 24, "д": 1, "дн": 1, "сед": 7, "сд": 7, "м": 30, "мес": 30, "г": 365,
       "m": 1 / 1440, "min": 1 / 1440, "h": 1 / 24, "hr": 1 / 24, "d": 1, "w": 7, "wk": 7, "mo": 30, "y": 365, "yr": 365}


def review_date(rv: Review, ref: datetime) -> datetime | None:
    s = (rv.date_full or rv.date_text or "").lower()
    m = re.search(r"(\d{1,2})\s+([a-zа-я]+)\s*,?\s*(\d{4})?", s)
    if m and m.group(2) in MONTHS:
        return datetime(int(m.group(3) or ref.year), MONTHS[m.group(2)], int(m.group(1)), tzinfo=ref.tzinfo)
    m = re.search(r"([a-z]+)\s+(\d{1,2}),?\s*(\d{4})?", s)
    if m and m.group(1) in MONTHS:
        return datetime(int(m.group(3) or ref.year), MONTHS[m.group(1)], int(m.group(2)), tzinfo=ref.tzinfo)
    return _relative(s, ref, max_days=10_000)


def _relative(s: str, ref: datetime, max_days: float) -> datetime | None:
    m = re.match(r"^\s*(\d+)\s*([a-zа-я]+)\.?\s*$", s or "")
    if not m or m.group(2) not in REL or REL[m.group(2)] > max_days:
        return None
    return ref - timedelta(days=int(m.group(1)) * REL[m.group(2)])


def _ref(res: LocationResult) -> datetime:
    try:
        return datetime.fromisoformat(res.scraped_at)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


# ------------------------------------------------------------------ per Page numbers + signals
def page_insights(res: LocationResult) -> dict:
    ref = _ref(res)
    revs = res.reviews
    sent = {s: sum(1 for r in revs if getattr(r, "sentiment", None) == s) for s in SENTIMENTS}
    pos_t: dict[str, int] = {}
    neg_t: dict[str, int] = {}
    for r in revs:
        for a in r.aspects or []:
            d = pos_t if a.get("sentiment") == "positive" else neg_t if a.get("sentiment") == "negative" else None
            if d is not None:
                d[a["topic"]] = d.get(a["topic"], 0) + 1
    replied = [r for r in revs if any(x.is_page_reply for x in r.replies)]
    negs = [r for r in revs if r.sentiment == "negative" or (r.sentiment is None and r.recommends is False)]
    neg_replied = [r for r in negs if any(x.is_page_reply for x in r.replies)]
    page_replies = [x for r in revs for x in r.replies if x.is_page_reply]
    templ = sum(1 for x in page_replies if x.reply_type == "generated")
    delays = []
    for r in replied:  # only replies with an hour / day / week stamp: a "1 г." stamp is too coarse to measure
        d0 = review_date(r, ref)
        x = next(x for x in r.replies if x.is_page_reply)
        d1 = _relative((x.date_text or "").lower(), ref, max_days=7)
        if d0 and d1:
            delays.append(max(0.0, (d1 - d0).total_seconds() / 86400))
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:3]]  # noqa: E731
    pct = lambda a, b: round(100 * a / b) if b else None  # noqa: E731
    return {
        "name": res.location.name, "url": res.location.url, "is_brand": res.is_brand, "reviews": len(revs),
        "positive": sent["positive"], "neutral": sent["neutral"], "negative": sent["negative"],
        "topics_positive": pos_t, "topics_negative": neg_t, "top_positive": top(pos_t), "top_negative": top(neg_t),
        "reply_rate": pct(len(replied), len(revs)), "negative_reply_rate": pct(len(neg_replied), len(negs)),
        "negative_unanswered": len(negs) - len(neg_replied), "template_share": pct(templ, len(page_replies)),
        "reply_delay_days": round(median(delays), 1) if delays else None, "reply_delay_measured": len(delays),
    }


def signals(results: list[LocationResult], days: int = 90) -> list[dict]:
    out = []
    for res in results:
        if not res.reviews:
            continue
        ref = _ref(res)
        dated = [(review_date(r, ref), r) for r in res.reviews]
        dated = [(d, r) for d, r in dated if d]
        if dated:
            last = max(d for d, _ in dated)
            cut = last - timedelta(days=days)
            recent = [r for d, r in dated if d >= cut]
            older = [r for d, r in dated if d < cut]
            neg = lambda rs: sum(1 for r in rs if r.sentiment == "negative")  # noqa: E731
            if len(recent) >= 3 and older and neg(recent) >= 3:
                now_s, before_s = neg(recent) / len(recent), neg(older) / len(older)
                if now_s - before_s >= 0.15:
                    out.append({"page": res.location.name, "kind": "worse_lately", "level": "high",
                                "text": f"негативните ревюта за последните {days} дни са {round(100 * now_s)}% "
                                        f"({neg(recent)} от {len(recent)}; преди: {round(100 * before_s)}%)"})
            for t in TOPICS:
                nr = sum(1 for r in recent for a in r.aspects or [] if a["topic"] == t and a["sentiment"] == "negative")
                no = sum(1 for r in older for a in r.aspects or [] if a["topic"] == t and a["sentiment"] == "negative")
                if nr >= 3 and older and nr / max(len(recent), 1) >= 2 * (no / len(older)) and t != "other":
                    out.append({"page": res.location.name, "kind": "topic_spike", "level": "medium", "topic": t,
                                "text": f"оплаквания за „{TOPICS[t]}“ зачестяват: {nr} за последните {days} дни"})
        pi = page_insights(res)
        if pi["negative_unanswered"] >= 5:
            out.append({"page": res.location.name, "kind": "unanswered", "level": "medium",
                        "text": f"{pi['negative_unanswered']} негативни ревюта без отговор от страницата"})
    order = {"high": 0, "medium": 1}
    return sorted(out, key=lambda s: order.get(s["level"], 2))


def build_insights(brand_results: list[LocationResult], results: list[LocationResult], method: str) -> dict:
    pages = [page_insights(r) for r in list(brand_results) + list(results) if r.reviews]
    return {"method": method, "topics": TOPICS, "pages": pages, "signals": signals(list(brand_results) + list(results))}
