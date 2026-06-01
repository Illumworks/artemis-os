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


def _ctx(session: AsyncSession, agent_id: str = _ADAPTER) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-cc12-test",
        pipeline_run_id=None,
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
    session: AsyncSession, name: str, args: dict[str, Any], agent: str = _ADAPTER
) -> str:
    factory = get_factory(name)
    assert factory is not None
    _, impl = factory(_ctx(session, agent))
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
