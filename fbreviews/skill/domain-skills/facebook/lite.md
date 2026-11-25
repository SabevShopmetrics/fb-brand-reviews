# Facebook lite mode: public data of every location, WITHOUT a Facebook login

You work in a fresh browser with no Facebook account. Facebook shows visitors only the public data of a Page (name,
category, address, phone, website, followers, "94% recommend (522 reviews)") and a login dialog over the rest. That
is all this mode collects. Never log in, never type credentials, never try to close or get around the login dialog
or any CAPTCHA. Reading the page behind the dialog is fine; the helpers do it.

Read `preflight.md` only for its safety rules; its review checks do not apply here (there is no review list).

## Helpers
| Helper | What it does |
|---|---|
| `fb_progress()` | `{locations, done, todo, stopped}` — call FIRST: the run may be a continuation |
| `fb_web_search(query, pages=1)` | Bing results pointing to facebook.com: `[{title, url, page_url, snippet}]`, or `{'error':'search_challenge'}` |
| `fb_add_locations([{name, url, address, city, country, category, source}])` | merge into locations.json (dedupe, cap) |
| `fb_page_summary(url)` | public data of one Page → `{name, category, address, phone, website, recommend_pct, review_total, …, state}` |
| `fb_save_summary(location, summary)` | save one location (status `summary_only`) |
| `fb_open(url)`, `fb_page_url(url)` | paced navigation, URL helper |

## Phase 1: find the locations (skip if the list is given or the cap is reached)
1. The brand's own website usually has a store / restaurant list ("Обекти", "Магазини", "Locations", "Store
   locator"). Open it with `goto_url` (it is not Facebook), read the locations (name, city, address) with `js()`.
   Many list a Facebook link per location: take it.
2. For each location without a link: `fb_web_search(f'{brand} {location name or mall} {city} facebook')`.
   Pick the result whose page is THIS location (the name or city matches); skip the brand's main page, fan pages,
   groups and other businesses.
3. Broad searches help too: `fb_web_search(f'site:facebook.com "{brand}" {city}')` for the big cities.
4. `fb_add_locations(batch)` after every few finds. If `fb_web_search` returns `search_challenge`, stop searching
   (do not solve it) and go on with the locations you have; say so in notes.

## Phase 2: every location
```python
for loc in fb_progress()['todo']:
    s = fb_page_summary(loc['url'])
    print(loc['name'], s.get('state'), s.get('recommend_pct'), s.get('review_total'))
    if s.get('state') in ('login_required', 'blocked', 'stopped', 'cap_reached'):
        break                     # Facebook demands a login or a check: stop the run
    print(fb_save_summary(loc, s))
```
Run it in ONE cell; it paces itself (6–14 s between pages). A Page with no "% recommend" simply has no reviews or
has them switched off: save it anyway (recommend_pct stays empty).

## Finish
Return the summary: locations found, locations done, `complete`, brand pages, and notes (searches that were blocked,
pages that redirected to login, anything odd).
