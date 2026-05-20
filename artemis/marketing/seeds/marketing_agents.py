"""Seed the 16 canonical Marketing OS agents.

Idempotent and seed-owned-field only: re-runs update markdown-derived fields
and routing defaults, but never touch ``agent_id``, ``owner_user_id``, or
``created_at``.
"""

from __future__ import annotations

import re
from json import dumps
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs/marketing-ops-v1/agents"
MODEL_IDS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-4-6"}


class MarketingAgentSpec(NamedTuple):
    agent_id: str
    relative_path: str
    model_tier: str
    memory_policy: str
    permission_mode: str
    output_schema: str


_SPECS = """
marketing.scout.starbridge_researcher|scout/1.1-starbridge-researcher.md|haiku|persistent|auto|signal
marketing.scout.regional_news|scout/1.2-regional-news-scout.md|haiku|persistent|auto|signal
marketing.scout.linkedin_observer|scout/1.3-linkedin-observer.md|haiku|persistent|auto|signal
marketing.scout.legislative|scout/1.4-legislative-scout.md|haiku|persistent|auto|signal
marketing.scout.federal_funding|scout/1.5-federal-funding-scout.md|haiku|persistent|auto|signal
marketing.scout.state_doe|scout/1.6-state-doe-scout.md|haiku|persistent|auto|signal
marketing.scout.procurement|scout/1.7-procurement-scout.md|haiku|persistent|auto|signal
marketing.scout.board_minutes|scout/1.8-board-minutes-scout.md|haiku|persistent|auto|signal
marketing.scout.leadership_transition|scout/1.9-leadership-transition-scout.md|haiku|persistent|auto|signal
marketing.qualifier.cross_reference|qualifier/2.1-cross-reference-agent.md|sonnet|session_scoped|ask|qualification_result
marketing.qualifier.ruleset_manager|qualifier/2.2-ruleset-manager-agent.md|sonnet|session_scoped|ask|qualification_result
marketing.qualifier.ruleset_compiler|qualifier/2.3-ruleset-compiler.md|haiku|session_scoped|ask|qualification_result
marketing.qualifier.brief_composer|qualifier/2.4-brief-composer-agent.md|sonnet|session_scoped|ask|qualification_result
marketing.content.brief_assembler|content/5.1-campaign-brief-assembler.md|haiku|session_scoped|ask|writing_studio_draft
marketing.content.asset_selector|content/5.2-asset-selector-agent.md|sonnet|session_scoped|ask|writing_studio_draft
marketing.content.writing_studio_adapter|content/5.3-writing-studio-adapter.md|haiku|session_scoped|ask|writing_studio_draft
""".strip()

MARKETING_AGENT_SPECS = tuple(MarketingAgentSpec(*line.split("|")) for line in _SPECS.splitlines())

_PERSONAS = """
marketing.scout.starbridge_researcher|I turn Starbridge's legislative and funding stream into district-ready signals before the window closes.|Precise, data-driven, slightly impatient. Speaks in bills passed, dollars allocated, deadlines counting down; demands the document before trusting the headline.
marketing.scout.regional_news|I catch local education signals while they are still small enough to become timely campaigns.|Curious, conversational, ear-to-the-ground. Reads like the person who checks the local paper before the local paper is done printing.
marketing.scout.linkedin_observer|I watch public leader posts for role changes, vendor frustration, and literacy priorities that deserve a signal.|Careful, restrained, context-aware. Treats LinkedIn as public evidence, not a private dossier, and never overstates soft social signals.
marketing.scout.legislative|I translate state legislation into clean literacy and funding signals with status, geography, and evidence intact.|Statutory, crisp, and citation-hungry. Cares about bill status, chamber movement, dates, and whether the policy actually touches K-12 literacy.
marketing.scout.federal_funding|I surface literacy and tutoring funding windows early enough for Marketing to act before deadlines compress.|Deadline-minded and practical. Talks in eligibility, close dates, grant language, and whether the opportunity is real enough to mobilize around.
marketing.scout.state_doe|I monitor state education agencies for guidance, vendor lists, grants, and mandates that create campaign openings.|Bureaucracy-literate and patient. Knows that the useful signal is often buried in a PDF, memo, agenda attachment, or quiet procurement page.
marketing.scout.procurement|I find literacy RFPs and adoption windows with enough specificity to support a timely response.|Operational, exacting, and allergic to vague opportunities. Prioritizes due dates, scope language, eligibility, and procurement source links.
marketing.scout.board_minutes|I read board evidence for public intent, pain, and decisions that become grounded marketing signals.|Patient, evidentiary, and speaker-aware. Values the quote, the attribution, and the agenda context more than the summary.
marketing.scout.leadership_transition|I identify leadership changes when new decision-makers create a short, real campaign window.|Network-aware but formal. Distinguishes interim from permanent hires, confirms with public sources, and tracks the ninety-day window.
marketing.qualifier.cross_reference|I decide whether a signal qualifies, why it qualifies, and where it should route.|Methodical, three-step thinker. Will explain why a signal passes and why it might not; never hides uncertainty behind a score.
marketing.qualifier.ruleset_manager|I help Josh evolve qualification rules through evidence-backed conversation, not YAML busywork.|Direct, analytical, and permissioned. Shows diffs, asks before writing, and backs proposals with hit-rate data or concrete examples.
marketing.qualifier.ruleset_compiler|I turn approved ruleset YAML into executable, validated runtime objects without trusting unsafe input.|Mechanical, strict, and security-minded. Speaks in compiler errors, schema validation, and warnings that help humans fix rules fast.
marketing.qualifier.brief_composer|I turn qualified signals into concise campaign briefs that help Josh and Angela make the next call.|A briefer, not a salesperson. Writes for a morning decision, keeps evidence close, and leaves hype out of the room.
marketing.content.brief_assembler|I build the immutable campaign brief that downstream content work can trust.|Orderly, validation-first, and quiet. Cares about clean transforms, frozen evidence, and failing loudly before bad input reaches drafting.
marketing.content.asset_selector|I pick the asset bundle that makes a campaign feel coherent instead of patched together.|Editorial and evidence-led. Balances relevance, freshness, campaign fit, and specificity without overfitting to a single deliverable.
marketing.content.writing_studio_adapter|I carry approved campaign inputs across the Writing Studio boundary reliably, one draft payload at a time.|Quiet, mechanical, reliable. Speaks only when something breaks, and when it does, it reports the exact boundary failure.
""".strip()

PERSONAS = {
    agent_id: (purpose, voice)
    for agent_id, purpose, voice in (line.split("|", 2) for line in _PERSONAS.splitlines())
}


def _extract_section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(heading)}(?:\s*\([^)]+\))?\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body").strip() if match else ""


def _title(markdown: str) -> str:
    match = re.search(
        r"^#\s+(?:Agent|Component)\s+\d+(?:\.\d+)?\s+—\s+(.+?)\s*$",
        markdown,
        re.MULTILINE,
    )
    if not match:
        raise ValueError("missing top-level agent title")
    return match.group(1).replace("(Mode B only in v1)", "").strip()


def _strip_code_fence(value: str) -> str:
    match = re.match(r"^```[a-zA-Z0-9_-]*\n(?P<body>.*)\n```$", value.strip(), re.DOTALL)
    return match.group("body").strip() if match else value.strip()


def _tools(section: str) -> list[str]:
    found: list[str] = []
    for raw_line in _strip_code_fence(section).splitlines():
        line = raw_line.split("#", 1)[0].strip()
        candidates = re.findall(r"\b[a-zA-Z_]\w*(?:\.(?:[a-zA-Z_]\w*|\*))+", line)
        candidates.extend(re.findall(r"\bimport\s+([a-zA-Z_]\w*)", line))
        for candidate in candidates:
            if candidate not in found and candidate != "response.status_code":
                found.append(candidate)
    return found


def _goal(purpose: str) -> str:
    collapsed = " ".join(purpose.split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", collapsed)
    return match.group(1) if match else collapsed


def _row(spec: MarketingAgentSpec, docs_root: Path) -> dict[str, Any]:
    markdown = (docs_root / spec.relative_path).read_text(encoding="utf-8")
    name = _title(markdown)
    purpose = _extract_section(markdown, "Purpose")
    persona_purpose, voice_notes = PERSONAS[spec.agent_id]
    model = MODEL_IDS[spec.model_tier]
    return {
        "agent_id": spec.agent_id,
        "name": name,
        "description": purpose,
        "goal": _goal(purpose),
        "system_prompt": _strip_code_fence(_extract_section(markdown, "Prompt scaffolding")),
        "tools": _tools(_extract_section(markdown, "Tools required")),
        "model": model,
        "provider": "claude-code",
        "fallback_provider": "anthropic",
        "fallback_model": model,
        "memory_policy": spec.memory_policy,
        "permission_mode": spec.permission_mode,
        "persona": {
            "name": name,
            "purpose": persona_purpose,
            "voice_notes": voice_notes,
            "ghostwrite": False,
            "profile_image_path": None,
        },
        "output_contract": {
            "schema": spec.output_schema,
            "version": 1,
            "trajectory_summary": {"enabled": True},
        },
    }


def load_marketing_agent_rows(docs_root: Path = DEFAULT_DOCS_ROOT) -> list[dict[str, Any]]:
    return [_row(spec, docs_root) for spec in MARKETING_AGENT_SPECS]


async def seed_marketing_agents(
    session: AsyncSession, *, docs_root: Path = DEFAULT_DOCS_ROOT
) -> dict[str, Any]:
    rows = load_marketing_agent_rows(docs_root)
    inserted = updated = 0
    for row in rows:
        params = {
            **row,
            "tools": dumps(row["tools"]),
            "persona": dumps(row["persona"]),
            "output_contract": dumps(row["output_contract"]),
        }
        cursor: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            text(
                """
                INSERT INTO agents (
                    agent_id, name, description, goal, system_prompt, tools, model, provider,
                    fallback_provider, fallback_model, memory_policy, permission_mode,
                    persona, output_contract, updated_at
                )
                VALUES (
                    :agent_id, :name, :description, :goal, :system_prompt, CAST(:tools AS jsonb),
                    :model, :provider, :fallback_provider, :fallback_model, :memory_policy,
                    :permission_mode, CAST(:persona AS jsonb), CAST(:output_contract AS jsonb),
                    now()
                )
                ON CONFLICT (agent_id) DO UPDATE SET
                    name = EXCLUDED.name, description = EXCLUDED.description,
                    goal = EXCLUDED.goal, system_prompt = EXCLUDED.system_prompt,
                    tools = EXCLUDED.tools, model = EXCLUDED.model,
                    provider = EXCLUDED.provider, fallback_provider = EXCLUDED.fallback_provider,
                    fallback_model = EXCLUDED.fallback_model, memory_policy = EXCLUDED.memory_policy,
                    permission_mode = EXCLUDED.permission_mode, persona = EXCLUDED.persona,
                    output_contract = EXCLUDED.output_contract, updated_at = now()
                RETURNING (xmax = 0) AS inserted
                """
            ),
            params,
        )
        was_inserted = bool(cursor.scalar_one())
        inserted += int(was_inserted)
        updated += int(not was_inserted)
    await session.commit()
    return {"inserted": inserted, "updated": updated, "agent_ids": [r["agent_id"] for r in rows]}


async def run_seed() -> None:
    from artemis.db import SessionLocal

    async with SessionLocal() as session:
        counts = await seed_marketing_agents(session)
        print(f"seed_marketing_agents: inserted={counts['inserted']} updated={counts['updated']}")
        for agent_id in counts["agent_ids"]:
            print(agent_id)
