"""Facebook reviews skill: helper functions for the browser-use agent.

browser-harness loads this file into the agent's Python REPL (every public name becomes a global there), next to
js(), goto_url(), wait_for_load(), capture_screenshot() ... The playbook is domain-skills/facebook/reviews.md.

All files live in the agent's workspace (the REPL's working directory):
  locations.json        every location Page found so far (deduplicated)
  results/<slug>.json   one finished location (reviews + replies + status)
  pace.json             page loads today (daily cap) and the last load time
  STOP                  written when Facebook shows a login wall / checkpoint: the whole run must stop
  preflight.json        the last fb_preflight() health report (is the extractor still reading Facebook correctly?)
  fb_extract_override.js  the agent's repair of the extractor after a Facebook UI change (loaded after fb_extract.js)
  override_log.json     what each override changed and whether preflight passed with it
  overrides/            earlier override versions (backups)
"""

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_HERE = Path(__file__).resolve().parent
_WS = Path(os.environ.get("BU_STATE_DIR", ".browser-use")).resolve().parent  # the agent workspace
_JS = (_HERE / "fb_extract.js").read_text() if (_HERE / "fb_extract.js").exists() else ""
_CFG = json.loads((_HERE / "fb_config.json").read_text()) if (_HERE / "fb_config.json").exists() else {}

PACE_MIN = float(_CFG.get("pace_min_s", 4))        # seconds between page loads, at least
PACE_MAX = float(_CFG.get("pace_max_s", 10))       # ... and at most
DAILY_PAGE_CAP = int(_CFG.get("daily_page_cap", 5000))
MAX_REVIEWS = int(_CFG.get("max_reviews", 100000))      # 100000 = all
MAX_LOCATIONS = int(_CFG.get("max_locations", 100000))  # 100000 = all
# Facebook does not show comment threads under reviews (recommendations) to viewers: only the count is collected.
# True would open every review with comments to try anyway (one extra page load each).
OPEN_THREADS = bool(_CFG.get("open_threads", False))
# Lite mode: a fresh browser with NO Facebook login. Only the public Page data is read (fb_page_summary); the login
# dialog Facebook shows to visitors is expected and is not a reason to stop (a redirect to /login still is).
LITE = bool(_CFG.get("lite", False))
SEARCH_CHALLENGE = re.compile(r"(bots use|confirm this search was made by a human|verify you are human|потвърдете, че сте "
                              r"човек|unusual traffic|captcha|are you a robot)", re.I)
SCROLL_MAX_S = float(_CFG.get("scroll_max_s", 3600))  # at most this long scrolling one reviews tab

# Preflight / self-repair (see domain-skills/facebook/preflight.md).
OVERRIDE_FILE = "fb_extract_override.js"   # in the workspace: survives rounds and --resume of the same run
PREFLIGHT_FILE = "preflight.json"
PREFLIGHT_MAX_AGE_H = float(_CFG.get("preflight_max_age_h", 6))
# Thresholds of a healthy extraction. coverage_* = reviews read / min(page's own total, max_reviews).
HEALTH = {"coverage_min": 0.8, "coverage_min_run": 0.6, "over_total_factor": 1.2, "empty_text_max": 0.3,
          "null_verdict_max": 0.2, "missing_permalink_max": 0.3, "missing_reviewer_max": 0.2,
          "duplicate_max": 0.1, "missing_date_warn": 0.5, "min_sample": 5, "comment_count_warn_sample": 15,
          "reply_truncated_warn": 0.2, "reply_empty_warn": 0.2, "reply_no_author_warn": 0.2,
          "reply_over_count_warn": 0.1}
HEALTH.update(_CFG.get("health", {}) or {})
# The output contract: every review must have these keys with these types (None = "not shown / unknown").
CONTRACT = {"reviewer": (str, type(None)), "recommends": (bool, type(None)), "rating": (int, type(None)),
            "text": (str, type(None)), "date_text": (str, type(None)), "permalink": (str, type(None)),
            "comment_count": (int, type(None)), "replies": (list,)}

_NON_PAGE = {"search", "groups", "events", "watch", "marketplace", "gaming", "hashtag", "photo", "photo.php",
             "story.php", "permalink.php", "login", "login.php", "sharer", "dialog", "help", "policies", "places",
             "reel", "share", "friends", "notifications", "messages", "bookmarks", "settings"}


def _h():
    return sys.modules["browser_harness.helpers"]


# ------------------------------------------------------------------ files
def _read(name, default):
    try:
        return json.loads((_WS / name).read_text())
    except (OSError, ValueError):
        return default


def _write(name, value):
    p = _WS / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(p)


_COUNTRY_BG = {"българия": "Bulgaria", "румъния": "Romania", "гърция": "Greece", "сърбия": "Serbia", "турция": "Turkey",
               "германия": "Germany", "австрия": "Austria", "италия": "Italy", "испания": "Spain", "франция": "France",
               "великобритания": "United Kingdom", "обединеното кралство": "United Kingdom", "сащ": "United States",
               "македония": "North Macedonia", "северна македония": "North Macedonia", "хърватия": "Croatia",
               "полша": "Poland", "чехия": "Czech Republic", "унгария": "Hungary", "кипър": "Cyprus", "молдова": "Moldova",
               "украйна": "Ukraine", "нидерландия": "Netherlands", "холандия": "Netherlands", "белгия": "Belgium",
               "швейцария": "Switzerland", "португалия": "Portugal", "словения": "Slovenia", "словакия": "Slovakia",
               "черна гора": "Montenegro", "албания": "Albania", "босна и херцеговина": "Bosnia and Herzegovina"}
_COUNTRY_EN = {"us": "United States", "usa": "United States", "uk": "United Kingdom", "bg": "Bulgaria"}


def fb_country(text):
    """Country from an address like 'Sea Garden, Varna, Bulgaria, 9000' or 'ул. …, София, България' → 'Bulgaria'."""
    parts = [p.strip() for p in str(text or "").split(",") if p.strip()]
    for p in reversed(parts):
        p2 = re.sub(r"\d[\d\s-]*", "", p).strip(" .")
        if not p2:
            continue
        low = p2.lower()
        if low in _COUNTRY_BG:
            return _COUNTRY_BG[low]
        if low in _COUNTRY_EN:
            return _COUNTRY_EN[low]
        if re.fullmatch(r"[A-Z][A-Za-z .'-]{2,40}", p2) and len(parts) >= 2:
            return p2   # an English country name, as Facebook prints it
        return None
    return None


def _fill_country(loc):
    if not loc.get("country"):
        c = fb_country(loc.get("address"))
        if c:
            loc["country"] = c
    return loc


def fb_page_url(url):
    """Canonical https://www.facebook.com/<page> (or profile.php?id=N); None when it is not a Page URL."""
    url = (url or "").strip()
    if url.startswith("/"):
        url = "https://www.facebook.com" + url
    u = urlsplit(url if "://" in url else "https://" + url)
    if not (u.netloc.lower().endswith("facebook.com") or u.netloc.lower() == "fb.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "profile.php":
        pid = parse_qs(u.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}" if pid.isdigit() else None
    if parts[0] == "p" and len(parts) > 1:
        return f"https://www.facebook.com/p/{parts[1]}"
    if parts[0] == "pages" and len(parts) >= 3 and parts[-1].isdigit():
        return f"https://www.facebook.com/{parts[-1]}"
    if parts[0].lower() in _NON_PAGE:
        return None
    return f"https://www.facebook.com/{parts[0]}"


def fb_slug(url):
    u = urlsplit(fb_page_url(url) or url)
    raw = parse_qs(u.query).get("id", [""])[0] or u.path.strip("/").replace("/", "-")
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()[:60] or "x"


def fb_reviews_url(url):
    page = fb_page_url(url) or url.rstrip("/")
    return page + ("&sk=reviews" if "profile.php" in page else "/reviews")


# ------------------------------------------------------------------ pacing / safety
def fb_stopped():
    """Text of the STOP file if Facebook demanded a login/check earlier in this run, else None."""
    p = _WS / "STOP"
    return p.read_text() if p.exists() else None


def _say(msg):
    """One line of progress for the user (Bulgarian): progress.log in the workspace, shown live in the run log."""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        with open(_WS / "progress.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print("•", msg)


def _short(url):
    return (url or "").replace("https://www.facebook.com/", "facebook/").replace("https://", "")[:90]


def _stop(reason):
    (_WS / "STOP").write_text(reason)


def _override_src():
    try:
        return (_WS / OVERRIDE_FILE).read_text()
    except OSError:
        return ""


_CHUNK = 20000  # characters per request: browser-harness takes at most 64 KiB per Runtime.evaluate


def _bundle_src(override=None):
    """The bundled extractor, then the workspace override (if any). The override runs inside try/catch (a runtime
    error is reported as __fbx.overrideError) and can never replace pageState() (window.__fbxBasePageState keeps
    the bundled one for the safety check)."""
    src = _override_src() if override is None else override
    parts = [_JS, "window.__fbxBasePageState = window.__fbx.pageState;"]
    if src.strip():
        parts += ["var __fbxSafePageState = window.__fbx.pageState;",
                  "try {", src, "} catch (e) { window.__fbx.overrideError = String((e && e.stack) || e); }",
                  "window.__fbx.pageState = __fbxSafePageState;"]
    return "\n".join(parts)


def _bundle(expression, override=None):
    """Extractor + override + expression as ONE script (only for writing the bundle to a file / syntax checks;
    pages get it in pieces through _inject, since the whole is larger than one request may be)."""
    return _bundle_src(override) + "\n" + expression


def _inject(h, override=None):
    """Load the extractor (+ override) into the current page once: sent in pieces of _CHUNK characters, joined in
    the page and evaluated there. Nothing is sent when the page already has this exact version (a navigation
    clears it, so every new page gets it again)."""
    src = _bundle_src(override)
    sig = hashlib.sha1(src.encode()).hexdigest()[:12]
    try:
        if h.js(f"!!(window.__fbx && window.__fbx.__sig === {json.dumps(sig)})") is True:
            return
    except Exception:
        pass
    h.js("window.__fbxSrc = []; true")
    for k in range(0, len(src), _CHUNK):
        h.js("window.__fbxSrc.push(" + json.dumps(src[k:k + _CHUNK], ensure_ascii=False) + "); true")
    h.js("(function () { var s = window.__fbxSrc.join(''); window.__fbxSrc = null; (0, eval)(s); "
         f"window.__fbx.__sig = {json.dumps(sig)}; return true; }})()")


def _js(expression, override=None):
    """Run `expression` against the injected extractor; inject first (and again if a navigation cleared it)."""
    h = _h()
    _inject(h, override)
    return h.js(expression)


def _run(expression):
    """Evaluate expression with the extractor (+ override) in the page. If the override itself does not even parse,
    record that (override_status.json, shown by fb_override / fb_preflight) and fall back to the bundled extractor."""
    if not _override_src().strip() or _read("override_status.json", {}).get("sig") == _override_sig():
        return _js(expression, override="")  # no override, or one already known not to parse
    try:
        _inject(_h())
    except Exception as e2:  # the override is broken, not the expression
        _write("override_status.json", {"error": f"{type(e2).__name__}: {e2}"[:2000], "sig": _override_sig(),
                                        "at": datetime.now().isoformat(timespec="seconds")})
        return _js(expression, override="")
    return _h().js(expression)


def fb_open(url):
    """goto_url with the skill's pacing (pause between page loads, daily cap) and a safety check afterwards.
    Returns {'ok': bool, 'state': 'ok'|'login_required'|'blocked'|'not_found'|'cap_reached'|'stopped', 'url': ...}."""
    if fb_stopped():
        return {"ok": False, "state": "stopped", "reason": fb_stopped()}
    pace = _read("pace.json", {})
    today = date.today().isoformat()
    if pace.get("day") != today:
        pace = {"day": today, "pages": 0, "last": 0}
    if pace["pages"] >= DAILY_PAGE_CAP:
        _say(f"⏸ Дневният лимит от {DAILY_PAGE_CAP} страници е достигнат — продължава утре (--resume)")
        return {"ok": False, "state": "cap_reached", "reason": f"daily cap of {DAILY_PAGE_CAP} page loads reached"}
    gap = random.uniform(PACE_MIN, PACE_MAX) - (time.time() - pace.get("last", 0))
    if gap > 0:
        time.sleep(gap)
    h = _h()
    _say(f"Отварям {_short(url)}")
    h.goto_url(url)
    try:
        h.wait_for_load(20)
    except Exception:
        pass
    time.sleep(3)
    pace["pages"] += 1
    pace["last"] = time.time()
    _write("pace.json", pace)
    state = {"state": "ok"}
    if _JS:  # the bundled check only, never an override's; a page still loading may not answer at once
        for attempt in range(3):
            try:
                _inject(h, override="")
                state = h.js("(window.__fbxBasePageState || window.__fbx.pageState)()") or {"state": "ok"}
                break
            except Exception as e:
                if attempt == 2:
                    _say(f"  страницата не отговаря ({str(e)[:60]}) — пропускам я")
                    return {"ok": False, "state": "unknown", "reason": str(e)[:200]}
                time.sleep(4)
    if LITE and state.get("state") == "login_required" and "/login" not in (state.get("url") or ""):
        state = {**state, "state": "ok", "logged_out": True}
    if state.get("state") in ("login_required", "blocked"):
        _say("⛔ Facebook поиска вход или проверка за сигурност — спирам (не я заобикалям)")
        _stop(f"{state['state']} at {url}")
    return {"ok": state.get("state") == "ok", **state}


def fb_js(expression):
    """Run the bundled extractor (+ the workspace override, if any) plus `expression` in the page, e.g.
    fb_js("__fbx.extract({})"), fb_js("__fbx.pageState()"), fb_js("__fbx.posts().length")."""
    return _run(expression)


# ------------------------------------------------------------------ discovery
SEARCH_IDLE_LIMIT = int(_CFG.get("search_idle_limit", 3))
# Page fields read from the header (no extra page load) and from the transparency tab (one extra paced page)
PAGE_KEYS = ("category", "followers", "price", "website", "phone", "address", "email", "hours", "likes", "verified",
             "response_badge", "instagram", "socials", "intro_locations")
DETAIL_KEYS = ("created", "name_changes", "ads_running", "admin_countries", "ads_active", "page_id")
PAGE_DETAILS = bool(_CFG.get("page_details", True))   # the Page's ads in the Ad Library (one extra page)
ADS = bool(_CFG.get("ads", True))
POSTS = bool(_CFG.get("posts", True))                  # the Pages' posts (Phase 3) …
POST_COMMENTS = bool(_CFG.get("post_comments", True))  # … and the comments under them (Phase 4)
MAX_POSTS = int(_CFG.get("max_posts", 100000))         # 100000 = all
POSTS_SINCE = _CFG.get("posts_since")                  # 'YYYY-MM-DD' = only posts since then; None = all
CONTENT_BUDGET_S = int(_CFG.get("content_budget_s", 2400))  # one helper call returns after this; call it again
# with NO location found yet the 3-search rule does not apply (don't give up before the first find), but this cap
# does: many brands (e.g. one restaurant chain with a single Page) have no Page per location at all
SEARCH_EMPTY_LIMIT = int(_CFG.get("search_empty_limit", 6))


def _search_state():
    return _read("search_state.json", {"queries": {}, "idle": 0, "closed": False})


def _search_gate(query, kind):
    """Repeated queries are answered from the cache (no page load); after SEARCH_IDLE_LIMIT searches in a row that
    added no location, searching is closed for the run so the agent moves on to extracting."""
    st = _search_state()
    key = f"{kind}:{query.strip().lower()}"
    if key in st["queries"]:  # a repeat: answered from memory, and it does not count as an empty search
        _say(f"  (вече търсено: „{query}“ — връщам запомнения резултат)")
        return st, key, st["queries"][key]
    if st.get("pending"):  # the previous NEW search was not followed by fb_add_locations: it added nothing
        st["idle"] = st.get("idle", 0) + 1
        st.pop("pending", None)
        if st["idle"] >= SEARCH_IDLE_LIMIT:
            st["closed"] = True
        _write("search_state.json", st)
    if st.get("closed") and fb_locations():
        _say("  търсенето е приключено (3 поредни без нова локация) — минавам към извличането")
        return st, key, {"error": "search_closed", "message": "Search is closed for this run: the last searches added no "
                         "new location. Go to Phase 1b / Phase 2 now: extract the brand Page(s) and every location in "
                         "fb_progress()['todo']."}
    if not fb_locations() and st.get("idle", 0) >= SEARCH_EMPTY_LIMIT:
        _say(f"  {SEARCH_EMPTY_LIMIT} търсения без нито една страница на локация — брандът явно няма отделни страници "
             "по обекти; спирам търсенето и вадя страницата на бранда")
        return st, key, {"error": "search_closed", "message": f"Search is closed for this run: {SEARCH_EMPTY_LIMIT} searches "
                         "found no location Page. This brand most likely has no Page per location. Do Phase 1b now: "
                         "record the brand's official Page(s) with fb_add_brand_pages and extract every one in "
                         "fb_progress()['brand_todo'], then finish (0 locations is a valid result; say so in notes)."}
    return st, key, None


def _search_note(st, key, results):
    st["queries"][key] = results
    st["pending"] = key
    _write("search_state.json", st)


class _NoResults(list):
    """An empty result list that also says why (r.error, r.message, r['error'])."""

    def __init__(self, info):
        super().__init__()
        self.info = dict(info or {})
        self.error, self.message = self.info.get("error"), self.info.get("message", "")

    def __getitem__(self, k):
        return self.info[k] if isinstance(k, str) else super().__getitem__(k)

    def get(self, k, default=None):
        return self.info.get(k, default)


def fb_search_pages(query, kind="pages", scrolls=6):
    """Facebook search results for `query` (kind 'pages' or 'places'): [{name, url, snippet}].
    The snippet holds what Facebook shows under the name (category, address, followers, rating)."""
    from urllib.parse import quote

    st, key, cached = _search_gate(query, kind)
    if isinstance(cached, dict):  # search closed: still a list (code that loops over results keeps working)
        print(f"fb_search_pages: {cached.get('error')} — {cached.get('message', '')}")
        return _NoResults(cached)
    if cached is not None:
        return cached
    _say(f"Търся във Facebook: „{query}“")
    r = fb_open(f"https://www.facebook.com/search/{kind}/?q={quote(query)}")
    if not isinstance(r, dict) or not r.get("ok"):
        print(f"fb_search_pages: the search page did not open: {r}")
        return _NoResults({"error": (r or {}).get("state", "unknown") if isinstance(r, dict) else "unknown", "state": r})
    h = _h()
    seen = {}
    for _ in range(scrolls):
        for item in _run("__fbx.searchResults()") or []:
            url = fb_page_url(item.get("href", ""))
            if url and url not in seen and item.get("name"):
                seen[url] = {"name": item["name"], "url": url, "snippet": item.get("snippet", "")}
        before = len(seen)
        h.js("window.scrollBy(0, Math.round(innerHeight * 0.9))")
        time.sleep(random.uniform(1.5, 3.0))
        if before == len(seen) and _ > 1:
            break
    _say(f"  → {len(seen)} страници в резултатите")
    out = list(seen.values())
    _search_note(st, key, out)
    return out


def fb_add_locations(items):
    """Merge location dicts {name, url, address, city, country, category, rating_text, source} into locations.json.
    Returns {'added': n, 'total': n}. URLs are normalized and deduplicated; the cap is max_locations."""
    locs = _read("locations.json", [])
    known = {l["url"] for l in locs} | {b["url"] for b in _read("brand_pages.json", [])}
    added = 0
    for it in items or []:
        url = fb_page_url((it or {}).get("url", ""))
        if not url or url in known or len(locs) >= MAX_LOCATIONS:
            continue
        locs.append(_fill_country({k: it.get(k) for k in ("name", "address", "city", "country", "category", "rating_text", "source")}
                                  | {"url": url}))
        known.add(url)
        added += 1
    _write("locations.json", locs)
    st = _search_state()
    if st.get("pending"):  # one count per search: did the last search bring a new location?
        st["idle"] = 0 if added else st.get("idle", 0) + 1
        st.pop("pending", None)
        if st["idle"] >= SEARCH_IDLE_LIMIT:
            st["closed"] = True
        _write("search_state.json", st)
    if added:
        _say(f"Добавени локации: +{added} (общо {len(locs)}): " + ", ".join(l.get("name") or l["url"] for l in locs[-added:])[:300])
    return {"added": added, "total": len(locs), "cap": MAX_LOCATIONS}


def fb_locations():
    return _read("locations.json", [])


# ------------------------------------------------------------------ one location
def fb_extract_reviews(url, max_reviews=None, name=None, location=None, save=True):
    """Like _extract_reviews below, and saves the result right away (results/<slug>.json) unless save=False.
    Statuses that mean "stop the run" (login_required / blocked / stopped / cap_reached) are not saved, so the
    location is retried after a resume. Neither are `unknown` and `suspect` (the health check says the extractor
    misreads the page, see preflight.md): repair, then extract that location again."""
    res = _extract_reviews(url, max_reviews=max_reviews, name=name)
    if res.get("status") in ("ok", "partial", "no_reviews", "reviews_disabled"):
        diag = res.setdefault("diagnostics", {})
        det = _page_details(url, diag.get("page_id"))
        if det:
            diag["ads"] = det.pop("ads", None)
            diag["page"] = {**(diag.get("page") or {}), **det}
    if save and res.get("status") not in ("login_required", "blocked", "stopped", "cap_reached", "unknown", "suspect"):
        res["saved"] = fb_save_result(location or {"url": url, "name": name}, res)
    return res


def _extract_reviews(url, max_reviews=None, name=None):
    """Reviews + replies of one location Page. Opens the reviews tab, scrolls (Facebook lazy-loads and also empties
    reviews far above the viewport, so they are collected while scrolling), then opens each review that has more
    comments than were captured to read its thread. Returns
    {status, reviews_url, reviews, diagnostics, notes, health}; status is ok | partial | no_reviews | reviews_disabled |
    not_found | login_required | blocked | stopped | cap_reached | unknown | suspect (+ fallback_status)."""
    max_reviews = max_reviews or MAX_REVIEWS
    rurl = fb_reviews_url(url)
    locs = fb_locations()
    pos = next((i for i, l in enumerate(locs, 1) if fb_page_url(l.get("url", "")) == fb_page_url(url)), None)
    where = f"Локация {pos}/{len(locs)}" if pos else "Страница"
    _say(f"{where}: {name or _short(url)} — отварям ревютата")
    r = fb_open(rurl)
    if not r.get("ok"):
        return {"status": r.get("state", "unknown"), "reviews_url": rurl, "reviews": [], "notes": r.get("reason", "")}
    opts = {"pageName": name or "", "maxReviews": max_reviews, "steps": 4, "delayMs": 900}
    reviews, diag = _collect(opts)
    try:
        diag["page_id"] = _run("__fbx.pageId()")
    except Exception:
        pass
    try:  # the Page header on the same tab (no extra page load): category, followers, price, % recommend
        head = _run("__fbx.pageSummary()") or {}
        diag["page"] = {k: head.get(k) for k in ("name",) + PAGE_KEYS if head.get(k)}
        if diag.get("recommend_pct") is None and head.get("recommend_pct") is not None:
            diag["recommend_pct"] = head["recommend_pct"]
    except Exception:
        pass
    page_replies = sum(1 for rv in reviews for c in (rv.get("replies") or []) if not c.get("truncated"))
    _say(f"  → {len(reviews)} ревюта (във Facebook: {diag.get('review_total') or '?'}), "
         f"{page_replies} отговора/коментара с текст")
    notes = []
    if not reviews:
        status = diag.get("tab_state") or "unknown"
        if (diag.get("review_total") or 0) > 0:
            status = "unknown"  # the page says it has reviews but none were read: the extractor may be broken
        return {"status": status, "reviews_url": rurl, "reviews": [], "diagnostics": diag,
                "notes": "no reviews found on the reviews tab" if status == "no_reviews" else
                         "check the page (if the page shows reviews, run fb_preflight(reload=False))"}
    _write(f"results/_partial_{fb_slug(url)}.json", reviews)  # nothing is lost if the thread pass stops

    if not OPEN_THREADS:
        with_comments = [rv for rv in reviews if (rv.get("comment_count") or 0) > len(rv.get("replies") or [])]
        for rv in with_comments:
            rv["replies_unavailable"] = True  # counted, not shown by Facebook
        total = sum(rv.get("comment_count") or 0 for rv in reviews)
        notes.append(f"{len(with_comments)} review(s) with {total} comment(s) counted; "
                     "Facebook does not show comment threads under reviews")
        expect = min(max_reviews, diag.get("review_total") or 0)
        status = "ok"
        if len(reviews) < expect:
            status = "partial"
            notes.append(f"only {len(reviews)} of {expect} reviews loaded (page total {diag.get('review_total')}, "
                         f"scroll stopped: {diag.get('scroll_reason') or 'unknown'})")
        res = {"status": status, "reviews_url": rurl, "reviews": reviews, "diagnostics": diag,
               "notes": "; ".join(notes)}
        return _guard(res, max_reviews)

    # Reply threads: the reviews list shows only the comment count.
    misses, tried = 0, 0
    for rv in reviews:
        want = rv.get("comment_count") or 0
        if not rv.get("permalink") or want <= len(rv.get("replies") or []):
            continue
        if misses >= 3:
            rv["replies_unavailable"] = True
            continue
        tried += 1
        o = fb_open(rv["permalink"])
        if not o.get("ok"):
            if o.get("state") in ("login_required", "blocked", "stopped", "cap_reached"):
                notes.append(f"thread pass stopped: {o.get('state')}")
                break
            rv["replies_unavailable"] = True
            misses += 1
            continue
        got = _run("__fbx.extractPost(" + json.dumps(opts) + ")") or {}
        replies = ((got.get("review") or {}).get("replies")) or []
        if replies:
            rv["replies"], misses = replies, 0
        else:
            rv["replies_unavailable"] = True
            misses += 1
    if misses >= 3:
        notes.append("Facebook did not load comment threads (3 in a row); remaining marked replies_unavailable")
    missing = sum(1 for rv in reviews if rv.get("replies_unavailable"))
    status = "partial" if missing or len(reviews) < min(max_reviews, diag.get("review_total") or 0) else "ok"
    if missing:
        notes.append(f"{missing} review(s) with comments that could not be loaded")
    return _guard({"status": status, "reviews_url": rurl, "reviews": reviews, "diagnostics": diag,
                   "notes": "; ".join(notes), "threads_tried": tried}, max_reviews)


COLLECT_CACHE_S = 3 * 3600  # a page's reviews collected in the last 3 h are reused (preflight + extract = one pass)


def _collect(opts, reset=False):
    """Scroll the open reviews tab until everything is loaded, then extract → (reviews, diagnostics).
    The result is cached per page for 3 h: the preflight's sample page is not scrolled again in Phase 2."""
    try:
        here = fb_page_url(_h().js("location.href") or "") or ""
    except Exception:
        here = ""
    cache = _read(f"cache/{fb_slug(here)}.json", {}) if here else {}
    want = int(opts.get("maxReviews") or 100000)
    cached = cache.get("reviews") or []
    complete = (cache.get("diag") or {}).get("scroll_reason") in ("reached_total", "no_more")
    # reuse only what covers this request: the preflight's 60 do not stand in for "all", and never return more than asked
    if cached and time.time() - cache.get("at", 0) < COLLECT_CACHE_S and not reset and (len(cached) >= want or complete):
        _say(f"  използвам току-що извлечените ревюта: {min(len(cached), want)} (без ново скролване)")
        return cached[:want], cache.get("diag", {})
    reviews, diag = _collect_live(opts, reset)
    if here and reviews:
        _write(f"cache/{fb_slug(here)}.json", {"at": time.time(), "reviews": reviews, "diag": diag})
    return reviews, diag


def _collect_live(opts, reset=False):
    if reset:
        _run("__fbx.reset()")
    st = {}
    deadline = time.time() + SCROLL_MAX_S
    last_count = -1
    while time.time() < deadline:
        st = _run("__fbx.scroll(" + json.dumps(opts) + ")") or {}
        if st.get("count") != last_count:
            last_count = st.get("count")
            _say(f"  зареждам ревюта: {st.get('count')}/{st.get('target') if (st.get('target') or 0) < 100000 else '?'}")
        if st.get("done"):
            break
    sw = {}
    need = _run("__fbx.needsSweep(" + json.dumps(opts) + ")") or {}
    if isinstance(need, dict) and need.get("reviews") and not need.get("need"):
        _say("  няма ревюта с коментари — второто минаване не е нужно")
        sw = {"skipped": "no_comments"}
    elif time.time() < deadline:   # second pass in the same page: late reply previews, expanded texts
        _say(f"  второ минаване по списъка за отговорите на страницата ({need.get('need', '?') if isinstance(need, dict) else '?'} ревюта с коментари)…")
        first = True
        while time.time() < deadline:   # short chunks: one js() call must finish within ~25 s
            sw = _run("__fbx.sweep(" + json.dumps({**opts, "restart": first, "chunkMs": 12000,
                                                    "maxMs": int(max(30, min(1800, deadline - time.time())) * 1000)}) + ")") or {}
            first = False
            if not isinstance(sw, dict):
                sw = {}
            if sw.get("done") or not sw:
                break
    data = _run("__fbx.extract(" + json.dumps(opts) + ")") or {}
    diag = data.get("diagnostics", {}) or {}
    diag["sweep"] = sw
    diag["scroll_reason"] = st.get("reason") or ("time_limit" if not st.get("done") else None)
    diag["scroll"] = {k: st.get(k) for k in ("count", "target", "stable", "nudges", "waited_ms")}
    return data.get("reviews", []) or [], diag


def _guard(res, max_reviews):
    """Health check of one location's result (same checks as fb_preflight, lenient coverage). When the data looks
    wrong (Facebook changed its UI), the result is NOT saved: status 'suspect', the agent repairs and redoes it."""
    health = fb_check_reviews(res["reviews"], res.get("diagnostics"), max_reviews=max_reviews, mode="run")
    res["health"] = health
    if health["healthy"] is False:
        res["fallback_status"] = res["status"]
        res["status"] = "suspect"
        res["notes"] = "; ".join(x for x in (res.get("notes"), "extractor health failed: " + ", ".join(health["failed"])) if x)
        _record_alert(res.get("reviews_url"), health)
    return res


def fb_save_result(location, result, notes="", folder="results"):
    """Save one finished location (call it for EVERY location, also for no_reviews / errors)."""
    loc = dict(location if isinstance(location, dict) else {"url": str(location)})
    url = fb_page_url(loc.get("url", "")) or loc.get("url")
    if folder == "results":  # one Page = one set of reviews: a store with the brand's / another store's Page has none
        owner = next((b for b in fb_brand_pages() if fb_page_url(b.get("url", "")) == url), None)
        if owner and (loc.get("name") or "").strip().lower() == (owner.get("name") or "").strip().lower():
            _say(f"  {owner.get('name')} е страницата на бранда — тя е в раздела „Бранд“, не като локация")
            return {"saved": None, "status": "is_brand_page", "reviews": 0}
        other = None
        for p in sorted((_WS / "results").glob("*.json")) if (_WS / "results").exists() else []:
            d = _read(f"results/{p.name}", {})
            dl = (d.get("location") if isinstance(d, dict) else None) or {}
            if not isinstance(dl, dict):
                continue
            if not p.stem.startswith("_") and fb_page_url(dl.get("url", "")) == url and dl.get("name") != loc.get("name"):
                other = dl.get("name")
                break
        if owner or other:
            import hashlib
            who = f"на бранда „{owner.get('name')}“" if owner else f"на „{other}“"
            key = fb_slug(url) + "--" + hashlib.md5((loc.get("name") or "").encode()).hexdigest()[:8]
            _write(f"results/{key}.json", {"location": {**loc, "url": url}, "status": "no_own_page", "reviews": [],
                                           "review_total": None,
                                           "notes": f"няма собствена Facebook страница — ревютата са в страницата {who}"})
            _say(f"✓ Записах обекта {loc.get('name')}: без своя страница (ревютата са в страницата {who})")
            return {"saved": f"results/{key}.json", "status": "no_own_page", "reviews": 0}
    for k, v in ((result.get("diagnostics") or {}).get("page") or {}).items():
        if v and not loc.get(k) and k != "name":
            loc[k] = v
    _fill_country(loc)
    out = {"location": {**loc, "url": url}, "status": result.get("status", "unknown"),
           "reviews_url": result.get("reviews_url"), "reviews": result.get("reviews", []),
           "review_total": (result.get("diagnostics") or {}).get("review_total"),
           "notes": "; ".join(x for x in (result.get("notes", ""), notes) if x)}
    rp = result.get("recommend_pct")
    if rp is None:
        rp = (result.get("diagnostics") or {}).get("recommend_pct")   # "80% препоръка" on the reviews tab
    if rp is not None:
        out["recommend_pct"] = rp
    ads = (result.get("diagnostics") or {}).get("ads")
    if ads is not None:
        out["ads"] = ads
    _write(f"{folder}/{fb_slug(url)}.json", out)
    _say(f"✓ Записах {'бранда' if folder == 'brand' else 'локацията'} {loc.get('name') or _short(url)}: "
         f"{out['status']}, {len(out['reviews'])} ревюта")
    partial = _WS / "results" / f"_partial_{fb_slug(url)}.json"
    if partial.exists():
        partial.unlink()
    return {"saved": f"{folder}/{fb_slug(url)}.json", "status": out["status"], "reviews": len(out["reviews"])}


def fb_progress():
    """{'locations': n, 'done': n, 'todo': [location dicts not saved yet], 'stopped': reason|None}."""
    locs = fb_locations()
    done = {p.stem for p in (_WS / "results").glob("*.json") if not p.stem.startswith("_")} if (_WS / "results").exists() else set()
    todo = [l for l in locs if fb_slug(l["url"]) not in done]
    pf = _read(PREFLIGHT_FILE, {})
    brands = fb_brand_pages()
    bdone = {p.stem for p in (_WS / "brand").glob("*.json")} if (_WS / "brand").exists() else set()
    return {"locations": len(locs), "done": len(locs) - len(todo), "todo": todo, "stopped": fb_stopped(),
            "brand_pages": len(brands), "brand_todo": [b for b in brands if fb_slug(b["url"]) not in bdone],
            "preflight": {"healthy": pf.get("healthy"), "at": pf.get("at"), "failed": pf.get("failed", []),
                          "due": (not LITE) and _preflight_due(pf), "unhealthy_streak": pf.get("unhealthy_streak", 0)},
            "override_active": bool(_override_src().strip()),
            "posts_todo": _posts_todo(), "comments_todo": _comments_todo()}


# ------------------------------------------------------------------ preflight / self-repair
def _share(n, total):
    return round(n / total, 3) if total else 0.0


def fb_check_reviews(reviews, diagnostics=None, max_reviews=None, mode="preflight"):
    """Health of an extraction against the output contract (pure: no page access).
    → {healthy: True|False|None, failed: [check names], warnings: [...], stats: {...}, details: {check: why}}.
    healthy None = inconclusive (the sample page has no reviews: pick another sample)."""
    diag = diagnostics or {}
    reviews = reviews or []
    n = len(reviews)
    total = diag.get("review_total")
    failed, warnings, details = [], [], {}

    def fail(name, why):
        failed.append(name)
        details[name] = why

    if diag.get("override_error"):
        fail("override_runs", f"the override threw: {str(diag['override_error'])[:300]}")
    bad = []
    for i, rv in enumerate(reviews):
        if not isinstance(rv, dict):
            bad.append(f"#{i} is {type(rv).__name__}, not an object")
            continue
        for k, types in CONTRACT.items():
            if k not in rv:
                bad.append(f"#{i} lacks '{k}'")
            elif not isinstance(rv[k], types) or (k in ("rating", "comment_count") and isinstance(rv[k], bool)):
                bad.append(f"#{i} '{k}' is {type(rv[k]).__name__}")
    if bad:
        fail("schema", "; ".join(bad[:5]) + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""))
    rv_ok = [rv for rv in reviews if isinstance(rv, dict)]

    stats = {"reviews": n, "review_total": total, "tab_state": diag.get("tab_state"),
             "empty_text": _share(sum(1 for r in rv_ok if not (r.get("text") or "").strip()), n),
             "null_verdict": _share(sum(1 for r in rv_ok if r.get("recommends") is None), n),
             "missing_permalink": _share(sum(1 for r in rv_ok if not r.get("permalink")), n),
             "missing_reviewer": _share(sum(1 for r in rv_ok if not (r.get("reviewer") or "").strip()), n),
             "missing_date": _share(sum(1 for r in rv_ok if not r.get("date_text")), n),
             "with_comment_count": sum(1 for r in rv_ok if r.get("comment_count") is not None),
             "comments_counted": sum(r.get("comment_count") or 0 for r in rv_ok
                                     if isinstance(r.get("comment_count"), int))}
    keys = [r.get("permalink") or f"{r.get('reviewer')}|{(r.get('text') or '')[:80]}" for r in rv_ok]
    stats["duplicates"] = _share(len(keys) - len(set(keys)), n)

    if n == 0:
        if total:
            fail("found_reviews", f"the page says {total} reviews, the extractor read 0 (posts/units not found)")
        elif diag.get("tab_state") in ("no_reviews", "reviews_disabled", "not_found"):
            return {"healthy": None, "failed": failed, "warnings": ["sample page has no reviews: use another sample"],
                    "stats": stats, "details": details}
        else:
            fail("found_reviews", "0 reviews read and the page total is unknown (tab_state "
                                  f"{diag.get('tab_state')!r}): look at the page")
    else:
        if total:
            expect = min(total, max_reviews or total)
            cov = round(n / expect, 3) if expect else 1.0
            stats["coverage"] = cov
            need = HEALTH["coverage_min"] if mode == "preflight" else HEALTH["coverage_min_run"]
            if cov < need and expect - n > 2:  # a tiny page (3 of 4): Facebook counts ratings it does not show
                fail("coverage", f"read {n} of {expect} reviews ({cov:.0%} < {need:.0%}): units/scrolling/accumulation")
            if n > total * HEALTH["over_total_factor"] + 2:
                fail("over_total", f"read {n} but the page says {total}: non-review units or duplicates are parsed")
        else:
            warnings.append("page total ('N reviews') not parsed: coverage unchecked (check __fbx.reviewTotal)")
        small = n < HEALTH["min_sample"]  # on tiny samples only "all missing" fails
        for key, lim, what in (("empty_text", "empty_text_max", "review text (message / text blocks)"),
                               ("null_verdict", "null_verdict_max", "recommends verdict (re.POS / re.NEG)"),
                               ("missing_permalink", "missing_permalink_max", "permalink (re.POST_LINK)"),
                               ("missing_reviewer", "missing_reviewer_max", "reviewer (header link)"),
                               ("duplicates", "duplicate_max", "duplicates (permalink / key)")):
            limit = 0.999 if small and key != "duplicates" else HEALTH[lim]
            if stats[key] > limit:
                fail(key, f"{stats[key]:.0%} of reviews lack/break the {what} (limit {limit:.0%})")
        if stats["missing_date"] > HEALTH["missing_date_warn"]:
            warnings.append(f"{stats['missing_date']:.0%} of reviews have no date_text")
        if n >= HEALTH["comment_count_warn_sample"] and not stats["with_comment_count"]:
            warnings.append("no review has a comment_count: check re.COMMENT_COUNT if the page shows comment counts")
        leaks = sum(1 for r in rv_ok if r.get("header") and r.get("text") and r["header"][:40] in r["text"])
        if leaks > n * 0.3:
            warnings.append(f"{leaks} review text(s) contain the header line (verdict text leaks into the review)")
        warnings += _reply_warnings(rv_ok, stats)
    return {"healthy": not failed, "failed": failed, "warnings": warnings, "stats": stats, "details": details}


_REPLY_BADGE = re.compile(r"^(от автора|автор|author|by the author|top fan|топ фен)\b", re.I)
_REPLY_MORE = re.compile(r"(вижте повече|виж още|see more|read more)\s*$", re.I)


def _reply_warnings(reviews, stats):
    """Warnings (never failures) about the Page's replies shown under reviews; fills stats['replies']."""
    reps = [c for r in reviews for c in (r.get("replies") or [])]
    bad = sum(1 for c in reps if not isinstance(c, dict))
    reps = [c for c in reps if isinstance(c, dict)]
    m = len(reps)
    counted = [r for r in reviews if isinstance(r.get("comment_count"), int)]
    over = sum(1 for r in counted if len(r.get("replies") or []) > r["comment_count"])
    st = {"replies": m, "reviews_with_replies": sum(1 for r in reviews if r.get("replies")),
          "truncated": _share(sum(1 for c in reps if c.get("truncated")), m),
          "empty_text": _share(sum(1 for c in reps if not (c.get("text") or "").strip()), m),
          "no_author": _share(sum(1 for c in reps if not (c.get("author") or "").strip()), m),
          "badge_in_text": sum(1 for c in reps if _REPLY_BADGE.match((c.get("text") or "").strip())),
          "see_more_in_text": sum(1 for c in reps if _REPLY_MORE.search((c.get("text") or "").strip())),
          "more_than_counted": over}
    stats["replies"] = st
    out = []
    if bad:
        out.append(f"replies: {bad} entr(y/ies) are not objects")
    if not m:
        return out
    if st["truncated"] > HEALTH["reply_truncated_warn"]:
        out.append(f"replies: {st['truncated']:.0%} still cut off at 'See more / Вижте повече' (check re.EXPAND / sweep)")
    if st["empty_text"] > HEALTH["reply_empty_warn"]:
        out.append(f"replies: {st['empty_text']:.0%} have no text (check parseComment / textBlocks)")
    if st["no_author"] > HEALTH["reply_no_author_warn"]:
        out.append(f"replies: {st['no_author']:.0%} have no author (check labelAuthor / re.COMMENT_LABEL)")
    if st["badge_in_text"]:
        out.append(f"replies: {st['badge_in_text']} text(s) start with the author badge ('от автора' / 'Author')")
    if st["see_more_in_text"]:
        out.append(f"replies: {st['see_more_in_text']} text(s) end with 'See more / Вижте повече' (expander text kept)")
    if counted and over > len(counted) * HEALTH["reply_over_count_warn"]:
        out.append(f"replies: {over} review(s) have more replies than their comment_count (non-replies parsed as replies?)")
    return out


def fb_check_search(results):
    """Health of fb_search_pages() output → {healthy, failed, warnings, stats}."""
    if isinstance(results, dict) and results.get("error"):
        st = (results["error"] or {}).get("state")
        return {"healthy": False, "failed": ["search_open"], "warnings": [], "stats": {"state": st},
                "details": {"search_open": f"search page did not open: {st}"}}
    results = results or []
    n = len(results)
    with_snip = sum(1 for r in results if (r.get("snippet") or "").strip())
    stats = {"results": n, "with_snippet": with_snip, "sample": [r.get("name") for r in results[:5]]}
    failed, warnings, details = [], [], {}
    if n == 0:
        failed.append("search_results")
        details["search_results"] = "0 Page results parsed (check __fbx.searchResults on the search page)"
    elif with_snip < n * 0.5:
        warnings.append(f"only {with_snip}/{n} results have a snippet (address / category)")
    return {"healthy": not failed, "failed": failed, "warnings": warnings, "stats": stats, "details": details}


def _preflight_due(pf=None):
    pf = _read(PREFLIGHT_FILE, {}) if pf is None else pf
    if not pf.get("healthy") or pf.get("reviews_checked") is False:
        return True
    if pf.get("override_sig") != _override_sig():
        return True
    try:
        age_h = (datetime.now() - datetime.fromisoformat(pf["at"])).total_seconds() / 3600
    except (KeyError, ValueError, TypeError):
        return True
    return age_h > PREFLIGHT_MAX_AGE_H


def _override_sig():
    src = _override_src()
    return hashlib.sha1(src.encode()).hexdigest()[:12] if src.strip() else None


def _record_alert(url, health):
    pf = _read(PREFLIGHT_FILE, {})
    pf.setdefault("run_alerts", []).append({"at": datetime.now().isoformat(timespec="seconds"), "url": url,
                                            "failed": health["failed"], "stats": health["stats"]})
    pf["run_alerts"] = pf["run_alerts"][-20:]
    _write(PREFLIGHT_FILE, pf)


def _trim(rv):
    return {k: (v[:160] if isinstance(v, str) else v) for k, v in rv.items() if k != "replies"} | \
        {"replies": len(rv.get("replies") or [])}


def fb_preflight(sample_url=None, search_query=None, reload=True, max_reviews=60):
    """Is the extractor still reading Facebook correctly? Runs it on one known reviews page and checks the result
    against the output contract (see preflight.md).
      sample_url    a location / reviews URL; default: the first locations in locations.json (up to 3 are tried
                    when a page has no reviews)
      search_query  also check fb_search_pages(search_query) (one more page load), e.g. the brand
      reload=False  re-check the page that is open now, without a new page load (use this while repairing)
    → {healthy: True|False|None, failed, warnings, reviews: {...}, search: {...}, override, next}.
    healthy None: nothing to check yet (no location known) → run it again after the first fb_add_locations.
    Saved to preflight.json; fb_progress() shows it."""
    if LITE:  # logged-out visitors see no review list: nothing to check or repair here (lite reads public Page data)
        return {"healthy": None, "lite": True, "note": "lite mode: reviews are not visible logged out; no review checks, "
                "do not repair the extractor for missing reviews"}
    if fb_stopped():
        return {"healthy": False, "stop": True, "reason": fb_stopped()}
    rep = {"at": datetime.now().isoformat(timespec="seconds"), "override": fb_override(code=False)}
    opts = {"pageName": "", "maxReviews": max_reviews, "steps": 4, "delayMs": 900}
    rev = None
    if not reload:
        reviews, diag = _collect(opts, reset=True)
        rev = {"url": diag.get("url"), **fb_check_reviews(reviews, diag, max_reviews, "preflight"),
               "sample": [_trim(r) for r in reviews[:3] if isinstance(r, dict)], "diagnostics": diag}
    else:
        cands = [sample_url] if sample_url else [l["url"] for l in fb_locations()[:3]]
        for u in cands:
            rurl = u if re.search(r"/reviews|sk=reviews", u) else fb_reviews_url(u)
            o = fb_open(rurl)
            if o.get("state") in ("login_required", "blocked", "stopped", "cap_reached"):
                rep.update(healthy=False, stop=True, reason=o.get("reason") or o.get("state"))
                return rep
            if not o.get("ok"):
                rev = {"url": rurl, "healthy": None, "failed": [], "warnings": [f"page state {o.get('state')}"]}
                continue
            reviews, diag = _collect(opts)
            rev = {"url": rurl, **fb_check_reviews(reviews, diag, max_reviews, "preflight"),
                   "sample": [_trim(r) for r in reviews[:3] if isinstance(r, dict)], "diagnostics": diag}
            if rev["healthy"] is not None:
                break
    rep["reviews"] = rev
    rep["override"] = fb_override(code=False)
    if rep["override"]["error"] and rev is not None:
        rev["healthy"] = False
        rev["failed"] = ["override_parses"] + rev.get("failed", [])
        rev.setdefault("details", {})["override_parses"] = "the override does not parse (bundled extractor was used): " \
            + rep["override"]["error"][:300]
    if search_query:
        rep["search"] = {"query": search_query, **fb_check_search(fb_search_pages(search_query, scrolls=2))}
        if rep["search"]["stats"].get("state") in ("login_required", "blocked", "stopped", "cap_reached"):
            rep.update(healthy=False, stop=True, reason=rep["search"]["stats"]["state"])
            return rep
    parts = [x for x in (rev, rep.get("search")) if x]
    failed = [f for x in parts for f in x.get("failed", [])]
    if failed:
        healthy = False
    elif rev is None or rev.get("healthy") is None:
        healthy = None
    else:
        healthy = True
    old = _read(PREFLIGHT_FILE, {})
    rep.update(healthy=healthy, failed=failed, reviews_checked=bool(rev and rev.get("healthy") is not None),
               warnings=[w for x in parts for w in x.get("warnings", [])], override_sig=_override_sig(),
               unhealthy_streak=(old.get("unhealthy_streak", 0) + 1) if healthy is False else 0,
               run_alerts=[] if healthy else old.get("run_alerts", []))
    if healthy is True:
        rep["next"] = "healthy: continue with the run"
    elif healthy is None:
        rep["next"] = "no reviews page checked yet: run fb_preflight() again once locations.json has locations"
    else:
        rep["next"] = ("UNHEALTHY: do not collect reviews. Repair (preflight.md → Repair): inspect the page, "
                       "fb_save_override(js, note), then fb_preflight(reload=False) until healthy")
    _write(PREFLIGHT_FILE, {k: v for k, v in rep.items() if k != "reviews"} |
           {"reviews": {k: v for k, v in (rev or {}).items() if k not in ("diagnostics", "sample")}})
    if rep["override"]["active"] and healthy is not None:
        log = _read("override_log.json", [])
        if log and log[-1].get("action") == "save":
            log[-1].setdefault("preflight", []).append({"at": rep["at"], "healthy": healthy, "failed": failed})
            _write("override_log.json", log)
    return rep


def fb_override(code=True):
    """The current extractor override: {active, path, error, log (last 5), code}."""
    src = _override_src()
    st = _read("override_status.json", {})
    out = {"active": bool(src.strip()), "path": str(_WS / OVERRIDE_FILE),
           "error": st.get("error") if src.strip() and st.get("sig") == _override_sig() else None,
           "log": _read("override_log.json", [])[-5:]}
    if code:
        out["code"] = src
    return out


def _js_syntax_error(code):
    """Syntax check of the full bundle with node (if installed); None = fine or cannot check."""
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(_bundle("true", override=code))
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=20)
        return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[-1500:]
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        os.unlink(path)


def fb_save_override(js_code, note):
    """Save a repair of the extractor: JS that runs right after fb_extract.js and changes parts of window.__fbx
    (__fbx.sel.*, __fbx.re.*, __fbx.posts, __fbx.parsePost, __fbx.reviewTotal, __fbx.searchResults ...).
    `note` = what Facebook changed and what you changed (kept in override_log.json). The previous version is kept
    in overrides/. Every helper loads the override from now on. Then run fb_preflight(reload=False)."""
    code = (js_code or "").strip()
    if not code:
        return {"saved": False, "error": "empty override: use fb_clear_override(note) to remove it"}
    if not (note or "").strip():
        return {"saved": False, "error": "say in `note` what Facebook changed and what the override changes"}
    if re.search(r"pageState\s*=", code):
        return {"saved": False, "error": "pageState (login / checkpoint detection) cannot be overridden"}
    err = _js_syntax_error(code)
    if err:
        return {"saved": False, "error": "syntax error: " + err}
    prev = _override_src()
    n = len(_read("override_log.json", [])) + 1
    if prev.strip():
        (_WS / "overrides").mkdir(exist_ok=True)
        (_WS / "overrides" / f"fb_extract_override.{n - 1}.js").write_text(prev)
    stamp = datetime.now().isoformat(timespec="seconds")
    header = f"// fb_extract_override.js v{n} — {stamp}\n// " + note.strip().replace("\n", "\n// ") + "\n"
    if "__fbx.override" not in code:
        code += f"\nwindow.__fbx.override = {json.dumps({'version': n, 'note': note.strip()[:300]}, ensure_ascii=False)};"
    (_WS / OVERRIDE_FILE).write_text(header + code + "\n")
    p = _WS / "override_status.json"
    if p.exists():
        p.unlink()
    log = _read("override_log.json", [])
    log.append({"version": n, "at": stamp, "action": "save", "note": note.strip(), "chars": len(code)})
    _write("override_log.json", log)
    return {"saved": True, "version": n, "path": str(_WS / OVERRIDE_FILE),
            "next": "fb_preflight(reload=False) on the open reviews page"}


def fb_clear_override(note):
    """Remove the override (back to the bundled extractor); the file is kept in overrides/."""
    prev = _override_src()
    if not prev.strip():
        return {"cleared": False, "reason": "no override active"}
    log = _read("override_log.json", [])
    (_WS / "overrides").mkdir(exist_ok=True)
    (_WS / "overrides" / f"fb_extract_override.{len(log)}.cleared.js").write_text(prev)
    (_WS / OVERRIDE_FILE).unlink()
    log.append({"version": len(log) + 1, "at": datetime.now().isoformat(timespec="seconds"), "action": "clear",
                "note": note})
    _write("override_log.json", log)
    return {"cleared": True}


# ------------------------------------------------------------------ lite mode (no Facebook login)
_last_search = [0.0]


def fb_web_search(query, pages=1):
    """Bing web search (no login anywhere) → [{title, url, snippet}] of facebook.com results; or {'error': ...}.
    Paced (6–12 s between searches). A search-engine challenge (CAPTCHA) is never solved: the result says
    'search_challenge' and you stop using web search for this run."""
    from urllib.parse import quote

    st, key, cached = _search_gate(query, "web")
    if cached is not None:
        return cached
    h = _h()
    out, seen = [], set()
    for page in range(max(1, pages)):
        gap = random.uniform(6, 12) - (time.time() - _last_search[0])
        if gap > 0:
            time.sleep(gap)
        first = page * 10 + 1
        h.goto_url(f"https://www.bing.com/search?q={quote(query)}&setlang=bg&first={first}")
        try:
            h.wait_for_load(15)
        except Exception:
            pass
        time.sleep(2.5)
        _last_search[0] = time.time()
        text = h.js("document.body.innerText.slice(0, 1500)") or ""
        _say(f"Търся в Bing: „{query}“")
        if SEARCH_CHALLENGE.search(text):
            _say("  Bing поиска CAPTCHA — спирам търсенето там (не я решавам)")
            return {"error": "search_challenge", "engine": "bing", "query": query}
        for r in _js("__fbx.webResults()") or []:
            url = fb_page_url(r.get("url", ""))
            if url and url not in seen:
                seen.add(url)
                out.append({**r, "page_url": url})
    _search_note(st, key, out)
    return out


def _page_details(url, page_id=None):
    """The Page's active ads in Meta's Ad Library (public, no login): {ads_running, ads_active, ads: [...]}.
    ("Page transparency" — creation date, name changes — is no longer shown on Pages, 2026.) One extra paced page;
    nothing, and never a stop, when the Page id is unknown or the library does not answer."""
    if not (PAGE_DETAILS and ADS) or fb_stopped():
        return {}
    page = fb_page_url(url) or url
    pid = page_id or (re.search(r"[?&]id=(\d+)", page) or [None, None])[1]
    if not pid:
        try:
            pid = _run("__fbx.pageId()")
        except Exception:
            pid = None
    if not pid:
        return {}
    lib = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL"
           f"&is_targeted_country=false&media_type=all&search_type=page&view_all_page_id={pid}")
    r = fb_open(lib)
    if not isinstance(r, dict) or not r.get("ok"):
        return {}
    res, seen = {}, -1
    for _ in range(12):  # the library loads cards as you scroll
        time.sleep(2)
        res = _run("__fbx.adsLibrary()")
        res = res if isinstance(res, dict) else {}
        n = len(res.get("ads") or [])
        if res.get("total") == 0 or n == seen or n >= 200:
            break
        seen = n
        try:
            _h().js("window.scrollTo(0, document.documentElement.scrollHeight)")
        except Exception:
            break
    total = res.get("total")
    if total is None:
        return {}
    _say(f"  реклами в Ad Library: {total} активни" if total else "  реклами в Ad Library: няма активни")
    return {"page_id": pid, "ads_running": bool(total), "ads_active": total, "ads": (res.get("ads") or [])[:200]}


def fb_page_summary(url):
    """Public data of a Facebook Page WITHOUT login: {name, category, address, phone, website, hours, followers,
    recommend_pct, review_total, first_review, state}. state 'ok' = read; 'not_found' / 'blocked' / 'stopped' as
    fb_open says (a redirect to the login page counts as 'login_required' and stops the run)."""
    page = fb_page_url(url) or url
    r = fb_open(page)
    if not r.get("ok"):
        return {"url": page, "state": r.get("state", "unknown"), "reason": r.get("reason")}
    s = {}
    for _ in range(10):  # the public view loads slowly: wait until the header data is there (≤ ~20 s)
        s = _js("__fbx.pageSummary()") or {}
        if s.get("name") and (s.get("recommend_pct") is not None or s.get("category")):
            break
        time.sleep(2)
    s["state"] = "ok"
    s["url"] = page
    try:
        s["page_id"] = _js("__fbx.pageId()")
    except Exception:
        pass
    det = _page_details(page, s.get("page_id"))
    s["ads"] = det.pop("ads", None)
    s.update(det)
    rec = (f"{s['recommend_pct']}% препоръка, {s.get('review_total')} ревюта" if s.get("recommend_pct") is not None
           else "без препоръки/ревюта")
    _say(f"  публични данни: {s.get('name') or _short(page)} — {rec}, {s.get('address') or 'без адрес'}")
    return s


def fb_save_summary(location, summary, folder="results"):
    """Save one location in lite mode (results/<slug>.json, status 'summary_only'; or the failure state)."""
    loc = dict(location if isinstance(location, dict) else {"url": str(location)})
    url = fb_page_url(loc.get("url", "")) or loc.get("url")
    for k in PAGE_KEYS + DETAIL_KEYS:
        if summary.get(k) not in (None, "", []) and not loc.get(k):
            loc[k] = summary[k]
    if summary.get("name") and (not loc.get("name") or " " not in loc["name"]):  # a slug like "HappyRakovski"
        loc["name"] = summary["name"]
    _fill_country(loc)
    if summary.get("recommend_pct") is not None:
        loc["rating_text"] = f"{summary['recommend_pct']}% препоръка ({summary.get('review_total')} отзива)"
    state = summary.get("state", "unknown")
    status = "summary_only" if state == "ok" else ("not_found" if state == "not_found" else "error")
    first = summary.get("first_review")
    reviews = [{"reviewer": None, "recommends": first.get("recommends"), "text": first.get("text"),
                "date_text": None, "permalink": None, "replies": []}] if first else []
    out = {"location": {**loc, "url": url}, "status": status, "reviews_url": None, "reviews": reviews,
           "review_total": summary.get("review_total"), "recommend_pct": summary.get("recommend_pct"),
           "notes": ("публични данни без вход (лек режим)" if LITE else "публични данни; страницата няма ревюта/препоръки")
                    + (f"; {summary.get('reason')}" if summary.get("reason") else "")}
    if summary.get("ads") is not None:
        out["ads"] = summary["ads"]
    _write(f"{folder}/{fb_slug(url)}.json", out)
    _say(f"✓ Записах {'бранда' if folder == 'brand' else 'локацията'} {loc.get('name') or _short(url)}: {status}")
    return {"saved": f"{folder}/{fb_slug(url)}.json", "status": status, "recommend_pct": summary.get("recommend_pct"),
            "review_total": summary.get("review_total")}



# ------------------------------------------------------------------ the brand's own Pages (a separate section)
def fb_add_brand_pages(items):
    """Record the brand's official Pages (global / country), [{name, url, scope}] → brand_pages.json. They are a
    separate section of the report and are never counted as locations."""
    pages = _read("brand_pages.json", [])
    known = {b["url"] for b in pages}
    added = 0
    for it in items or []:
        url = fb_page_url((it or {}).get("url", ""))
        if not url or url in known:
            continue
        pages.append({"name": it.get("name"), "url": url, "scope": it.get("scope")})
        _say(f"Официална страница на бранда: {it.get('name') or _short(url)}")
        known.add(url)
        added += 1
    _write("brand_pages.json", pages)
    # a brand page that slipped into the location list is moved out of it
    locs = _read("locations.json", [])
    kept = [l for l in locs if l.get("url") not in known]
    if len(kept) != len(locs):
        _write("locations.json", kept)
    return {"added": added, "total": len(pages)}


def fb_brand_pages():
    return _read("brand_pages.json", [])


def fb_extract_brand(url, name=None, max_reviews=None):
    """The brand's own Page as its own section (brand/<slug>.json): its public data (category, followers, address,
    website, % recommend, number of reviews) and — when logged in (not lite) — its reviews with the Page's replies,
    exactly like a location. Returns what was saved, or the stop state."""
    page = fb_page_url(url) or url
    _say(f"Бранд: извличам „{name or _short(page)}“ (отделен раздел)")
    summary = fb_page_summary(page)
    if summary.get("state") in ("login_required", "blocked", "stopped", "cap_reached"):
        return {"status": summary["state"], "url": page, "reason": summary.get("reason")}
    scope = next((b.get("scope") for b in fb_brand_pages() if b.get("url") == page), None)
    loc = {"name": summary.get("name") or name or page, "url": page, "source": "brand_page",
           "country": (scope if scope and str(scope).lower() not in ("global", "country", "world") else None)}
    for k in PAGE_KEYS + DETAIL_KEYS:
        if summary.get(k) not in (None, "", []):
            loc[k] = summary[k]
    if LITE or summary.get("state") != "ok":
        return fb_save_summary(loc, summary, folder="brand")
    res = _extract_reviews(page, max_reviews=max_reviews, name=loc["name"])
    if res.get("status") in ("login_required", "blocked", "stopped", "cap_reached"):
        return res
    if not res.get("reviews"):  # reviews switched off or not shown: keep the public data
        return fb_save_summary(loc, summary, folder="brand")
    res["recommend_pct"] = summary.get("recommend_pct")
    diag = dict(res.get("diagnostics") or {})
    diag["review_total"] = diag.get("review_total") or summary.get("review_total")
    if summary.get("ads") is not None:
        diag["ads"] = summary["ads"]
    res["diagnostics"] = diag
    return fb_save_result(loc, res, folder="brand")


# ------------------------------------------------------------------ Phase 3 / 4: the Pages' posts and their comments
CONTENT_STATUSES = ("ok", "partial", "no_reviews", "reviews_disabled", "summary_only")


def _saved_pages():
    """Every saved Page (brand first, then locations) that has a Page of its own: [{name, url, folder}]."""
    out, seen = [], set()
    for folder in ("brand", "results"):
        d = _WS / folder
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            if f.stem.startswith("_"):
                continue
            r = _read(f"{folder}/{f.name}", {})
            if not isinstance(r, dict) or r.get("status") not in CONTENT_STATUSES:
                continue
            loc = r.get("location") or {}
            url = fb_page_url(loc.get("url", "")) or loc.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"name": loc.get("name") or _short(url), "url": url, "folder": folder})
    return out


def _posts_todo():
    if LITE or not POSTS:
        return []
    return [p for p in _saved_pages() if not (_WS / "posts" / f"{fb_slug(p['url'])}.json").exists()]


def _comments_todo():
    if LITE or not POSTS or not POST_COMMENTS:
        return 0
    n = 0
    for f in sorted((_WS / "posts").glob("*.json")) if (_WS / "posts").exists() else []:
        for p in (_read(f"posts/{f.name}", {}) or {}).get("posts", []):
            if (p.get("comments") or 0) > 0 and p.get("permalink") and p.get("comments_list") is None:
                n += 1
    return n


def _safe_posts():
    try:
        got = _run("__fbx.collectedPosts()")
    except Exception:
        return []
    return got if isinstance(got, list) else []


def fb_extract_posts(url, name=None):
    """Phase 3. All posts of one Page (its timeline): date, text, type (photo / album / video / reel / link / text),
    photos, reactions, comments, shares, views, permalink → posts/<slug>.json. Paced like everything else; returns
    {status, posts} or the stop state."""
    page = fb_page_url(url) or url
    _say(f"Постове: {name or _short(page)} — отварям страницата")
    r = fb_open(page)
    if not isinstance(r, dict) or not r.get("ok"):
        return {"status": (r or {}).get("state", "unknown"), "posts": 0}
    opts = {"steps": 6, "delayMs": 1100, "maxPosts": MAX_POSTS, "since": POSTS_SINCE, "budgetMs": 8000}
    deadline, last, st, errors, posts, k = time.time() + SCROLL_MAX_S, -1, {}, 0, [], 0
    while time.time() < deadline:
        try:
            st = _run("__fbx.scrollPosts(" + json.dumps(opts) + ")")
            errors = 0
        except Exception as e:  # a slow step or a hiccup: keep what is collected, retry, never hand the error up
            errors += 1
            _say(f"  бавна стъпка при постовете ({str(e)[:80]}) — опитвам пак ({errors}/3)")
            if errors >= 3:
                st = {"reason": "errors"}
                break
            time.sleep(3)
            continue
        if not isinstance(st, dict):
            st = {}
            break
        k += 1
        if st.get("count") != last:
            last = st.get("count")
            _say(f"  вадя постове: {last}")
        if k % 5 == 0:  # keep a snapshot: a later failure never loses what was already read
            got = _safe_posts()
            posts = got if len(got) >= len(posts) else posts
        if st.get("done"):
            break
    got = _safe_posts()
    posts = got if len(got) >= len(posts) else posts
    pid = None
    try:
        pid = _run("__fbx.pageId()")
    except Exception:
        pass
    _write(f"posts/{fb_slug(page)}.json", {"url": page, "name": name, "page_id": pid, "posts": posts,
                                         "reason": st.get("reason") or "time_limit", "at": time.time()})
    with_c = sum(1 for p in posts if (p.get("comments") or 0) > 0)
    _say(f"✓ Постове на {name or _short(page)}: {len(posts)} ({with_c} с коментари)")
    return {"status": "ok", "posts": len(posts), "with_comments": with_c}


def fb_extract_comments(budget_s=None):
    """Phase 4. Opens every saved post that has comments (newest first, across all Pages) and reads its comments
    (author, text, date, reply level, the Page's own replies). Saves after every post, so it continues where it
    stopped. Works for `budget_s` seconds, then returns {done: False, left}: call it again until done is True."""
    t0, opened = time.time(), 0
    budget = budget_s or CONTENT_BUDGET_S
    names = {p["url"]: p["name"] for p in _saved_pages()}
    queue = []
    for f in sorted((_WS / "posts").glob("*.json")) if (_WS / "posts").exists() else []:
        data = _read(f"posts/{f.name}", {}) or {}
        for i, p in enumerate(data.get("posts", [])):
            if (p.get("comments") or 0) > 0 and p.get("permalink") and p.get("comments_list") is None:
                queue.append((f.name, i, p.get("date_full") or "", data.get("name") or names.get(data.get("url"), "")))
    total = len(queue)
    for k, (fname, i, _, page_name) in enumerate(queue, 1):
        if time.time() - t0 > budget:
            return {"done": False, "opened": opened, "left": total - k + 1}
        data = _read(f"posts/{fname}", {}) or {}
        p = data["posts"][i]
        _say(f"Коментари: пост {k}/{total} на {page_name or fname} ({p.get('comments')} коментара)")
        r = fb_open(p["permalink"])
        if not isinstance(r, dict) or r.get("state") in ("login_required", "blocked", "stopped", "cap_reached"):
            return {"done": False, "stopped": (r or {}).get("state"), "opened": opened, "left": total - k + 1}
        opened += 1
        if not r.get("ok"):
            p["comments_list"] = []
            p["comments_error"] = r.get("state")
        else:
            time.sleep(2)
            try:
                _run("__fbx.expandComments({})")
                c = _run("__fbx.postComments(" + json.dumps(page_name) + ")")
            except Exception as e:
                c = {"comments": [], "error": str(e)[:200]}
            p["comments_list"] = (c or {}).get("comments", []) if isinstance(c, dict) else []
        data["posts"][i] = p
        _write(f"posts/{fname}", data)
    return {"done": True, "opened": opened, "left": 0, **_comment_gaps(),
            "next": "Phase 4 is complete. Finish the round now with the summary; do not open posts again or read the "
                    "page yourself. Fewer comments read than the post's count is a known limit (reels, link posts, "
                    "hidden or deleted comments): say the numbers in notes."}


def _comment_gaps():
    """How complete the read comments are, so nobody has to open posts again to check."""
    read = fewer = none = reels = 0
    for f in sorted((_WS / "posts").glob("*.json")) if (_WS / "posts").exists() else []:
        for p in (_read(f"posts/{f.name}", {}) or {}).get("posts", []):
            if not (p.get("comments") or 0) or p.get("comments_list") is None:
                continue
            got = len(p["comments_list"])
            read += 1
            if got == 0:
                none += 1
                reels += "/reel/" in str(p.get("permalink"))
            elif got < (p.get("comments") or 0):
                fewer += 1
    return {"posts_read": read, "posts_fewer_than_count": fewer, "posts_none_read": none, "of_them_reels": reels}
