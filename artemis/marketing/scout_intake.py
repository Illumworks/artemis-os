"""Scout intake normalization — shared seam used by signal-queue intake and scout runs.

Port of Node's server/scout-intake.js (157 lines).

Validates and normalizes incoming signal payloads from scouts or operators.
Anti-spoof: discoveredBy is unconditionally overridden to the scout_type param
so no scout can claim a different identity.

H2 addition: ``normalize_intake_payload`` now accepts an optional
``reason_codes_allowlist`` parameter.  When provided it:
  1. Parses the payload through ``ScoutEmittedSignal`` (strict Pydantic validation).
  2. Validates every ``reasonCode.code`` against the allowlist.
Both failures raise ``ValueError`` so the caller can reject the whole signal.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from artemis.marketing.josh_spec import (
    CANONICAL_CAMPAIGN_FAMILIES,
    normalize_campaign_family,
)
from artemis.marketing.scout_schemas import (
    ReasonCodeAllowlistError,
    ScoutEmittedSignal,
    validate_reason_codes_against_allowlist,
)

# ─────────────────────────────────────────────────────────────────────────────
# Validation constants (mirror Node exactly)
# ─────────────────────────────────────────────────────────────────────────────

VALID_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "manual",
        "starbridge",
        "news_article",
        "board_minutes",
        "state_doe",
        "linkedin_post",
    }
)

# Canonical campaign-family slugs — single source of truth lives in josh_spec.
VALID_CAMPAIGN_FAMILIES: frozenset[str] = frozenset(CANONICAL_CAMPAIGN_FAMILIES)

VALID_URGENCY_TIERS: frozenset[str] = frozenset({"hot", "standard", "low"})

# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class NormalizedFinding:
    """Output of normalize_intake_payload — ready for DB insertion."""

    headline: str
    verbatim_snippet: str | None
    campaign_family: str
    source_type: str
    source_url: str | None
    source_title: str | None
    source_published_at: str | None  # ISO date YYYY-MM-DD
    source_author: str | None
    source_metadata_json: Any | None
    state_code: str | None
    district: str | None
    reason_codes: list[dict[str, Any]]
    urgency_tier: str
    urgency_deadline: Any | None
    fit_score: float | None
    discovered_by: str  # always overridden to scout_type
    discovered_at: Any | None
    why_flagged: str
    evidence: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _derive_headline_from_snippet(snippet: str | None) -> str | None:
    """Extract a sentence-length headline from a verbatim snippet."""
    if not snippet:
        return None
    trimmed = snippet.strip()
    m = re.match(r"^(.+?[.?!])(?:\s|$)", trimmed)
    sentence = m.group(1) if m else trimmed
    return sentence[:200].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def normalize_intake_payload(
    payload: dict[str, Any],
    scout_type: str,
    *,
    reason_codes_allowlist: list[str] | None = None,
) -> NormalizedFinding:
    """Validate and normalize a single intake payload from a scout.

    Anti-spoof: discovered_by is unconditionally overridden to scout_type
    regardless of what the payload claims.

    Args:
        payload: Raw inbound dict (camelCase keys from HTTP body).
        scout_type: The identity of the calling scout (e.g. "starbridge", "manual").
        reason_codes_allowlist: When provided (from agent.reason_codes_emitted),
            the payload is first validated through ``ScoutEmittedSignal`` (Pydantic
            strict shape), then every ``reasonCode.code`` is checked against the
            allowlist.  A violation raises ``ValueError`` — the WHOLE signal is
            rejected, not silently stripped.  Pass ``None`` for manual/legacy intake
            paths that don't yet have an allowlist.

    Returns:
        NormalizedFinding ready for DB insertion.

    Raises:
        ValueError: If validation fails. FastAPI catch-all converts to 422.
    """
    # ── Pydantic strict-shape + allowlist check (H2) ──────────────────────────
    if reason_codes_allowlist is not None:
        from pydantic import ValidationError as PydanticValidationError

        try:
            parsed = ScoutEmittedSignal.model_validate(payload)
        except PydanticValidationError as exc:
            raise ValueError(f"Scout payload failed Pydantic validation: {exc}") from exc
        try:
            validate_reason_codes_against_allowlist(
                parsed.reason_codes, reason_codes_allowlist, scout_type
            )
        except ReasonCodeAllowlistError as exc:
            raise ValueError(str(exc)) from exc
    errors: list[str] = []

    source_type = payload.get("sourceType") or ""
    if not source_type or source_type not in VALID_SOURCE_TYPES:
        errors.append(f"sourceType must be one of: {', '.join(sorted(VALID_SOURCE_TYPES))}")

    # sourceUrl: required for non-manual; must be http(s) if provided
    source_url_raw = payload.get("sourceUrl")
    source_url_str: str = source_url_raw if isinstance(source_url_raw, str) else ""
    has_url = bool(source_url_str.strip())
    if source_type and source_type != "manual" and not has_url:
        errors.append("sourceUrl is required for non-manual source types")
    if has_url and not re.match(r"^https?://", source_url_str.strip(), re.IGNORECASE):
        errors.append("sourceUrl must be a valid http/https URL")

    # headline / verbatimSnippet
    raw_headline = payload.get("headline") or ""
    raw_snippet = payload.get("verbatimSnippet") or ""
    raw_headline = raw_headline.strip() if isinstance(raw_headline, str) else ""
    raw_snippet = raw_snippet.strip() if isinstance(raw_snippet, str) else ""

    if not raw_headline and not raw_snippet:
        errors.append("headline or verbatimSnippet is required")

    # campaignFamily — normalize any label / canonical slug / legacy alias to the
    # canonical slug (single source of truth in josh_spec), then validate.
    campaign_family = normalize_campaign_family(payload.get("campaignFamily")) or ""
    if not campaign_family:
        errors.append(
            "campaignFamily must be one of: "
            + ", ".join(CANONICAL_CAMPAIGN_FAMILIES)
            + " (spec labels and legacy aliases are also accepted)"
        )

    # stateCode (optional — validate format when present)
    state_code_raw = payload.get("stateCode")
    if (
        state_code_raw is not None
        and state_code_raw != ""
        and not re.match(r"^[a-zA-Z]{2}$", str(state_code_raw).strip())
    ):
        errors.append("stateCode must be a 2-letter US state code")

    # sourcePublishedAt (optional — validate ISO date format when present)
    source_published_at_raw = payload.get("sourcePublishedAt")
    if source_published_at_raw is not None and source_published_at_raw != "":
        try:
            # Try parsing via date or datetime
            _parsed = date.fromisoformat(str(source_published_at_raw)[:10])
        except (ValueError, TypeError):
            errors.append("sourcePublishedAt must be a valid ISO date")

    # urgencyTier (optional)
    urgency_tier_raw = payload.get("urgencyTier")
    if (
        urgency_tier_raw is not None
        and urgency_tier_raw != ""
        and urgency_tier_raw not in VALID_URGENCY_TIERS
    ):
        errors.append(f"urgencyTier must be one of: {', '.join(sorted(VALID_URGENCY_TIERS))}")

    # fitScore (optional)
    fit_score_raw = payload.get("fitScore")
    if fit_score_raw is not None:
        try:
            n = float(fit_score_raw)
            if n < 0 or n > 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("fitScore must be a number between 0 and 1")

    if errors:
        raise ValueError("; ".join(errors))

    # ── Normalize ─────────────────────────────────────────────────────────────
    derived_headline = (
        raw_headline or _derive_headline_from_snippet(raw_snippet) or raw_snippet[:200]
    )
    normalized_headline = re.sub(r"\s+", " ", derived_headline).strip()[:500]
    normalized_snippet = raw_snippet or None

    normalized_published_at: str | None = None
    if source_published_at_raw:
        # Store as YYYY-MM-DD
        normalized_published_at = str(source_published_at_raw)[:10]

    # Normalize reason_codes
    reason_codes_raw = payload.get("reasonCodes") or []
    normalized_reason_codes: list[dict[str, Any]] = []
    if isinstance(reason_codes_raw, list):
        for rc in reason_codes_raw:
            if not isinstance(rc, dict):
                continue
            entry = dict(rc)
            if "confidence" in entry and entry["confidence"] is not None:
                with contextlib.suppress(TypeError, ValueError):
                    entry["confidence"] = max(0.0, min(1.0, float(entry["confidence"])))
            normalized_reason_codes.append(entry)

    normalized_urgency_tier = urgency_tier_raw or "standard"

    # Anti-spoof: unconditionally override discoveredBy to scout_type
    normalized_discovered_by = scout_type

    normalized_fit_score: float | None = None
    if fit_score_raw is not None:
        normalized_fit_score = float(fit_score_raw)

    source_title_raw = payload.get("sourceTitle")
    source_title: str | None = (
        str(source_title_raw).strip()
        if isinstance(source_title_raw, str) and source_title_raw.strip()
        else None
    )

    source_author_raw = payload.get("sourceAuthor")
    source_author: str | None = (
        str(source_author_raw).strip()
        if isinstance(source_author_raw, str) and source_author_raw.strip()
        else None
    )

    # Accept the canonical tool field `districtId` (camelCase, matches the
    # signal_queue.write schema + every other field) with a fallback to the
    # legacy `district` key. Without this, scouts that populate districtId per
    # the tool schema had it silently dropped here — leaving district NULL and
    # the DIST3 resolver with no input. (Resolves the districtId/district
    # key-mismatch; required for scout geography emission to actually land.)
    district_raw = payload.get("districtId") or payload.get("district")
    district: str | None = (
        str(district_raw).strip()
        if isinstance(district_raw, str) and district_raw.strip()
        else None
    )

    state_code: str | None = (
        str(state_code_raw).strip().upper()
        if state_code_raw and str(state_code_raw).strip()
        else None
    )

    why_flagged_raw = payload.get("whyFlagged")
    why_flagged = str(why_flagged_raw) if why_flagged_raw else (normalized_snippet or "")

    evidence_raw = payload.get("evidence")
    evidence = str(evidence_raw) if evidence_raw else (normalized_snippet or "")

    return NormalizedFinding(
        headline=normalized_headline,
        verbatim_snippet=normalized_snippet,
        campaign_family=str(campaign_family),
        source_type=str(source_type),
        source_url=source_url_str.strip() if has_url else None,
        source_title=source_title,
        source_published_at=normalized_published_at,
        source_author=source_author,
        source_metadata_json=payload.get("sourceMetadataJson"),
        state_code=state_code,
        district=district,
        reason_codes=normalized_reason_codes,
        urgency_tier=normalized_urgency_tier,
        urgency_deadline=payload.get("urgencyDeadline"),
        fit_score=normalized_fit_score,
        discovered_by=normalized_discovered_by,
        discovered_at=payload.get("discoveredAt"),
        why_flagged=why_flagged,
        evidence=evidence,
    )
