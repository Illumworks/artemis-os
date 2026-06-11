"""Content-agent MCP tools for campaign briefs, approved assets, and Studio enqueue."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import CampaignBrief, ContentAsset
from artemis.marketing.repository import list_approved_content_assets
from artemis.marketing.state_machine import DeliverableState, transition
from artemis.marketing.writing_studio.invoke import create_draft_from_candidate
from artemis.pipelines.repository import get_pipeline_run
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool
from artemis.writing_rules.models import WritingProfile

logger = logging.getLogger(__name__)

_MARKETING_PREFIX = "marketing."


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


async def _valid_profile_slugs(ctx: ToolContext) -> set[str]:
    rows = (
        await ctx.session.execute(
            select(WritingProfile)
            .where(WritingProfile.status != "archived")
            .order_by(WritingProfile.id)
        )
    ).scalars()
    return {_slug(row.name) for row in rows}


def _asset_json(asset: ContentAsset) -> dict[str, Any]:
    metadata = asset.asset_metadata or {}
    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "status": asset.status,
        "summary": asset.summary,
        "metadata": metadata,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


_ENQUEUE_DEF = Tool(
    name="writing_studio.enqueue",
    description=(
        "Push a content draft into the Writing Studio for Angela/Julie/Olivia review. "
        "Creates a deliverable, attaches metadata bundle, fires the appropriate events. "
        "Returns the deliverable_id."
    ),
    input_schema={
        "type": "object",
        "required": ["campaign_brief_id", "draft_title", "draft_body", "voice_profile_slug"],
        "properties": {
            "campaign_brief_id": {"type": "integer"},
            "draft_title": {"type": "string", "minLength": 1, "maxLength": 200},
            "draft_body": {"type": "string", "minLength": 1, "maxLength": 20000},
            "voice_profile_slug": {"type": "string"},
            "asset_refs": {"type": "array", "items": {"type": "string"}},
            "context_summary": {"type": "string", "maxLength": 2000},
        },
    },
)


def _enqueue_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot enqueue writing drafts"

        brief_id = arguments.get("campaign_brief_id")
        title = arguments.get("draft_title")
        body = arguments.get("draft_body")
        voice_slug = arguments.get("voice_profile_slug")
        if not isinstance(brief_id, int):
            return "VALIDATION_ERROR: 'campaign_brief_id' is required and must be an integer"
        if not isinstance(title, str) or not title.strip():
            return "VALIDATION_ERROR: 'draft_title' is required and must be a non-empty string"
        if not isinstance(body, str) or not body.strip():
            return "VALIDATION_ERROR: 'draft_body' is required and must be a non-empty string"
        if not isinstance(voice_slug, str) or not voice_slug.strip():
            return (
                "VALIDATION_ERROR: 'voice_profile_slug' is required and must be a non-empty string"
            )

        valid_slugs = await _valid_profile_slugs(ctx)
        if voice_slug not in valid_slugs:
            return (
                f"VALIDATION_ERROR: unknown voice_profile_slug {voice_slug!r}. "
                f"Valid slugs: {', '.join(sorted(valid_slugs)) or '(none)'}"
            )

        brief = await ctx.session.get(CampaignBrief, brief_id)
        if brief is None:
            return f"NOT_FOUND: no campaign brief with id={brief_id}"

        candidate_id = brief.candidate_id
        if ctx.pipeline_run_id:
            try:
                run = await get_pipeline_run(ctx.session, ctx.pipeline_run_id)
            except ValueError:
                return f"NOT_FOUND: no pipeline run with id={ctx.pipeline_run_id}"
            if run.target_candidate_id is not None:
                candidate_id = run.target_candidate_id
                if brief.candidate_id != run.target_candidate_id:
                    return (
                        "VALIDATION_ERROR: "
                        f"campaign_brief_id={brief_id} belongs to candidate "
                        f"{brief.candidate_id}, but pipeline run {ctx.pipeline_run_id} "
                        f"targets candidate {run.target_candidate_id}"
                    )

        asset_bundle: list[dict[str, Any]] = []
        raw_refs = arguments.get("asset_refs") or []
        if isinstance(raw_refs, list):
            ids = [int(ref) for ref in raw_refs if str(ref).isdigit()]
            if ids:
                assets = (
                    await ctx.session.execute(select(ContentAsset).where(ContentAsset.id.in_(ids)))
                ).scalars()
                asset_bundle = [
                    {
                        "id": asset.id,
                        "title": f"Asset {asset.id}",
                        "assetType": asset.asset_type,
                        "summary": asset.summary or "",
                        "sourceUrl": (asset.asset_metadata or {}).get("source_url"),
                    }
                    for asset in assets
                ]

        draft = await create_draft_from_candidate(
            ctx.session,
            candidate_id,
            brief_payload=brief.content if isinstance(brief.content, dict) else {},
            asset_context_bundle=asset_bundle,
        )
        deliverable = await transition(
            ctx.session,
            "deliverable",
            draft.id,
            DeliverableState.draft_ready,
            actor=ctx.agent_id,
            reason="writing_studio.enqueue",
        )
        metadata = dict(deliverable.deliverable_metadata or {})
        composed_body = body.strip()
        composed_at = datetime.now(UTC).isoformat()
        metadata.update(
            {
                "draftTitle": title.strip(),
                # draftBody retained for backwards compat (Slack gate cards still read it).
                "draftBody": composed_body,
                # Canonical body location: versions[0].content is where
                # _latest_draft_content (compose_engine) and _serialize_deliverable_detail
                # (writing_studio route) both read from when live_content is absent.
                "versions": [
                    {
                        "id": "v1",
                        "version_number": 1,
                        "content": composed_body,
                        "created_at": composed_at,
                        "source": "pipeline_generated",
                    }
                ],
                "voiceProfileSlug": voice_slug,
                "campaignBriefId": brief.id,
                "contextSummary": arguments.get("context_summary"),
                "agentRunId": ctx.agent_run_id,
            }
        )
        deliverable.deliverable_metadata = metadata
        await ctx.session.flush()
        return json.dumps({"deliverable_id": draft.id, "status": deliverable.status})

    return (_ENQUEUE_DEF, _impl)


register_tool("writing_studio.enqueue", _enqueue_factory)


_LIST_ASSETS_DEF = Tool(
    name="content_registry.list_approved_assets",
    description=(
        "List approved content assets available for inclusion in a draft. "
        "Filterable by campaign_family. Approved = status='approved'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "campaign_family": {"type": "string"},
            "limit": {"type": "integer", "default": 50, "maximum": 200},
        },
    },
)


def _list_assets_factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        try:
            raw_limit = arguments.get("limit", 50)
            limit = raw_limit if isinstance(raw_limit, int) else 50
            family = arguments.get("campaign_family")
            assets = await list_approved_content_assets(
                ctx.session,
                campaign_family=family if isinstance(family, str) and family else None,
                limit=limit,
            )
            return json.dumps({"assets": [_asset_json(asset) for asset in assets]})
        except Exception:
            logger.warning("content_registry.list_approved_assets failed", exc_info=True)
            return json.dumps({"assets": []})

    return (_LIST_ASSETS_DEF, _impl)


register_tool("content_registry.list_approved_assets", _list_assets_factory)
