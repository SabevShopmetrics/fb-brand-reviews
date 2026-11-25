"""Write the brand report: one JSON (grouped by location) + an Excel workbook."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import BrandReport

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
STATUS_BG = {"ok": "готово", "partial": "частично", "summary_only": "само публични данни", "no_reviews": "няма ревюта",
             "reviews_disabled": "ревютата са изключени", "not_found": "не е намерена", "error": "грешка",
             "login_required": "иска вход", "blocked": "блокирано"}
SUMMARY_BG = [
    ("locations", "Локации"), ("by_status", "Статуси на локациите"),
    ("facebook_review_total", "Ревюта във Facebook (общо, локации)"), ("reviews", "Извлечени ревюта"),
    ("recommends", "Препоръчват"), ("not_recommends", "Не препоръчват"), ("recommend_share", "Препоръчват (%)"),
    ("reviews_with_page_reply", "Ревюта с отговор от страницата"), ("page_replies", "Отговори от страницата"),
    ("generated", "  от тях генерирани (шаблон / AI)"), ("manual", "  от тях ръчни"), ("uncertain", "  от тях неясни"),
    ("other_comments", "Други коментари"), ("reviews_with_comments", "Ревюта с коментари"),
    ("comments_counted", "Коментари (брой по Facebook)"),
    ("reviews_with_hidden_comments", "Ревюта с коментари, които Facebook не показва"),
]
HEADER_FONT = Font(color="FFFFFF", bold=True)


def summarize(report: BrandReport) -> dict:
    total: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for loc in report.locations:
        statuses[loc.status] = statuses.get(loc.status, 0) + 1
        for k, v in loc.stats().items():
            if k == "facebook_recommend_pct":
                continue
            total[k] = total.get(k, 0) + (v or 0)
    out = {"locations": len(report.locations), "by_status": statuses, **total}
    if report.brand_sections:
        out["brand_pages"] = [
            {"name": b.location.name, "url": b.location.url, "status": b.status, "recommend_pct": b.recommend_pct,
             "review_total": b.review_total, "reviews_extracted": len(b.reviews),
             "page_replies": b.stats()["page_replies"]}
            for b in report.brand_sections
        ]
    return out


def write_json(report: BrandReport, path: Path) -> None:
    data = report.model_dump(mode="json")
    for loc, res in zip(data["locations"], report.locations):
        loc["stats"] = res.stats()
    for b, res in zip(data.get("brand_sections", []), report.brand_sections):
        b["stats"] = res.stats()
    import json

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], widths: list[int]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=isinstance(c.value, str) and len(c.value) > 40, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def write_excel(report: BrandReport, path: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    yn = {True: "да", False: "не", None: ""}

    def page_rows(items):
        out = []
        for loc in items:
            st = loc.stats()
            l = loc.location
            out.append([l.name, l.country, l.category, l.city, l.address, l.phone, l.website, l.email, l.followers, l.url,
                        STATUS_BG.get(loc.status, loc.status), loc.recommend_pct, loc.review_total, st["reviews"],
                        st["recommends"], st["not_recommends"], st["reviews_with_comments"], st["comments_counted"],
                        st["reviews_with_page_reply"], st["page_replies"], st["generated"], st["manual"], st["uncertain"],
                        loc.notes])
        return out

    PAGE_HEADERS = ["Страница", "Държава", "Категория", "Град", "Адрес", "Телефон", "Сайт", "Имейл", "Последователи", "Facebook",
                    "Статус", "% препоръка (FB)", "Ревюта във FB (общо)", "Извлечени ревюта", "Препоръчват",
                    "Не препоръчват", "Ревюта с коментари", "Коментари (брой по FB)", "Ревюта с отговор от страницата",
                    "Отговори от страницата", "Генерирани", "Ръчни", "Неясни", "Бележки"]
    PAGE_WIDTHS = [36, 14, 18, 12, 30, 14, 18, 22, 12, 40, 16, 11, 12, 11, 11, 11, 12, 12, 14, 12, 11, 8, 8, 40]
    if report.brand_sections:
        _sheet(wb, "Бранд", PAGE_HEADERS, page_rows(report.brand_sections), PAGE_WIDTHS)

    def label(loc):
        return f"[Бранд] {loc.location.name}" if loc.is_brand else loc.location.name

    every = list(report.brand_sections) + list(report.locations)

    _sheet(wb, "Локации", PAGE_HEADERS, page_rows(report.locations), PAGE_WIDTHS)

    rows = []
    for loc in every:
        for i, rv in enumerate(loc.reviews, 1):
            page = [r for r in rv.replies if r.is_page_reply]
            first = page[0] if page else None
            rows.append([label(loc), i, rv.reviewer, yn[rv.recommends], rv.rating, rv.text, rv.date_text,
                         rv.permalink, rv.comment_count, len(rv.replies),
                         "да" if rv.replies_unavailable else "", first.text if first else "",
                         first.reply_type if first else "", first.confidence if first else None])
    _sheet(wb, "Ревюта",
           ["Страница", "№", "Автор", "Препоръчва", "Оценка", "Текст", "Дата", "Линк", "Коментари (по FB)",
            "Извлечени отговори", "Коментарите са скрити от Facebook", "Отговор от страницата", "Тип отговор", "Увереност"],
           rows, [28, 5, 22, 11, 8, 60, 12, 40, 10, 10, 12, 60, 13, 10])

    rows = []
    for loc in every:
        for i, rv in enumerate(loc.reviews, 1):
            for rp in rv.replies:
                rows.append([label(loc), i, rv.reviewer, (rv.text or "")[:300], rp.author,
                             yn[rp.is_page_reply], rp.depth, rp.text, rp.date_text, rp.reply_type, rp.generated_kind,
                             rp.confidence, "; ".join(rp.signals), rp.llm_reason])
    _sheet(wb, "Отговори",
           ["Страница", "Ревю №", "Автор на ревюто", "Ревю (откъс)", "Автор на отговора", "От страницата", "Ниво",
            "Отговор", "Дата", "Тип", "Вид генериране", "Увереност", "Сигнали", "Обяснение от модела"],
           rows, [28, 7, 20, 45, 22, 11, 6, 60, 12, 13, 13, 10, 45, 40])

    s = dict(report.summary)
    rec, nrec = s.get("recommends") or 0, s.get("not_recommends") or 0
    s["recommend_share"] = f"{round(100 * rec / (rec + nrec))}%" if rec + nrec else "—"
    s["by_status"] = ", ".join(f"{STATUS_BG.get(k, k)}: {v}" for k, v in (s.get("by_status") or {}).items()) or "—"
    method = report.classification_method or ""
    if method.startswith("no page replies"):
        method = "няма отговори от страницата за класифициране"
    else:
        method = method.replace("template clusters", "шаблонни клъстери").replace("style heuristics", "стилови сигнали") \
                       .replace("model judge", "модел")
    try:
        from datetime import datetime
        generated = datetime.fromisoformat(report.generated_at).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        generated = report.generated_at
    rows = [["Бранд", report.brand], ["Генерирано", generated], ["Модел", report.model or "—"],
            ["Класификация на отговорите", method]]
    for b in s.get("brand_pages", []):
        bits = [f"{b['recommend_pct']}% препоръка, {b['review_total']} ревюта" if b.get("recommend_pct") is not None
                else "без препоръки/ревюта на страницата"]
        if b.get("reviews_extracted"):
            bits.append(f"{b['reviews_extracted']} извлечени, {b['page_replies']} отговора от страницата")
        rows.append([f"Страница на бранда: {b['name']}", "; ".join(bits)])
    for key, label in SUMMARY_BG:
        if key in s:
            v = s[key]
            rows.append([label, "—" if v is None else v])
    _sheet(wb, "Обобщение", ["Показател", "Стойност"], rows, [44, 80])
    wb.move_sheet("Обобщение", offset=-(len(wb.sheetnames) - 1))
    wb.save(path)
