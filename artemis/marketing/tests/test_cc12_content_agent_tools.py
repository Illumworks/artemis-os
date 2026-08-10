"""CC12 — content-agent tools close the pipeline → Writing Studio handoff."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
import artemis.tools  # noqa: F401
import artemis.tools.models  # noqa: F401
import artemis.writing_rules.models  # noqa: F401
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as builder_repo
from artemis.builders.executor import run_agent
from artemis.marketing.models import (
    CampaignBrief,
    CampaignCandidate,
    CampaignDeliverable,
    ContentAsset,
)
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents
from artemis.pipelines import repository as pipeline_repo
from artemis.tools.context import ToolContext
from artemis.tools.registry import get_factory, known_tool_names
from artemis.writing_rules.models import WritingProfile

_ADAPTER = "marketing.content.writing_studio_adapter"
_ASSEMBLER = "marketing.content.brief_assembler"
_ASSET_SELECTOR = "marketing.content.asset_selector"


@pytest.fixture(autouse=True)
async def _clean_cc12_tables(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "TRUNCATE tool_invocations, campaign_state_transitions, approvals, "
            "campaign_deliverables, content_asset_links, content_assets, campaign_briefs, "
            "campaign_candidate_signals, "
            "campaign_candidates, writing_sources, writing_examples, writing_rules, "
            "writing_folders, writing_profiles, agent_context, "
            "agent_run_trajectory_summaries, definition_proposals, agent_runs, "
            "agent_skills, agents RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()


def _ctx(
    session: AsyncSession,
    agent_id: str = _ADAPTER,
    *,
    pipeline_run_id: str | None = None,
) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-cc12-test",
        pipeline_run_id=pipeline_run_id,
    )


async def _fixture_brief(session: AsyncSession) -> CampaignBrief:
    session.add(WritingProfile(name="Amira Marketing Voice", status="active"))
    candidate = CampaignCandidate(campaign_family="obc")
    session.add(candidate)
    await session.flush()
    brief = CampaignBrief(
        candidate_id=candidate.id,
        content={"audience": "district leaders", "offer": "literacy acceleration"},
        generated_by=_ASSEMBLER,
    )
    session.add(brief)
    await session.flush()
    return brief


async def _call_tool(
    session: AsyncSession,
    name: str,
    args: dict[str, Any],
    agent: str = _ADAPTER,
    *,
    pipeline_run_id: str | None = None,
) -> str:
    factory = get_factory(name)
    assert factory is not None
    _, impl = factory(_ctx(session, agent, pipeline_run_id=pipeline_run_id))
    return await impl(args)


@pytest.mark.asyncio
async def test_writing_studio_enqueue_creates_deliverable(db_session: AsyncSession) -> None:
    brief = await _fixture_brief(db_session)
    result = json.loads(
        await _call_tool(
            db_session,
            "writing_studio.enqueue",
            {
                "campaign_brief_id": brief.id,
                "draft_title": "OBC follow-up",
                "draft_body": "Draft body",
                "voice_profile_slug": "amira-marketing-voice",
            },
        )
    )
    row = await db_session.get(CampaignDeliverable, result["deliverable_id"])
    assert row is not None
    assert row.status == "draft_ready"
    assert row.deliverable_metadata["draftTitle"] == "OBC follow-up"


@pytest.mark.asyncio
async def test_writing_studio_enqueue_binds_to_pipeline_target_candidate(
    db_session: AsyncSession,
) -> None:
    brief = await _fixture_brief(db_session)
    pipeline = await pipeline_repo.create_pipeline(
        db_session, name="CC12 Pipeline", nodes=[], edges=[]
    )
    run = await pipeline_repo.create_pipeline_run(
        db_session,
        pipeline_id=pipeline.id,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=brief.candidate_id,
    )

    result = json.loads(
        await _call_tool(
            db_session,
            "writing_studio.enqueue",
            {
                "campaign_brief_id": brief.id,
                "draft_title": "Target-bound draft",
                "draft_body": "Draft body",
                "voice_profile_slug": "amira-marketing-voice",
            },
            pipeline_run_id=run.id,
        )
    )

    row = await db_session.get(CampaignDeliverable, result["deliverable_id"])
    assert row is not None
    assert row.candidate_id == brief.candidate_id


@pytest.mark.asyncio
async def test_writing_studio_enqueue_rejects_brief_for_other_pipeline_candidate(
    db_session: AsyncSession,
) -> None:
    target_brief = await _fixture_brief(db_session)
    other_candidate = CampaignCandidate(campaign_family="obc")
    db_session.add(other_candidate)
    await db_session.flush()
    other_brief = CampaignBrief(
        candidate_id=other_candidate.id,
        content={"audience": "other"},
        generated_by=_ASSEMBLER,
    )
    db_session.add(other_brief)
    await db_session.flush()
    pipeline = await pipeline_repo.create_pipeline(
        db_session, name="CC12 Pipeline", nodes=[], edges=[]
    )
    run = await pipeline_repo.create_pipeline_run(
        db_session,
        pipeline_id=pipeline.id,
        status="queued",
        trigger="manual",
        triggered_by="test",
        target_candidate_id=target_brief.candidate_id,
    )

    result = await _call_tool(
        db_session,
        "writing_studio.enqueue",
        {
            "campaign_brief_id": other_brief.id,
            "draft_title": "Wrong brief",
            "draft_body": "Draft body",
            "voice_profile_slug": "amira-marketing-voice",
        },
        pipeline_run_id=run.id,
    )

    assert "VALIDATION_ERROR" in result
    rows = (await db_session.execute(select(CampaignDeliverable))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_writing_studio_enqueue_rejects_invalid_voice_slug(db_session: AsyncSession) -> None:
    brief = await _fixture_brief(db_session)
    result = await _call_tool(
        db_session,
        "writing_studio.enqueue",
        {
            "campaign_brief_id": brief.id,
            "draft_title": "Title",
            "draft_body": "Body",
            "voice_profile_slug": "wrong-voice",
        },
    )
    assert "VALIDATION_ERROR" in result
    assert "amira-marketing-voice" in result


@pytest.mark.asyncio
async def test_campaign_brief_read_returns_brief_by_id(db_session: AsyncSession) -> None:
    brief = await _fixture_brief(db_session)
    result = json.loads(
        await _call_tool(db_session, "campaign_brief.read", {"brief_id": brief.id}, _ASSEMBLER)
    )
    assert result["id"] == brief.id
    assert result["content"]["offer"] == "literacy acceleration"


@pytest.mark.asyncio
async def test_campaign_brief_read_missing_id_returns_not_found(db_session: AsyncSession) -> None:
    result = await _call_tool(db_session, "campaign_brief.read", {"brief_id": 999}, _ASSEMBLER)
    assert result == "NOT_FOUND: no campaign brief for brief id=999"


@pytest.mark.asyncio
async def test_list_approved_assets_returns_approved_only(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            ContentAsset(asset_type="snippet", status="approved", summary="A"),
            ContentAsset(asset_type="snippet", status="approved", summary="B"),
            ContentAsset(asset_type="snippet", status="draft", summary="C"),
        ]
    )
    await db_session.flush()
    result = json.loads(
        await _call_tool(db_session, "content_registry.list_approved_assets", {}, _ASSET_SELECTOR)
    )
    assert [a["summary"] for a in result["assets"]] == ["B", "A"]


@pytest.mark.asyncio
async def test_list_approved_assets_filters_by_campaign_family(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            ContentAsset(
                asset_type="snippet",
                status="approved",
                summary="OBC",
                asset_metadata={"campaign_family": "obc"},
            ),
            ContentAsset(
                asset_type="snippet",
                status="approved",
                summary="Other",
                asset_metadata={"campaign_family": "renewal"},
            ),
        ]
    )
    await db_session.flush()
    result = json.loads(
        await _call_tool(
            db_session,
            "content_registry.list_approved_assets",
            {"campaign_family": "obc"},
            _ASSET_SELECTOR,
        )
    )
    assert [a["summary"] for a in result["assets"]] == ["OBC"]


@pytest.mark.asyncio
async def test_asset_selector_seed_prunes_claude_complete(db_session: AsyncSession) -> None:
    await seed_marketing_agents(db_session)
    tools = (
        await db_session.execute(
            text("SELECT tools FROM agents WHERE agent_id = 'marketing.content.asset_selector'")
        )
    ).scalar_one()
    assert tools == ["content_registry.list_approved_assets"]


@pytest.mark.asyncio
async def test_e2e_agent_run_calls_tools_and_creates_deliverable(db_session: AsyncSession) -> None:
    brief = await _fixture_brief(db_session)
    db_session.add(
        ContentAsset(
            asset_type="snippet",
            status="approved",
            summary="Relevant proof point",
            asset_metadata={"campaign_family": "obc"},
        )
    )
    await builder_repo.create_agent(
        db_session,
        agent_id=_ADAPTER,
        name="Writing Studio Adapter",
        goal="Push draft",
        system_prompt="Call the content handoff tools.",
        tools=[
            "campaign_brief.read",
            "content_registry.list_approved_assets",
            "writing_studio.enqueue",
        ],
        model="claude-sonnet-4-6",
    )
    await db_session.commit()

    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[
                    ("t1", "campaign_brief.read", {"brief_id": brief.id}),
                    ("t2", "content_registry.list_approved_assets", {"campaign_family": "obc"}),
                    (
                        "t3",
                        "writing_studio.enqueue",
                        {
                            "campaign_brief_id": brief.id,
                            "draft_title": "Pipeline draft",
                            "draft_body": "Ready for review.",
                            "voice_profile_slug": "amira-marketing-voice",
                        },
                    ),
                ],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="Done.", stop_reason="end_turn"),
        ]
    )
    run = await run_agent(session=db_session, agent_id=_ADAPTER, model_adapter=adapter)
    rows = (await db_session.execute(select(CampaignDeliverable))).scalars().all()
    assert run.status == "completed"
    assert len(rows) == 1
    assert rows[0].deliverable_metadata["draftTitle"] == "Pipeline draft"


def test_registry_includes_cc12_tools() -> None:
    names = set(known_tool_names())
    assert {
        "writing_studio.enqueue",
        "campaign_brief.read",
        "content_registry.list_approved_assets",
    } <= names


# ── C3d: round-trip — pipeline body readable by _latest_draft_content ──────────


@pytest.mark.asyncio
async def test_enqueue_body_readable_by_latest_draft_content(db_session: AsyncSession) -> None:
    """Round-trip: writing_studio.enqueue body → _latest_draft_content returns it.

    This is the C3d regression guard.  Before the fix, the tool wrote only
    ``draftBody`` while ``_latest_draft_content`` reads ``live_content`` /
    ``versions[0].content`` — so the editor rendered an empty document.
    After the fix, the enqueue tool also populates ``versions[0].content``
    so both the LLM compose prompt and the detail-view serializer return
    the composed body correctly.
    """
    from artemis.marketing.writing_studio.compose_engine import _latest_draft_content

    brief = await _fixture_brief(db_session)
    composed_body = "This is the pipeline-generated draft body."

    result = json.loads(
        await _call_tool(
            db_session,
            "writing_studio.enqueue",
            {
                "campaign_brief_id": brief.id,
                "draft_title": "Round-trip test draft",
                "draft_body": composed_body,
                "voice_profile_slug": "amira-marketing-voice",
            },
        )
    )
    row = await db_session.get(CampaignDeliverable, result["deliverable_id"])
    assert row is not None

    # Verify versions[0].content was written.
    meta = row.deliverable_metadata or {}
    versions = meta.get("versions", [])
    assert len(versions) == 1, "Expected exactly one version entry after enqueue"
    assert versions[0]["content"] == composed_body
    assert versions[0]["source"] == "pipeline_generated"

    # The canonical reader must return the body (this is the failing path pre-fix).
    returned = _latest_draft_content(row)
    assert returned == composed_body, (
        f"_latest_draft_content returned {returned!r}; expected {composed_body!r}. "
        "The read↔write alignment is broken."
    )


@pytest.mark.asyncio
async def test_enqueue_keeps_draftbody_for_backwards_compat(db_session: AsyncSession) -> None:
    """Backwards compat: draftBody is still written (Slack gate cards read it)."""
    brief = await _fixture_brief(db_session)
    composed_body = "Draft body for backwards compat check."

    result = json.loads(
        await _call_tool(
            db_session,
            "writing_studio.enqueue",
            {
                "campaign_brief_id": brief.id,
                "draft_title": "Compat test",
                "draft_body": composed_body,
                "voice_profile_slug": "amira-marketing-voice",
            },
        )
    )
    row = await db_session.get(CampaignDeliverable, result["deliverable_id"])
    assert row is not None
    meta = row.deliverable_metadata or {}
    assert meta.get("draftBody") == composed_body


@pytest.mark.asyncio
async def test_backfill_legacy_deliverable_becomes_readable(db_session: AsyncSession) -> None:
    """Simulates the pre-fix write and verifies a manual versions backfill makes it readable.

    This mirrors the migration 0079 logic applied to any legacy row that only
    has draftBody (created before the fix) and verifies that after copying to
    versions, _latest_draft_content picks it up.
    """
    from artemis.marketing.writing_studio.compose_engine import _latest_draft_content

    # Create a legacy row (draftBody only, no versions).
    brief = await _fixture_brief(db_session)
    candidate_id = brief.candidate_id
    legacy_body = "Legacy pipeline-generated body without versions."
    legacy_meta: dict[str, Any] = {
        "draftBody": legacy_body,
        "draftTitle": "Legacy draft",
        "externalDraftId": "stub-legacy-1",
    }
    legacy_row = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id="stub-legacy-1",
        campaign_id="obc",
        status="draft_ready",
        deliverable_metadata=legacy_meta,
    )
    db_session.add(legacy_row)
    await db_session.flush()

    # Before backfill: _latest_draft_content returns empty (the pre-fix bug).
    content_before = _latest_draft_content(legacy_row)
    assert content_before == "", f"Pre-backfill content should be empty, got {content_before!r}"

    # Apply the backfill (mirrors the 0079 migration logic in Python).
    backfilled_meta = dict(legacy_meta)
    backfilled_meta["versions"] = [
        {
            "id": "v1",
            "version_number": 1,
            "content": legacy_body,
            "created_at": "2026-06-10T00:00:00+00:00",
            "source": "pipeline_generated",
        }
    ]
    legacy_row.deliverable_metadata = backfilled_meta
    await db_session.flush()

    # After backfill: _latest_draft_content returns the body.
    content_after = _latest_draft_content(legacy_row)
    assert content_after == legacy_body, (
        f"Post-backfill content should be {legacy_body!r}, got {content_after!r}"
    )
