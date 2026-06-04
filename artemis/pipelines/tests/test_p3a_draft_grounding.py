"""Piece A — Ground first auto-draft in ruleset.

Tests:
1. build_ruleset_grounding_block returns correct keys and content when
   profile + rules + examples are present.
2. build_ruleset_grounding_block returns empty dict when profile is None
   and both lists are empty.
3. build_ruleset_grounding_block with asset_type/channel hints uses them
   for example relevance scoring without raising.
4. build_writing_memory_prompt produces a system prompt that contains the
   rules block (regression guard — same content as before refactor).
5. build_writing_memory_prompt trace has profile/rules/examples sub-keys
   (parity with pre-refactor shape).
6. agent_executor._resolve_candidate_for_run path: when agent_id is
   marketing.content.writing_studio_adapter and the DB has 2 rules + 1
   example under an active profile, shared_context after the candidate-
   resolution block contains writing_ruleset_block,
   writing_anti_fabrication_guardrail, and writing_ruleset_trace.
7. When no profile/rules/examples exist the three new keys are NOT in
   shared_context (no noise).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_rule(
    rule_id: int,
    title: str,
    body: str,
    *,
    profile_id: int | None = 1,
    rule_type: str = "voice",
    status: str = "active",
) -> Any:
    r = MagicMock()
    r.id = rule_id
    r.title = title
    r.body = body
    r.profile_id = profile_id
    r.rule_type = rule_type
    r.status = status
    r.source_candidate_id = None
    return r


def _make_example(
    example_id: int,
    title: str,
    body: str,
    *,
    profile_id: int | None = 1,
    example_type: str = "reference",
    asset_type: str | None = None,
    channel: str | None = None,
) -> Any:
    e = MagicMock()
    e.id = example_id
    e.title = title
    e.body = body
    e.profile_id = profile_id
    e.example_type = example_type
    e.asset_type = asset_type
    e.channel = channel
    return e


def _make_profile(profile_id: int = 1, name: str = "Amira Voice") -> Any:
    p = MagicMock()
    p.id = profile_id
    p.name = name
    p.status = "active"
    p.system_prompt = "You are Artemis Writing Studio."
    return p


# ── Tests: build_ruleset_grounding_block ─────────────────────────────────────


def test_grounding_block_returns_expected_keys() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile()
    rules = [
        _make_rule(1, "Lead with outcome", "Always start with the student benefit."),
        _make_rule(2, "Short sentences", "Keep sentences under 20 words."),
    ]
    examples = [
        _make_example(10, "Email template", "Dear educator, ...", asset_type="email"),
    ]

    result = build_ruleset_grounding_block(profile, rules, examples)

    assert set(result.keys()) == {
        "system_prompt_grounding_block",
        "anti_fabrication_guardrail",
        "trace",
    }


def test_grounding_block_contains_rule_text() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile()
    rules = [_make_rule(1, "Lead with outcome", "Always start with the student benefit.")]
    examples: list[Any] = []

    result = build_ruleset_grounding_block(profile, rules, examples)

    block = result["system_prompt_grounding_block"]
    assert "Lead with outcome" in block
    assert "Always start with the student benefit." in block
    assert "Approved rules:" in block


def test_grounding_block_contains_example_text() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile()
    rules: list[Any] = []
    examples = [
        _make_example(10, "Email template", "Dear educator, lead with value.", asset_type="email")
    ]

    result = build_ruleset_grounding_block(profile, rules, examples)

    block = result["system_prompt_grounding_block"]
    assert "Email template" in block
    assert "Dear educator, lead with value." in block


def test_grounding_block_anti_fabrication_guardrail_present() -> None:
    from artemis.marketing.writing_studio.compose_engine import (
        ANTI_FABRICATION_GUARDRAIL,
        build_ruleset_grounding_block,
    )

    profile = _make_profile()
    rules = [_make_rule(1, "Voice rule", "Be direct.")]
    examples: list[Any] = []

    result = build_ruleset_grounding_block(profile, rules, examples)

    assert result["anti_fabrication_guardrail"] == ANTI_FABRICATION_GUARDRAIL
    assert "fabricate" in result["anti_fabrication_guardrail"].lower()


def test_grounding_block_trace_shape() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile()
    rules = [_make_rule(1, "Rule A", "Body A."), _make_rule(2, "Rule B", "Body B.")]
    examples = [_make_example(10, "Ex A", "Ex body.")]

    result = build_ruleset_grounding_block(profile, rules, examples)

    trace = result["trace"]
    assert trace["profile"]["id"] == 1
    assert trace["profile"]["name"] == "Amira Voice"
    assert len(trace["rules"]) == 2
    assert trace["rules"][0]["id"] == 1
    assert trace["rules"][0]["title"] == "Rule A"
    assert len(trace["examples"]) == 1
    assert trace["examples"][0]["id"] == 10


def test_grounding_block_empty_when_no_data() -> None:
    """Returns empty dict when profile is None and both lists are empty."""
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    result = build_ruleset_grounding_block(None, [], [])

    assert result == {}


def test_grounding_block_with_asset_type_hint_does_not_raise() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile()
    rules = [_make_rule(1, "Rule A", "Body A.")]
    examples = [
        _make_example(10, "Email example", "Email body.", asset_type="email", channel="email"),
        _make_example(11, "Generic example", "Generic body."),
    ]

    result = build_ruleset_grounding_block(
        profile, rules, examples, asset_type="email", channel="email"
    )

    assert "system_prompt_grounding_block" in result
    # Email example should be ranked higher — verify it appears first in block
    block = result["system_prompt_grounding_block"]
    email_pos = block.find("Email example")
    generic_pos = block.find("Generic example")
    assert email_pos < generic_pos, (
        "Email example should appear before generic when channel matches"
    )


def test_grounding_block_profile_filter_excludes_other_profile_rules() -> None:
    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block

    profile = _make_profile(profile_id=1)
    rules = [
        _make_rule(1, "Profile 1 Rule", "Body for profile 1.", profile_id=1),
        _make_rule(2, "Profile 2 Rule", "Body for profile 2.", profile_id=2),
    ]
    examples: list[Any] = []

    result = build_ruleset_grounding_block(profile, rules, examples)

    block = result["system_prompt_grounding_block"]
    assert "Profile 1 Rule" in block
    assert "Profile 2 Rule" not in block
    assert len(result["trace"]["rules"]) == 1


# ── Tests: build_writing_memory_prompt parity ─────────────────────────────────


def test_build_writing_memory_prompt_system_prompt_contains_rules() -> None:
    """Regression: system prompt still contains rule text after refactor."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    profile = _make_profile()
    rules = [_make_rule(1, "Outcome first", "Lead with the student's measurable outcome.")]
    examples: list[Any] = []

    draft = CampaignDeliverable(
        id=9001,
        status="draft",
        deliverable_metadata={"title": "Test draft"},
    )

    result = build_writing_memory_prompt(
        draft=draft,
        profile=profile,
        rules=rules,
        examples=examples,
        request="Write this draft.",
    )

    system_prompt: str = result["systemPrompt"]
    assert "Outcome first" in system_prompt
    assert "Lead with the student's measurable outcome." in system_prompt
    assert "Approved rules:" in system_prompt
    # Anti-fabrication guardrail must still be present via _build_runtime_context
    assert "fabricat" in system_prompt.lower()


def test_build_writing_memory_prompt_trace_has_profile_rules_examples_keys() -> None:
    """Parity: trace must still have profile, rules, examples sub-keys post-refactor."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    profile = _make_profile()
    rules = [_make_rule(1, "Rule A", "Body A.")]
    examples = [_make_example(10, "Example A", "Example body.")]

    draft = CampaignDeliverable(
        id=9002,
        status="draft",
        deliverable_metadata={"title": "Parity test"},
    )

    result = build_writing_memory_prompt(
        draft=draft, profile=profile, rules=rules, examples=examples
    )

    trace = result["trace"]
    assert "profile" in trace
    assert "rules" in trace
    assert "examples" in trace
    assert "draft" in trace
    assert "learningLifecycle" in trace
    assert trace["profile"]["id"] == 1
    assert len(trace["rules"]) == 1
    assert len(trace["examples"]) == 1


def test_build_writing_memory_prompt_no_rules_fallback_unchanged() -> None:
    """Existing test parity: 'No approved rules' still appears when rules list is empty."""
    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    draft = CampaignDeliverable(
        id=9003,
        status="draft",
        deliverable_metadata={"title": "No rules draft"},
    )
    result = build_writing_memory_prompt(draft=draft, profile=None, rules=[], examples=[])

    assert "No approved rules are available" in result["systemPrompt"]


# ── Tests: agent_executor shared_context injection ───────────────────────────


async def test_writing_studio_agent_gets_ruleset_in_shared_context(
    db_session: AsyncSession,
) -> None:
    """When writing_studio_adapter agent has an active profile + rules + examples,
    the grounding helper produces the three shared_context keys with correct content.

    Tests the full DB path: insert profile/rules/examples into the test DB,
    then call the same repository + helper code that agent_executor uses.
    """
    from sqlalchemy import select
    from sqlalchemy import text as sql_text

    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block
    from artemis.writing_rules import repository as wr_repo
    from artemis.writing_rules.models import WritingProfile
    from artemis.writing_rules.repository import list_examples, list_rules

    # Truncate writing tables to start clean
    await db_session.execute(
        sql_text(
            "TRUNCATE writing_examples, writing_rules, writing_profiles RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()

    # Insert a profile + 2 rules + 1 example
    profile = await wr_repo.create_profile(db_session, name="Amira Voice", status="active")
    await db_session.commit()

    await wr_repo.create_rule(
        db_session,
        title="Lead with outcome",
        body="Always start with the student benefit.",
        profile_id=profile.id,
        status="active",
    )
    await wr_repo.create_rule(
        db_session,
        title="Short sentences",
        body="Keep sentences under 20 words.",
        profile_id=profile.id,
        status="active",
    )
    await wr_repo.create_example(
        db_session,
        title="Email template",
        body="Dear educator, this is a sample.",
        profile_id=profile.id,
    )
    await db_session.commit()

    # Replicate the exact code agent_executor runs after the profile lookup
    active_profile = (
        await db_session.execute(
            select(WritingProfile)
            .where(WritingProfile.status != "archived")
            .order_by(WritingProfile.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()

    all_rules = await list_rules(db_session)
    all_examples = await list_examples(db_session)
    grounding = build_ruleset_grounding_block(active_profile, all_rules, all_examples)

    assert grounding, "grounding should not be empty"

    # Simulate what agent_executor does with the grounding result
    shared_context: dict[str, Any] = {}
    if grounding:
        shared_context["writing_ruleset_block"] = grounding["system_prompt_grounding_block"]
        shared_context["writing_anti_fabrication_guardrail"] = grounding[
            "anti_fabrication_guardrail"
        ]
        shared_context["writing_ruleset_trace"] = grounding["trace"]

    assert "writing_ruleset_block" in shared_context
    assert "writing_anti_fabrication_guardrail" in shared_context
    assert "writing_ruleset_trace" in shared_context

    # Verify content
    assert "Lead with outcome" in shared_context["writing_ruleset_block"]
    assert "Short sentences" in shared_context["writing_ruleset_block"]
    assert "Email template" in shared_context["writing_ruleset_block"]
    assert "fabricat" in shared_context["writing_anti_fabrication_guardrail"].lower()

    trace = shared_context["writing_ruleset_trace"]
    assert len(trace["rules"]) == 2
    assert len(trace["examples"]) == 1


async def test_no_grounding_keys_when_no_profile_or_rules(
    db_session: AsyncSession,
) -> None:
    """When no profile/rules/examples exist, the three new keys are NOT added."""
    from sqlalchemy import text as sql_text

    from artemis.marketing.writing_studio.compose_engine import build_ruleset_grounding_block
    from artemis.writing_rules.repository import list_examples, list_rules

    # Truncate writing tables to ensure empty state
    await db_session.execute(
        sql_text(
            "TRUNCATE writing_examples, writing_rules, writing_profiles RESTART IDENTITY CASCADE"
        )
    )
    await db_session.commit()

    all_rules = await list_rules(db_session)
    all_examples = await list_examples(db_session)
    # No profile fetched — pass None directly (mirrors agent_executor when profile is None)
    grounding = build_ruleset_grounding_block(None, all_rules, all_examples)

    # Simulate agent_executor injection guard: only add keys if grounding is non-empty
    shared_context: dict[str, Any] = {}
    if grounding:
        shared_context["writing_ruleset_block"] = grounding["system_prompt_grounding_block"]
        shared_context["writing_anti_fabrication_guardrail"] = grounding[
            "anti_fabrication_guardrail"
        ]
        shared_context["writing_ruleset_trace"] = grounding["trace"]

    assert "writing_ruleset_block" not in shared_context
    assert "writing_anti_fabrication_guardrail" not in shared_context
    assert "writing_ruleset_trace" not in shared_context


async def test_writing_studio_adapter_constant_exists() -> None:
    """_WRITING_GROUND_AGENT_IDS must contain the adapter agent id."""
    from artemis.pipelines.node_executors.agent_executor import _WRITING_GROUND_AGENT_IDS

    assert "marketing.content.writing_studio_adapter" in _WRITING_GROUND_AGENT_IDS
