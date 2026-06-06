"""Routing candidate sets for the Cost → Routing opportunities tab.

Defines which alternative providers are eligible for each feature tier,
trade-off notes for the UI, and the FEATURE_TIER mapping that assigns
each feature_tag to a candidate set.

Critical features only suggest Anthropic Haiku as a downgrade — never
LM Studio / Gemini / OpenAI.  Low-stakes features expose the full set
of alternatives.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Candidate sets
# Each tuple is (provider, model).  Candidates are ordered by preference
# (cheapest / most-private first).
# ---------------------------------------------------------------------------

CANDIDATES: dict[str, list[tuple[str, str]]] = {
    # For low-stakes summarization or transformation
    "low_stakes": [
        ("lm-studio", "qwen/qwen3-14b"),  # local, free, privacy-friendly
        ("gemini", "gemini-2.5-flash"),  # cloud, strict JSON, free-tier
        ("openai", "gpt-4o-mini"),  # cloud, general-purpose cheap
        ("anthropic", "claude-haiku-4-5-20251001"),  # same-provider downgrade
    ],
    # For low-stakes tasks needing strict JSON schema (graph extraction, consolidation)
    "low_stakes_json_strict": [
        ("gemini", "gemini-2.5-flash"),  # Gemini's JSON adherence wins
        ("lm-studio", "qwen/qwen3-14b"),  # local fallback
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
    # For agentic / structured-output / customer-facing critical work
    "critical": [
        (
            "anthropic",
            "claude-haiku-4-5-20251001",
        ),  # only suggest haiku; never offload off-provider
    ],
}

# ---------------------------------------------------------------------------
# Feature → tier mapping
# ---------------------------------------------------------------------------

FEATURE_TIER: dict[str, str] = {
    "agent_run": "critical",
    "floating_artemis": "critical",
    "workflow": "critical",
    "writing_studio_compose": "critical",
    "campaign_brief_assembler": "critical",
    "campaign_initiation": "critical",
    "meetings_qa": "critical",
    "spawn_subagent": "critical",
    "memory_consolidation": "low_stakes_json_strict",
    "memory_graph_extraction": "low_stakes_json_strict",
    "trajectory_summary": "low_stakes",
    "meeting_summary": "low_stakes",
    "marketing_brief": "critical",
    "marketing_scout": "critical",
    "signal_qualifier": "low_stakes",
    "background": "low_stakes",
    "unknown": "low_stakes",
    "pipeline_canvas_ai": "critical",
    "builder_propose_agent": "critical",
    "builder_propose_skill": "critical",
    "okr_suggest_kr": "low_stakes",
    "okr_extract_activity": "low_stakes_json_strict",
    "dev_projects_loop": "critical",
    "mcp_sandbox": "critical",
}

# ---------------------------------------------------------------------------
# Trade-off notes — mandatory on every alternative in the UI
# ---------------------------------------------------------------------------

TRADEOFF_NOTES: dict[tuple[str, str], str] = {
    ("lm-studio", "qwen/qwen3-14b"): (
        "Local model on the Mac mini. $0 marginal cost, full privacy, but single-stream"
        " — concurrent calls queue. Drifts on strict JSON schemas ~5-15% of the time"
        " without explicit JSON-mode tuning."
    ),
    ("gemini", "gemini-2.5-flash"): (
        "Strong for summarization + strict JSON. 1M context window. Free-tier rate-limited"
        " (~15 RPM / 1500 RPD on 2.0 Flash) — bursts may 429."
    ),
    ("openai", "gpt-4o-mini"): (
        "Cheap general-purpose; less reliable on tool-calling than Anthropic."
    ),
    ("anthropic", "claude-haiku-4-5-20251001"): (
        "Same provider, smaller model. Good drop-in for low-stakes tasks; doesn't reduce"
        " Claude subscription load."
    ),
}

# ---------------------------------------------------------------------------
# Setup hints for unavailable providers
# ---------------------------------------------------------------------------

SETUP_HINTS: dict[str, str] = {
    "gemini": "Add GEMINI_API_KEY in Connectors to enable.",
    "openai": "Add OPENAI_API_KEY in Connectors to enable.",
    "anthropic": "Add ANTHROPIC_API_KEY in Connectors to enable.",
    "lm-studio": "Start LM Studio and load a model to enable local inference.",
    "codex": "Install and expose the Codex CLI binary on PATH.",
}
