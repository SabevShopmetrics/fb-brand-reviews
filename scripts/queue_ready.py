"""Can the queue start the next brand now? Prints one word and changes nothing:
  ok          - OpenWork's browser is reachable, has a tab, Facebook is logged in (or it cannot tell), the model answers
  no_browser  - OpenWork is not running / its browser has no tab (a run would fail at once)
  logged_out  - Facebook is logged out in OpenWork (a run would quietly fall back to lite mode)
  no_model    - the model server does not answer (a run would fail at once)
The queue waits and asks again instead of burning through brands. Run from the project folder."""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fbreviews.cdp_bridge import facebook_logged_in  # noqa: E402
from fbreviews.config import Settings  # noqa: E402


def main() -> str:
    s = Settings.load()
    cdp = s.openwork_cdp_url
    if not cdp:
        return "no_browser"
    try:
        tabs = json.load(urllib.request.urlopen(cdp.rstrip("/") + "/json", timeout=5))
    except Exception:
        return "no_browser"
    pages = [t for t in tabs if t.get("type") == "page" and (t.get("url") or "").startswith("http")]
    fb = [t for t in pages if "facebook.com" in t.get("url", "")]
    if not (fb or pages):
        return "no_browser"
    if facebook_logged_in(cdp, (fb or pages)[0].get("id")) is False:
        return "logged_out"
    if os.environ.get("QUEUE_SKIP_MODEL_CHECK"):
        return "ok"
    try:
        from openai import OpenAI
        OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "not-needed", timeout=60).chat.completions.create(
            model=s.llm_model, messages=[{"role": "user", "content": "Reply with: ok"}], max_tokens=5)
    except Exception:
        return "no_model"
    return "ok"


if __name__ == "__main__":
    print(main())
