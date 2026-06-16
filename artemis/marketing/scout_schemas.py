"""Pydantic schemas for the scout LLM emitted payload (H2).

These models are the structural contract between the scout prompt and intake.
Strict mode: extra fields are rejected, enum literals are validated.
Confidence out of bounds is rejected (not silently clamped) — consistent with
the strict-shape principle. The caller (scout_runner) sees a ValidationError
and increments signals_rejected.

camelCase field names are the LLM's native output format; Pydantic aliases map
them to Python-idiomatic attribute names. ``model_config`` uses
``populate_by_name=True`` so tests can use either name.

Layer-C tolerance (provider shape differences)
----------------------------------------------
``ScoutEmittedSignal`` contains a ``@model_validator(mode="before")`` that
coerces provider shape differences WITHOUT loosening semantic validation:

* ``reasonCodes``/``reason_codes`` entries: bare strings are promoted to the
  canonical ``{code, confidence, evidence}`` object shape.  NOTE: bare-string
  codes lose per-code confidence (defaulted to 0.7); quality caveat applies —
  see ``_coerce_reason_codes_entries``.
* ``districtId`` is declared as an optional field so Gemini's habit of
  emitting it alongside camelCase fields no longer trips ``extra="forbid"``.
  The downstream normalizer (``scout_intake.py``) already reads ``districtId``
  as the canonical key; declaring it here makes the Pydantic strict schema
  consistent with that contract.

``extra="forbid"`` is retained for all OTHER unknown fields — the
anti-hallucination guard stands.

Shared helper
-------------
``validate_llm_json_emission(model_cls, raw_text)`` is the reusable seam for
H3 (trajectory summarizer) and H4 (meeting summarizer). Import from here.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypeVar

from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

# ── Sub-model ─────────────────────────────────────────────────────────────────


class ReasonCode(BaseModel):
    """A single reason code emitted by a scout LLM.

    ``code`` is validated against the scout's allowlist AFTER model construction
    via ``validate_reason_codes_against_allowlist``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = None


# ── Main emitted payload ───────────────────────────────────────────────────────

_SOURCE_TYPES = Literal[
    "manual",
    "starbridge",
    "news_article",
    "board_minutes",
    "state_doe",
    "linkedin_post",
    "regional_news",
    "federal_register",
    "grants_gov",
    "legiscan",
]


class ScoutEmittedSignal(BaseModel):
    """Strict shape of the JSON a scout LLM is instructed to emit.

    Field names are camelCase (LLM output) via alias_generator=to_camel.
    Python attribute access uses snake_case. ``populate_by_name=True`` allows
    both in tests.

    Deviations (wrong types, extra keys, invalid enum values) raise ValidationError.
    The intake caller must REJECT the whole signal — not strip bad fields.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=AliasGenerator(alias=to_camel),
    )

    headline: str = Field(min_length=1, max_length=500)
    source_type: _SOURCE_TYPES
    source_url: str | None = None
    source_title: str | None = None
    campaign_family: str | None = None
    # Canonical urgency tiers (josh_spec §2). The legacy slug "low" is mapped
    # to "enrichment" by the model_validator below so an in-flight scout that
    # still emits the old slug doesn't blow up Pydantic validation.
    urgency_tier: Literal["hot", "standard", "enrichment"] = "standard"
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    why_flagged: str | None = None
    evidence: str | None = None
    fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    state_code: str | None = None
    discovered_by: str = ""  # anti-spoof: overridden to scout_type at intake

    # Layer-C tolerance: ``districtId`` is the canonical key the downstream
    # normalizer (scout_intake.py ~line 281) already reads.  Declaring it here
    # prevents ``extra="forbid"`` from rejecting it when a provider (e.g.
    # Gemini) emits it alongside other camelCase fields.  Snake-case alias
    # ``district_id`` is accepted via ``populate_by_name=True``.
    district_id: str | None = Field(default=None, alias="districtId")

    @model_validator(mode="before")
    @classmethod
    def _coerce_reason_codes_entries(cls, values: Any) -> Any:
        """Layer-C: promote bare-string reason-code entries to the canonical object shape.

        Gemini (and potentially other non-Claude providers) may emit ``reasonCodes``
        as a list of plain strings, e.g. ``["DISTRICT_STRATEGIC_LITERACY", ...]``,
        rather than the expected ``[{"code": "...", "confidence": 0.9, ...}]``.
        This validator promotes each bare string to the minimal valid object.

        QUALITY CAVEAT: bare-string codes default to confidence=0.7 and evidence=null.
        Per-code confidence information is lost; callers that care about confidence
        granularity should use a prompt variant that instructs the model to emit
        full objects (see the tightened-prompt variant in the A/B test script).
        Already-valid object entries are passed through untouched.

        The validator runs before ReasonCode sub-model construction, so ``extra="forbid"``
        on ReasonCode is never bypassed — only the outer list shape is coerced.
        """
        if not isinstance(values, dict):
            return values
        for key in ("reasonCodes", "reason_codes"):
            raw = values.get(key)
            if not isinstance(raw, list):
                continue
            coerced: list[Any] = []
            for entry in raw:
                if isinstance(entry, str):
                    # Bare string → minimal canonical object.
                    # Default confidence=0.7 (mid-range, not max, to avoid inflating
                    # scores when per-code confidence was never emitted).
                    coerced.append({"code": entry.strip(), "confidence": 0.7, "evidence": None})
                else:
                    coerced.append(entry)
            values = {**values, key: coerced}
        return values

    @model_validator(mode="before")
    @classmethod
    def _normalise_missing_discovered_by(cls, values: Any) -> Any:
        """discoveredBy / discovered_by may be absent from older LLM emissions — default to ''."""
        if not isinstance(values, dict):
            return values
        if "discoveredBy" not in values and "discovered_by" not in values:
            values = {**values, "discoveredBy": ""}
        return values

    @model_validator(mode="before")
    @classmethod
    def _normalise_legacy_urgency_low(cls, values: Any) -> Any:
        """Map the legacy 'low' slug to canonical 'enrichment' before Literal check."""
        if not isinstance(values, dict):
            return values
        for key in ("urgencyTier", "urgency_tier"):
            raw = values.get(key)
            if isinstance(raw, str) and raw.strip().lower() == "low":
                values = {**values, key: "enrichment"}
        return values


# ── Allowlist enforcement ─────────────────────────────────────────────────────


class ReasonCodeAllowlistError(ValueError):
    """Raised when a scout emits a reason_code.code not in its allowlist.

    Carries the bad codes and the allowed set for structured logging.
    Named with Error suffix per ruff N818.
    """

    def __init__(
        self,
        bad_codes: list[str],
        allowed: list[str],
        scout_type: str,
    ) -> None:
        self.bad_codes = bad_codes
        self.allowed = allowed
        self.scout_type = scout_type
        super().__init__(
            f"Scout {scout_type!r} emitted disallowed reason_code(s): {bad_codes}. "
            f"Allowed codes: {allowed}. "
            "Reject the whole signal — do not strip. "
            "Update the agent's reason_codes_emitted if this code is legitimate."
        )


# Keep the old name as an alias for backwards compat with any callers.
ReasonCodeAllowlistViolation = ReasonCodeAllowlistError


def validate_reason_codes_against_allowlist(
    reason_codes: list[ReasonCode],
    allowlist: list[str],
    scout_type: str,
) -> None:
    """Raise ReasonCodeAllowlistError if any code is not in the allowlist.

    If allowlist is empty, all codes pass (agent has no restriction).
    """
    if not allowlist:
        return
    allowed_set = set(allowlist)
    bad = [rc.code for rc in reason_codes if rc.code not in allowed_set]
    if bad:
        raise ReasonCodeAllowlistError(bad, sorted(allowed_set), scout_type)


# ── Shared JSON-parse + validate helper (H3 / H4 reuse point) ────────────────

_M = TypeVar("_M", bound=BaseModel)


def validate_llm_json_emission(model_cls: type[_M], raw_text: str) -> _M:
    """Parse ``raw_text`` as JSON and validate against ``model_cls``.

    Raises:
        json.JSONDecodeError: if raw_text is not valid JSON.
        pydantic.ValidationError: if the parsed dict does not conform to model_cls.

    H3 (trajectory summarizer) and H4 (meeting summarizer) should import and
    call this helper instead of rolling their own json.loads + model(**data).
    Location: ``artemis.marketing.scout_schemas.validate_llm_json_emission``.
    """
    data = json.loads(raw_text)
    return model_cls.model_validate(data)
