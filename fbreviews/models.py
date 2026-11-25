"""Data models: what the agents return and what the final report holds."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ReplyType = Literal["generated", "manual", "uncertain", "not_page_reply"]
LocationStatus = Literal["ok", "partial", "summary_only", "no_own_page", "no_reviews", "reviews_disabled", "login_required", "blocked", "not_found", "error"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- agent outputs (kept small: the bulk data goes through files in the agent workspace) ----------
class LocationPage(BaseModel):
    name: str
    url: str = Field(description="Full https://www.facebook.com/... URL of the location's own Page")
    address: str | None = None
    city: str | None = None
    country: str | None = None
    category: str | None = None
    phone: str | None = None
    website: str | None = None
    followers: str | None = None
    price: str | None = None
    email: str | None = None
    rating_text: str | None = Field(default=None, description="e.g. '4.3 (120 reviews)' or '87% recommend' if shown")
    source: str | None = Field(default=None, description="Where it was found: locations_tab | search | map | other")


class DiscoveryReport(BaseModel):
    """Final answer of the discovery agent."""

    brand_pages: list[str] = Field(default_factory=list, description="URLs of the brand's main/global/country Pages")
    locations_file: str = Field(default="locations.json", description="Workspace file holding the full location list")
    location_count: int = 0
    exhausted: bool = Field(default=False, description="True if every search strategy was tried to the end")
    notes: str = ""


class LocationRunReport(BaseModel):
    """Final answer of a per-location agent."""

    status: LocationStatus
    reviews_url: str | None = None
    reviews_file: str | None = Field(default=None, description="Workspace JSON file with the extracted reviews list")
    review_count: int = 0
    notes: str = ""


# ---------- extracted data ----------
class Reply(BaseModel):
    author: str | None = None
    text: str = ""
    date_text: str | None = None
    permalink: str | None = None
    depth: int = 1  # 1 = direct reply to the review, 2 = reply to a reply
    truncated: bool = False  # only Facebook's cut-off preview was available
    is_page_reply: bool = False
    reply_type: ReplyType | None = None
    generated_kind: Literal["template", "ai"] | None = None
    confidence: float | None = None
    signals: list[str] = Field(default_factory=list)
    llm_reason: str | None = None


class Review(BaseModel):
    reviewer: str | None = None
    recommends: bool | None = None
    rating: int | None = None
    text: str | None = None
    date_text: str | None = None
    permalink: str | None = None
    comment_count: int | None = None
    date_full: str | None = None      # "Петък, 14 април 2023 г. в 13:09"
    tags: list[str] = Field(default_factory=list)  # Facebook review tags: "Kid friendly", "Fast delivery"…
    reactions: int | None = None
    photos: int = 0
    replies_unavailable: bool = False  # Facebook showed a comment count but did not load the thread
    replies: list[Reply] = Field(default_factory=list)


class LocationResult(BaseModel):
    location: LocationPage
    status: LocationStatus
    reviews_url: str | None = None
    review_total: int | None = None  # Facebook's own count for the Page ("94% препоръка (522 отзива)")
    recommend_pct: int | None = None  # Facebook's own "% recommend" for the Page
    reviews: list[Review] = Field(default_factory=list)
    notes: str = ""
    is_brand: bool = False  # the brand's own Page (global / country), reported apart from the locations
    scraped_at: str = Field(default_factory=now_iso)

    def stats(self) -> dict:
        replies = [r for rv in self.reviews for r in rv.replies]
        page = [r for r in replies if r.is_page_reply]
        return {
            "facebook_review_total": self.review_total,
            "facebook_recommend_pct": self.recommend_pct,
            "reviews": len(self.reviews),
            "recommends": sum(1 for r in self.reviews if r.recommends is True),
            "not_recommends": sum(1 for r in self.reviews if r.recommends is False),
            "reviews_with_page_reply": sum(1 for rv in self.reviews if any(r.is_page_reply for r in rv.replies)),
            "page_replies": len(page),
            "generated": sum(1 for r in page if r.reply_type == "generated"),
            "manual": sum(1 for r in page if r.reply_type == "manual"),
            "uncertain": sum(1 for r in page if r.reply_type == "uncertain"),
            "other_comments": len(replies) - len(page),
            "reviews_with_comments": sum(1 for rv in self.reviews if (rv.comment_count or 0) > 0),
            "comments_counted": sum(rv.comment_count or 0 for rv in self.reviews),
            "reviews_with_hidden_comments": sum(1 for rv in self.reviews if rv.replies_unavailable),
        }


class BrandReport(BaseModel):
    brand: str
    generated_at: str = Field(default_factory=now_iso)
    model: str | None = None
    brand_pages: list[str] = Field(default_factory=list)
    brand_sections: list[LocationResult] = Field(default_factory=list)  # the brand's own Pages, same data as a location
    locations: list[LocationResult] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    classification_method: str = ""
