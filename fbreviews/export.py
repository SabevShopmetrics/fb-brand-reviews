"""Write the brand report: one JSON (grouped by location) + an Excel workbook."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import BrandReport

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def summarize(report: BrandReport) -> dict:
    total: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for loc in report.locations:
        statuses[loc.status] = statuses.get(loc.status, 0) + 1
        for k, v in loc.stats().items():
            total[k] = total.get(k, 0) + v
    return {"locations": len(report.locations), "by_status": statuses, **total}


def write_json(report: BrandReport, path: Path) -> None:
    data = report.model_dump(mode="json")
    for loc, res in zip(data["locations"], report.locations):
        loc["stats"] = res.stats()
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

    rows = []
    for loc in report.locations:
        st = loc.stats()
        l = loc.location
        rows.append([l.name, l.city, l.country, l.address, l.url, loc.status, st["reviews"], st["recommends"],
                     st["not_recommends"], st["reviews_with_comments"], st["comments_counted"],
                     st["reviews_with_page_reply"], st["page_replies"], st["generated"],
                     st["manual"], st["uncertain"], l.rating_text, loc.notes])
    _sheet(wb, "Локации",
           ["Локация", "Град", "Държава", "Адрес", "Facebook", "Статус", "Ревюта", "Препоръчват", "Не препоръчват",
            "Ревюта с коментари", "Коментари (брой по FB)",
            "Ревюта с отговор от страницата", "Отговори от страницата", "Генерирани", "Ръчни", "Неясни", "Рейтинг",
            "Бележки"],
           rows, [32, 14, 12, 30, 40, 14, 9, 11, 13, 12, 12, 14, 12, 11, 8, 8, 16, 40])

    rows = []
    for loc in report.locations:
        for i, rv in enumerate(loc.reviews, 1):
            page = [r for r in rv.replies if r.is_page_reply]
            first = page[0] if page else None
            rows.append([loc.location.name, i, rv.reviewer, yn[rv.recommends], rv.rating, rv.text, rv.date_text,
                         rv.permalink, rv.comment_count, len(rv.replies),
                         "да" if rv.replies_unavailable else "", first.text if first else "",
                         first.reply_type if first else "", first.confidence if first else None])
    _sheet(wb, "Ревюта",
           ["Локация", "№", "Автор", "Препоръчва", "Оценка", "Текст", "Дата", "Линк", "Коментари (по FB)",
            "Извлечени отговори", "Коментарите са скрити от Facebook", "Отговор от страницата", "Тип отговор", "Увереност"],
           rows, [28, 5, 22, 11, 8, 60, 12, 40, 10, 10, 12, 60, 13, 10])

    rows = []
    for loc in report.locations:
        for i, rv in enumerate(loc.reviews, 1):
            for rp in rv.replies:
                rows.append([loc.location.name, i, rv.reviewer, (rv.text or "")[:300], rp.author,
                             yn[rp.is_page_reply], rp.depth, rp.text, rp.date_text, rp.reply_type, rp.generated_kind,
                             rp.confidence, "; ".join(rp.signals), rp.llm_reason])
    _sheet(wb, "Отговори",
           ["Локация", "Ревю №", "Автор на ревюто", "Ревю (откъс)", "Автор на отговора", "От страницата", "Ниво",
            "Отговор", "Дата", "Тип", "Вид генериране", "Увереност", "Сигнали", "Обяснение от модела"],
           rows, [28, 7, 20, 45, 22, 11, 6, 60, 12, 13, 13, 10, 45, 40])

    s = report.summary
    _sheet(wb, "Обобщение", ["Показател", "Стойност"],
           [["Бранд", report.brand], ["Генерирано", report.generated_at], ["Модел", report.model],
            ["Метод за класификация", report.classification_method]]
           + [[k, str(v)] for k, v in s.items()],
           [34, 70])
    wb.move_sheet("Обобщение", offset=-3)
    wb.save(path)
