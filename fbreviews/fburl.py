"""Facebook Page URL helpers."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit

NON_PAGE_PATHS = {
    "search", "groups", "events", "watch", "marketplace", "gaming", "hashtag", "photo", "photo.php", "story.php",
    "permalink.php", "login", "login.php", "sharer", "dialog", "help", "policies", "pages", "places", "reel", "share",
}
PAGE_TABS = {"reviews", "about", "photos", "videos", "posts", "community", "events", "mentions", "locations", "map"}


def normalize_page_url(url: str) -> str | None:
    """Canonical https://www.facebook.com/<page> (or profile.php?id=N) for a Page URL; None when not a Page URL."""
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith("/"):
        url = "https://www.facebook.com" + url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    u = urlsplit(url)
    host = u.netloc.lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "profile.php":
        pid = parse_qs(u.query).get("id", [None])[0]
        return f"https://www.facebook.com/profile.php?id={pid}" if pid and pid.isdigit() else None
    if parts[0] == "pages" and len(parts) >= 3:  # legacy /pages/<name>/<id>
        return f"https://www.facebook.com/{parts[-1]}" if parts[-1].isdigit() else None
    if parts[0] == "p" and len(parts) >= 2:  # /p/<Name>-<id>/
        return f"https://www.facebook.com/p/{parts[1]}"
    if parts[0].lower() in NON_PAGE_PATHS:
        return None
    return f"https://www.facebook.com/{parts[0]}"


def reviews_url(page_url: str) -> str:
    norm = normalize_page_url(page_url) or page_url.rstrip("/")
    if "profile.php" in norm:
        u = urlsplit(norm)
        q = parse_qs(u.query)
        q["sk"] = ["reviews"]
        return f"https://www.facebook.com/profile.php?{urlencode({k: v[0] for k, v in q.items()})}"
    return norm.rstrip("/") + "/reviews"


_CYR = dict(zip("абвгдежзийклмнопрстуфхцчшщъьюяѝ",
                ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                 "f", "h", "ts", "ch", "sh", "sht", "a", "y", "yu", "ya", "i"]))


def slug(text: str, max_len: int = 60) -> str:
    text = "".join(_CYR.get(ch.lower(), ch) for ch in (text or ""))  # "Лидл" → "lidl", not "x"
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower()
    return (t or "x")[:max_len]


def page_slug(page_url: str) -> str:
    norm = normalize_page_url(page_url) or page_url
    u = urlsplit(norm)
    pid = parse_qs(u.query).get("id", [None])[0]
    return slug(pid or u.path.strip("/").replace("/", "-"))


def post_key(url: str) -> str:
    """One key per Facebook post, whatever Page's feed it was seen in: a location Page that shows the brand's post
    gives the same permalink (tracking parameters dropped), so the post is read and counted once."""
    u = urlsplit(str(url or "").strip())
    host = u.netloc.lower().replace("m.facebook.com", "www.facebook.com").replace("web.facebook.com", "www.facebook.com")
    q = parse_qs(u.query)
    keep = "&".join(f"{k}={q[k][0]}" for k in ("story_fbid", "fbid", "v", "id") if k in q)
    return f"{host}{u.path.rstrip('/').lower()}" + (f"?{keep}" if keep else "")
