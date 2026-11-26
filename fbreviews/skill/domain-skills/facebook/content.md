# Facebook content: the Pages' posts and the comments under them (Phase 3 and 4)

The reviews are saved. This phase reads what the brand itself publishes and how people react to it. It only reads:
never like, comment, follow or message. The same safety rules as `preflight.md`: if Facebook asks for a login or a
check (`login_required` / `blocked`), stop; never get around it.

## Helpers
| Helper | What it does |
|---|---|
| `fb_progress()` | `posts_todo` (Pages without posts yet), `comments_todo` (posts whose comments are not read yet), `stopped` |
| `fb_extract_posts(url, name=...)` | scrolls the Page timeline: every post with date, text, type (photo / album / video / reel / link / text), photos, reactions, comments, shares, views, link → `posts/<slug>.json` |
| `fb_extract_comments()` | opens every post that has comments (newest first), reads author, text, date, reply level and the Page's own replies; works ~40 min per call and saves after every post → call again until `done` |

## Phase 3 — posts (one cell)
```python
for p in fb_progress()['posts_todo']:
    r = fb_extract_posts(p['url'], name=p['name'])
    print(p['name'], r)
    if r.get('status') in ('login_required', 'blocked', 'stopped', 'cap_reached'):
        break
```

## Phase 4 — comments under the posts (repeat the cell)
```python
r = fb_extract_comments()
print(r)          # {'done': False, 'left': N} → run the same cell again; 'stopped' → stop the run
```
A post opens as a window over the account's news feed: the helpers read ONLY that window (the post and its
comments), never the feed. Comments that are only tagged friends or stickers are kept as they are (they show
reach, not opinion).

## Finish — as soon as `fb_extract_comments()` returns `done: True`
Stop there and return the summary. Do NOT open posts again, read the page with your own JavaScript or compare
counts: some posts always give fewer comments than their count (reels, link posts, hidden / deleted / filtered
comments) — that is a known limit, not an error to investigate. The result already gives `posts_fewer_than_count`
and `posts_none_read`: put them in notes. Return the summary: posts collected, posts whose comments were read, `complete` = `posts_todo` empty and
`comments_todo` 0, and notes (pages that would not open, a stop).
