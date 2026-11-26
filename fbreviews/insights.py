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


def content_signals(content: list[dict]) -> list[dict]:
    out = []
    for c in content:
        if c["posts"] and c.get("last_post"):
            days = (datetime.now(timezone.utc).date() - datetime.fromisoformat(c["last_post"]).date()).days
            if days > 60:
                out.append({"page": c["name"], "kind": "silent", "level": "medium",
                            "text": f"не е публикувала от {days} дни (последен пост {c['last_post']})"})
        q, a = c.get("questions_or_complaints") or 0, c.get("questions_answered") or 0
        if q >= 5 and a / q < 0.3:
            out.append({"page": c["name"], "kind": "comments_unanswered", "level": "medium",
                        "text": f"отговорено на {a} от {q} въпроса/оплаквания в коментарите под постовете"})
    return out


def build_insights(brand_results: list[LocationResult], results: list[LocationResult], method: str,
                   content_method: str = "") -> dict:
    every = list(brand_results) + list(results)
    pages = [page_insights(r) for r in every if r.reviews]
    content = [content_insights(r) for r in every if r.reviews or r.posts]
    return {"method": method, "content_method": content_method, "topics": TOPICS, "post_topics": POST_TOPICS,
            "intents": INTENTS, "pages": pages, "content": content,
            "signals": signals(every) + content_signals(content)}


# ------------------------------------------------------------------ posts, comments, time lines, the business summary
POST_TOPICS = {
    "promo": "промоция / оферта", "product": "продукт / меню", "event": "събитие", "hiring": "търсим служители",
    "cause": "кауза / благотворителност", "holiday": "празник / поздрав", "brand": "атмосфера / бранд",
    "info": "информация (работно време, ново място)", "contest": "игра / томбола", "other": "друго",
}
INTENTS = {"question": "въпрос", "praise": "похвала", "complaint": "оплакване", "mention": "отбелязва приятел",
           "other": "друго"}

POST_SYSTEM = """You classify posts that a brand published on its Facebook Page. For every item return "topic" — one of:
promo (offer, discount, price), product (a dish, product, menu, new item), event (an event, concert, opening),
hiring (job offer), cause (charity, social cause), holiday (greeting, holiday), brand (atmosphere, team, behind the
scenes, brand story), info (opening hours, new address, practical news), contest (giveaway, game), other — and
"summary": what the post says, in Bulgarian, at most 10 words.
Return only JSON: {"items": [{"id": 0, "topic": "...", "summary": "..."}]}"""

COMMENT_SYSTEM = """You classify comments under a brand's Facebook posts. For every item return "intent" — question
(asks something: price, hours, booking, availability), praise, complaint, mention (only tags friends or a sticker, no
opinion), other — and "sentiment": positive | neutral | negative.
Return only JSON: {"items": [{"id": 0, "intent": "...", "sentiment": "..."}]}"""

SUMMARY_SYSTEM = """You are a business analyst. You get statistics computed from a brand's Facebook Pages (its reviews,
the Page's replies, its posts, the comments under them and its ads). Write, in Bulgarian, what these numbers tell a
manager about the business. Rules:
- Use ONLY the numbers given and quote them. Never invent figures, causes, or comparisons with an industry, sector,
  market or "average" — you have no such benchmark; compare only within the data (page vs page, type vs type,
  topic vs topic, positive vs negative).
- Every action must follow from a finding and agree with the numbers: never suggest more of a content type or topic
  that performs WORSE than the others; never recommend something the data cannot support.
- Missing is not zero: posts_collected / comments_collected / ads_checked false means we did NOT collect that part
  for the page — say nothing about posting, comments or ads for it (never "0 posts", never "start posting").
  A field that is null means unknown, not zero.
- Actions are for the brand's managers: what to do on Facebook and with customers. Never actions about this report,
  about collecting more data, or asking anyone (an owner, Facebook) for data.
- Name pages and locations as given. Write plain Bulgarian for a manager: never field names (reply_rate,
  template_share, ads_active…) — say "отговаря на 15% от ревютата", "80% от отговорите са шаблонни". Be specific and short.
Return only JSON:
{"headline": "one sentence: the main finding",
 "findings": [{"title": "short", "text": "2-3 sentences with the numbers", "tone": "good|bad|neutral",
               "metric": "the ONE key number of this finding as shown, e.g. \"80%\" or \"13\"",
               "metric_label": "2-4 words: what the number is, e.g. \"от отговорите са шаблонни\""}],   // 4-8
 "actions": [{"text": "a concrete next step, one sentence", "priority": "high|medium",
              "because": "the finding title it follows from"}]}   // 2-5"""


async def _llm_json(system: str, payload: dict, llm: dict) -> dict:
    from openai import AsyncOpenAI

    from .classify import _parse_json

    client = AsyncOpenAI(base_url=llm["base_url"], api_key=llm.get("api_key") or "not-needed")
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=llm["model"], temperature=0,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            return _parse_json(resp.choices[0].message.content or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("insights call failed (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(2 * (attempt + 1))
    return {}


def _load_cache(path: Path | None) -> dict:
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _post_heuristic(text: str) -> str:
    t = (text or "").lower()
    for topic, rx in (("hiring", r"търсим|позиция|кандидатств|работа при нас|hiring|we.re hiring|join our team"),
                      ("contest", r"игра|томбола|печели|подарък за|giveaway|win "),
                      ("promo", r"промо|отстъпк|%|оферта|цена|само за|black friday|черен петък|discount|offer"),
                      ("event", r"събитие|концерт|откриване|заповядайте на \d|event|opening"),
                      ("holiday", r"честит|празник|коледа|великден|нова година|happy|merry"),
                      ("cause", r"кампания|благотвор|дарение|кауза|charity"),
                      ("info", r"работно време|затворено|нов адрес|отваряме|opening hours"),
                      ("product", r"меню|ястие|вкус|сезонн|нов продукт|опитайте|menu|dish|taste")):
        if re.search(rx, t):
            return topic
    return "brand" if t else "other"


async def analyze_posts(results: list[LocationResult], *, llm: dict | None, cache_path: Path | None,
                        batch_size: int = 20, concurrency: int = 4) -> str:
    cache = _load_cache(cache_path)
    posts = [p for r in results for p in r.posts]
    comments = [c for p in posts for c in (p.comments_list or [])]
    key = lambda kind, text: kind + ":" + hashlib.sha1((text or "").encode()).hexdigest()[:16]  # noqa: E731
    sem = asyncio.Semaphore(concurrency)

    async def run(kind, items, system):
        payload = [{"id": i, "text": (t or "")[:1200]} for i, (_, t) in enumerate(items)]
        async with sem:
            got = await _llm_json(system, {"items": payload}, llm)
        by = {int(x["id"]): x for x in (got or {}).get("items", []) if isinstance(x, dict) and "id" in x}
        for i, (k, _) in enumerate(items):
            if i in by:
                cache[k] = by[i]

    todo_p = [(key("p", p.text), p.text) for p in posts if p.text and key("p", p.text) not in cache]
    todo_c = [(key("c", c.text), c.text) for c in comments
              if c.text and not c.tags_only and key("c", c.text) not in cache]
    if llm:
        await asyncio.gather(*(run("p", todo_p[j:j + batch_size], POST_SYSTEM) for j in range(0, len(todo_p), batch_size)),
                             *(run("c", todo_c[j:j + 30], COMMENT_SYSTEM) for j in range(0, len(todo_c), 30)))
    for p in posts:
        c = cache.get(key("p", p.text)) or {}
        p.topic = c.get("topic") if c.get("topic") in POST_TOPICS else _post_heuristic(p.text)
        p.summary = c.get("summary")
    for cm in comments:
        if cm.tags_only or not cm.text:
            cm.intent, cm.sentiment = "mention", "neutral"
            continue
        c = cache.get(key("c", cm.text)) or {}
        cm.intent = c.get("intent") if c.get("intent") in INTENTS else ("question" if "?" in cm.text else "other")
        cm.sentiment = c.get("sentiment") if c.get("sentiment") in SENTIMENTS else "neutral"
    if cache_path:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if not posts:
        return ""
    return f"model ({llm['model']})" if llm else "keywords (no model)"


def _count(text: str | None) -> int | None:
    m = re.match(r"\s*([\d.,]+)\s*(хил\.|млн\.|[KkMm])?", text or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    mult = {"хил.": 1e3, "k": 1e3, "млн.": 1e6, "m": 1e6}.get((m.group(2) or "").lower(), 1)
    return int(v * mult)


def _month(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def content_insights(res: LocationResult) -> dict:
    """One Page: reviews and posts by month, posting rhythm, content types and topics, engagement, comments."""
    ref = _ref(res)
    rev_m: dict[str, dict] = {}
    for r in res.reviews:
        m = _month(review_date(r, ref))
        if not m:
            continue
        b = rev_m.setdefault(m, {"pos": 0, "neg": 0, "photos": 0, "text": 0})
        b["neg" if r.recommends is False else "pos"] += 1
        b["photos"] += 1 if (r.photos or 0) > 0 else 0
        b["text"] += 1 if (r.text or "").strip() else 0
    posts = res.posts
    post_m: dict[str, int] = {}
    dated = []
    for p in posts:
        d = review_date(Review(date_full=p.date_full), ref)
        if d:
            dated.append(d)
            post_m[_month(d)] = post_m.get(_month(d), 0) + 1
    last90 = [d for d in dated if d >= ref - timedelta(days=90)]
    eng = [(p.reactions or 0) + (p.comments or 0) + (p.shares or 0) for p in posts]
    by_type: dict[str, dict] = {}
    by_topic: dict[str, dict] = {}
    for p, e in zip(posts, eng):
        for dct, k in ((by_type, p.type or "text"), (by_topic, p.topic or "other")):
            b = dct.setdefault(k, {"posts": 0, "engagement": 0})
            b["posts"] += 1
            b["engagement"] += e
    for dct in (by_type, by_topic):
        for b in dct.values():
            b["avg_engagement"] = round(b["engagement"] / b["posts"], 1) if b["posts"] else 0
    followers = _count(res.location.followers)
    comments = [c for p in posts for c in (p.comments_list or [])]
    intents: dict[str, int] = {}
    for c in comments:
        intents[c.intent or "other"] = intents.get(c.intent or "other", 0) + 1
    questions = [c for c in comments if c.intent in ("question", "complaint") and not c.is_page_reply and c.depth == 1]
    answered = 0
    for p in posts:
        cl = p.comments_list or []
        for i, c in enumerate(cl):
            if c not in questions:
                continue
            thread = []
            for x in cl[i + 1:]:
                if x.depth == 1:
                    break
                thread.append(x)
            to_author = [x for x in cl if x.is_page_reply and x.reply_to and c.author
                         and x.reply_to.strip().lower() == c.author.strip().lower()]
            if any(x.is_page_reply for x in thread) or to_author:
                answered += 1
    top = sorted(zip(posts, eng), key=lambda pe: -pe[1])[:5]
    avg = round(sum(eng) / len(eng), 1) if eng else None
    return {
        "name": res.location.name, "url": res.location.url, "is_brand": res.is_brand,
        "posts_collected": bool(res.posts_collected or posts),
        "comments_collected": any(p.comments_list is not None for p in posts),
        "ads_checked": res.location.ads_active is not None,
        "followers": followers, "reviews_by_month": dict(sorted(rev_m.items())),
        "posts": len(posts), "posts_by_month": dict(sorted(post_m.items())),
        "posts_per_week_90d": round(len(last90) / (90 / 7), 1) if posts else None,
        "last_post": max(dated).date().isoformat() if dated else None,
        "by_type": by_type, "by_topic": by_topic, "avg_engagement": avg,
        "engagement_per_1k_followers": round(1000 * avg / followers, 2) if avg is not None and followers else None,
        "top_posts": [{"date": p.date_full, "type": p.type, "topic": p.topic, "text": (p.text or "")[:160],
                       "summary": p.summary, "engagement": e, "reactions": p.reactions, "comments": p.comments,
                       "shares": p.shares, "permalink": p.permalink} for p, e in top],
        "comments_read": len(comments), "comment_intents": intents,
        "questions_or_complaints": len(questions), "questions_answered": answered,
        "ads_active": res.location.ads_active, "ads": len(res.ads),
    }


async def business_summary(report_data: dict, *, llm: dict | None, cache_path: Path | None) -> dict:
    """What the numbers say about the business: a headline, 4-8 findings, 2-5 next steps (model; cached by input)."""
    if not llm:
        return {}
    ins = report_data.get("insights") or {}
    pages = []
    for loc in (report_data.get("brand_sections") or []) + (report_data.get("locations") or []):
        st = loc.get("stats") or {}
        pages.append({"page": loc["location"]["name"], "brand_page": loc.get("is_brand"), "status": loc.get("status"),
                      "facebook_recommend_pct": loc.get("recommend_pct"), "facebook_review_total": loc.get("review_total"),
                      "followers": loc["location"].get("followers"), "reviews_extracted": st.get("reviews"),
                      "not_recommends": st.get("not_recommends"), "ads_active": loc["location"].get("ads_active")})
    payload = {
        "brand": report_data.get("brand"), "pages": pages,
        "reviews_by_page": [{k: p.get(k) for k in ("name", "reviews", "positive", "neutral", "negative", "top_positive",
                                                   "top_negative", "reply_rate", "negative_reply_rate",
                                                   "negative_unanswered", "template_share")} for p in ins.get("pages", [])],
        "content_by_page": [{k: c.get(k) for k in ("name", "posts_collected", "comments_collected", "ads_checked",
                                                   "followers", "posts", "posts_per_week_90d", "last_post",
                                                   "by_type", "by_topic", "avg_engagement", "engagement_per_1k_followers",
                                                   "comments_read", "comment_intents", "questions_or_complaints",
                                                   "questions_answered", "ads_active")} for c in ins.get("content", [])],
        "signals": [s.get("page", "") + ": " + s.get("text", "") for s in ins.get("signals", [])],
        "topic_names": TOPICS, "post_topic_names": POST_TOPICS,
    }
    k = hashlib.sha1((SUMMARY_SYSTEM + json.dumps(payload, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:16]
    cache = _load_cache(cache_path)
    if k in cache:
        return cache[k]
    out = await _llm_json(SUMMARY_SYSTEM, payload, llm)
    for f in out.get("findings") or []:  # the big number is a number: "80% препоръки на X" → "80%" + label
        m = re.match(r"^\s*([\d][\d.,\s]*%?)\s*(.*)$", str(f.get("metric") or ""))
        if m and len(str(f.get("metric"))) > 8:
            f["metric"], f["metric_label"] = m.group(1).strip(), (f.get("metric_label") or m.group(2)).strip()
        elif f.get("metric") and not re.search(r"\d", str(f["metric"])):
            f["metric"] = None
    if out.get("findings") and not out.get("actions"):  # the model sometimes leaves them out: ask for them alone
        more = await _llm_json(
            SUMMARY_SYSTEM + "\nNow return ONLY {\"actions\": [...]} (2-5), each following from one of the findings given.",
            {"data": payload, "findings": out["findings"]}, llm)
        out["actions"] = more.get("actions") or []
    if out.get("findings"):
        cache = {k: out}
        if cache_path:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out
