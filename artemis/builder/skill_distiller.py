"""P5 learning loop — Skill Distiller.

``distill_skill_candidates(session, agent_id)`` analyses the last 10 trajectory
summaries for an agent and, via a single LLM call, identifies repeated
multi-step procedures that could be encoded as reusable skills. For each
non-duplicate candidate it creates a ``DefinitionProposal`` (kind="skill",
proposed_by="self-improvement") and returns a summary.

Design decisions (from the brief):
- Human-gated: never auto-approves; only creates proposals.
- Fail-safe: LLM/JSON error or no provider → zero proposals, log, no crash.
- Dedup: receives existing skill catalog in the prompt; skips proposals whose
  slug/name duplicates a pending proposal or an existing skill.
- One LLM call per invocation; Tier 3 (LM-first) via the resolver.
- Feature tag: "skill_distiller" (mirrors trajectory_summary pattern).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Prompt ────────────────────────────────────────────────────────────────────

_DISTILL_PROMPT = """\
You are a self-improvement analyst reviewing an AI agent's recent run history.

Your job: identify multi-step procedures the agent performed successfully in
≥2 of the runs below that could be encoded as a reusable skill.

RULES
- A qualifying procedure is: (a) multi-step, (b) appeared in the "what_worked"
  field in AT LEAST 2 of the last {n_summaries} trajectory summaries, and
  (c) is NOT already covered by the existing skills listed below.
- Be conservative (precision over recall). If you are unsure whether something
  qualifies, do not include it.
- Only output skill candidates that are genuinely reusable and not redundant
  with existing skills.
- For each candidate, the "tools" list should name only the tools the skill
  relies on (from the agent's tool list if visible in the summaries).

EXISTING SKILLS (do not duplicate these):
{existing_skills}

AGENT TRAJECTORY SUMMARIES (last {n_summaries} runs, most recent first):
{summaries}

Respond with valid JSON only — a JSON array (may be empty) where each element
has the following shape:
{{
  "slug": "short-kebab-case-id",
  "name": "Human-readable name",
  "description": "One sentence describing what this skill does.",
  "instructions": "Step-by-step instructions the agent should follow when this skill is relevant. Be specific and actionable.",
  "tools": ["tool_name_1", "tool_name_2"],
  "rationale": "One sentence explaining which runs demonstrate this procedure and why it qualifies."
}}

If no procedures qualify, respond with an empty JSON array: []
"""


# ── Dedup helpers ────────────────────────────────────────────────────────────

def _normalize_slug(s: str) -> str:
    return s.lower().strip()


async def _pending_proposal_slugs(session: AsyncSession) -> set[str]:
    """Return slugs (proposed_definition.slug) of all pending skill proposals."""
    from sqlalchemy import select

    from artemis.builders.models import DefinitionProposal

    result = await session.execute(
        select(DefinitionProposal.proposed_definition)
        .where(
            DefinitionProposal.kind == "skill",
            DefinitionProposal.status == "pending",
        )
    )
    slugs: set[str] = set()
    for (defn,) in result.all():
        if isinstance(defn, dict) and defn.get("slug"):
            slugs.add(_normalize_slug(str(defn["slug"])))
    return slugs


# ── Core function ─────────────────────────────────────────────────────────────


async def distill_skill_candidates(
    session: AsyncSession,
    agent_id: str,
    *,
    adapter: Any = None,  # ModelAdapter override — for testing
) -> dict[str, Any]:
    """Analyse trajectory summaries and create skill proposals for repeated
    procedures.

    Returns a summary dict:
      n_summaries:   int  — number of trajectory summaries inspected
      n_proposed:    int  — new proposals created this run
      n_skipped:     int  — duplicates / non-qualifying candidates skipped
      proposal_ids:  list[int]  — ids of newly created proposals

    Fail-safe: any exception during LLM call or JSON parsing → returns
    {"n_summaries": N, "n_proposed": 0, "n_skipped": 0, "proposal_ids": [],
     "error": "..."}  without raising.
    """
    from artemis.builder.engine import read_existing
    from artemis.builder.repository import (
        create_definition_proposal,
        get_trajectory_summaries_for_agent,
    )

    # 1. Load last 10 trajectory summaries for this agent.
    summaries = await get_trajectory_summaries_for_agent(session, agent_id, limit=10)
    if not summaries:
        logger.info("skill_distiller: agent=%r has no trajectory summaries — skipping", agent_id)
        return {
            "n_summaries": 0,
            "n_proposed": 0,
            "n_skipped": 0,
            "proposal_ids": [],
        }

    # 2. Load existing skill catalog for dedup context.
    try:
        existing_skills: list[dict[str, Any]] = await read_existing("skill", db_session=session)
    except Exception:
        logger.warning("skill_distiller: failed to load existing skills — proceeding without dedup context", exc_info=True)
        existing_skills = []

    # 3. Build existing slug set for hard dedup (catalog + pending proposals).
    existing_slugs: set[str] = {
        _normalize_slug(str(s.get("slug", "")))
        for s in existing_skills
        if s.get("slug")
    }
    pending_slugs = await _pending_proposal_slugs(session)
    all_known_slugs = existing_slugs | pending_slugs

    # 4. Resolve adapter via provider cascade (same pattern as trajectory_summarizer).
    if adapter is None:
        import artemis.db as _db
        from artemis.providers.resolver import resolve_adapter_async

        try:
            async with _db.SessionLocal() as _override_session:
                adapter = await resolve_adapter_async(
                    provider="claude-code",
                    feature_tag="skill_distiller",
                    session=_override_session,
                )
        except Exception:
            logger.warning(
                "skill_distiller: adapter resolution failed for agent=%r — zero proposals",
                agent_id,
                exc_info=True,
            )
            return {
                "n_summaries": len(summaries),
                "n_proposed": 0,
                "n_skipped": 0,
                "proposal_ids": [],
                "error": "no_provider",
            }

    # 5. Build prompt.
    summaries_text = _format_summaries(summaries)
    existing_skills_text = _format_existing_skills(existing_skills)
    prompt = _DISTILL_PROMPT.format(
        n_summaries=len(summaries),
        existing_skills=existing_skills_text,
        summaries=summaries_text,
    )

    # 6. Call LLM (single call, fail-safe).
    try:
        candidates = await _call_llm(adapter, prompt)
    except Exception as exc:
        logger.warning(
            "skill_distiller: LLM call failed for agent=%r — zero proposals: %s",
            agent_id,
            exc,
            exc_info=True,
        )
        return {
            "n_summaries": len(summaries),
            "n_proposed": 0,
            "n_skipped": 0,
            "proposal_ids": [],
            "error": f"llm_error:{type(exc).__name__}",
        }

    if not isinstance(candidates, list):
        logger.warning(
            "skill_distiller: LLM returned non-list for agent=%r (type=%s) — zero proposals",
            agent_id,
            type(candidates).__name__,
        )
        return {
            "n_summaries": len(summaries),
            "n_proposed": 0,
            "n_skipped": 0,
            "proposal_ids": [],
            "error": "json_not_list",
        }

    # 7. Build run_ids citation from summaries.
    run_ids: list[int] = [s.run_id for s in summaries]

    # 8. Create proposals for non-duplicate candidates.
    n_proposed = 0
    n_skipped = 0
    proposal_ids: list[int] = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            n_skipped += 1
            continue

        slug = _normalize_slug(str(candidate.get("slug", "")))
        if not slug:
            n_skipped += 1
            logger.debug("skill_distiller: skipping candidate with empty slug: %r", candidate)
            continue

        if slug in all_known_slugs:
            n_skipped += 1
            logger.debug(
                "skill_distiller: skipping duplicate slug=%r (already in catalog or pending)",
                slug,
            )
            continue

        # Validate required fields.
        name = str(candidate.get("name") or slug)
        instructions = candidate.get("instructions") or ""
        tools = candidate.get("tools") or []
        rationale = str(candidate.get("rationale") or "")

        definition: dict[str, Any] = {
            "slug": slug,
            "name": name,
            "description": candidate.get("description") or "",
            "instructions": instructions,
            "tools": tools if isinstance(tools, list) else [],
            "kind": "user",
        }
        citations: dict[str, Any] = {
            "run_ids": run_ids,
            "agent_id": agent_id,
            "rationale": rationale,
        }

        try:
            proposal = await create_definition_proposal(
                session,
                kind="skill",
                target_id=None,  # new skill — not a revision
                proposed_by="self-improvement",
                proposed_definition=definition,
                citations=citations,
            )
            proposal_ids.append(proposal.id)
            # Mark slug as known so a second candidate with the same slug in this
            # batch is also deduplicated.
            all_known_slugs.add(slug)
            n_proposed += 1
            logger.info(
                "skill_distiller: created proposal id=%d slug=%r for agent=%r",
                proposal.id,
                slug,
                agent_id,
            )
        except Exception:
            logger.warning(
                "skill_distiller: failed to create proposal for slug=%r — skipping",
                slug,
                exc_info=True,
            )
            n_skipped += 1

    return {
        "n_summaries": len(summaries),
        "n_proposed": n_proposed,
        "n_skipped": n_skipped,
        "proposal_ids": proposal_ids,
    }


# ── LLM helpers ───────────────────────────────────────────────────────────────


async def _call_llm(adapter: Any, prompt: str) -> list[dict[str, Any]]:
    """Make a single LLM call and parse the JSON array response.

    Uses the agent loop pattern (run_turn with no tools) to keep the call
    consistent with the rest of the codebase.

    Raises on network/parse errors — caller handles fail-safe.
    """
    from artemis.agent.loop import user_message as make_user_message
    from artemis.agent.loop import run_turn
    from artemis.agent.types import TextBlock

    result = await run_turn(
        adapter=adapter,
        messages=[make_user_message(prompt)],
        tools=None,
        system="You are a JSON-only response machine. Output valid JSON and nothing else.",
        max_tokens=2048,
        max_iterations=1,
        cache_system=False,
        cache_tools=False,
    )

    # Extract text from last assistant message.
    raw = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    raw += block.text
            break

    raw = raw.strip()
    # Strip markdown code fences if present.
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Drop first and last lines (```json / ```)
        lines = lines[1:] if lines[0].startswith("```") else lines
        lines = lines[:-1] if lines and lines[-1].strip() == "```" else lines
        raw = "\n".join(lines).strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed


# ── Formatting helpers ────────────────────────────────────────────────────────


def _format_summaries(summaries: list[Any]) -> str:
    """Format trajectory summaries for the prompt."""
    parts = []
    for i, s in enumerate(summaries, 1):
        worked = s.what_worked or "(none)"
        stalled = s.what_stalled or "(none)"
        missing = s.what_was_missing or "(none)"
        parts.append(
            f"Run {i} (id={s.run_id}):\n"
            f"  what_worked:      {worked}\n"
            f"  what_stalled:     {stalled}\n"
            f"  what_was_missing: {missing}"
        )
    return "\n\n".join(parts)


def _format_existing_skills(skills: list[dict[str, Any]]) -> str:
    """Format existing skill catalog for the prompt."""
    if not skills:
        return "(none)"
    lines = []
    for s in skills:
        slug = s.get("slug", "")
        name = s.get("name", "")
        desc = s.get("description") or ""
        lines.append(f"- {slug}: {name} — {desc}")
    return "\n".join(lines)
