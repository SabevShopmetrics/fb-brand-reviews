import asyncio
import json
from pathlib import Path

from openpyxl import load_workbook

from fbreviews.cdp_bridge import TargetPolicy
from fbreviews.classify import classify_replies, normalize, template_clusters
from fbreviews.export import summarize, write_excel, write_json
from fbreviews.fburl import normalize_page_url, page_slug, reviews_url
from fbreviews.models import BrandReport, LocationPage, LocationResult, Reply, Review
from fbreviews.pipeline import to_reviews


def test_urls():
    assert normalize_page_url("https://m.facebook.com/ZaraSofia/reviews?ref=page") == "https://www.facebook.com/ZaraSofia"
    assert normalize_page_url("facebook.com/profile.php?id=123&sk=about") == "https://www.facebook.com/profile.php?id=123"
    assert normalize_page_url("https://www.facebook.com/search/pages?q=zara") is None
    assert normalize_page_url("https://example.com/zara") is None
    assert reviews_url("https://www.facebook.com/ZaraSofia/") == "https://www.facebook.com/ZaraSofia/reviews"
    assert reviews_url("https://www.facebook.com/profile.php?id=123") == "https://www.facebook.com/profile.php?id=123&sk=reviews"
    assert page_slug("https://www.facebook.com/profile.php?id=123") == "123"


def test_bridge_policy():
    infos = [
        {"targetId": "ui", "type": "page", "url": "file:///Applications/OpenWork.app/x/index.html", "browserContextId": "D"},
        {"targetId": "t1", "type": "page", "url": "https://example.org", "browserContextId": "B"},
        {"targetId": "fb", "type": "page", "url": "https://www.facebook.com/", "browserContextId": "B"},
    ]
    p = TargetPolicy(default_context="D")
    assert [t["targetId"] for t in p.filter_infos(infos)] == ["t1", "fb"]
    assert p.pick_lease(infos, None)["targetId"] == "fb"
    assert p.pick_lease(infos, "example")["targetId"] == "t1"
    p.lease_target, p.protected = "fb", {"t1", "fb"}
    assert not p.may_close("fb") and not p.may_close("t1") and not p.may_close("ui")
    assert p.may_close("new-tab")


def test_template_clusters():
    texts = [normalize(t, [n]) for t, n in [
        ("Thank you Maria for your kind review! We hope to see you again soon at ZARA.", "Maria"),
        ("Thank you Ivan for your kind review! We hope to see you again soon at ZARA.", "Ivan"),
        ("Thank you Petya for your kind review!! We hope to see you again soon at ZARA", "Petya"),
        ("мерси, ще проверим с колегите на касата утре", None),
    ]]
    c = template_clusters(texts)
    assert c[0] == c[1] == c[2] and c[3] != c[0]


def _report() -> BrandReport:
    loc_a = LocationPage(name="Zara Mall Sofia", url="https://www.facebook.com/zaramallsofia", city="София")
    loc_b = LocationPage(name="Zara Plovdiv", url="https://www.facebook.com/zaraplovdiv", city="Пловдив")
    tmpl = "Thank you {n} for your kind review! We truly appreciate your feedback and hope to see you again soon."
    revs_a = [Review(reviewer=n, recommends=True, text="Great store", replies=[Reply(author="Zara Mall Sofia", text=tmpl.format(n=n))])
              for n in ("Maria", "Ivan")]
    revs_a.append(Review(reviewer="Georgi", recommends=False, text="Опашката на касата беше 30 минути, само една каса работеше.",
                         replies=[Reply(author="Zara Mall Sofia", text="Георги, прав сте за касите в събота, говорих с управителя :)"),
                                  Reply(author="Some User", text="и при мен така")]))
    revs_b = [Review(reviewer="Petya", recommends=True, text="", replies=[Reply(author="ZARA Plovdiv", text=tmpl.format(n="Petya"))])]
    return BrandReport(brand="Zara", locations=[
        LocationResult(location=loc_a, status="ok", reviews=revs_a),
        LocationResult(location=loc_b, status="ok", reviews=revs_b),
    ])


def test_classify_and_export(tmp_path: Path):
    report = _report()
    method = asyncio.run(classify_replies(report.locations, "Zara", llm=None))
    assert "template" in method
    a, b = report.locations
    for rv in a.reviews[:2] + b.reviews:
        rp = rv.replies[0]
        assert rp.is_page_reply and rp.reply_type == "generated" and rp.generated_kind == "template"
    human = a.reviews[2].replies[0]
    assert human.is_page_reply and human.reply_type == "manual"
    other = a.reviews[2].replies[1]
    assert not other.is_page_reply and other.reply_type == "not_page_reply"

    report.summary = summarize(report)
    assert report.summary["page_replies"] == 4 and report.summary["generated"] == 3
    write_json(report, tmp_path / "r.json")
    data = json.loads((tmp_path / "r.json").read_text())
    assert [l["location"]["name"] for l in data["locations"]] == ["Zara Mall Sofia", "Zara Plovdiv"]
    assert data["locations"][0]["stats"]["reviews"] == 3
    write_excel(report, tmp_path / "r.xlsx")
    wb = load_workbook(tmp_path / "r.xlsx")
    assert wb.sheetnames == ["Обобщение", "Локации", "Ревюта", "Отговори"]
    assert wb["Отговори"].max_row == 1 + 5


def test_to_reviews_tolerates_agent_output():
    raw = [{"reviewer": "A", "recommends": "yes", "rating": "4", "text": "ok",
            "replies": [{"author": "P", "text": None, "depth": None}, "junk"]}]
    r = to_reviews(raw)[0]
    assert r.recommends is None and r.rating == 4 and r.replies[0].text == "" and r.replies[0].depth == 1


def test_html_report(tmp_path):
    from fbreviews.html_report import write_html
    report = _report()
    asyncio.run(classify_replies(report.locations, "Zara", llm=None))
    report.summary = summarize(report)
    write_json(report, tmp_path / "r.json")
    write_html(json.loads((tmp_path / "r.json").read_text()), tmp_path / "r.html")
    html = (tmp_path / "r.html").read_text()
    assert "Zara Mall Sofia" in html and "<script id=\"data\" type=\"application/json\">" in html
    assert "</script>" not in html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
