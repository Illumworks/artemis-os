"""Tool: signal_queue.write

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories are available on first
``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, text

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.models import SignalQueue
from artemis.marketing.scout_intake import normalize_intake_payload
from artemis.tools.context import ToolContext
from artemis.tools.district_resolve import resolve_district
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="signal_queue.write",
    description=(
        "Write a signal to the qualification queue. The signal will be normalized "
        "and validated against the reason-code allowlist before insertion. Returns "
        "the new signal ID on success, or an error message if validation fails — "
        "the LLM may retry after correcting the payload."
    ),
    input_schema={
        "type": "object",
        "required": [
            "sourceType",
            "headline",
            "campaignFamily",
            "urgencyTier",
            "reasonCodes",
            "evidence",
        ],
        "properties": {
            "sourceType": {
                "type": "string",
                "enum": [
                    "manual",
                    "starbridge",
                    "news_article",
                    "board_minutes",
                    "state_doe",
                    "linkedin_post",
                ],
            },
            "headline": {"type": "string"},
            "campaignFamily": {
                "type": "string",
                "description": (
                    "Campaign family — one of: obc, dyslexia, biliteracy, hit, general_growth. "
                    "Josh-spec labels (e.g. 'Dyslexia / structured literacy', "
                    "'High-impact tutoring (HIT)') are also accepted and normalized to the slug."
                ),
            },
            "urgencyTier": {
                "type": "string",
                "enum": ["hot", "standard", "enrichment"],
                "description": (
                    "Urgency tier — one of: hot, standard, enrichment. "
                    "Spec §2 default urgencies and the qualifier suppress/boost "
                    "ladder use these three. The legacy slug 'low' is also "
                    "accepted and normalized to 'enrichment'."
                ),
            },
            "reasonCodes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "evidence": {"type": "string"},
            "districtId": {
                "type": "string",
                "description": (
                    "The school district this signal is about, by NAME as commonly written "
                    "(e.g. 'Fort Bend ISD', 'Grosse Pointe Schools', 'St. Louis Public Schools'). "
                    "ALWAYS populate when the signal concerns a specific district — the system "
                    "resolves it to the canonical NCES district and its size tier (D1-D4). "
                    "Leave empty only for genuinely state/federal-level signals with no single district."
                ),
            },
            "stateCode": {
                "type": "string",
                "description": (
                    "2-letter US state of the district (e.g. 'TX', 'FL'). Populate whenever known; "
                    "it disambiguates districts that share a name across states."
                ),
            },
            "sourceUrl": {"type": "string"},
            "whyFlagged": {"type": "string"},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith("marketing.scout."):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot write signals"

        slug = ctx.agent_id.rsplit(".", 1)[-1]
        spec = parse_spec()
        allowed_codes = {rc.code for rc in reason_codes_for_scout(spec, slug)}

        submitted_codes: list[str] = []
        raw_codes = arguments.get("reasonCodes") or []
        if isinstance(raw_codes, list):
            for item in raw_codes:
                if isinstance(item, str):
                    submitted_codes.append(item)
                elif isinstance(item, dict):
                    submitted_codes.append(str(item.get("code", "")))

        for code in submitted_codes:
            if code and code not in allowed_codes:
                return (
                    f"VALIDATION_ERROR: reason code {code!r} not in this scout's allowlist "
                    f"{sorted(allowed_codes)}"
                )

        # Convert string codes → dict shape; strip anti-spoof fields before calling intake
        normalized_raw_codes: list[dict[str, str]] = [{"code": c} for c in submitted_codes if c]
        intake_payload = {
            k: v for k, v in arguments.items() if k not in ("discoveredBy", "scout_type")
        }
        intake_payload["reasonCodes"] = normalized_raw_codes

        try:
            normalized = normalize_intake_payload(intake_payload, scout_type=slug)
        except ValueError as exc:
            return f"VALIDATION_ERROR: {exc}"

        # ── Source-URL dedup (fallback for null-district / federal signals) ──────
        # If source_url is set, check for a recent non-archived row with the same URL.
        # District-based dedup in the qualifier suppress-stale rule handles non-null
        # districts; this catches the federal_funding case where district_id is null.
        if normalized.source_url:
            _dedup_statuses_excluded = ("archived", "rejected_hard_filter")
            existing_stmt = (
                select(SignalQueue.id)
                .where(SignalQueue.source_url == normalized.source_url)
                .where(SignalQueue.signal_status.notin_(_dedup_statuses_excluded))
                .where(SignalQueue.created_at >= text("now() - interval '30 days'"))
                .limit(1)
            )
            existing_result = await ctx.session.execute(existing_stmt)
            existing_id = existing_result.scalar_one_or_none()
            if existing_id is not None:
                logger.info(
                    "signal_queue.write: deduped agent=%s source_url=%s existing_id=%s",
                    ctx.agent_id,
                    normalized.source_url,
                    existing_id,
                )
                return json.dumps(
                    {
                        "signal_id": existing_id,
                        "status": "deduplicated",
                        "duplicate_of": existing_id,
                    }
                )

        row = SignalQueue(
            source_type=normalized.source_type,
            headline=normalized.headline,
            campaign_family=normalized.campaign_family,
            urgency_tier=normalized.urgency_tier,
            reason_codes=normalized.reason_codes,
            district_id=normalized.district,
            state=normalized.state_code,
            discovered_by=normalized.discovered_by,
            signal_status="pending_qualification",
            source_url=normalized.source_url,
            summary=normalized.verbatim_snippet or normalized.headline,
            pipeline_run_id=ctx.pipeline_run_id,
            provenance={
                "agent_run_id": ctx.agent_run_id,
                "agent_id": ctx.agent_id,
                "why_flagged": normalized.why_flagged,
            },
        )
        ctx.session.add(row)
        await ctx.session.flush()

        # DIST3 — intake-time district resolution hook.
        # Attempt to resolve the raw district_id text to a canonical districts FK.
        # On confident match: set resolved_district_id. On no-match: leave NULL.
        # The agent never fabricates a district; a data gap stays NULL.
        if normalized.district:
            resolve_result = await resolve_district(
                ctx.session, normalized.district, normalized.state_code
            )
            if resolve_result.matched and resolve_result.district_id is not None:
                row.resolved_district_id = resolve_result.district_id
                await ctx.session.flush()
                logger.info(
                    "signal_queue.write: resolved district %r → id=%s (confidence=%.2f)",
                    normalized.district,
                    resolve_result.district_id,
                    resolve_result.confidence,
                )
            else:
                logger.info(
                    "signal_queue.write: district %r unresolved (%s) — leaving NULL",
                    normalized.district,
                    resolve_result.message,
                )

        logger.info(
            "signal_queue.write: agent=%s signal_id=%s status=pending_qualification",
            ctx.agent_id,
            row.id,
        )
        return json.dumps({"signal_id": row.id, "status": "written"})

    return (_DEF, _impl)


register_tool("signal_queue.write", _factory)
