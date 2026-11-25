---
name: facebook-brand-reviews
description: Collect Facebook reviews (recommendations) for every location of a brand or chain, split by location, into one report (JSON + Excel). Use when the user asks for Facebook reviews / отзиви / ревюта / препоръки of a brand, store, chain or its locations (e.g. "извлечи ревютата на Zara от Facebook", "Facebook reviews for all Happy Bar & Grill locations").
---

# Facebook ревюта по локации на бранд

A ready tool does the work: one browser-use agent (on our own model) finds the brand's location Pages on Facebook and
collects each location's reviews through **this OpenWork's built-in browser**. You only start it, follow it and
report the result. Do not scrape Facebook yourself and do not edit the tool's code.

Tool folder (TOOL_DIR): `__TOOL_DIR__`

## Before starting
1. (Full mode only.) The built-in browser must have a **facebook.com** tab, logged in. If there is none, open
   `https://www.facebook.com/` in the built-in browser and ask the user to log in there. Never type credentials,
   never log in for them.
2. If `TOOL_DIR/.env` is missing or `scripts/run_bg.sh` answers `NEEDS_ENV`, ask the user to fill
   `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `TOOL_DIR/.env` (open it for them). Never print the key.
3. Ask only what is missing: the brand (required). Defaults: 50 locations, 200 reviews per location. For a quick
   test suggest 2 locations / 20 reviews.

## Run
```bash
cd "__TOOL_DIR__" && scripts/run_bg.sh "<brand>" <max_locations> <max_reviews>
```
**Lite mode (no Facebook profile needed):** add `lite` as the 4th argument
(`scripts/run_bg.sh "<brand>" <max_locations> 0 lite`). It runs in this OpenWork's built-in browser, so the user can
watch it, and collects only each location's public data: category, address, phone, website, % recommend and number
of reviews — no review texts, no replies. It works whether or not that browser is logged in to Facebook; for a test
that is truly without a profile, the user logs out of Facebook in the built-in browser themselves (never do it for
them). `lite-hidden` instead of `lite` uses a hidden fresh browser with no account (nothing to watch).
Use lite when the user has no Facebook profile to use, says "без профил / лек режим", or wants ratings/totals only.
It returns at once (`STARTED pid=… log=…`; `ALREADY_RUNNING` means a run is in progress — just follow it).
The first run installs itself (2–3 minutes).

**While a run is going (full or lite, not lite-hidden), do not use the built-in browser** (no browser tools at all): the tool drives the Facebook tab and
any click or navigation of yours breaks its run. Tell the user the same: don't touch the Facebook tab.

## Follow
```bash
cd "__TOOL_DIR__" && scripts/status.sh
```
Shows RUNNING / FINISHED, the key log lines (`Рунд N`, `→ локации X, готови Y`) and, when done, the report paths.
Check it when the user asks, or about every 3–5 minutes while you wait. It is slow on purpose (6–14 s between
pages, daily page cap) to keep the Facebook account safe: a few minutes per location.

## When it is finished
- Read `report.json` (path from `status.sh`). Reply with: number of locations and their statuses, total reviews,
  % recommend / not recommend, reviews with comments and comments counted, and a short table per location
  (name, city, reviews, recommend %, comments). Give the Excel path (`report.xlsx`) for the details.
- The brand's own Pages (e.g. "Lidl България") are a separate section: `brand_sections` in report.json, sheet
  "Бранд" in Excel. Report them first and apart ("Бранд: 71% препоръка, 1200 ревюта"), then the locations; never
  count a brand Page as a location.
- Facebook's own total per location is `review_total` in each location of `report.json` (also in its `stats`).
  Take numbers only from report.json, location by location; never quote totals from memory or from another location.
- Replies: under many reviews Facebook shows the Page's own reply as a preview; the tool expands it ("Вижте
  повече") and saves the full text, then classifies each Page reply as generated (template / AI) or manual. Full
  threads of other people's comments are not shown by Facebook, so for those only `comment_count` is saved.
  Mention how many Page replies there are and how many look generated vs manual.

## If it stopped
- `Спрях заради Facebook` / `login_required` / `blocked`: Facebook asked for a login or a security check. Do not
  try to get around it. Ask the user to check the account in the built-in browser; then continue with
  `scripts/run_bg.sh --resume`.
- `cap_reached` (daily page cap): continue tomorrow with `scripts/run_bg.sh --resume`.
- `✗ моделът не отговаря`: the model server is down; retry later (`--resume`).
- Stop on request: `scripts/stop.sh` (progress is kept; `--resume` continues).
