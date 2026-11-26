"""report.html / index.html — self-contained, interactive views of report.json (offline, no external libraries).

* report.html (one run): everything in report.json — brand Pages, every location, every review with its text,
  verdict, exact date, tags, reactions, photos, comment count and the replies (generated / manual), plus statistics
  and charts computed in the browser from the same data.
* output/index.html (dashboard): the same view for every brand extracted so far, one tab per brand (its latest run
  with data), plus an overview tab comparing the brands. Rebuilt after every run (and every interim round).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

TEMPLATE = (Path(__file__).with_name("report_template.html")).read_text(encoding="utf-8")


def _render(datasets: list[dict], mode: str, title: str) -> str:
    blob = json.dumps({"mode": mode, "datasets": datasets}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", title.replace("<", "&lt;")).replace("__DATA__", blob)


def write_html(data: dict, path: Path) -> None:
    """report.html for one run. data = the report.json content (BrandReport.model_dump + stats)."""
    html = _render([{"key": "report", "data": data, "run": path.parent.name}], "single",
                   f"{data.get('brand', '')} — Facebook ревюта")
    path.write_text(html, encoding="utf-8")


_RUN = re.compile(r"^(?P<slug>.+)_(?P<ts>\d{8}-\d{6})$")


def _count(data: dict) -> tuple[int, int]:
    pages = (data.get("locations") or []) + (data.get("brand_sections") or [])
    return sum(len(p.get("reviews") or []) for p in pages), len(pages)


def collect_runs(out_dir: Path) -> list[dict]:
    """One dataset per brand: its latest run with reviews (else the latest with any Page), newest brand first."""
    groups: dict[str, list[tuple[str, Path, dict, int, int]]] = {}
    for f in sorted(Path(out_dir).glob("*/report.json")):
        m = _RUN.match(f.parent.name)
        if not m:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a report being written right now: picked up next time
        reviews, pages = _count(data)
        groups.setdefault(m["slug"], []).append((m["ts"], f.parent, data, reviews, pages))
    sets = []
    for key, runs in groups.items():
        runs.sort(key=lambda r: r[0], reverse=True)
        pick = next((r for r in runs if r[3]), None) or next((r for r in runs if r[4]), None)
        if not pick:
            continue
        sets.append({
            "key": key, "run": pick[1].name, "data": pick[2],
            "runs": [{"run": r[1].name, "reviews": r[3],
                      "href": f"{r[1].name}/report.html" if (r[1] / "report.html").exists() else None}
                     for r in runs if r[3] or r[4]],
        })
    sets.sort(key=lambda s: s["data"].get("generated_at") or "", reverse=True)
    return sets


def write_index(out_dir: Path, path: Path | None = None) -> Path:
    """output/index.html: every brand extracted so far, one tab each, plus an overview tab."""
    path = path or Path(out_dir) / "index.html"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_render(collect_runs(out_dir), "index", "Facebook ревюта — всички брандове"), encoding="utf-8")
    tmp.replace(path)  # atomic: a browser reloading it never sees half a file
    return path


if __name__ == "__main__":  # python -m fbreviews.html_report [output]
    import sys

    print(write_index(Path(sys.argv[1] if len(sys.argv) > 1 else "output")))
