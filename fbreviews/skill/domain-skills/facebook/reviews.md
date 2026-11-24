# Facebook: reviews (and their comment counts) of every location of a brand

You work in the user's own, already logged-in Facebook session. This skill gives you ready Python helpers (already
loaded as globals in your REPL) and the playbook. Use the helpers: they handle pacing, lazy loading, saving and
safety. Write your own code only when a helper clearly fails on a page.

**Read `preflight.md` (same folder) first.** It defines the exact output we want and how to check that the extractor
still reads Facebook correctly (`fb_preflight`). It also says how to repair the extractor yourself when Facebook
changes its UI (`fb_save_override`).

## Hard rules
- Read only: never like, react, comment, post, message, follow, join or change settings. Never type in a comment box.
- Never log in or out, never type credentials, never try to solve or get around a CAPTCHA, checkpoint or security
  check. If `fb_stopped()` returns text, or any helper returns status `login_required` / `blocked` / `stopped`,
  STOP the whole run at once and finish with that reason in notes. Do not retry.
- Status `cap_reached` (daily page cap): stop and finish; the run continues another day.
- Only open pages with `fb_open(url)` (or the helpers that use it), never `goto_url` directly: it keeps the pace
  that keeps the account safe. One tab only.
- `fb_extract_reviews` saves each location itself. Use `fb_save_result(...)` only for a location you handled by hand.

## Helpers
| Helper | What it does |
|---|---|
| `fb_progress()` | `{locations, done, todo, stopped, preflight: {healthy, due, failed, unhealthy_streak}, override_active}` — call FIRST: the run may be a continuation |
| `fb_preflight(sample_url=None, search_query=None, reload=True)` | health report of the extractor on one reviews page (and search) → `{healthy, failed, warnings, reviews, search, next}` — see preflight.md |
| `fb_save_override(js_code, note)` / `fb_override()` / `fb_clear_override(note)` | repair the extractor (JS run after fb_extract.js), show it, remove it |
| `fb_check_reviews(reviews, diagnostics)` | the same health checks on any list of reviews (no page load) |
| `fb_search_pages(query, kind='pages'\|'places', scrolls=6)` | search results `[{name, url, snippet}]`; snippet = category, address, followers, rating |
| `fb_open(url)` | paced navigation + safety check → `{ok, state}` |
| `fb_add_locations([{name, url, address, city, country, category, rating_text, source}])` | merge into locations.json (dedupe, cap) |
| `fb_locations()` | the saved list |
| `fb_extract_reviews(url, name=...)` | reviews tab → all reviews + comment counts → `{status, reviews_url, reviews, diagnostics, notes, health}` |
| `fb_save_result(location, result, notes='')` | write results/<slug>.json |
| `fb_js(expr)` | run the extractor JS (+ your override) + `expr` in the page (`__fbx.extract({})`, `__fbx.posts().length`, `__fbx.pageState()`) |
| `fb_page_url(url)`, `fb_reviews_url(url)` | URL helpers |

## Phase 1 — find the locations (skip if `fb_progress()['locations']` already reached the cap or the task says so)
1. `fb_search_pages(brand)` → find the brand's official global / country Pages (verified, huge follower counts).
   Remember their URLs as brand pages (do NOT add them as locations).
2. On those Pages look for a locations / store list: `fb_open(page + '/locations')`, the Intro / About section,
   "See all" store links. Read child location Pages from the DOM with `js()`.
3. Search widely, country by country and city by city: `fb_search_pages(f"{brand} {city}")`,
   `fb_search_pages(f"{brand} store")`, `fb_search_pages(f"{brand} {mall}")`, also with `kind='places'`.
4. Keep only real physical locations of THIS brand: name contains the brand, address / city in the snippet, a local
   business category (Clothing store, Restaurant, Shopping & retail, Local business...). Drop fan pages, jobs,
   groups, resellers, look-alike businesses, duplicates.
5. Parse the snippet into address / city / country / category / rating_text and call `fb_add_locations(batch)`
   after every search. Stop when the cap is reached or new searches stop adding locations.

## Before Phase 2 — preflight
If `fb_progress()['preflight']['due']` is true, run `fb_preflight()` (it uses the first saved locations). If it is not
`healthy`, repair as described in preflight.md before you extract any location. Do not loosen the checks.

## Phase 2 — every location
```python
suspect = 0
for loc in fb_progress()['todo']:
    res = fb_extract_reviews(loc['url'], name=loc['name'], location=loc)   # also SAVES the result
    print(loc['name'], res['status'], len(res['reviews']), res.get('notes'))
    if res['status'] in ('login_required', 'blocked', 'stopped', 'cap_reached'):
        break                      # finish the run with this reason
    if res['status'] == 'suspect':  # health check failed: NOT saved
        suspect += 1
        if suspect >= 2:
            break                  # the extractor is broken: fb_preflight(reload=False) + repair (preflight.md)
        continue
    suspect = 0
    if res['status'] == 'unknown':
        ...                        # not saved: look at the page (screenshot / js), decide, then fb_save_result
```
Run this loop in ONE cell for many locations at once; it paces itself. Investigate only `unknown` and `suspect` results.
After a repair, run the loop again: `suspect` locations were not saved, so they are still in `todo`. If one
location stays `suspect` while preflight is healthy (an unusual page), save it as it is:
`fb_save_result(loc, {**res, 'status': res['fallback_status']}, notes='health: ' + ', '.join(res['health']['failed']))`.
Accepted outcomes, move on without investigating: `ok`, `partial`, `no_reviews`, `reviews_disabled`, `not_found`.
Replies: under many reviews Facebook shows the Page's own reply as a PREVIEW in the list; the helper expands it
("Вижте повече") and keeps the full text, with a second slow pass over the loaded list for late previews. Full comment
threads (opening a review) do not load, so for other comments only `comment_count` is kept and the review carries
`replies_unavailable`. Do NOT open review permalinks to read threads; it wastes page loads and cannot work. Spend at most ~5 steps on any
single location; never re-run a saved location.
- `unknown`: the reviews tab did not give a clear answer. Take a screenshot. If the Page has a "Reviews" /
  "Отзиви" tab under another URL or under "More"/"Още", open it with `fb_open` and run the extractor yourself:
  `fb_js("__fbx.scroll({})")` (repeat until `done`), then `fb_js("__fbx.extract({})")`; then save. If the Page
  simply has no reviews, save with status `no_reviews`; if reviews are switched off, `reviews_disabled`.
- Spot-check only the FIRST location against the page (verdicts, texts): if reviews themselves are clearly wrong,
  say so in notes. Missing replies are a Facebook limitation, not an extractor bug.

## Finish
Return the summary: locations found, locations done, whether everything is done (`complete`), the brand pages,
and notes (blocked / cap / anything odd). If you repaired the extractor, say so in notes: what changed and which
override version is in use (`fb_override()['log']`). If the repair failed, use stop_reason `extractor_broken`.
