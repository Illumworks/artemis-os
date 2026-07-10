"""Canonical scout Finding contract.

Every scout mapper historically emitted its own ad-hoc dict shape
(``urgency`` vs ``urgencyTier``, ``source_url`` buried in ``metadata``,
no ``headline``, no ``campaignFamily``).  The ingest endpoint
``POST /api/scouts/runs`` requires top-level ``headline``,
``campaignFamily`` and ``sourceUrl`` — so every finding was rejected.

This module is the single normalization chokepoint.  ``BaseScout.emit_signals``
runs every raw mapper dict through :meth:`Finding.from_raw` before POSTing,
so individual mappers can no longer drift out of contract.

Wire shape produced by :meth:`Finding.to_wire` (camelCase, matches what
``artemis/marketing/routes/scouts.py`` consumes):

    {
        "headline": str,            # required, non-empty
        "campaignFamily": str,      # required, canonical slug
        "sourceUrl": str,           # required — dedupe key (url + headline)
        "sourceType": str,
        "sourceId": str | None,
        "urgencyTier": "hot" | "standard" | "enrichment",
        "reasonCodes": [str, ...],
        "summary": str,             # evidence text (signal_queue.summary)
        "evidence": str,            # kept for downstream/qualifier consumers
        "districtId": str | None,
        "state": str | None,
        "discoveredBy": str,        # overridden server-side anyway
        "provenance": dict,         # mapper metadata, preserved verbatim
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from artemis.marketing.josh_spec import (
    CANONICAL_CAMPAIGN_FAMILIES,
    normalize_campaign_family,
    normalize_urgency_tier,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reason-code → campaign-family derivation
# ---------------------------------------------------------------------------
# Mappers emit reason codes but historically never a campaign family.  This
# table derives the canonical family (josh_spec taxonomy) from the FIRST
# matching reason code; anything unmapped falls back to "general_growth".

DEFAULT_CAMPAIGN_FAMILY: str = "general_growth"

REASON_CODE_CAMPAIGN_FAMILY: dict[str, str] = {
    # OBC / outcomes-based contracting
    "BOARD_OBC_DISCUSSION": "obc",
    "STATE_OBC_LEGISLATION": "obc",
    "RFP_OUTCOMES_BASED_LANGUAGE": "obc",
    "RFP_EFFICACY_LANGUAGE": "obc",
    # Dyslexia / structured literacy
    "TX_HB3_DYSLEXIA_COMPLIANCE": "dyslexia",
    "STATE_DYSLEXIA_MANDATE": "dyslexia",
    # Biliteracy / DLL
    "DISTRICT_DLL_EXPANSION": "biliteracy",
    "STATE_BILITERACY_INITIATIVE": "biliteracy",
    # High-impact tutoring
    "TX_HB1416_WAIVER": "hit",
    "DISTRICT_MTSS_STRAIN": "hit",
    "RFP_TUTORING_POSTED": "hit",
    # Everything else (procurement, strategic literacy, leadership, peer
    # validation, funding, news…) defaults to general_growth.
}


def campaign_family_for_reason_codes(reason_codes: list[str]) -> str:
    """Derive the canonical campaign family from a list of reason codes."""
    for code in reason_codes:
        family = REASON_CODE_CAMPAIGN_FAMILY.get(str(code).strip().upper())
        if family:
            return family
    return DEFAULT_CAMPAIGN_FAMILY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Metadata keys checked (in order) when the mapper did not put a source URL
# at the top level.  Covers every in-repo mapper's metadata shape.
_METADATA_URL_KEYS: tuple[str, ...] = (
    "source_url",
    "sourceUrl",
    "html_url",
    "url",
    "link",
    "goto_url",
    "state_link",
)

# Metadata keys checked for a stable source identifier (dedupe fallback).
_METADATA_ID_KEYS: tuple[str, ...] = (
    "source_id",
    "sourceId",
    "document_number",
    "bill_id",
    "rfp_id",
    "post_id",
    "item_id",
    "portal_id",
)

_MAX_HEADLINE_LEN = 300


def _first(raw: dict[str, Any], *keys: str) -> Any:
    """Return the first truthy value among raw[key] for the given keys."""
    for key in keys:
        value = raw.get(key)
        if value:
            return value
    return None


def _derive_headline(raw: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Best-effort headline: explicit > metadata title > first evidence sentence."""
    explicit = _first(raw, "headline") or _first(metadata, "headline", "title") or raw.get("title")
    if explicit:
        return str(explicit).strip()[:_MAX_HEADLINE_LEN]

    evidence = str(raw.get("evidence") or raw.get("summary") or "").strip()
    if evidence:
        # First sentence, else a hard prefix.
        sentence = re.split(r"(?<=[.!?])\s+", evidence, maxsplit=1)[0].strip()
        return (sentence or evidence)[:_MAX_HEADLINE_LEN]
    return ""


def _derive_source_url(raw: dict[str, Any], metadata: dict[str, Any], scout_type: str) -> str:
    """Best-effort source URL; falls back to a deterministic URN from sourceId.

    The server dedupes on ``(source_url, headline)`` so this MUST be a stable,
    non-empty value.  When no URL exists anywhere (e.g. Starbridge items that
    only carry an item_id), a ``urn:artemis-scout:...`` identifier is
    synthesized so dedupe still keys off populated fields.
    """
    url = _first(raw, "sourceUrl", "source_url")
    if not url:
        url = _first(metadata, *_METADATA_URL_KEYS)
    if url:
        return str(url).strip()

    source_id = _first(raw, "sourceId", "source_id") or _first(metadata, *_METADATA_ID_KEYS)
    if source_id:
        return f"urn:artemis-scout:{scout_type}:{source_id}"
    return ""


def _coerce_reason_codes(raw_codes: Any) -> list[str]:
    """Accept ["CODE", ...] or [{"code": "CODE", ...}, ...]; return list[str]."""
    if not isinstance(raw_codes, list):
        return []
    codes: list[str] = []
    for entry in raw_codes:
        if isinstance(entry, str) and entry.strip():
            codes.append(entry.strip())
        elif isinstance(entry, dict) and str(entry.get("code", "")).strip():
            codes.append(str(entry["code"]).strip())
    return codes


# ---------------------------------------------------------------------------
# Canonical model
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """Canonical, validated scout finding.

    Construct via :meth:`from_raw` (tolerant of legacy mapper shapes) and
    serialize via :meth:`to_wire` for ``POST /api/scouts/runs``.
    """

    model_config = ConfigDict(extra="forbid")

    #: Fields the server-side ingest validator requires to be non-empty.
    REQUIRED_WIRE_FIELDS: ClassVar[tuple[str, ...]] = ("headline", "campaignFamily", "sourceUrl")

    headline: str = Field(min_length=1, max_length=_MAX_HEADLINE_LEN)
    source_url: str = Field(min_length=1)
    campaign_family: str = Field(min_length=1)
    urgency_tier: Literal["hot", "standard", "enrichment"] = "standard"
    reason_codes: list[str] = Field(default_factory=list)
    evidence: str = ""
    source_type: str = "manual"
    source_id: str | None = None
    district_id: str | None = None
    state: str | None = None
    discovered_by: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("campaign_family")
    @classmethod
    def _canonical_family(cls, value: str) -> str:
        if value not in CANONICAL_CAMPAIGN_FAMILIES:
            raise ValueError(
                f"campaign_family {value!r} is not canonical "
                f"(expected one of {CANONICAL_CAMPAIGN_FAMILIES})"
            )
        return value

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: dict[str, Any], *, scout_type: str) -> Finding:
        """Normalize a legacy/loose mapper dict into a canonical Finding.

        Raises ``ValueError`` (via pydantic) when a finding cannot be
        salvaged — e.g. no headline derivable and no evidence text, or no
        source URL / stable identifier anywhere.  Callers should drop such
        findings and log, never crash.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"finding must be a dict, got {type(raw).__name__}")

        metadata_raw = raw.get("metadata")
        metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

        reason_codes = _coerce_reason_codes(raw.get("reasonCodes") or raw.get("reason_codes"))

        family_raw = _first(raw, "campaignFamily", "campaign_family")
        family = normalize_campaign_family(str(family_raw)) if family_raw else None
        if family is None:
            family = campaign_family_for_reason_codes(reason_codes)

        urgency_raw = _first(raw, "urgencyTier", "urgency_tier", "urgency")
        urgency = normalize_urgency_tier(str(urgency_raw)) if urgency_raw else None
        if urgency is None:
            urgency = "standard"

        evidence = str(raw.get("evidence") or raw.get("summary") or "").strip()

        source_id = _first(raw, "sourceId", "source_id") or _first(metadata, *_METADATA_ID_KEYS)

        state = _first(raw, "state") or _first(metadata, "state")
        district_id = _first(raw, "districtId", "district_id") or _first(metadata, "district_id")

        return cls(
            headline=_derive_headline(raw, metadata),
            source_url=_derive_source_url(raw, metadata, scout_type),
            campaign_family=family,
            urgency_tier=urgency,  # normalized to a canonical tier above
            reason_codes=reason_codes,
            evidence=evidence,
            source_type=str(
                _first(raw, "sourceType", "source_type")
                or _first(metadata, "source_type")
                or "manual"
            ),
            source_id=str(source_id) if source_id else None,
            district_id=str(district_id) if district_id else None,
            state=str(state) if state else None,
            discovered_by=scout_type,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_wire(self) -> dict[str, Any]:
        """CamelCase payload for ``POST /api/scouts/runs``."""
        return {
            "headline": self.headline,
            "campaignFamily": self.campaign_family,
            "sourceUrl": self.source_url,
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "urgencyTier": self.urgency_tier,
            "reasonCodes": list(self.reason_codes),
            "summary": self.evidence,
            "evidence": self.evidence,
            "districtId": self.district_id,
            "state": self.state,
            "discoveredBy": self.discovered_by,
            "provenance": self.metadata,
        }
