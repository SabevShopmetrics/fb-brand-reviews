# Facebook: preflight and self-repair (read this FIRST, before reviews.md)

Facebook changes its UI (DOM, labels, layout) without notice. The extractor (`fb_extract.js`, `window.__fbx`) was
written for the UI as it was. When the UI changes, the extractor may not fail. It may quietly return wrong data:
0 reviews, reviews without text, every verdict `null`, the header line copied into the text. Your job is to notice
this before collecting, repair the extractor, and never save data you know is wrong.

## 1. The output we want (the contract)

One result per location (`results/<slug>.json`, written by `fb_extract_reviews` / `fb_save_result`):

| Field | Meaning |
|---|---|
| `status` | `ok` · `partial` · `no_reviews` (the page has none) · `reviews_disabled` · `not_found` (+ stop states, never saved) |
| `reviews_url` | the location's reviews tab URL |
| `reviews` | **every** review on the tab (up to `max_reviews`), in page order, each one as below |

Each review:

| Field | Type | Meaning |
|---|---|---|
| `reviewer` | str | the reviewer's display name (the link in the header line) |
| `recommends` | true / false / null | header says "X recommends / препоръчва Page" → true; "doesn't recommend / не препоръчва" → false; null only if truly not shown |
| `rating` | int 1–5 / null | old-style star rating if shown, else null (most pages: null) |
| `text` | str | the review text the reviewer wrote. Not the header line and not buttons ("Like", "Харесвам", "See translation") |
| `date_text` | str / null | the date link text exactly as shown ("2 г.", "12 March 2024") |
| `permalink` | str | the review's own link (`/posts/pfbid…`, `story_fbid=…`, `/permalink/…`), cleaned of tracking params |
| `comment_count` | int / null | the "N comments / коментара" count under the review; null when no count is shown (0 comments) |
| `replies` | list | the replies Facebook shows inline under the review, usually the Page's own reply: `{author, text, truncated, date_text, permalink, depth, author_badge}`. `text` has no "от автора / Author" badge and no "… Вижте повече / See more" |
| `replies_unavailable` | bool | set by the helper when `comment_count` > visible replies (counted, not shown) |

Facebook shows the comment count and an inline preview of a reply or two (often the Page's reply), never the full
thread. The helpers expand "Вижте повече / See more" and read those previews. Do not try to open threads. Missing
replies beyond the preview are not a defect.

## 2. What "healthy" looks like

`fb_preflight()` runs the extractor on one known reviews page and checks it (`fb_check_reviews`):

| Check | Fails when |
|---|---|
| `schema` | a review lacks a contract key or has the wrong type |
| `found_reviews` | the page says "(N отзива / reviews)" with N > 0, but 0 reviews were read |
| `coverage` | reviews read < 80% of min(page total, max_reviews) (60% during the run) |
| `over_total` | more reviews read than the page total × 1.2 (non-review units parsed, duplicates) |
| `empty_text` | > 30% of reviews have no text |
| `null_verdict` | > 20% have `recommends` null |
| `missing_permalink` | > 30% have no permalink |
| `missing_reviewer` | > 20% have no reviewer |
| `duplicates` | > 10% are duplicates |
| `override_runs` / `override_parses` | your override throws / does not parse |
| `search_results` | (with `search_query`) the search page gives 0 Page results |

With fewer than 5 reviews, a share check fails only if every review is missing the field.
Warnings (do not fail, but look at them): the page total was not parsed, no date texts, no comment counts on a big page,
the header line leaking into the text. Reply warnings (`stats['replies']`):
- more than 20% of replies are still `truncated` (check `re.EXPAND` and the sweep)
- more than 20% have no text or no author
- a reply text starts with the author badge, or ends with "Вижте повече / See more"
- more than 10% of reviews have more replies than their `comment_count`

If a reply warning shows up and the page clearly shows the replies, repair it the same way as a failed check.
`healthy: None` means nothing could be checked yet. Either no location is known yet, or the sample page has no reviews.

## 3. When to run it

- **Start of every round**: if `fb_progress()['preflight']['due']` is true, run `fb_preflight()` before Phase 2.
  With no locations yet: `fb_preflight(search_query=brand)` checks search, then run `fb_preflight()` again
  right after the first `fb_add_locations(...)`, before extracting any location.
- **During Phase 2**: every location result is health-checked too. A result that fails comes back with status
  `suspect` and is **not saved**. Two `suspect` results in a row, or one `unknown` on a page that clearly shows
  reviews, mean the extractor is broken: stop the loop and repair.
- A preflight costs 1 page load (+1 with `search_query`). `fb_preflight(reload=False)` costs none.

## 4. Repair (when `healthy` is False)

1. Stay on the failing reviews page. Look at `report['reviews']['failed']`, `['details']`, `['sample']` and
   `['diagnostics']`. Take a screenshot and compare: what does the page show that the sample does not?
2. Find the new structure with small read-only `js()` probes, for example:
   - `fb_js("__fbx.posts().length")`: how many review units are found (0 or 1 means the unit selector is broken)
   - `js("[...document.querySelectorAll('[role=article]')].length")`, `js("document.querySelector('[aria-posinset]')?.outerHTML.slice(0,3000)")`
   - the header text of one review, its links (`a[href]`), the comment-count element, the "(N отзива)" text
3. Write the smallest override that fixes it. It is plain JS that runs **after** `fb_extract.js` and changes parts of
   `window.__fbx`. Everything inside the extractor calls these parts through `__fbx`, so replacing one works everywhere:
   - selectors: `__fbx.sel.unit` (one review), `__fbx.sel.article`, `__fbx.sel.message` (review text), `__fbx.sel.textBlock`
   - keyword patterns: `__fbx.re.POS`, `re.NEG` (verdict), `re.COMMENT_COUNT`, `re.POST_LINK` (permalink),
     `re.UI_NOISE`, `re.EXPAND` ("See more"), `re.TIME_TXT`, `re.COMMENT_LABEL`, `re.REPLY_LABEL`
   - functions: `__fbx.posts()`, `__fbx.parsePost(P, opts)` (must return the contract keys + `header`),
     `__fbx.reviewTotal()`, `__fbx.tabState(n)`, `__fbx.searchResults()`, `__fbx.textBlocks(root, exclude)`,
     `__fbx.commentSet(P)`, `__fbx.clickExpanders()`
   - wrap instead of rewriting where you can:
     `var orig = __fbx.parsePost; __fbx.parsePost = function (P, o) { var r = orig(P, o); r.text = ...; return r; };`
4. Save it: `fb_save_override(js_code, note)`. In `note`, say what Facebook changed and what you changed, for example
   "review units no longer carry aria-posinset; units = div[data-pagelet^=Review]". The helper checks syntax, keeps
   the previous version, and logs it.
5. Check it: `fb_preflight(reload=False)`. Repeat steps 2–5 until `healthy` is True. Then go on with the run.
   Redo the locations that came back `suspect`. They were not saved.
6. Budget: at most about 8 steps and 4 override versions per repair. If it is still unhealthy
   (`fb_progress()['preflight']['unhealthy_streak']` ≥ 4), stop the run. Finish with stop_reason `extractor_broken`,
   and give the failed checks and what you tried in notes. Do not collect data you know is wrong.

The override lives in the workspace (`fb_extract_override.js`). Every helper (`fb_js`, `fb_extract_reviews`,
`fb_search_pages`, `fb_preflight`) loads it automatically, in this round, in later rounds and after `--resume`.
`fb_override()` shows it with its log. `fb_clear_override(note)` removes it if it makes things worse.

## 5. What a repair must never do

- No new navigation tricks. Only `fb_open` opens pages. Read-only probes only: no clicks on Like, Comment, Follow or
  Message. Clicking "See more" / expanders is fine; it is what `clickExpanders` does.
- Never touch login or checkpoint detection (`pageState` cannot be overridden). Never bypass a login wall, checkpoint
  or CAPTCHA. Never spoof the user agent or browser fingerprint.
- Do not turn on comment-thread opening. The comment count is all Facebook gives.
- Do not loosen the checks to make preflight pass. Fix the extraction, not the test.

**Lite mode (no login):** reviews are not visible to logged-out visitors, so `fb_preflight` returns `healthy: None`
and nothing is repaired because of missing reviews.
