"""Crisis-comms content-approval pipeline -- slice A (reader + parser).

Read-only: fetches the vendor-owned Google Doc through its HTML export
endpoint and parses "review cards" out of it. No Slack, no polling loop, no
document writes -- see ``docs/crisis-content-approval-pipeline.md`` for the
full design and ``briefs/cca1-doc-card-reader.md`` for this slice's scope.
"""

from __future__ import annotations

from artemis.crisis_content.export_client import (
    TARGET_DOCUMENT_ID,
    fetch_crisis_content_export_html,
)
from artemis.crisis_content.models import ReviewCard, StatusClassification
from artemis.crisis_content.parser import (
    CrisisContentParseError,
    NoReviewCardsFoundError,
    SignInPageError,
    classify_status,
    looks_like_sign_in_page,
    parse_review_cards,
    unwrap_google_redirect_url,
)

__all__ = [
    "TARGET_DOCUMENT_ID",
    "ReviewCard",
    "StatusClassification",
    "CrisisContentParseError",
    "SignInPageError",
    "NoReviewCardsFoundError",
    "classify_status",
    "fetch_crisis_content_export_html",
    "looks_like_sign_in_page",
    "parse_review_cards",
    "unwrap_google_redirect_url",
]
