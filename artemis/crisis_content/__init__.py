"""Crisis-comms content-approval pipeline -- slices A-C (reader/parser,
watcher/routing, and the decision loop).

Fetches the vendor-owned Google Doc through its HTML export endpoint,
parses "review cards" out of it, posts Callie's card with decision buttons,
and records authenticated, authorized Slack clicks as append-only decisions.
See ``docs/crisis-content-approval-pipeline.md`` for the full design;
``briefs/cca1-doc-card-reader.md`` .. ``briefs/cca5-approval-loop.md`` for
each slice's scope. Doc write-back, Drive/Gmail notification, and Writing
Studio harvest are later slices and are not implemented here.
"""

from __future__ import annotations

from artemis.crisis_content.authorization import (
    asset_route_approver_emails,
    copy_route_approver_emails,
    is_authorized_for_route,
)
from artemis.crisis_content.decisions import (
    Decision,
    get_latest_decision,
    is_blocked_by_existing_decision,
    record_decision,
)
from artemis.crisis_content.export_client import (
    TARGET_DOCUMENT_ID,
    fetch_crisis_content_export_html,
)
from artemis.crisis_content.models import ReviewCard, StatusClassification
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
    CrisisContentDecision,
    CrisisContentNotification,
)
from artemis.crisis_content.parser import (
    CrisisContentParseError,
    NoReviewCardsFoundError,
    SignInPageError,
    classify_status,
    looks_like_sign_in_page,
    parse_review_cards,
    unwrap_google_redirect_url,
)
from artemis.crisis_content.transitions import (
    Route,
    Transition,
    find_card_id,
    has_notified,
    mark_notified,
    record_observation,
)

__all__ = [
    "TARGET_DOCUMENT_ID",
    "ReviewCard",
    "StatusClassification",
    "CrisisContentParseError",
    "SignInPageError",
    "NoReviewCardsFoundError",
    "CrisisContentCard",
    "CrisisContentCopyVersion",
    "CrisisContentNotification",
    "CrisisContentDecision",
    "Route",
    "Transition",
    "Decision",
    "asset_route_approver_emails",
    "copy_route_approver_emails",
    "is_authorized_for_route",
    "classify_status",
    "fetch_crisis_content_export_html",
    "find_card_id",
    "get_latest_decision",
    "has_notified",
    "is_blocked_by_existing_decision",
    "looks_like_sign_in_page",
    "mark_notified",
    "parse_review_cards",
    "record_decision",
    "record_observation",
    "unwrap_google_redirect_url",
]
