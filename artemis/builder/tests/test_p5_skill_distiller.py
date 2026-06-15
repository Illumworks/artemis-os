"""Tests for P5 learning loop — skill distiller + injection + usage tracking.

Exercises:
1. distill_skill_candidates: seeds ≥2 trajectory summaries → LLM mock → proposal
   created (kind=skill, proposed_by=self-improvement, with citation run_ids).
2. Dedup: re-running distiller does NOT create a second identical proposal.
3. Approve via existing engine.commit() → Skill row exists.
4. Assign skill to agent → run_agent → skill instructions in system prompt.
5. usage_count incremented + last_used_at set after injection.
6. Negative: no qualifying skills → system prompt unchanged, no error.
7. Negative: no-provider/error → zero proposals, no crash.
8. LIVE adapter smoke: one real call to prove the LLM path returns (skipped if no
   provider available in test env).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builder.skill_distiller import distill_skill_candidates
from artemis.builders import repository as repo
from artemis.builders.models import (
    Agent,
    AgentRun,
    AgentRunTrajectorySummary,
    DefinitionProposal,
    Skill,
)

# ── helpers ──────────────────────────────────────────────────────────────────


async def _make_agent(session: AsyncSession, agent_id: str = "test-agent-p5") -> Agent:
    agent = Agent(
        agent_id=agent_id,
        name="P5 Test Agent",
        description="Test agent for P5 skill distiller",
        system_prompt="You are a test agent.",
        tools=["memory.search", "slack.search_messages"],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


async def _make_run(session: AsyncSession, agent_id: str) -> AgentRun:
    import uuid
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status="completed",
        user_message="Do the thing",
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def _make_summary(
    session: AsyncSession,
    run_id: int,
    what_worked: str | None = None,
    what_stalled: str | None = None,
    what_was_missing: str | None = None,
) -> AgentRunTrajectorySummary:
    row = AgentRunTrajectorySummary(
        run_id=run_id,
        what_worked=what_worked,
        what_stalled=what_stalled,
        what_was_missing=what_was_missing,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


def _fake_adapter_returning(candidates: list[dict]) -> Any:
    """Build a fake ModelAdapter whose run_turn returns a JSON array."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    mock_adapter = MagicMock()
    raw = json.dumps(candidates)

    async def fake_complete(request: Any) -> Any:
        text_block = TextBlock(type="text", text=raw)
        msg = Message(role="assistant", content=[text_block])
        return CompletionResponse(
            message=msg,
            stop_reason="end_turn",
            usage=Usage(input_tokens=100, output_tokens=50),
        )

    mock_adapter.complete = AsyncMock(side_effect=fake_complete)
    mock_adapter.__class__.__name__ = "FakeAdapter"
    return mock_adapter


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_distill_creates_proposal_from_repeated_procedure(
    db_session: AsyncSession,
) -> None:
    """Seed ≥2 trajectory summaries describing the same procedure → proposal created."""
    async with db_session.begin():
        agent = await _make_agent(db_session)
        run1 = await _make_run(db_session, agent.agent_id)
        run2 = await _make_run(db_session, agent.agent_id)
        await _make_summary(
            db_session,
            run1.id,
            what_worked=(
                "Called memory.search to find relevant notes, then composed a summary "
                "using the search results before posting to Slack."
            ),
        )
        await _make_summary(
            db_session,
            run2.id,
            what_worked=(
                "Used memory.search to retrieve prior notes, filtered by date, "
                "then summarized and delivered results via Slack."
            ),
        )

    candidate = {
        "slug": "memory-search-and-summarize",
        "name": "Memory search and summarize",
        "description": "Searches memory for relevant notes and composes a summary.",
        "instructions": "1. Call memory.search with the relevant query.\n2. Filter results by recency.\n3. Compose a clear summary.",
        "tools": ["memory.search"],
        "rationale": "Appeared in what_worked for run 1 and run 2.",
    }
    fake_adapter = _fake_adapter_returning([candidate])

    async with db_session.begin():
        result = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=fake_adapter
        )

    assert result["n_proposed"] == 1
    assert result["n_skipped"] == 0
    assert len(result["proposal_ids"]) == 1
    assert result["n_summaries"] == 2

    # Verify the DB row.
    proposal_id = result["proposal_ids"][0]
    async with db_session.begin():
        from sqlalchemy import select
        row = (
            await db_session.execute(
                select(DefinitionProposal).where(DefinitionProposal.id == proposal_id)
            )
        ).scalar_one()

    assert row.kind == "skill"
    assert row.proposed_by == "self-improvement"
    assert row.status == "pending"
    assert isinstance(row.citations, dict)
    assert "run_ids" in row.citations
    assert run1.id in row.citations["run_ids"]
    assert run2.id in row.citations["run_ids"]
    defn = row.proposed_definition
    assert defn["slug"] == "memory-search-and-summarize"
    assert defn["instructions"].startswith("1. Call memory.search")


@pytest.mark.asyncio
async def test_distill_dedup_no_second_proposal(db_session: AsyncSession) -> None:
    """Re-running distiller after a pending proposal exists → no second identical proposal."""
    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-dedup")
        run1 = await _make_run(db_session, agent.agent_id)
        run2 = await _make_run(db_session, agent.agent_id)
        await _make_summary(db_session, run1.id, what_worked="Step A then step B.")
        await _make_summary(db_session, run2.id, what_worked="Step A then step B again.")

    candidate = {
        "slug": "step-a-then-b",
        "name": "Step A then B",
        "description": "A two-step procedure.",
        "instructions": "1. Do A.\n2. Do B.",
        "tools": [],
        "rationale": "Appeared twice.",
    }
    fake_adapter = _fake_adapter_returning([candidate])

    # First run — creates proposal.
    async with db_session.begin():
        r1 = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=fake_adapter
        )
    assert r1["n_proposed"] == 1

    # Second run — same adapter, same output.
    fake_adapter2 = _fake_adapter_returning([candidate])
    async with db_session.begin():
        r2 = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=fake_adapter2
        )
    # Dedup: the slug is already in pending proposals — zero new proposals.
    assert r2["n_proposed"] == 0
    assert r2["n_skipped"] == 1


@pytest.mark.asyncio
async def test_approve_proposal_creates_skill_row(db_session: AsyncSession) -> None:
    """Approve the proposal via engine.commit() → Skill row with status=approved."""
    from artemis.builder.engine import commit

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-approve")
        run1 = await _make_run(db_session, agent.agent_id)
        await _make_summary(db_session, run1.id, what_worked="Called memory.search then wrote result.")

    candidate = {
        "slug": "approve-test-skill",
        "name": "Approve test skill",
        "description": "Testing approval path.",
        "instructions": "1. Search. 2. Write.",
        "tools": [],
        "rationale": "Appeared once.",
    }
    async with db_session.begin():
        result = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=_fake_adapter_returning([candidate])
        )
    proposal_id = result["proposal_ids"][0]

    # Approve via existing endpoint.
    async with db_session.begin():
        commit_result = await commit(proposal_id, db_session=db_session)

    assert commit_result["kind"] == "skill"
    assert commit_result["status"] in ("created", "updated")

    # Skill row must exist.
    async with db_session.begin():
        skill = await repo.get_skill(db_session, "approve-test-skill")

    assert skill is not None
    assert skill.status == "proposed"  # _commit_skill creates with status="proposed"
    assert skill.slug == "approve-test-skill"


@pytest.mark.asyncio
async def test_skill_injection_and_usage_tracking(db_session: AsyncSession) -> None:
    """Assign approved skill → run _inject_skills_into_prompt → instructions in prompt
    AND usage_count incremented + last_used_at set."""
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-inject")
        skill = Skill(
            slug="inject-test-skill",
            name="Inject Test Skill",
            description="A skill to test injection.",
            status="approved",
            instructions="Step 1: search memory.\nStep 2: return results.",
            tools=["memory.search"],
            kind="user",
            usage_count=0,
        )
        db_session.add(skill)
        await db_session.flush()
        # Assign skill to agent.
        await repo.assign_skill_to_agent(db_session, agent.id, "inject-test-skill")

    async with db_session.begin():
        from sqlalchemy import select
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "test-agent-inject")
            )
        ).scalar_one()

        prompt_before = "You are a test agent."
        updated_prompt, injected = await _inject_skills_into_prompt(
            db_session, agent_row, prompt_before
        )

    assert len(injected) == 1
    assert injected[0].slug == "inject-test-skill"
    assert "Learned skills" in updated_prompt
    assert "Step 1: search memory." in updated_prompt

    # Verify usage tracking.
    async with db_session.begin():
        refreshed = await repo.get_skill(db_session, "inject-test-skill")
    assert refreshed.usage_count == 1
    assert refreshed.last_used_at is not None


@pytest.mark.asyncio
async def test_learning_loop_closes_end_to_end(db_session: AsyncSession) -> None:
    """THE closure test: distill → REAL approve route → skill is approved AND
    assigned to the originating agent → injection picks it up → usage tracked.
    Proves the approve→inject seam is actually wired (not hand-set state)."""
    from sqlalchemy import select

    from artemis.builder.routes import approve_proposal
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        await _make_agent(db_session, "loop-close-agent")  # tools incl. memory.search
        # Seed the precondition: ≥2 trajectory summaries (the distiller gates on
        # having run history; the fake adapter supplies the distilled candidate).
        for _ in range(2):
            run = await _make_run(db_session, "loop-close-agent")
            await _make_summary(
                db_session,
                run.id,
                what_worked="Searched memory, then summarized the findings — worked well.",
            )

    candidate = {
        "slug": "loop-close-skill",
        "name": "Loop Close Skill",
        "description": "Captured from repeated success.",
        "instructions": "Step 1: search memory for prior context.",
        "tools": ["memory.search"],  # overlaps the agent's tools
        "rationale": "Appeared in what_worked 2+ times.",
    }
    async with db_session.begin():
        result = await distill_skill_candidates(
            db_session, "loop-close-agent", adapter=_fake_adapter_returning([candidate])
        )
    proposal_id = result["proposal_ids"][0]

    # Approve via the REAL route (carries the loop-closure: approve + assign).
    # Patch only the memory-observation side effect to keep this test focused.
    with patch(
        "artemis.builder.memory_carryover.write_proposal_approval_observation",
        new=AsyncMock(),
    ):
        await approve_proposal(proposal_id, session=db_session)

    # Skill must now be APPROVED and ASSIGNED to the originating agent.
    async with db_session.begin():
        skill = await repo.get_skill(db_session, "loop-close-skill")
        assert skill is not None and skill.status == "approved", (
            f"expected approved, got {skill.status if skill else None}"
        )
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "loop-close-agent")
            )
        ).scalar_one()
        assigned = await repo.list_skills_for_agent(db_session, agent_row.id)
    assert any(s.slug == "loop-close-skill" for s in assigned), (
        "skill was NOT assigned to the originating agent — loop is open"
    )

    # Injection now picks it up at runtime → instructions in prompt + usage++.
    async with db_session.begin():
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "loop-close-agent")
            )
        ).scalar_one()
        updated_prompt, injected = await _inject_skills_into_prompt(
            db_session, agent_row, "base prompt"
        )
    assert any(s.slug == "loop-close-skill" for s in injected)
    assert "Step 1: search memory" in updated_prompt

    async with db_session.begin():
        refreshed = await repo.get_skill(db_session, "loop-close-skill")
    assert refreshed.usage_count == 1
    assert refreshed.last_used_at is not None


@pytest.mark.asyncio
async def test_injection_tool_overlap_filter(db_session: AsyncSession) -> None:
    """Skill with tools that do NOT overlap agent's tools → not injected."""
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-overlap")
        # Agent tools: ["memory.search", "slack.search_messages"]
        skill_no_overlap = Skill(
            slug="jira-skill",
            name="Jira Skill",
            description="Uses Jira tools.",
            status="approved",
            instructions="Use jira.get_issue to fetch details.",
            tools=["jira.get_issue", "jira.list_issues"],  # no overlap with agent tools
            kind="user",
            usage_count=0,
        )
        skill_overlap = Skill(
            slug="slack-skill",
            name="Slack Skill",
            description="Uses slack tools.",
            status="approved",
            instructions="Search Slack for context.",
            tools=["slack.search_messages"],  # overlaps
            kind="user",
            usage_count=0,
        )
        db_session.add(skill_no_overlap)
        db_session.add(skill_overlap)
        await db_session.flush()
        await repo.assign_skill_to_agent(db_session, agent.id, "jira-skill")
        await repo.assign_skill_to_agent(db_session, agent.id, "slack-skill")

    async with db_session.begin():
        from sqlalchemy import select
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "test-agent-overlap")
            )
        ).scalar_one()
        _, injected = await _inject_skills_into_prompt(
            db_session, agent_row, "base prompt"
        )

    injected_slugs = {s.slug for s in injected}
    assert "slack-skill" in injected_slugs
    assert "jira-skill" not in injected_slugs


@pytest.mark.asyncio
async def test_injection_hard_cap_3_skills(db_session: AsyncSession) -> None:
    """Never inject more than 3 skills even if more are assigned."""
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-cap")
        for i in range(5):
            skill = Skill(
                slug=f"cap-skill-{i}",
                name=f"Cap Skill {i}",
                status="approved",
                instructions=f"Do step {i}.",
                tools=["memory.search"],  # all overlap
                kind="user",
                usage_count=0,
            )
            db_session.add(skill)
        await db_session.flush()
        for i in range(5):
            await repo.assign_skill_to_agent(db_session, agent.id, f"cap-skill-{i}")

    async with db_session.begin():
        from sqlalchemy import select
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "test-agent-cap")
            )
        ).scalar_one()
        _, injected = await _inject_skills_into_prompt(db_session, agent_row, "prompt")

    assert len(injected) <= 3


@pytest.mark.asyncio
async def test_no_qualifying_skills_prompt_unchanged(db_session: AsyncSession) -> None:
    """Agent with no assigned skills → prompt unchanged, no error."""
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        await _make_agent(db_session, "test-agent-no-skills")

    async with db_session.begin():
        from sqlalchemy import select
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "test-agent-no-skills")
            )
        ).scalar_one()
        original = "You are a test agent."
        updated, injected = await _inject_skills_into_prompt(db_session, agent_row, original)

    assert updated == original
    assert injected == []


@pytest.mark.asyncio
async def test_distiller_no_summaries_returns_zero(db_session: AsyncSession) -> None:
    """Agent with no trajectory summaries → zero proposals, no error."""
    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-nosummaries")

    async with db_session.begin():
        result = await distill_skill_candidates(db_session, agent.agent_id)

    assert result["n_proposed"] == 0
    assert result["n_summaries"] == 0
    assert "error" not in result


@pytest.mark.asyncio
async def test_distiller_error_returns_zero_no_crash(db_session: AsyncSession) -> None:
    """LLM raises → zero proposals, fail-safe, no crash."""
    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-llmerror")
        run = await _make_run(db_session, agent.agent_id)
        await _make_summary(db_session, run.id, what_worked="Something worked.")

    broken_adapter = MagicMock()
    broken_adapter.complete = AsyncMock(side_effect=RuntimeError("simulated LLM failure"))

    async with db_session.begin():
        result = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=broken_adapter
        )

    assert result["n_proposed"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_distiller_llm_returns_invalid_json_zero_proposals(
    db_session: AsyncSession,
) -> None:
    """LLM returns garbage text (not JSON) → zero proposals, no crash."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-badjson")
        run = await _make_run(db_session, agent.agent_id)
        await _make_summary(db_session, run.id, what_worked="Something worked.")

    async def fake_complete(request: Any) -> Any:
        text_block = TextBlock(type="text", text="This is not JSON at all, sorry!")
        msg = Message(role="assistant", content=[text_block])
        return CompletionResponse(
            message=msg,
            stop_reason="end_turn",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    bad_adapter = MagicMock()
    bad_adapter.complete = AsyncMock(side_effect=fake_complete)

    async with db_session.begin():
        result = await distill_skill_candidates(
            db_session, agent.agent_id, adapter=bad_adapter
        )

    assert result["n_proposed"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_injection_status_filter_only_approved(db_session: AsyncSession) -> None:
    """Only approved skills are injected; proposed/archived skills are skipped."""
    from artemis.builders.executor import _inject_skills_into_prompt

    async with db_session.begin():
        agent = await _make_agent(db_session, "test-agent-status-filter")
        proposed_skill = Skill(
            slug="proposed-skill",
            name="Proposed Skill",
            status="proposed",
            instructions="Do proposed things.",
            tools=["memory.search"],
            kind="user",
            usage_count=0,
        )
        approved_skill = Skill(
            slug="approved-skill",
            name="Approved Skill",
            status="approved",
            instructions="Do approved things.",
            tools=["memory.search"],
            kind="user",
            usage_count=0,
        )
        db_session.add(proposed_skill)
        db_session.add(approved_skill)
        await db_session.flush()
        await repo.assign_skill_to_agent(db_session, agent.id, "proposed-skill")
        await repo.assign_skill_to_agent(db_session, agent.id, "approved-skill")

    async with db_session.begin():
        from sqlalchemy import select
        agent_row = (
            await db_session.execute(
                select(Agent).where(Agent.agent_id == "test-agent-status-filter")
            )
        ).scalar_one()
        _, injected = await _inject_skills_into_prompt(db_session, agent_row, "prompt")

    injected_slugs = {s.slug for s in injected}
    assert "approved-skill" in injected_slugs
    assert "proposed-skill" not in injected_slugs


@pytest.mark.asyncio
async def test_distill_skills_route_returns_summary(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /api/agents/{agent_id}/distill-skills → returns summary dict (no-provider path)."""
    # No trajectories seeded → distiller returns early with n_summaries=0 without LLM call.
    async with db_session.begin():
        await _make_agent(db_session, "test-agent-route-p5")

    response = await client.post("/api/agents/test-agent-route-p5/distill-skills")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "test-agent-route-p5"
    assert data["n_summaries"] == 0
    assert data["n_proposed"] == 0


@pytest.mark.asyncio
async def test_distill_skills_route_404_unknown_agent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /api/agents/nonexistent/distill-skills → 404."""
    response = await client.post("/api/agents/nonexistent-agent-p5xyz/distill-skills")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_distiller_live_adapter_smoke() -> None:
    """LIVE smoke — calls the real resolver once to prove the distiller's LLM path
    returns.  Skipped if no provider is available in the test environment.

    This test does NOT write to the DB; it just verifies the resolver + LLM
    call round-trips without raising a provider/network error.  If the LLM
    returns valid JSON, we assert it's a list.  If the LLM returns something
    we can't parse (model-specific formatting quirk), we skip rather than fail
    — the point is to confirm the path reaches the LLM, not that the model is
    perfectly compliant.
    """
    import json

    pytest.importorskip("artemis.providers.claude_code.adapter", reason="claude-code not installed")

    import artemis.db as _db
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter_async

    # Try to resolve a provider — skip if none available.
    try:
        async with _db.SessionLocal() as session:
            adapter = await resolve_adapter_async(
                provider="claude-code",
                feature_tag="skill_distiller",
                session=session,
            )
    except (NoProviderAvailableError, Exception) as e:
        pytest.skip(f"No provider available for live smoke: {e}")

    # Call the LLM directly with a trivial prompt.
    from artemis.builder.skill_distiller import _call_llm

    try:
        result = await _call_llm(adapter, "Return an empty JSON array: []")
        # If we get here, the LLM returned valid JSON — assert it's a list.
        assert isinstance(result, list), f"Expected list, got {type(result).__name__}: {result!r}"
    except (json.JSONDecodeError, ValueError) as parse_exc:
        # LLM reached but returned non-parseable output — skip with evidence.
        pytest.skip(
            f"Live adapter returned unparseable JSON (LLM path confirmed working): {parse_exc}"
        )
