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

from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout

DEFAULT_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs/marketing-ops-v1/agents"
MODEL_IDS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-4-6"}
_JOSH_SPEC = parse_spec()


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
marketing.district.classifier|district/3.1-district-classifier.md|haiku|session_scoped|auto|district_resolution
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
marketing.district.classifier|I map raw district names to canonical NCES entities so qualification and approval have real district context.|Precise and hallucination-immune. Resolves names only; never guesses enrollment or tier. A NULL is always better than a fabricated match.
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


def _bullets(section: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(
            r"^\s*-\s+(.+?)(?=\n\s*-\s+|\Z)", section, re.MULTILINE | re.DOTALL
        )
    ]


def _cadence_seconds(section: str) -> int | None:
    text = " ".join(section.split()).lower()
    match = re.search(r"\bevery\s+(\d+)\s*(h|hr|hour|hours|m|min|minute|minutes)\b", text)
    if match:
        value = int(match.group(1))
        return value * 3600 if match.group(2).startswith("h") else value * 60
    if re.search(r"\bdaily\b|\bevery day\b", text):
        return 86400
    return None


def _status(markdown: str) -> str | None:
    match = re.search(r"^\*\*Status:\*\*\s*(.+)$", markdown, re.MULTILINE)
    if not match:
        return None
    text = re.sub(r"[*`]", "", match.group(1)).strip().lower()
    if "bench-test" in text or "bench test" in text:
        return "bench_test"
    if "deprecated" in text:
        return "deprecated"
    if "active" in text:
        return "active"
    return re.sub(r"[^a-z0-9]+", "_", text.split(".")[0]).strip("_") or None


def _urgency_tiers_v2(section: str) -> dict[str, str] | None:
    """Permissive parser for bold bullets, plain bullets, and reserve-for prose."""
    tiers: dict[str, str] = {}
    if not section.strip():
        return None

    for bullet in _bullets(section):
        clean = " ".join(bullet.split())
        match = re.match(r"\*\*(?P<tier>[^*]+)\*\*\s*[—:-]\s*(?P<body>.+)", clean)
        if not match:
            match = re.match(
                r"(?P<tier>[A-Za-z][A-Za-z0-9_ /()-]{0,60}?)\s*(?:[—:]|\s+-\s+)\s*(?P<body>.+)",
                clean,
            )
        if match:
            tiers[match.group("tier").strip().lower()] = " ".join(match.group("body").split())

    lead_in = re.split(r"^\s*-\s+", section, maxsplit=1, flags=re.MULTILINE)[0]
    reserve = re.search(r"\bReserve\s+(?P<tier>[a-z][a-z0-9_-]*)\s+for:\s*$", lead_in, re.I)
    if reserve:
        tier = reserve.group("tier").strip().lower()
        tiers[tier] = "; ".join(" ".join(bullet.split()) for bullet in _bullets(section))

    for inline in re.finditer(
        r"\b(?P<body>[A-Za-z][A-Za-z0-9_ /()-]{1,80}?)\s*=\s*"
        r"(?P<tier>hot|standard|enrichment|low|medium|high|critical)\b",
        lead_in,
        re.I,
    ):
        tiers[inline.group("tier").strip().lower()] = inline.group("body").strip().lower()
    return tiers or None


def _urgency_tiers(section: str) -> dict[str, str] | None:
    return _urgency_tiers_v2(section)


def _failure_modes(section: str) -> list[dict[str, str]] | None:
    modes: list[dict[str, str]] = []
    for bullet in _bullets(section):
        clean = " ".join(bullet.split())
        match = re.match(r"\*\*(?P<name>[^*]+)\*\*\s*(?:[—:-]|→)\s*(?P<body>.+)", clean)
        if not match:
            match = re.match(r"(?P<name>.+?)\s*(?:[—:-]|→)\s*(?P<body>.+)", clean)
        if match:
            modes.append(
                {
                    "name": re.sub(r"`", "", match.group("name")).strip(),
                    "description": match.group("body").strip(),
                }
            )
        elif clean:
            modes.append({"name": clean, "description": ""})
    return modes or None


def _db_tables(section: str) -> list[str] | None:
    tables: list[str] = []
    for bullet in _bullets(section):
        for part in re.split(r",|\band\b", bullet):
            table = re.sub(r"[`*()]", "", part).strip()
            table = table.split()[0] if table else ""
            if table and table not in tables:
                tables.append(table)
    return tables or None


def _inputs(section: str) -> list[dict[str, str]] | None:
    inputs: list[dict[str, str]] = []
    for bullet in _bullets(section):
        clean = " ".join(bullet.split())
        key_match = re.search(r"`([A-Z][A-Z0-9_]+)`", clean)
        name_match = re.match(r"\*\*(?P<name>[^*]+)\*\*", clean)
        key = (
            key_match.group(1) if key_match else (name_match.group("name") if name_match else clean)
        )
        desc = re.sub(r"^\*\*[^*]+\*\*\s*[—:-]?\s*", "", clean).strip()
        inputs.append({"kind": "env" if key_match else "resource", "key": key, "description": desc})
    return inputs or None


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
    reason_codes = []
    if spec.agent_id.startswith("marketing.scout."):
        slug = spec.agent_id.rsplit(".", 1)[-1]
        reason_codes = [rc.code for rc in reason_codes_for_scout(_JOSH_SPEC, slug)]
    tools = _tools(_extract_section(markdown, "Tools required"))
    if spec.agent_id == "marketing.content.asset_selector":
        tools = [tool for tool in tools if tool != "claude.complete"]
    return {
        "agent_id": spec.agent_id,
        "name": name,
        "description": purpose,
        "goal": _goal(purpose),
        "system_prompt": _strip_code_fence(_extract_section(markdown, "Prompt scaffolding")),
        "tools": tools,
        "reason_codes_emitted": reason_codes,
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
        "cadence_seconds": _cadence_seconds(_extract_section(markdown, "Cadence")),
        "lifecycle_status": _status(markdown),
        "urgency_tiers": _urgency_tiers_v2(_extract_section(markdown, "Urgency tiers")),
        "failure_modes": _failure_modes(_extract_section(markdown, "Failure modes")),
        "db_tables_touched": _db_tables(_extract_section(markdown, "DB tables touched")),
        "implementation_notes": _extract_section(markdown, "Implementation notes for Codex")
        or _extract_section(markdown, "Implementation notes")
        or None,
        "inputs_required": _inputs(_extract_section(markdown, "Inputs")),
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
            "reason_codes_emitted": dumps(row["reason_codes_emitted"]),
            "urgency_tiers": dumps(row["urgency_tiers"])
            if row["urgency_tiers"] is not None
            else None,
            "failure_modes": dumps(row["failure_modes"])
            if row["failure_modes"] is not None
            else None,
            "db_tables_touched": (
                dumps(row["db_tables_touched"]) if row["db_tables_touched"] is not None else None
            ),
            "inputs_required": dumps(row["inputs_required"])
            if row["inputs_required"] is not None
            else None,
        }
        cursor: CursorResult[tuple[()]] = await session.execute(  # type: ignore[assignment]
            text(
                """
                INSERT INTO agents (
                    agent_id, name, description, goal, system_prompt, tools, model, provider,
                    fallback_provider, fallback_model, memory_policy, permission_mode,
                    persona, output_contract, reason_codes_emitted, cadence_seconds,
                    lifecycle_status, urgency_tiers, failure_modes, db_tables_touched,
                    implementation_notes, inputs_required, updated_at
                )
                VALUES (
                    :agent_id, :name, :description, :goal, :system_prompt, CAST(:tools AS jsonb),
                    :model, :provider, :fallback_provider, :fallback_model, :memory_policy,
                    :permission_mode, CAST(:persona AS jsonb), CAST(:output_contract AS jsonb),
                    CAST(:reason_codes_emitted AS jsonb), :cadence_seconds, :lifecycle_status,
                    CAST(:urgency_tiers AS jsonb), CAST(:failure_modes AS jsonb),
                    CAST(:db_tables_touched AS jsonb), :implementation_notes,
                    CAST(:inputs_required AS jsonb), now()
                )
                ON CONFLICT (agent_id) DO UPDATE SET
                    name = EXCLUDED.name, description = EXCLUDED.description,
                    goal = EXCLUDED.goal, system_prompt = EXCLUDED.system_prompt,
                    tools = EXCLUDED.tools, model = EXCLUDED.model,
                    provider = EXCLUDED.provider, fallback_provider = EXCLUDED.fallback_provider,
                    fallback_model = EXCLUDED.fallback_model, memory_policy = EXCLUDED.memory_policy,
                    permission_mode = EXCLUDED.permission_mode, persona = EXCLUDED.persona,
                    output_contract = EXCLUDED.output_contract,
                    reason_codes_emitted = EXCLUDED.reason_codes_emitted,
                    cadence_seconds = COALESCE(EXCLUDED.cadence_seconds, agents.cadence_seconds),
                    lifecycle_status = COALESCE(EXCLUDED.lifecycle_status, agents.lifecycle_status),
                    urgency_tiers = COALESCE(EXCLUDED.urgency_tiers, agents.urgency_tiers),
                    failure_modes = COALESCE(EXCLUDED.failure_modes, agents.failure_modes),
                    db_tables_touched = COALESCE(EXCLUDED.db_tables_touched, agents.db_tables_touched),
                    implementation_notes = COALESCE(EXCLUDED.implementation_notes, agents.implementation_notes),
                    inputs_required = COALESCE(EXCLUDED.inputs_required, agents.inputs_required),
                    updated_at = now()
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
