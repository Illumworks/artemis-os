"""Agent invocation node executor.

Handles: agent_invocation nodes.

Config shape:
  {
    "agent_id":      str,         # slug of the agent to run
    "mode":          str,         # "scheduled" | "manual" (informational)
    "cost_cap_usd":  float | None # per-node cap (None = no cap)
  }

Behaviour:
1. Resolves credentials via connectors/resolver.py for any tool namespaces
2. Injects reason_codes_emitted from agent blueprint into system context
3. Calls builders/executor.py::run_agent() — do NOT reimplement
4. Accumulates cost; if cumulative > cost_cap_usd, marks partial_complete
5. Returns NodeState-compatible dict

Cost units: estimated via token counts (input_tokens * 3e-6 + output_tokens * 15e-6
for claude-sonnet-4-6 pricing; these are approximate and rounded for display).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Approximate USD/token rates (sonnet-level; haiku is cheaper but we round up)
_INPUT_COST_PER_TOKEN = 3e-6
_OUTPUT_COST_PER_TOKEN = 15e-6
_CANDIDATE_CONTEXT_AGENT_IDS = frozenset(
    {
        "marketing.content.asset_selector",
        "marketing.content.writing_studio_adapter",
    }
)
_BRIEF_REQUIRED_AGENT_IDS = frozenset(
    {
        "marketing.content.writing_studio_adapter",
    }
)

# Agent IDs that should receive full writing-ruleset grounding in shared_context.
# Grounding is only injected when the node has a deliverable_type_slug in its config
# (i.e., the node is actually drafting a deliverable). Preflight nodes (no
# deliverable_type_slug) do not get the ruleset — it causes the LLM to attempt a
# full draft inline, which takes 900+ seconds without calling any tools.
# See: briefs/content-draft-node-hang.md
_WRITING_GROUND_AGENT_IDS: frozenset[str] = frozenset(
    {
        "marketing.content.writing_studio_adapter",
    }
)

# Agents that receive prior-rejection context from memory before each run.
# Only qualifier and content/writing agents participate — scouts, district,
# and qualifier-builder agents do NOT get rejection context.
_REJECTION_CONTEXT_AGENT_PREFIXES: tuple[str, ...] = (
    "marketing.qualifier.",
    "marketing.content.",
)

# Instruction injected alongside prior_rejections to guide silent behavior change.
_PRIOR_REJECTIONS_INSTRUCTION = (
    "You have a list of recent operator decisions on your prior outputs in"
    " `prior_rejections`. Each entry includes the operator's reason (if any)."
    " Use this to avoid repeating the same mistakes — favor patterns the"
    " operator approved; avoid patterns the operator rejected."
    " Do NOT mention these decisions in your output; let them shape your"
    " behavior silently."
)


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _INPUT_COST_PER_TOKEN) + (output_tokens * _OUTPUT_COST_PER_TOKEN)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


async def execute_agent_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    session: AsyncSession,
    run_id: str,
    accumulated_cost_usd: float = 0.0,
    model_adapter: Any | None = None,
) -> dict[str, Any]:
    """Execute an agent_invocation node.

    Args:
        node:                  Node dict (id, type, config, label).
        node_states:           Current node_states for this run.
        session:               Async DB session (caller owns transaction boundary).
        run_id:                Pipeline run ID (for shared_context injection).
        accumulated_cost_usd:  Running cost total for this run (enforces cost cap).
        model_adapter:         Override adapter for tests (FakeAdapter).

    Returns:
        NodeState-compatible dict with status, output_summary, cost_usd.
        If cost cap is exceeded: status="partial_complete".
    """
    from artemis.builders.executor import run_agent
    from artemis.connectors.resolver import ConnectorNotConfigured, get_credentials_for_tool

    config: dict[str, Any] = node.get("config") or {}
    agent_id: str = config.get("agent_id", "")
    cost_cap: float | None = config.get("cost_cap_usd")
    deliverable_type_slug = config.get("deliverable_type_slug")

    if not agent_id:
        return {
            "status": "failed",
            "error": "agent_executor: node config missing 'agent_id'",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    if config.get("propose_initiation") is True:
        return await _execute_campaign_initiation_proposal(
            session=session,
            run_id=run_id,
            agent_id=agent_id,
            model_adapter=model_adapter,
        )

    if isinstance(deliverable_type_slug, str) and deliverable_type_slug:
        enabled = await _deliverable_enabled_for_run(session, run_id, deliverable_type_slug)
        if enabled is False:
            return {
                "status": "skipped",
                "output_summary": (
                    f"Skipped deliverable '{deliverable_type_slug}': not in confirmed candidate mix"
                ),
                "cost_usd": 0.0,
            }
        if enabled is None:
            return {
                "status": "failed",
                "error": (
                    "No initiated candidate found for this pipeline run; "
                    f"cannot schedule deliverable '{deliverable_type_slug}'"
                ),
                "output_summary": "",
                "cost_usd": 0.0,
            }

    # Build shared context from prior node outputs
    shared_context: dict[str, Any] = {
        "pipeline_run_id": run_id,
        "triggering_node": node.get("id", ""),
    }
    candidate = None
    if deliverable_type_slug or agent_id in _CANDIDATE_CONTEXT_AGENT_IDS:
        candidate = await _resolve_candidate_for_run(session, run_id, initiated_only=True)
        if candidate is not None:
            shared_context["campaign_candidate_id"] = candidate.id
            shared_context["candidate_id"] = candidate.id
            shared_context["campaign_family"] = candidate.campaign_family or ""
            shared_context["campaign_name"] = candidate.name or ""
            shared_context["confirmed_deliverable_type_slugs"] = (
                candidate.deliverable_types_json or []
            )
            if candidate.target_scope_json is not None:
                shared_context["target_scope"] = candidate.target_scope_json
            from artemis.marketing.repository import get_campaign_brief

            brief = await get_campaign_brief(session, candidate.id)
            if brief is None and agent_id in _BRIEF_REQUIRED_AGENT_IDS:
                return {
                    "status": "failed",
                    "error": (
                        f"Target candidate {candidate.id} has no campaign brief; "
                        f"cannot run agent '{agent_id}' for pipeline run {run_id}"
                    ),
                    "output_summary": "",
                    "cost_usd": 0.0,
                }
            if brief is not None:
                shared_context["campaign_brief_id"] = brief.id
                shared_context["campaign_brief"] = brief.content
            from artemis.writing_rules.models import WritingProfile

            profile = (
                await session.execute(
                    select(WritingProfile)
                    .where(WritingProfile.status != "archived")
                    .order_by(WritingProfile.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if profile is not None:
                shared_context["default_voice_profile_slug"] = _slug(profile.name)

            # For writing-grounded agents: inject approved ruleset + anti-fabrication
            # guardrail so the auto-draft sees the same voice rules as compose
            # conversations do.  Only added when:
            #   1. The agent is in _WRITING_GROUND_AGENT_IDS, AND
            #   2. The node has a deliverable_type_slug (i.e., it is actually drafting).
            # Preflight nodes (no deliverable_type_slug) do NOT get the ruleset — the
            # extra 7K+ of writing rules causes the LLM to attempt a full inline draft
            # instead of a quick readiness check, which hangs the subprocess for 900s
            # without ever calling any tools.
            # See: briefs/content-draft-node-hang.md root-cause analysis.
            if agent_id in _WRITING_GROUND_AGENT_IDS and deliverable_type_slug:
                from artemis.marketing.writing_studio.compose_engine import (
                    build_ruleset_grounding_block,
                )
                from artemis.writing_rules.repository import list_examples, list_rules

                all_rules = await list_rules(session)
                all_examples = await list_examples(session)
                grounding = build_ruleset_grounding_block(profile, all_rules, all_examples)
                if grounding:
                    shared_context["writing_ruleset_block"] = grounding[
                        "system_prompt_grounding_block"
                    ]
                    shared_context["writing_anti_fabrication_guardrail"] = grounding[
                        "anti_fabrication_guardrail"
                    ]
                    shared_context["writing_ruleset_trace"] = grounding["trace"]

    # Inject brief summary from qualifier node outputs if present
    for state_key, state_val in node_states.items():
        if isinstance(state_val, dict) and state_val.get("output_summary"):
            shared_context[f"prior_{state_key}"] = state_val["output_summary"]

    # Inject prior rejection context for qualifier and content agents (C3).
    # Failure is purely advisory — never lets memory retrieval break execution.
    if any(agent_id.startswith(prefix) for prefix in _REJECTION_CONTEXT_AGENT_PREFIXES):
        try:
            from artemis.pipelines.node_executors.agent_memory_context import (
                fetch_agent_rejection_context,
            )

            prior_rejections = await fetch_agent_rejection_context(session, agent_id)
            if prior_rejections:
                shared_context["prior_rejections"] = prior_rejections
                shared_context["prior_rejections_instruction"] = _PRIOR_REJECTIONS_INSTRUCTION
        except Exception:
            logger.warning(
                "Prior rejection context fetch failed for agent_id=%r; continuing",
                agent_id,
                exc_info=True,
            )

    # Resolve connector credentials for any tool namespaces this agent uses
    # We do a best-effort resolution — if an agent has tools with connector
    # namespaces, attempt to load them. Failure raises ConnectorNotConfigured.
    try:
        from artemis.builders.repository import get_agent

        agent = await get_agent(session, agent_id)
    except ValueError as exc:
        return {
            "status": "failed",
            "error": f"Agent '{agent_id}' not found: {exc}",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Check tool namespaces for connector requirements
    tools_list = agent.tools if isinstance(agent.tools, list) else []
    for tool_spec in tools_list:
        if isinstance(tool_spec, dict):
            namespace = tool_spec.get("namespace") or tool_spec.get("connector_kind")
            if namespace and isinstance(namespace, str) and "." in namespace:
                namespace = namespace.split(".")[0]
            if namespace and namespace not in ("", "builtin"):
                try:
                    await get_credentials_for_tool(session, agent.id, namespace)
                except ConnectorNotConfigured:
                    raise ConnectorNotConfigured(agent.id, namespace) from None

    # Inject reason_codes_emitted from blueprint
    reason_codes: list[str] = []
    if isinstance(agent.reason_codes_emitted, list):
        reason_codes = agent.reason_codes_emitted
    if reason_codes:
        shared_context["reason_codes_emitted"] = ", ".join(reason_codes)

    # Resolve the model adapter via the provider cascade unless the caller
    # supplied one (e.g., FakeAdapter in tests).
    resolved_adapter = model_adapter
    if resolved_adapter is None:
        from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

        try:
            resolved_adapter = resolve_adapter(
                getattr(agent, "provider", None),
                getattr(agent, "fallback_provider", None),
            )
        except NoProviderAvailableError as exc:
            return {
                "status": "failed",
                "error": f"No LLM provider available for agent '{agent_id}': {exc}",
                "output_summary": "",
                "cost_usd": 0.0,
            }

    # Imperative instruction that drives immediate tool use (shared with the scout
    # scheduler so autonomous runs behave identically). A per-node override in
    # config["instruction"] wins over the role-based default.
    from artemis.builders.executor import default_agent_instruction

    instruction = default_agent_instruction(agent_id, config.get("instruction"))

    # Run the agent
    agent_run = await run_agent(
        session=session,
        agent_id=agent_id,
        shared_context=shared_context,
        model_adapter=resolved_adapter,
        user_message=instruction,
    )

    # Compute cost
    input_tokens = agent_run.cost_input_tokens or 0
    output_tokens = agent_run.cost_output_tokens or 0
    node_cost = _estimate_cost(input_tokens, output_tokens)
    new_accumulated = accumulated_cost_usd + node_cost

    # Enforce cost cap
    if cost_cap is not None and new_accumulated > cost_cap:
        return {
            "status": "partial_complete",
            "error": f"cost_cap_exceeded: ${new_accumulated:.4f} > cap ${cost_cap:.2f}",
            "output_summary": f"Agent '{agent_id}' stopped: cost cap ${cost_cap:.2f} exceeded",
            "cost_usd": node_cost,
        }

    if agent_run.status == "failed":
        return {
            "status": "failed",
            "error": agent_run.error or f"Agent '{agent_id}' run failed",
            "output_summary": "",
            "cost_usd": node_cost,
        }

    # Extract final response text from agent_context (run_id is the UUID string field)
    output_text = ""
    try:
        from artemis.builders.repository import get_agent_context

        ctx_row = await get_agent_context(session, agent_run.run_id, "final_response")
        raw_val = ctx_row.value
        output_text = str(raw_val or "")[:500]
    except (ValueError, Exception):
        pass

    run_uuid: str = agent_run.run_id or ""
    summary = f"Agent '{agent_id}' completed (run {run_uuid[:8]})"
    if output_text:
        summary += f": {output_text[:120]}…" if len(output_text) > 120 else f": {output_text}"

    # FIX115: advance candidate.workspace_state along its legal path as the
    # deliverable run progresses. Only fires when this node resolved a
    # candidate (content_asset_selector / writing_studio_adapter / a
    # deliverable_X node) so non-content pipelines are untouched.
    if candidate is not None:
        from artemis.marketing.workspace import advance_workspace_for_node

        try:
            await advance_workspace_for_node(
                session,
                candidate.id,
                node.get("id", ""),
                actor="deliverable_run",
            )
        except Exception:
            # Workspace sync is advisory — never fail a successful agent run
            # because a transition was illegal. advance_workspace_for_node
            # already swallows IllegalTransition; this guards everything else.
            logger.warning(
                "advance_workspace_for_node failed for candidate=%s node=%s",
                candidate.id,
                node.get("id", ""),
                exc_info=True,
            )

    return {
        "status": "succeeded",
        "output_summary": summary,
        "cost_usd": node_cost,
        "agent_run_id": run_uuid,
    }


async def _execute_campaign_initiation_proposal(
    *,
    session: AsyncSession,
    run_id: str,
    agent_id: str,
    model_adapter: Any | None,
) -> dict[str, Any]:
    from artemis.marketing.brief_assembler import propose_campaign_initiation
    from artemis.marketing.repository import list_run_candidates

    # An operator-selection at Gate-1 may produce multiple candidates per run;
    # the assembler processes the most recent uninitiated one per pass.
    # Subsequent runs (or operator-triggered re-runs) pick up the remaining
    # uninitiated candidates.
    uninitiated = await list_run_candidates(session, run_id, initiated_only=False)
    if not uninitiated:
        return {
            "status": "failed",
            "error": (
                "campaign initiation proposal requires at least one uninitiated candidate "
                f"for pipeline run {run_id}; found 0"
            ),
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Pick the most recently created uninitiated candidate.
    target = max(uninitiated, key=lambda c: c.created_at)
    skipped = [c for c in uninitiated if c.id != target.id]
    if skipped:
        logger.info(
            "[initiation] run=%s: assembling for candidate %s (most recent); "
            "skipping %d uninitiated candidate(s): %s",
            run_id,
            target.id,
            len(skipped),
            [c.id for c in skipped],
        )

    result = await propose_campaign_initiation(
        session,
        target.id,
        model_adapter=model_adapter,
    )
    if result.proposal is None:
        return {
            "status": "failed",
            "error": "CampaignInitiationProposal validation failed after retry",
            "output_summary": "",
            "cost_usd": 0.0,
            "candidate_id": target.id,
            "proposal_validation_failed": True,
        }

    return {
        "status": "succeeded",
        "output_summary": (
            f"Proposed campaign initiation for candidate {target.id}: {result.proposal.name}"
        ),
        "cost_usd": 0.0,
        "candidate_id": target.id,
        "proposal": result.proposal.model_dump(mode="json"),
        "retries_used": result.retries_used,
        "agent_id": agent_id,
    }


async def _deliverable_enabled_for_run(
    session: AsyncSession,
    run_id: str,
    deliverable_type_slug: str,
) -> bool | None:
    candidate = await _resolve_candidate_for_run(session, run_id, initiated_only=True)
    if candidate is None:
        return None
    return deliverable_type_slug in (candidate.deliverable_types_json or [])


async def _resolve_candidate_for_run(
    session: AsyncSession,
    run_id: str,
    *,
    initiated_only: bool | None,
) -> Any | None:
    from artemis.marketing.repository import get_candidate, list_run_candidates
    from artemis.pipelines.repository import get_pipeline_run

    try:
        run = await get_pipeline_run(session, run_id)
    except ValueError:
        return None

    if run.target_candidate_id is not None:
        try:
            candidate = await get_candidate(session, run.target_candidate_id)
        except ValueError:
            return None
        if initiated_only is True and candidate.initiated_at is None:
            return None
        if initiated_only is False and candidate.initiated_at is not None:
            return None
        return candidate

    candidates = await list_run_candidates(session, run_id, initiated_only=initiated_only)
    if not candidates:
        return None
    return candidates[0]
