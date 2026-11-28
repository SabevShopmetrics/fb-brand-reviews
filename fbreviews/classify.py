"""Was a Page's reply to a review generated (template / AI) or written by hand?

Facebook does not label AI or automated replies on Page reviews, and research shows that neither people nor detectors
tell single AI-written texts from human ones reliably. So no single signal decides; we combine:

1. Template clusters (strongest, brand-wide): the same reply, after removing names/emojis/numbers, under many reviews
   and locations => a canned or automated template.
2. Style heuristics (weak): stock customer-service phrases, LLM tells (em dash, "we truly appreciate"...), formal
   sign-offs, length/structure vs. short informal human replies, typos, mirroring of the review's own details.
3. Our model as a judge (medium): sees the review, the reply, and the signals above, returns a label + confidence.

The final label is generated / manual / uncertain with a confidence and the list of signals that led to it, so a
person can audit any decision.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.process import cdist

from .models import LocationResult, Reply, Review

logger = logging.getLogger("fbreviews.classify")

URL_RE = re.compile(r"https?://\S+|www\.\S+")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF‍️]")
NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)

# Stock / LLM-ish phrases (en + bg + es/de/fr basics). Each hit is a weak signal.
STOCK_PHRASES = [
    r"thank you (so much )?for (taking the time|your (kind )?(feedback|review|words|recommendation))",
    r"we (truly|really|greatly|sincerely) appreciate",
    r"we('re| are) (thrilled|delighted|so glad|happy to hear)",
    r"your feedback (is|has been) (invaluable|valuable|important)",
    r"we('re| are) sorry to hear",
    r"we apologi[sz]e for (any|the) inconvenience",
    r"rest assured",
    r"(look|looking) forward to (welcoming|seeing|serving) you",
    r"(please|kindly) (reach out|contact us|send us a (private|direct) message|dm us)",
    r"we hope to see you (again|soon)",
    r"valued (customer|feedback)",
    r"(have|wish you) a (wonderful|great|lovely) day",
    r"благодарим (ви )?(за|ви за) (отзива|отделеното време|обратната връзка|препоръката|високата оценка)",
    r"радваме се,? че",
    r"съжаляваме,? че",
    r"извиняваме се за (неудобството|причиненото)",
    r"ценим (вашето|мнението|обратната)",
    r"очакваме (ви|ви отново|да ви видим)",
    r"моля,? (свържете се|пишете ни)",
    r"gracias por (tu|su) (opinión|reseña|comentario)",
    r"vielen dank für (ihre|deine) (bewertung|empfehlung|feedback)",
    r"merci pour (votre|ton) (avis|retour)",
]
STOCK_RE = [re.compile(p, re.I) for p in STOCK_PHRASES]
SIGNOFF_RE = re.compile(r"(^|\n|[.!]\s+)(best regards|kind regards|warm regards|sincerely|cheers|"
                        r"с уважение|поздрави|екипът на|the .{1,30} team|team .{1,30})[,!.]?\s*\S*\s*$", re.I)
INFORMAL_RE = re.compile(r"(:\)|:D|;\)|\)\)+|!!+|\?\?+|\bok\b|\bmersi\b|\bмерси\b|\bпоздрав\b|\bxaxa\b|\bхаха\b)", re.I)
STOPWORDS = set(
    "the a an and or of to in for on at is are was were be we you your our it this that with very so but not "
    "и в на за с да се не от по че е са ви ние вие той тя това много но или как".split()
)


@dataclass
class Item:
    loc_idx: int
    rev_idx: int
    rep_idx: int
    review: Review
    reply: Reply
    location_name: str


def normalize(text: str, names: list[str | None]) -> str:
    t = (text or "").lower()
    t = URL_RE.sub(" ", t)
    t = EMOJI_RE.sub(" ", t)
    for n in names:
        for part in (n or "").lower().split():
            if len(part) > 2:
                t = t.replace(part, " ")
    t = re.sub(r"\d+", " ", t)
    t = NON_WORD.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_page_reply(author: str | None, page_name: str, brand: str) -> bool:
    a = (author or "").strip().lower()
    if not a:
        return False
    p, b = page_name.strip().lower(), brand.strip().lower()
    return a == p or bool(b and b in a) or fuzz.ratio(a, p) >= 88 or (len(a) > 4 and fuzz.partial_ratio(a, p) >= 92)


def template_clusters(texts: list[str], threshold: int = 88, min_len: int = 15) -> list[int]:
    """Cluster id per text (-1 = too short to judge). Union-find over pairs with similarity >= threshold."""
    n = len(texts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    idx = [i for i, t in enumerate(texts) if len(t) >= min_len]
    if len(idx) > 1:
        sub = [texts[i] for i in idx]
        scores = cdist(sub, sub, scorer=fuzz.ratio, score_cutoff=threshold, workers=-1)
        for a in range(len(idx)):
            for b in scores[a].nonzero()[0]:
                if b > a:
                    parent[find(idx[a])] = find(idx[int(b)])
    long_set = set(idx)
    return [find(i) if i in long_set else -1 for i in range(n)]


def heuristic_score(review: Review, reply: Reply, cluster_size: int) -> tuple[float, list[str]]:
    """Probability-like score that the reply is generated (0..1) and the signals behind it."""
    text = reply.text or ""
    low = text.lower()
    signals: list[str] = []
    score = 0.35  # prior: unknown leans slightly manual

    if cluster_size >= 3:
        score += 0.45
        signals.append(f"template: same reply text used {cluster_size}x")
    elif cluster_size == 2:
        score += 0.2
        signals.append("template: near-identical reply used twice")

    hits = [p.pattern for p in STOCK_RE if p.search(low)]
    if hits:
        score += min(0.2, 0.07 * len(hits))
        signals.append(f"stock phrases: {len(hits)}")
    if "—" in text:
        score += 0.08
        signals.append("em dash")
    if SIGNOFF_RE.search(text.strip()):
        score += 0.05
        signals.append("formal sign-off")
    sentences = [s for s in re.split(r"[.!?]+\s", text) if s.strip()]
    if len(text) >= 250 and len(sentences) >= 3:
        score += 0.06
        signals.append("long, polished, multi-sentence")

    if len(text) < 40 and cluster_size < 2:
        score -= 0.15
        signals.append("very short, unique")
    if INFORMAL_RE.search(text):
        score -= 0.1
        signals.append("informal markers")
    if text and text[0].islower():
        score -= 0.05
        signals.append("starts lowercase")

    # Mirroring specific details of the review (not the stock words) is more human — or a well-prompted LLM.
    rev_words = {w for w in normalize(review.text or "", []).split() if len(w) > 4 and w not in STOPWORDS}
    rep_words = {w for w in normalize(text, []).split() if len(w) > 4 and w not in STOPWORDS}
    if rev_words and len(rev_words & rep_words) >= 3 and cluster_size < 2:
        score -= 0.05
        signals.append("mirrors review details")
    if not (review.text or "").strip() and cluster_size < 2 and hits:
        score += 0.05
        signals.append("stock reply to a review without text")
    return max(0.0, min(1.0, score)), signals


# ------------------------------------------------------------------ LLM judge
JUDGE_SYSTEM = """\
You audit business replies to Facebook reviews. For each item decide whether the business's reply was GENERATED
(a canned template or written by an AI tool) or MANUAL (typed by a person for this review). Facebook does not label
this, so judge from evidence: templated/stock wording, generic content that would fit any review, AI style
(over-polished, effusive gratitude, em dashes, perfectly structured), versus specific, informal, imperfect, context-
aware human writing. The "signals" field holds automatic findings; "template_count" says how many replies of the same
brand share near-identical text (>=3 is strong evidence of a template). Say "uncertain" when evidence is thin.
Answer ONLY with JSON: {"items":[{"id":<id>,"label":"generated|manual|uncertain","kind":"template|ai|none",
"confidence":0.0-1.0,"reason":"<max 20 words>"}]}"""


LLM_TIMEOUT = 180  # seconds per model call; a server that drops the connection must not hang the report forever


async def llm_chat(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    """One model call that always ends: a request timeout plus a hard asyncio deadline."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=LLM_TIMEOUT, max_retries=0)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(model=model, temperature=0, messages=messages), LLM_TIMEOUT + 15)
        return resp.choices[0].message.content or ""
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


async def llm_judge(batch: list[dict], *, base_url: str, api_key: str, model: str) -> dict[int, dict]:
    msg = json.dumps({"items": batch}, ensure_ascii=False)
    for attempt in range(3):
        try:
            data = _parse_json(await llm_chat(base_url, api_key, model, [
                {"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": msg}]))
            return {int(x["id"]): x for x in data.get("items", []) if "id" in x}
        except Exception as e:  # malformed JSON, transient server error
            logger.warning("LLM judge batch failed (attempt %d): %s", attempt + 1, e)
            await asyncio.sleep(2 * (attempt + 1))
    return {}


def _llm_p(j: dict) -> float | None:
    label, conf = j.get("label"), float(j.get("confidence") or 0.5)
    conf = max(0.0, min(1.0, conf))
    if label == "generated":
        return 0.5 + conf / 2
    if label == "manual":
        return 0.5 - conf / 2
    if label == "uncertain":
        return 0.5
    return None


async def classify_replies(
    results: list[LocationResult],
    brand: str,
    *,
    llm: dict | None = None,  # {"base_url", "api_key", "model"}; None = heuristics only
    batch_size: int = 15,
    concurrency: int = 4,
) -> str:
    items: list[Item] = []
    for li, res in enumerate(results):
        for ri, rv in enumerate(res.reviews):
            for pi, rp in enumerate(rv.replies):
                rp.is_page_reply = is_page_reply(rp.author, res.location.name, brand)
                if not rp.is_page_reply:
                    rp.reply_type, rp.generated_kind, rp.confidence = "not_page_reply", None, None
                    continue
                items.append(Item(li, ri, pi, rv, rp, res.location.name))

    if not items:
        return ("no page replies to classify: Facebook shows only the comment count under reviews, not the "
                "comments themselves (see comment_count per review)")

    names_per = [[it.review.reviewer, it.location_name, brand] for it in items]
    norm = [normalize(it.reply.text, names) for it, names in zip(items, names_per)]
    clusters = template_clusters(norm)
    sizes: dict[int, int] = {}
    for c in clusters:
        if c >= 0:
            sizes[c] = sizes.get(c, 0) + 1
    csize = [sizes.get(c, 1) if c >= 0 else 1 for c in clusters]

    heur = [heuristic_score(it.review, it.reply, n) for it, n in zip(items, csize)]

    judged: dict[int, dict] = {}
    if llm:
        payload = [
            {
                "id": i,
                "location": it.location_name,
                "review_recommends": it.review.recommends,
                "review": (it.review.text or "")[:800],
                "reply": (it.reply.text or "")[:1200],
                "template_count": csize[i],
                "signals": heur[i][1],
            }
            for i, it in enumerate(items)
            if csize[i] < 3 and not it.reply.truncated  # templates are decided without the model
        ]
        sem = asyncio.Semaphore(concurrency)

        async def run(chunk: list[dict]) -> None:
            async with sem:
                judged.update(await llm_judge(chunk, **llm))

        await asyncio.gather(*(run(payload[k:k + batch_size]) for k in range(0, len(payload), batch_size)))

    for i, it in enumerate(items):
        h, signals = heur[i]
        if it.reply.truncated:  # only a cut-off preview: not enough text to judge
            it.reply.reply_type, it.reply.generated_kind, it.reply.confidence = "uncertain", None, None
            it.reply.signals, it.reply.llm_reason = ["truncated preview"], None
            continue
        j = judged.get(i)
        p = h
        rp = it.reply
        rp.llm_reason = None
        if csize[i] >= 3:
            p = max(h, 0.9)
            kind = "template"
        else:
            lp = _llm_p(j) if j else None
            if lp is not None:
                p = 0.4 * h + 0.6 * lp
                rp.llm_reason = j.get("reason")
                signals = signals + [f"model: {j.get('label')} ({float(j.get('confidence') or 0):.2f})"]
            kind = (j or {}).get("kind") if (j or {}).get("kind") in ("template", "ai") else ("template" if csize[i] == 2 else "ai")
        if p >= 0.65:
            rp.reply_type, rp.generated_kind = "generated", kind
            rp.confidence = round(p, 2)
        elif p <= 0.35:
            rp.reply_type, rp.generated_kind = "manual", None
            rp.confidence = round(1 - p, 2)
        else:
            rp.reply_type, rp.generated_kind = "uncertain", None
            rp.confidence = round(1 - abs(p - 0.5) * 2, 2)
        rp.signals = signals
    method = "template clusters + style heuristics" + (f" + model judge ({llm['model']})" if llm else "")
    return method
