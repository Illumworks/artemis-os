"""Feature catalog — single source of truth for feature_tags and their routing defaults.

Every LLM call site maps to exactly one feature_tag here. The resolver uses
``get_default_cascade(feature_tag)`` when no per-feature override exists in
``feature_routing_overrides``. The routing UI reads ``FEATURES`` to populate
the per-feature routing table.

Tier philosophy (from docs/provider-routing-cost-plan.md):
  Tier 1 — claude-code: customer-facing, decision-gating, quality-critical
  Tier 2 — codex: code-shaped, structured extraction, operator-facing
  Tier 3 — lm-studio/gemini: background, internal, high-volume, classification
"""

from __future__ import annotations

# Canonical provider IDs (must match artemis/providers/registry.py _BUILDERS keys)
_T1 = [{"provider": "claude-code"}, {"provider": "anthropic"}]
_T2 = [{"provider": "codex"}, {"provider": "claude-code"}, {"provider": "anthropic"}]
_T3_LM_FIRST = [
    {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
    {"provider": "gemini", "model": "gemini-2.5-flash"},
    {"provider": "claude-code"},
]
_T3_GEMINI_FIRST = [
    {"provider": "gemini", "model": "gemini-2.5-flash"},
    {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
    {"provider": "claude-code"},
]

# One entry per feature_tag from the canonical list in the brief.
FEATURES: dict[str, dict[str, object]] = {
    "agent_run": {
        "label": "Agent run executor",
        "description": "Runs a named agent against a task. Customer- and operator-facing. Per-agent provider config drives routing; this default applies when no agent row overrides.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "floating_artemis": {
        "label": "Floating Artemis chat",
        "description": "User-facing conversational AI. Quality-critical; always Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "workflow": {
        "label": "Workflow executor",
        "description": "Executes workflow steps defined in the builder. Customer-facing; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "pipeline": {
        "label": "Pipeline AI assistant",
        "description": "Canvas AI turn during pipeline construction. Operator-facing; Tier 2 candidate once Codex is on PATH.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "marketing_scout": {
        "label": "Marketing scout",
        "description": "Signal intake and judgment for marketing intelligence scouts. Quality-critical for judgment scouts; Tier 3 for pure classification scouts.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "marketing_brief": {
        "label": "Campaign brief assembler",
        "description": "Composes campaign brief proposals. Customer-facing; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "meeting_summary": {
        "label": "Meeting summarizer",
        "description": "Post-meeting background summarization. Long-context (Gemini 1M wins); Tier 3.",
        "default_cascade": _T3_GEMINI_FIRST,
        "recommended_tier": 3,
    },
    "memory_consolidation": {
        "label": "Memory consolidator",
        "description": "Batches observations into consolidated summaries. Runs per scope when 25 observations accumulate. Strict JSON schema; concurrent bursts favor Gemini first.",
        "default_cascade": _T3_GEMINI_FIRST,
        "recommended_tier": 3,
    },
    "memory_graph_extraction": {
        "label": "Memory graph extractor",
        "description": "Extracts entities and relations from observations for the graph layer. Strict JSON schema; internal-facing. Tier 3.",
        "default_cascade": _T3_GEMINI_FIRST,
        "recommended_tier": 3,
    },
    "trajectory_summary": {
        "label": "Trajectory summarizer",
        "description": "Post-hoc analysis of agent runs. Internal context for memory; simple structure; Tier 3 LM-first.",
        "default_cascade": _T3_LM_FIRST,
        "recommended_tier": 3,
    },
    "skill_distiller": {
        "label": "Skill distiller",
        "description": "Distills repeated procedures from trajectory summaries into skill proposals. Internal; one call per distill invocation. Tier 3 LM-first.",
        "default_cascade": _T3_LM_FIRST,
        "recommended_tier": 3,
    },
    "signal_qualifier": {
        "label": "Signal qualifier",
        "description": "Deterministic signal scoring (no LLM call). Tier 1 placeholder for any non-deterministic qualifier reasoning.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "background": {
        "label": "Background task",
        "description": "Generic background/internal tasks with no customer surface. Tier 3.",
        "default_cascade": _T3_LM_FIRST,
        "recommended_tier": 3,
    },
    "pipeline_canvas_ai": {
        "label": "Pipeline canvas AI",
        "description": "UI proposal generation in the pipeline visual canvas. Tier 2 once Codex is on PATH.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "builder_propose_agent": {
        "label": "Builder — propose agent",
        "description": "Agent definition proposal in the builder flow. Operator-facing; Tier 2.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "builder_propose_skill": {
        "label": "Builder — propose skill",
        "description": "Skill definition proposal in the builder flow. Operator-facing; Tier 2.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "okr_suggest_kr": {
        "label": "OKR — suggest KR progress",
        "description": "Optional helper to suggest KR progress updates. Low-stakes; Tier 3.",
        "default_cascade": _T3_LM_FIRST,
        "recommended_tier": 3,
    },
    "okr_extract_activity": {
        "label": "OKR — extract activity",
        "description": "Structured extraction of OKR activities. Code-shaped; Tier 2.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "meetings_qa": {
        "label": "Meetings Q&A",
        "description": "User question against a meeting transcript. User-facing; quality matters; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "dev_projects_loop": {
        "label": "Dev projects loop runner",
        "description": "Code-shaped sandbox loop. User selects provider; Tier 2 default.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "mcp_sandbox": {
        "label": "MCP tool sandbox",
        "description": "Tool safety evaluation. Code-shaped; Tier 2.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
    "spawn_subagent": {
        "label": "Spawn subagent",
        "description": "Operator-triggered subagent spawning from Floating Artemis. Quality matters; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "campaign_brief_assembler": {
        "label": "Campaign brief assembler (HTTP)",
        "description": "HTTP-triggered campaign brief assembly. Customer-facing; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "campaign_initiation": {
        "label": "Campaign initiation",
        "description": "Campaign initiation proposal generation. Customer-facing; Tier 1.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "writing_studio_compose": {
        "label": "Writing Studio compose",
        "description": "Customer-facing writing generation. Always Tier 1. Writing profile drives model selection.",
        "default_cascade": _T1,
        "recommended_tier": 1,
    },
    "slack_channel_gate": {
        "label": "Slack channel relevance gate",
        "description": "Tool-less YES/NO classifier: should the agent respond to an ambient channel message? No API key needed; Codex CLI primary, claude-code fallback. Fail-closed on any error.",
        "default_cascade": _T2,
        "recommended_tier": 2,
    },
}

# Canonical tag list — used for validation
FEATURE_TAGS: frozenset[str] = frozenset(FEATURES.keys())

# Known provider IDs — used for validation (mirrors registry._BUILDERS keys)
KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "claude-code", "codex", "gemini", "lm-studio", "openai", "openrouter"}
)


def get_default_cascade(feature_tag: str) -> list[dict[str, str]]:
    """Return the default cascade for a feature_tag.

    Falls back to the global DEFAULT_CASCADE pattern if the feature_tag is
    not in the catalog (should not happen in production — validators reject
    unknown tags, but belt-and-suspenders for future tags).
    """
    entry = FEATURES.get(feature_tag)
    if entry is None:
        # Fallback to Tier 1 default
        return [{"provider": "claude-code"}, {"provider": "anthropic"}]
    cascade = entry["default_cascade"]
    assert isinstance(cascade, list)
    return [dict(s) for s in cascade]
