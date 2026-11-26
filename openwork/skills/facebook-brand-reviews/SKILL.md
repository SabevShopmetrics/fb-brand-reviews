---
name: facebook-brand-reviews
description: Full Facebook picture of a brand or chain — every location's reviews with the Page's replies, the posts, the comments under them, active ads, and business conclusions — into one report and the all-brands dashboard. Use when the user's message is just a brand / store / chain name (e.g. "Дивака", "Zara", "Лидл") or asks for anything about a brand on Facebook (ревюта, отзиви, препоръки, постове, коментари, реклами, локации).
---

# Facebook ревюта по локации на бранд

A ready tool does the work: one browser-use agent (on our own model) finds the brand's location Pages on Facebook and
collects each location's reviews through **this OpenWork's built-in browser**. You only start it, follow it and
report the result. Do not scrape Facebook yourself and do not edit the tool's code.

Tool folder (TOOL_DIR): `__TOOL_DIR__`

## Free-form requests — the user writes whatever they want
The user writes in their own words (Bulgarian or English, any length). Work out what they want and do it; ask a
question only when the brand is missing or the request is truly ambiguous. A brand name alone ("Дивака", "Zara")
means: start the full run now with every default and follow it to the end. ONE message is always enough: never ask
the user to write "продължи" — `run_bg.sh "<brand>"` finishes an unfinished recent run of that brand by itself
(it prints `RESUMING <folder>`: say "довършвам започнатото на <date>") and starts a new one otherwise.

**Defaults** (never add limits on your own): all locations, all reviews with the Page's replies, the brand's own
Pages, every Page's posts and the comments under them, active ads, the whole world, then the business conclusions.

**First decide: new data, or an answer from data we already have?**
`cd "__TOOL_DIR__" && .venv/bin/python -m fbreviews latest "<brand>"` lists the latest run with data for that brand
(folder, report.json, report.html, date, numbers); `latest` with no brand lists every brand extracted so far.
- A question about a brand that is already there ("какво пишат за обслужването в Дивака?", "коя локация на Raffy е
  най-зле?", "сравни Raffy и Дивака", "колко често постват?", "какви реклами пуска Лидл?") → answer from its
  `report.json` (numbers only from there; quote reviews and comments word for word), say the date of the data, and
  offer a fresh run only if the data is old or the user asks for "нови" / "обнови" / "пусни пак".
- Asks for data on a brand, a brand that is not there yet, or anything is missing → `scripts/run_bg.sh "<brand>"`
  (it completes what is missing). Only "обнови", "нови данни", "пусни наново от нулата" → a brand-new run:
  `FRESH=1 scripts/run_bg.sh "<brand>"`.
- "покажи / отвори" → open the report (`report.html` of the latest run) or the dashboard (`output/index.html`).
- "докъде е / какво прави" → `scripts/status.sh` and report; "спри" → `scripts/stop.sh`; "продължи" →
  `scripts/run_bg.sh --resume`.

**Turning the request into a run** — `scripts/run_bg.sh "<brand>" <max_loc> <max_rev> <mode> <country> <posts> "<hint>"`
| The user says (examples) | Argument |
|---|---|
| a country or "само в България", "в Румъния" | 5th: `"Bulgaria"` (English name); the whole world = `all` |
| "само ревюта", "без постове", "бързо, без коментари" | 6th: `noposts` (otherwise `all`) |
| "бърз тест", "само 2 локации", "по 20 ревюта" | 2nd / 3rd: the numbers (0 = all) |
| "невидимо", "без профил" | 4th: `lite-hidden` (otherwise `auto`) |
| anything else about WHAT or WHERE to collect: "страницата им е facebook.com/divaka.bg", "само ресторантите в моловете", "игнорирай франчайзите", "търси и като Divaka" | 7th: the user's words as a hint, passed to the tool's agent as they are |

Examples:
- "Дивака" → `scripts/run_bg.sh "Дивака"`
- "Извади ревютата на Лидл само за България, без постовете" → `scripts/run_bg.sh "Лидл" 0 0 auto "Bulgaria" noposts`
- "Raffy, официалната им страница е facebook.com/raffybargelato, искам и коментарите" →
  `scripts/run_bg.sh "Raffy Bar & Gelato" 0 0 auto all all "официалната страница е facebook.com/raffybargelato"`
- "Колко от ревютата на Дивака са негативни?" → `fbreviews latest "Дивака"` and answer from its report.json.

What the tool cannot do, say so plainly: it only reads Facebook (no posting, liking or replying), never gets around a
login or a CAPTCHA, and Facebook does not show the text of other people's comments under reviews (only their count).

## Logged in or not — automatic (never choose the mode yourself)
The tool decides by itself: logged in to Facebook in the built-in browser → FULL mode (all reviews, the Page's
replies); not logged in → lite mode (public data only). Do NOT pass `lite` on your own — even if you think the user
is logged out; the tool checks the real login. Without a login Facebook shows a visitor dialog like "Вижте повече от …
— влезте"; that is normal. A remembered account screen ("Продължаване") is NOT a login.

## Country
Default: the WHOLE WORLD — the brand's global Page, its official Page per country, and locations in every country;
the report splits everything by country (report.html: "По държави" table and a country filter). Only if the user
names one country, pass it as the 5th argument to limit the run: `scripts/run_bg.sh "<brand>" 0 0 auto "Bulgaria"`.
When you report the result, give the per-country summary first.

## Before starting
1. The tool drives a tab of OpenWork's built-in browser, but it cannot open one itself. If the built-in browser is
   not open or has no tab, OPEN IT YOURSELF with your browser tool at `https://www.facebook.com/` — do not ask the
   user. The browser keeps its Facebook login between sessions, so a user who logged in once is still logged in:
   just start. Only if the page shows a login form (not a remembered-account screen the tool can pass), ask the user
   to log in there; never type credentials, never log in for them. Then leave the tab alone: the tool navigates it.
2. If `TOOL_DIR/.env` is missing or `scripts/run_bg.sh` answers `NEEDS_ENV`, ask the user to fill
   `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `TOOL_DIR/.env` (open it for them). Never print the key.
3. Ask only for the brand if it is missing. By default the tool takes EVERYTHING (see "Free-form requests").
   Never add limits on your own; use numbers only if the user explicitly asks for a quick test.

## Run
```bash
cd "__TOOL_DIR__" && scripts/run_bg.sh "<brand>"
```
(= everything. Only for an explicit quick test: `scripts/run_bg.sh "<brand>" <max_locations> <max_reviews>`.)
`lite-hidden` as the 4th argument runs a hidden fresh browser with no account (only if the user explicitly asks for
an invisible run).
It returns at once (`STARTED pid=… log=…`; `ALREADY_RUNNING` means a run is in progress — just follow it).
The first run installs itself (2–3 minutes).

**While a run is going (full or lite, not lite-hidden), do not use the built-in browser** (no browser tools at all): the tool drives the Facebook tab and
any click or navigation of yours breaks its run. Tell the user the same: don't touch the Facebook tab.

## Follow — keep the user informed until there is a result
```bash
cd "__TOOL_DIR__" && scripts/status.sh
```
It prints RUNNING / FINISHED and "какво прави": the tool's own step-by-step lines (`• Търся във Facebook…`,
`• Локация 2/4: … — отварям ревютата`, `• зареждам ревюта: 45/120`, `• ✓ Записах локацията …`).

- Check it every 60 seconds (`sleep 60; scripts/status.sh | tail -15`) and after EACH check write the user one short update in
  plain Bulgarian from those lines: what is done, where it is now, what comes next. Example:
  "Намерих бранда (Дивака) и 3 локации. Сега съм на локация 2/3 (Гладстон): заредени 40 от 91 ревюта. Следва 6-ти септември."
- Keep checking until FINISHED. Do not end your turn while it is RUNNING unless the user asks you to stop following.
- Never stop, restart or start a second run yourself; only one run at a time (`ALREADY_RUNNING` = follow it). If the
  user has another session running the tool, tell them instead of starting your own.
- It is slow on purpose (4–10 s between pages) to keep the account safe; a large brand can take hours. That is normal.
- The tool itself writes `report.html` (+ `report.xlsx`, `report.json`) from its own output: after every round an
  interim one ("междинен отчет: …" in the log), and the final one at the end. Never build or edit a report yourself;
  only open the file the tool made. On a long run, offer the interim report (`open "<path>"`).
- The tool also keeps a dashboard of EVERY brand extracted so far: `output/index.html` (one tab per brand, its latest
  run with data, plus an "Всички" overview comparing the brands). It is rebuilt after every round and every run.
  When the user asks for "all brands", "the dashboard", "табове" or to compare brands, open it:
  `open "<project>/output/index.html"` (path from `status.sh`, line "табло:"). To rebuild it without a run:
  `.venv/bin/python -m fbreviews dashboard`.

## When it is finished
- Say clearly that it is finished, then OPEN the interactive report for the user: `open "<run folder>/report.html"`
  (statistics, charts, brand Pages, locations comparison, every review with text, tags, reactions, photos, replies;
  filters, search, CSV/JSON download). Also give the paths of `report.xlsx` (Excel) and `report.json` (all data),
  and mention the all-brands dashboard `output/index.html` (tabs for every brand extracted so far).
- In full mode a run also collects every Page's posts, the comments under them and its active ads (after the reviews;
  the log says "Съдържание, рунд N"). That part is slow too: one page per post with comments. Report it as well:
  `insights.business` (headline, findings, actions — say these FIRST), `insights.content` per Page (posts per week,
  topics and types that work, comment intents, questions answered, ads).
- `report.json` also has `insights`: per Page the tone counts, `top_positive` / `top_negative` topics, reply rates,
  negative reviews without an answer, and `signals` (a location getting worse, a topic that spikes). Say the signals
  first if there are any, and for each location what customers praise and what they complain about.
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
