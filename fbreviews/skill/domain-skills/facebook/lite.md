# Facebook lite mode: public data of every location (no Facebook account needed)

This mode collects only the public data of a Page — what Facebook shows a visitor without an account: name,
category, address, phone, website, followers, "94% recommend (522 reviews)". The browser is OpenWork's built-in one
(the user is watching it; it may be logged in to Facebook or not) or a fresh hidden one with no account. Either way
collect only that public data. Never log in, never type credentials, never try to close or get around the login dialog
or any CAPTCHA. Reading the page behind the dialog is fine; the helpers do it.

Read `preflight.md` only for its safety rules; its review checks do not apply here (there is no review list).

## Helpers
| Helper | What it does |
|---|---|
| `fb_progress()` | `{locations, done, todo, stopped}` — call FIRST: the run may be a continuation |
| `fb_add_brand_pages([{name, url, scope}])` | record the brand's official Pages (global / country): a separate section, never locations |
| `fb_extract_brand(url, name=...)` | the brand Page as its own section: public data |
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

**Same brand, not a namesake.** Many names are generic ("Pizza Lab", "Happy", "Boom Burgers"): a Page with the same
name is a location of THIS brand only with proof — it links to the brand's website or official Page, it is listed
on the brand's Page / website, or it shares the brand's logo and naming pattern (e.g. "Raffy Bar & Gelato Plovdiv").
A same-name business in another city or country without that proof is NOT added. Check the snippet (website,
category, address) and, when unsure, open the Page once and look for the brand's link.

**Stop searching** when 3 searches in a row add no new location (`fb_add_locations` returns `added: 0`), and
never repeat a query. Then go on: extracting is the goal, not searching. If 6 searches find no location Page at all, the brand has no Page per location (common for a chain with one Page): searching closes by itself (`search_closed`); do Phase 1b (the brand Page) and finish. Never search other countries for a brand that clearly operates in one country only.

## Phase 1b — the brand's own Pages (a separate section of the report)
The official brand Pages (e.g. "Lidl", "Lidl България") are not locations, but they are reported on their own.
Find them on the brand's website (its Facebook link) or with `fb_web_search(f'{brand} official facebook')`, then:
```python
fb_add_brand_pages([{'name': 'Lidl България', 'url': 'https://www.facebook.com/lidlbulgaria', 'scope': 'country'}])
for b in fb_progress()['brand_todo']:
    print(fb_extract_brand(b['url'], name=b['name']))   # public data, saved under brand/
```
Brand Pages: the global one and one official Page per country in scope (`scope` = 'global' or the country).

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
