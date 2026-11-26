import importlib.util
import json
import sys
from pathlib import Path

from fbreviews.agents import install_skill, task_text
from fbreviews.pipeline import load_results, progress_of

ROOT = Path(__file__).resolve().parents[1]


def load_helpers(ws: Path):
    """Load agent_helpers.py the way browser-harness does, with the workspace as BU_STATE_DIR's parent."""
    aw = install_skill(ws, {"max_locations": 3, "pace_min_s": 0, "pace_max_s": 0})
    import os
    os.environ["BU_STATE_DIR"] = str(ws / ".browser-use")
    spec = importlib.util.spec_from_file_location("agent_helpers_test", aw / "agent_helpers.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, aw


def test_install_and_task(tmp_path):
    m, aw = load_helpers(tmp_path)
    assert (aw / "domain-skills" / "facebook" / "reviews.md").exists()
    assert (aw / "fb_extract.js").exists() and m._JS
    t = task_text("Zara", aw, 50, None, 1, False)
    assert "reviews.md" in t and "fb_progress()" in t


def test_helpers_files(tmp_path):
    m, _ = load_helpers(tmp_path)
    assert m.fb_page_url("https://m.facebook.com/ZaraSofia/reviews?x=1") == "https://www.facebook.com/ZaraSofia"
    assert m.fb_reviews_url("https://www.facebook.com/profile.php?id=5") == "https://www.facebook.com/profile.php?id=5&sk=reviews"
    r = m.fb_add_locations([
        {"name": "Zara A", "url": "https://www.facebook.com/zaraa/"},
        {"name": "dup", "url": "https://facebook.com/zaraa"},
        {"name": "Zara B", "url": "https://www.facebook.com/profile.php?id=7&sk=about"},
        {"name": "Zara C", "url": "https://www.facebook.com/zarac"},
        {"name": "over cap", "url": "https://www.facebook.com/zarad"},
        {"name": "search", "url": "https://www.facebook.com/search/pages?q=x"},
    ])
    assert r == {"added": 3, "total": 3, "cap": 3}
    p = m.fb_progress()
    assert p["locations"] == 3 and p["done"] == 0 and p["stopped"] is None
    loc = p["todo"][0]
    res = {"status": "ok", "reviews_url": m.fb_reviews_url(loc["url"]), "notes": "",
           "reviews": [{"reviewer": "A", "recommends": True, "text": "ok", "comment_count": 1, "replies_unavailable": True,
                        "replies": [{"author": "Zara A", "text": "Thanks!", "depth": 1}]}]}
    assert m.fb_save_result(loc, res)["reviews"] == 1
    assert m.fb_progress()["done"] == 1

    run_dir = tmp_path.parent / tmp_path.name  # tmp_path plays the role of run_dir/workspace's parent
    (tmp_path / "workspace").mkdir(exist_ok=True)
    for f in ("locations.json",):
        (tmp_path / "workspace" / f).write_text((tmp_path / f).read_text())
    (tmp_path / "workspace" / "results").mkdir(exist_ok=True)
    for f in (tmp_path / "results").glob("*.json"):
        (tmp_path / "workspace" / "results" / f.name).write_text(f.read_text())
    out = load_results(run_dir)
    assert len(out) == 1 and out[0].reviews[0].replies_unavailable and out[0].reviews[0].comment_count == 1
    assert progress_of(run_dir) == {"locations": 3, "done": 1, "stopped": None, "posts_todo": 1, "comments_todo": 0}


def test_stop_file_blocks_navigation(tmp_path):
    m, _ = load_helpers(tmp_path)
    (tmp_path / "STOP").write_text("blocked at x")
    assert m.fb_open("https://www.facebook.com/x") == {"ok": False, "state": "stopped", "reason": "blocked at x"}


# ------------------------------------------------------------------ preflight / self-repair
import re
import shutil
import subprocess
import types

import pytest


def _review(i, **kw):
    r = {"reviewer": f"R{i}", "recommends": True, "rating": None, "text": f"text {i}", "date_text": "2 г.",
         "permalink": f"https://www.facebook.com/r{i}/posts/pfbid{i}", "comment_count": None, "replies": [],
         "header": f"R{i} препоръчва Zara"}
    r.update(kw)
    return r


class FakePage:
    """Stands in for browser_harness.helpers: records every js() source and answers by the trailing __fbx call."""

    def __init__(self, reviews, total, tab_state="ok", search=None, broken=None):
        self.reviews, self.total, self.tab_state = reviews, total, tab_state
        self.search = search or []
        self.broken = broken  # a substring: js() raises if the source contains it (an override that does not parse)
        self.sources, self.urls = [], []
        self.injected, self.sig, self._buf = [], None, []  # the extractor arrives in pieces, once per page

    def goto_url(self, url):
        self.urls.append(url)
        self.sig = None  # a navigation clears the injected extractor

    def wait_for_load(self, timeout=20):
        return True

    def js(self, src):
        self.sources.append(src)
        if src.startswith("!!(window.__fbx"):
            return bool(self.sig) and self.sig in src
        if src.startswith("window.__fbxSrc = []"):
            self._buf = []
            return True
        if src.startswith("window.__fbxSrc.push("):
            self._buf.append(json.loads(src[len("window.__fbxSrc.push("):-len("); true")]))
            return True
        if "(0, eval)(s)" in src:
            code = "".join(self._buf)
            if self.broken and self.broken in code:
                raise RuntimeError("SyntaxError: Unexpected token")
            self.injected.append(code)
            self.sig = re.search(r'__sig = "(\w+)"', src).group(1)
            return True
        assert self.sig, "an __fbx call before the extractor was injected"
        tail = src.rsplit("\n", 1)[-1]
        if "pageState" in tail:
            return {"state": "ok", "url": self.urls[-1] if self.urls else ""}
        if "__fbx.scroll(" in tail:
            return {"count": len(self.reviews), "done": True}
        if "__fbx.extract(" in tail:
            return {"reviews": [dict(r) for r in self.reviews],
                    "diagnostics": {"review_total": self.total, "tab_state": self.tab_state, "url": "u"}}
        if "searchResults()" in tail:
            return self.search
        return True


@pytest.fixture
def fake(tmp_path, monkeypatch):
    m, aw = load_helpers(tmp_path)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)

    def use(page):
        mod = types.ModuleType("browser_harness.helpers")
        for k in ("goto_url", "wait_for_load", "js"):
            setattr(mod, k, getattr(page, k))
        monkeypatch.setitem(sys.modules, "browser_harness.helpers", mod)
        return page
    return m, use, tmp_path


def test_preflight_skill_is_installed_and_referenced(tmp_path):
    m, aw = load_helpers(tmp_path)
    pre = aw / "domain-skills" / "facebook" / "preflight.md"
    assert pre.exists() and "fb_save_override" in pre.read_text()
    assert "preflight.md" in (aw / "domain-skills" / "facebook" / "reviews.md").read_text()
    t = task_text("Zara", aw, 50, None, 1, False)
    assert t.index("preflight.md") < t.index("reviews.md") and "fb_preflight()" in t
    from fbreviews.agents import SYSTEM_RULES
    assert "preflight.md" in SYSTEM_RULES and "fb_save_override" in SYSTEM_RULES
    assert m.OPEN_THREADS is False


def test_check_reviews_thresholds(tmp_path):
    m, _ = load_helpers(tmp_path)
    good = [_review(i) for i in range(10)]
    h = m.fb_check_reviews(good, {"review_total": 10, "tab_state": "ok"})
    assert h["healthy"] is True and h["failed"] == [] and h["stats"]["coverage"] == 1.0

    # Facebook changed the verdict wording: every recommends is null; text holds the header line
    bad = [_review(i, recommends=None, text=None) for i in range(10)]
    h = m.fb_check_reviews(bad, {"review_total": 10})
    assert h["healthy"] is False and {"null_verdict", "empty_text"} <= set(h["failed"])

    assert m.fb_check_reviews([], {"review_total": 12})["failed"] == ["found_reviews"]
    assert m.fb_check_reviews([], {"review_total": None, "tab_state": "no_reviews"})["healthy"] is None
    h = m.fb_check_reviews(good[:5], {"review_total": 40})
    assert h["failed"] == ["coverage"]
    assert m.fb_check_reviews(good[:5], {"review_total": 40}, max_reviews=5)["healthy"] is True
    assert "over_total" in m.fb_check_reviews(good, {"review_total": 3})["failed"]
    h = m.fb_check_reviews([_review(1, recommends="yes", comment_count="2")], {"review_total": 1})
    assert h["failed"] == ["schema"] and "recommends" in h["details"]["schema"]
    # tiny sample: one missing permalink out of 2 is fine, all missing is not
    assert m.fb_check_reviews([_review(1), _review(2, permalink=None)], {"review_total": 2})["healthy"] is True
    assert "missing_permalink" in m.fb_check_reviews([_review(1, permalink=None)], {"review_total": 1})["failed"]
    assert any("not parsed" in w for w in m.fb_check_reviews(good, {})["warnings"])
    assert m.fb_check_search([])["failed"] == ["search_results"]
    assert m.fb_check_search([{"name": "Zara", "url": "u", "snippet": "Clothing store · Sofia"}])["healthy"] is True


def test_preflight_healthy_and_unhealthy(fake):
    m, use, ws = fake
    m.fb_add_locations([{"name": "Zara A", "url": "https://www.facebook.com/zaraa"}])
    assert m.fb_progress()["preflight"]["due"] is True
    page = use(FakePage([_review(i) for i in range(8)], 8,
                        search=[{"name": "Zara A", "href": "https://www.facebook.com/zaraa", "snippet": "Store"}]))
    rep = m.fb_preflight(search_query="Zara")
    assert rep["healthy"] is True and rep["reviews"]["url"] == "https://www.facebook.com/zaraa/reviews"
    assert rep["search"]["healthy"] is True and len(page.urls) == 2  # reviews page + search page
    pf = m.fb_progress()["preflight"]
    assert pf["healthy"] is True and pf["due"] is False

    use(FakePage([_review(i, recommends=None) for i in range(8)], 8))
    rep = m.fb_preflight(reload=False)
    assert rep["healthy"] is False and rep["failed"] == ["null_verdict"] and "Repair" in rep["next"]
    assert m.fb_progress()["preflight"] == {**m.fb_progress()["preflight"], "healthy": False, "due": True,
                                             "unhealthy_streak": 1}

    (ws / "STOP").write_text("blocked at x")
    assert m.fb_preflight()["stop"] is True


def test_preflight_without_locations_is_inconclusive(fake):
    m, use, _ = fake
    use(FakePage([], None, search=[{"name": "Zara", "href": "https://www.facebook.com/zara", "snippet": "x"}]))
    rep = m.fb_preflight(search_query="Zara")
    assert rep["healthy"] is None and rep["search"]["healthy"] is True
    assert m.fb_progress()["preflight"]["due"] is True


def test_suspect_result_is_not_saved(fake):
    m, use, ws = fake
    loc = {"name": "Zara A", "url": "https://www.facebook.com/zaraa"}
    use(FakePage([_review(i, text="") for i in range(6)], 6))
    res = m.fb_extract_reviews(loc["url"], name=loc["name"], location=loc)
    assert res["status"] == "suspect" and res["fallback_status"] == "ok" and "empty_text" in res["notes"]
    assert "saved" not in res and not (ws / "results" / "zaraa.json").exists()
    assert m._read("preflight.json", {})["run_alerts"][0]["failed"] == ["empty_text"]

    use(FakePage([_review(i, comment_count=1 if i == 0 else None) for i in range(6)], 6))
    res = m.fb_extract_reviews(loc["url"], name=loc["name"], location=loc)
    assert res["status"] == "ok" and res["health"]["healthy"] is True and res["saved"]["reviews"] == 6
    assert res["reviews"][0]["replies_unavailable"] is True

    # the page says it has reviews but none were read: unknown (not saved), not "no_reviews"
    use(FakePage([], 9, tab_state="no_reviews"))
    assert m.fb_extract_reviews("https://www.facebook.com/zarab")["status"] == "unknown"


def test_override_is_saved_loaded_and_logged(fake):
    m, use, ws = fake
    page = use(FakePage([_review(i) for i in range(6)], 6))
    assert m.fb_save_override("__fbx.sel.unit = 'div.x';", "")["saved"] is False  # a note is required
    assert m.fb_save_override("__fbx.pageState = function () { return {state: 'ok'}; };", "x")["saved"] is False
    r = m.fb_save_override("__fbx.sel.unit = 'div.unit-v9';", "units lost aria-posinset")
    assert r["saved"] is True and r["version"] == 1
    src = (ws / "fb_extract_override.js").read_text()
    assert "units lost aria-posinset" in src and "__fbx.override" in src

    m.fb_js("__fbx.posts().length")
    assert "div.unit-v9" in page.injected[-1]
    assert page.injected[-1].index("div.unit-v9") < page.injected[-1].index("window.__fbx.pageState = __fbxSafePageState")
    assert all(len(x) < 64 * 1024 for x in page.sources)  # every request fits browser-harness's limit
    m.fb_open("https://www.facebook.com/zaraa/reviews")
    assert "div.unit-v9" not in page.injected[-1]  # login / checkpoint check: bundled extractor only
    m.fb_extract_reviews("https://www.facebook.com/zaraa", save=False)
    assert "div.unit-v9" in page.injected[-1]

    m.fb_add_locations([{"name": "Zara A", "url": "https://www.facebook.com/zaraa"}])
    assert m.fb_preflight()["healthy"] is True
    assert m.fb_override()["log"][-1]["preflight"][-1]["healthy"] is True

    # a new helper load (next round / --resume) still uses the override from the workspace
    m2, _ = load_helpers(ws)
    assert "div.unit-v9" in m2._bundle("true")
    assert m2.fb_progress()["override_active"] is True

    assert m.fb_save_override("__fbx.sel.unit = 'div.y';", "v2")["version"] == 2
    assert (ws / "overrides" / "fb_extract_override.1.js").exists()
    assert m.fb_clear_override("back to bundled")["cleared"] is True
    assert not (ws / "fb_extract_override.js").exists() and m.fb_override()["active"] is False


def test_broken_override_falls_back_and_fails_preflight(fake):
    m, use, ws = fake
    m.fb_add_locations([{"name": "Zara A", "url": "https://www.facebook.com/zaraa"}])
    (ws / "fb_extract_override.js").write_text("__fbx.sel.unit = BROKEN(;")  # written by hand, not checked
    page = use(FakePage([_review(i) for i in range(6)], 6, broken="BROKEN(;"))
    assert m.fb_js("__fbx.posts().length") is True  # fell back to the bundled extractor
    assert "SyntaxError" in m.fb_override()["error"]
    rep = m.fb_preflight()
    assert rep["healthy"] is False and "override_parses" in rep["failed"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_override_reaches_extractor_internals(tmp_path):
    """Run the real fb_extract.js + an override in node with a stub DOM: internal calls go through __fbx, so
    replacing a selector / function changes what scroll() and extract() use; pageState stays the bundled one."""
    m, _ = load_helpers(tmp_path)
    assert m.fb_save_override("__fbx.sel.unit = 'div.review-v2'; __fbx.reviewTotal = function () { return 77; };",
                              "test")["saved"]
    stub = """
var seen = [];
var el = {innerText: '', textContent: '', getAttribute: function () { return null; }};
global.window = global; global.innerHeight = 800; global.scrollBy = function () {}; global.scrollTo = function () {};
global.location = {href: 'https://www.facebook.com/x/reviews', pathname: '/x/reviews', search: ''};
global.document = {title: 't', visibilityState: 'visible', body: el,
  querySelectorAll: function (s) { seen.push(s); return []; }, querySelector: function () { return null; }};
"""
    src = stub + m._bundle("__fbx.extract({}).then(function (r) { console.log(JSON.stringify({seen: seen, d: r.diagnostics, "
                           "same: __fbx.pageState === window.__fbx.pageState})); });")
    f = tmp_path / "run.js"
    f.write_text(src)
    out = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert "div.review-v2" in res["seen"] and "[aria-posinset]" not in res["seen"]
    assert res["d"]["review_total"] == 77 and res["d"]["override"]["version"] == 1 and res["same"] is True


def test_counts_only_branch_reports_partial(fake):
    m, use, ws = fake
    # live case: the page says 522, max_reviews 20, only 14 were loaded → partial (saved), not ok
    use(FakePage([_review(i) for i in range(14)], 522))
    res = m.fb_extract_reviews("https://www.facebook.com/HappyMorskaGara", max_reviews=20)
    assert res["status"] == "partial" and "only 14 of 20 reviews loaded" in res["notes"]
    assert res["saved"]["status"] == "partial"
    # everything up to max_reviews loaded → ok
    use(FakePage([_review(i) for i in range(20)], 522))
    assert m.fb_extract_reviews("https://www.facebook.com/x1", max_reviews=20)["status"] == "ok"
    # far too few → suspect (not saved), fallback partial
    use(FakePage([_review(i) for i in range(8)], 522))
    res = m.fb_extract_reviews("https://www.facebook.com/x2", max_reviews=20)
    assert res["status"] == "suspect" and res["fallback_status"] == "partial" and "coverage" in res["health"]["failed"]
    # preflight flags the same shortfall (80% rule)
    assert m.fb_check_reviews([_review(i) for i in range(14)], {"review_total": 522}, max_reviews=20)["failed"] == ["coverage"]


def test_reply_warnings_never_fail(tmp_path):
    m, _ = load_helpers(tmp_path)
    good_reply = {"author": "Zara A", "text": "Благодарим за отзива!", "truncated": False, "depth": 1}
    good = [_review(i, comment_count=1, replies=[dict(good_reply)]) for i in range(10)]
    h = m.fb_check_reviews(good, {"review_total": 10})
    assert h["healthy"] is True and not [w for w in h["warnings"] if w.startswith("replies")]
    assert h["stats"]["replies"]["replies"] == 10 and h["stats"]["replies"]["reviews_with_replies"] == 10

    broken = [_review(i, comment_count=1, replies=[
        {"author": None, "text": "от автора Благодарим … Вижте повече", "truncated": True, "depth": 1},
        {"author": "Zara A", "text": "", "truncated": False, "depth": 1}]) for i in range(10)]
    h = m.fb_check_reviews(broken, {"review_total": 10})
    assert h["healthy"] is True  # replies only warn
    w = " | ".join(h["warnings"])
    for part in ("cut off", "no text", "no author", "author badge", "end with", "more replies than"):
        assert part in w, part
    assert h["stats"]["replies"]["badge_in_text"] == 10 and h["stats"]["replies"]["truncated"] == 0.5


def test_lite_summary_saved(tmp_path):
    m, _ = load_helpers(tmp_path)
    loc = {"name": "Happy Morska Gara", "url": "https://www.facebook.com/HappyMorskaGara"}
    summary = {"state": "ok", "name": "Happy Bar & Grill (Morska Gara Varna)", "category": "European Restaurant",
               "address": "Sea Garden, Varna, Bulgaria", "phone": "0700 20 888", "website": "happy.bg",
               "followers": "3.1K", "recommend_pct": 94, "review_total": 522, "first_review": None}
    r = m.fb_save_summary(loc, summary)
    assert r["status"] == "summary_only" and r["recommend_pct"] == 94
    d = json.loads((tmp_path / "results" / "happymorskagara.json").read_text())
    assert d["review_total"] == 522 and d["location"]["phone"] == "0700 20 888"
    assert m.fb_save_summary(loc, {"state": "not_found"})["status"] == "not_found"

    (tmp_path / "workspace" / "results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "results" / "happymorskagara.json").write_text(json.dumps(d))
    out = load_results(tmp_path)
    assert out[0].status == "summary_only" and out[0].recommend_pct == 94 and out[0].location.phone == "0700 20 888"


def test_brand_pages_separate_from_locations(tmp_path):
    m, _ = load_helpers(tmp_path)
    m.fb_add_locations([{"name": "Lidl Varna", "url": "https://www.facebook.com/lidlvarna"},
                        {"name": "Lidl България", "url": "https://www.facebook.com/lidlbulgaria"}])
    r = m.fb_add_brand_pages([{"name": "Lidl България", "url": "https://www.facebook.com/lidlbulgaria/", "scope": "country"}])
    assert r == {"added": 1, "total": 1}
    assert [l["url"] for l in m.fb_locations()] == ["https://www.facebook.com/lidlvarna"]  # moved out of locations
    assert m.fb_add_locations([{"name": "x", "url": "https://www.facebook.com/lidlbulgaria"}])["added"] == 0
    p = m.fb_progress()
    assert p["brand_pages"] == 1 and len(p["brand_todo"]) == 1 and p["locations"] == 1
    m.fb_save_summary({"name": "Lidl България", "url": "https://www.facebook.com/lidlbulgaria", "source": "brand_page"},
                      {"state": "ok", "category": "Supermarket", "recommend_pct": 71, "review_total": 1200}, folder="brand")
    assert m.fb_progress()["brand_todo"] == []

    ws = tmp_path / "workspace"
    (ws / "brand").mkdir(parents=True, exist_ok=True)
    (ws / "brand" / "lidlbulgaria.json").write_text((tmp_path / "brand" / "lidlbulgaria.json").read_text())
    brands = load_results(tmp_path, "brand")
    assert len(brands) == 1 and brands[0].is_brand and brands[0].recommend_pct == 71


def test_brand_section_in_excel(tmp_path):
    from openpyxl import load_workbook
    from fbreviews.export import summarize, write_excel, write_json
    from fbreviews.models import BrandReport, LocationPage, LocationResult, Review

    brand = LocationResult(location=LocationPage(name="Lidl България", url="https://www.facebook.com/lidlbulgaria"),
                           status="summary_only", recommend_pct=71, review_total=1200, is_brand=True,
                           reviews=[Review(reviewer="A", recommends=True, text="ok")])
    loc = LocationResult(location=LocationPage(name="Lidl Varna", url="https://www.facebook.com/lidlvarna"),
                         status="summary_only", recommend_pct=88, review_total=40)
    rep = BrandReport(brand="Lidl", locations=[loc], brand_sections=[brand])
    rep.summary = summarize(rep)
    assert rep.summary["locations"] == 1 and rep.summary["brand_pages"][0]["recommend_pct"] == 71
    write_excel(rep, tmp_path / "r.xlsx")
    write_json(rep, tmp_path / "r.json")
    wb = load_workbook(tmp_path / "r.xlsx")
    assert wb.sheetnames[0] == "Обобщение" and "Бранд" in wb.sheetnames and "Локации" in wb.sheetnames
    assert wb["Бранд"]["A2"].value == "Lidl България" and wb["Локации"]["A2"].value == "Lidl Varna"
    assert wb["Ревюта"]["A2"].value == "[Бранд] Lidl България"
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["brand_sections"][0]["stats"]["facebook_recommend_pct"] == 71 and len(data["locations"]) == 1


def test_search_cache_and_close(tmp_path):
    m, _ = load_helpers(tmp_path)
    loads = []
    m.fb_open = lambda url: (loads.append(url), {"ok": True, "state": "ok"})[1]

    class H:
        def js(self, expr):
            return [] if "searchResults" in expr else None
    m._h = lambda: H()
    m.fb_add_locations([{"name": "A", "url": "https://www.facebook.com/a"}])
    m.fb_search_pages("Дивака", scrolls=1)
    m.fb_search_pages("Дивака", scrolls=1)            # cached: no second page load
    assert len(loads) == 1
    m.fb_search_pages("Дивака Варна", scrolls=1)
    m.fb_search_pages("Дивака Бургас", scrolls=1)
    m.fb_add_locations([])                           # third search added nothing -> closed
    r = m.fb_search_pages("Дивака Русе", scrolls=1)
    assert isinstance(r, dict) and r["error"] == "search_closed" and len(loads) == 3


def test_search_closes_when_no_location_found(tmp_path):
    m, _ = load_helpers(tmp_path)
    loads = []
    m.fb_open = lambda url: (loads.append(url), {"ok": True, "state": "ok"})[1]

    class H:
        def js(self, expr):
            return [] if "searchResults" in expr else None
    m._h = lambda: H()
    for i in range(6):                                # 6 searches, never a location
        assert isinstance(m.fb_search_pages(f"Щастливеца {i}", scrolls=1), list)
    r = m.fb_search_pages("Щастливеца Румъния", scrolls=1)
    assert isinstance(r, dict) and r["error"] == "search_closed" and "brand_todo" in r["message"] and len(loads) == 6


def test_store_without_own_page_and_cache_limit(tmp_path):
    m, _ = load_helpers(tmp_path)
    brand = "https://www.facebook.com/shtastlivecarestaurants"
    m.fb_add_brand_pages([{"name": "Щастливеца", "url": brand, "scope": "global"}])
    revs = {"status": "ok", "reviews": [{"reviewer": "A", "text": "x"}]}
    assert m.fb_save_result({"name": "Щастливеца", "url": brand}, revs)["status"] == "is_brand_page"
    a = m.fb_save_result({"name": "Щастливеца Витоша", "url": brand}, revs)
    b = m.fb_save_result({"name": "Щастливеца Стара Загора", "url": brand}, revs)
    assert a["status"] == b["status"] == "no_own_page" and a["saved"] != b["saved"] and a["reviews"] == 0

    class H:
        def js(self, expr):
            return "https://www.facebook.com/somepage/reviews" if expr == "location.href" else None
    m._h = lambda: H()
    sixty = [{"reviewer": str(i)} for i in range(60)]
    m._write("cache/somepage.json", {"at": m.time.time(), "reviews": sixty, "diag": {"scroll_reason": "reached_max"}})
    r, _ = m._collect({"maxReviews": 20})
    assert len(r) == 20                                   # never more than asked
    m._collect_live = lambda opts, reset=False: ([{"reviewer": "live"}], {})
    r, _ = m._collect({"maxReviews": 100000})
    assert r == [{"reviewer": "live"}]                    # the preflight's 60 do not stand in for "all"


def test_posts_and_comments_helpers(tmp_path):
    m, _ = load_helpers(tmp_path)
    page = "https://www.facebook.com/chainpage"
    m._write("brand/chainpage.json", {"location": {"name": "Chain", "url": page}, "status": "ok", "reviews": []})
    assert [p["url"] for p in m.fb_progress()["posts_todo"]] == [page]
    opened = []
    m.fb_open = lambda url: (opened.append(url), {"ok": True, "state": "ok"})[1]
    posts = [{"permalink": page + "/posts/pfbidA", "date_full": "Сряда, 25 ноември 2026 г. в 12:19", "text": "Ново меню",
              "type": "photo", "photos": 1, "reactions": 10, "comments": 2, "shares": 1},
             {"permalink": page + "/posts/pfbidB", "date_full": "Вторник, 24 ноември 2026 г. в 10:00", "text": "Здравейте",
              "type": "text", "photos": 0, "reactions": 3, "comments": 0, "shares": 0}]

    def run(expr):
        if "scrollPosts" in expr:
            return {"count": 2, "done": True, "reason": "no_more"}
        if "collectedPosts" in expr:
            return posts
        if "pageId" in expr:
            return "123"
        if "postComments" in expr:
            return {"comments": [{"author": "Ana", "text": "Колко струва?", "depth": 1},
                                 {"author": "Chain", "text": "10 лв.", "depth": 2, "is_page_reply": True}]}
        return {}
    m._run = run
    assert m.fb_extract_posts(page, name="Chain")["posts"] == 2
    assert m.fb_progress()["posts_todo"] == [] and m.fb_progress()["comments_todo"] == 1
    r = m.fb_extract_comments()
    assert r["done"] and opened[-1] == page + "/posts/pfbidA" and m.fb_progress()["comments_todo"] == 0
    saved = m._read("posts/chainpage.json", {})["posts"]
    assert len(saved[0]["comments_list"]) == 2 and saved[1].get("comments_list") is None


def test_load_posts_and_content_insights(tmp_path):
    import asyncio as aio
    from fbreviews.insights import analyze_posts, build_insights
    run_dir = tmp_path / "chain_20261126-100000"
    ws = run_dir / "workspace"
    (ws / "brand").mkdir(parents=True)
    (ws / "posts").mkdir()
    page = "https://www.facebook.com/chainpage"
    (ws / "brand" / "chainpage.json").write_text(json.dumps({"location": {"name": "Chain", "url": page, "followers": "2 хил."},
                                                            "status": "ok", "reviews": [], "ads": [{"library_id": "1", "text": "Оферта"}]}))
    (ws / "posts" / "chainpage.json").write_text(json.dumps({"url": page, "posts": [
        {"permalink": page + "/posts/pfbidA", "date_full": "Сряда, 25 ноември 2026 г. в 12:19", "text": "Ново меню: опитайте",
         "type": "photo", "photos": 1, "reactions": 10, "comments": 2, "shares": 1,
         "comments_list": [{"author": "Ana", "text": "Колко струва?", "depth": 1},
                           {"author": "Chain", "text": "10 лв.", "depth": 2, "is_page_reply": True}]},
        {"permalink": page + "/posts/pfbidB", "date_full": "Вторник, 24 ноември 2026 г. в 10:00", "text": "-20% тази седмица",
         "type": "text", "reactions": 3, "comments": 0, "shares": 0}]}))
    brand = load_results(run_dir, "brand")
    assert len(brand[0].posts) == 2 and brand[0].ads[0].text == "Оферта"
    aio.run(analyze_posts(brand, llm=None, cache_path=None))
    assert brand[0].posts[0].topic == "product" and brand[0].posts[1].topic == "promo"
    assert brand[0].posts[0].comments_list[0].intent == "question"
    c = build_insights(brand, [], "")["content"][0]
    assert c["posts"] == 2 and c["followers"] == 2000 and c["questions_or_complaints"] == 1 and c["questions_answered"] == 1


def test_content_rounds_start_the_agent(tmp_path, monkeypatch):
    """Regression: the content phase must really call the agent (it once failed with NameError: run_agent)."""
    import asyncio as aio

    import fbreviews.agents as agents
    import fbreviews.pipeline as pl
    run_dir = tmp_path / "chain_20261126-100000"
    ws = run_dir / "workspace"
    (ws / "brand").mkdir(parents=True)
    (ws / "brand" / "chainpage.json").write_text(json.dumps(
        {"location": {"name": "Chain", "url": "https://www.facebook.com/chainpage"}, "status": "ok", "reviews": []}))
    calls = []

    async def fake_agent(*a, **k):
        calls.append(k.get("phase"))
        (ws / "posts").mkdir(exist_ok=True)
        (ws / "posts" / "chainpage.json").write_text(json.dumps({"posts": []}))

    async def no_finalize(*a, **k):
        return None
    monkeypatch.setattr(agents, "run_agent", fake_agent)
    monkeypatch.setattr(pl, "finalize", no_finalize)

    class S:
        max_rounds = 3
    aio.run(pl._content_rounds(run_dir, "Chain", S(), None, None, ws, {}, None, lambda *_: None))
    assert calls == ["content"] and pl.progress_of(run_dir)["posts_todo"] == 0


def test_unfinished_run_is_found(tmp_path):
    from fbreviews.pipeline import unfinished_run
    d = tmp_path / "divaka_20261126-150309"
    (d / "workspace" / "results").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"rounds": [{}], "brand": "Дивака"}))
    (d / "workspace" / "locations.json").write_text(json.dumps([{"url": "https://www.facebook.com/d1"}]))
    (d / "workspace" / "results" / "d1.json").write_text(json.dumps(
        {"location": {"name": "D1", "url": "https://www.facebook.com/d1"}, "status": "ok", "reviews": []}))
    (d / "report.json").write_text("{}")
    assert unfinished_run(tmp_path, "Дивака") == d            # reviews done, posts missing → finish it
    (d / "meta.json").write_text(json.dumps({"rounds": [{}], "brand": "Дивака", "posts": False}))
    assert unfinished_run(tmp_path, "Дивака") is None         # a run without posts is complete
    (d / "meta.json").write_text(json.dumps({"rounds": [{}], "brand": "Дивака"}))
    (d / "workspace" / "posts").mkdir()
    (d / "workspace" / "posts" / "d1.json").write_text(json.dumps({"posts": [{"permalink": "p", "comments": 0}]}))
    assert unfinished_run(tmp_path, "Дивака") is None         # everything collected
    assert unfinished_run(tmp_path, "Raffy") is None
