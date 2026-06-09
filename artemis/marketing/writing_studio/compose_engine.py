"""Writing Studio compose engine — prompt assembly + proposed-learning extraction.

Port of the relevant functions from the frozen Node reference:
  server/writing-studio-invoke.js
    - buildWritingMemoryPrompt  → build_writing_memory_prompt
    - extractProposedLearnings  → extract_proposed_learnings
    - PROMPT_RULE_LIMIT, PROMPT_EXAMPLE_LIMIT, PROMPT_CONTEXT_LIMIT

This module is pure (no I/O, no DB, no SDK calls) so it is easy to test.
The compose route (artemis/marketing/routes/writing_studio.py) drives the
DB queries and provider invocation; this module only assembles the prompt
and parses the response.

GUARDRAIL (ported from Node):
  The system prompt instructs the model to ground responses exclusively on
  the rules and draft context provided — it MUST NOT fabricate efficacy
  claims, impact statistics, or outcomes not present in the source material.
  See _RUNTIME_GUARDRAIL and _build_runtime_context below.

Public helpers exposed for reuse by other callers (e.g. agent_executor):
  build_ruleset_grounding_block — formats the "Approved rules:" + examples
    block from a profile/rules/examples triple, capped at PROMPT_RULE_LIMIT /
    PROMPT_EXAMPLE_LIMIT and profile-filtered, WITHOUT draft-specific parts.
    Returns a dict with keys: system_prompt_grounding_block,
    anti_fabrication_guardrail, trace.
"""

from __future__ import annotations

import re
from typing import Any

# ── Constants (mirrors Node) ──────────────────────────────────────────────────

PROMPT_RULE_LIMIT = 8
PROMPT_EXAMPLE_LIMIT = 4
PROMPT_CONTEXT_LIMIT = 5000
PROMPT_ATTACHMENT_LIMIT = 1800

# Added for the Python rebuild: hard cap for the rules/examples block length
# to ensure the system prompt stays cache-friendly across turns.
PROMPT_SYSTEM_CACHE_BLOCK_LIMIT = 12_000

# Anti-fabrication guardrail text — single source of truth shared by both
# compose conversations and the initial auto-draft pipeline path.
ANTI_FABRICATION_GUARDRAIL = (
    "NEVER fabricate efficacy claims, impact statistics, outcome numbers, "
    "or 'proof points' not present in the campaign brief or source material. "
    "If information is missing, say what is missing or make the lightest "
    "reasonable assumption instead of inventing unseen source text."
)

COMPOSE_CHAT_PRESENTATION_DIRECTIVE = (
    "You are replying inside a live document editor, conversationally, as a writing collaborator "
    "not a report generator. Do NOT emit 'Recommended framing' or 'Compliance check' section "
    "headers, and do NOT enumerate Tier ratings, proof-pack IDs (E001-style), or claim-evidence "
    "tables in your reply. Compliance is handled by the document's inline claim flags. If a "
    "sentence you write uses an unapproved or Tier-4 strong claim, add at most one short plain-"
    "English heads-up line at the end, not a section. Keep replies tight, natural, and human.\n\n"
    "DELIVERABLE FENCE RULE: When you produce new or revised draft copy that should replace the "
    "document body, wrap ONLY that copy in a fenced block like this:\n"
    "```artemis-draft\n"
    "<the revised copy here>\n"
    "```\n"
    "Any conversational lead-in or brief explanation stays OUTSIDE the fence, before it. "
    "For pure questions, feedback, or turns where you are NOT rewriting the document, emit NO "
    "fence at all. Never put section headers, your explanation, or meta-commentary inside the fence."
)

# Regex to extract the content of an ```artemis-draft ... ``` fenced block.
_ARTEMIS_DRAFT_FENCE_RE = re.compile(
    r"```artemis-draft\s*\n(.*?)\n?```",
    re.DOTALL,
)

_PROPOSED_LEARNING_RE = re.compile(
    r"^proposed\s+(?:reusable\s+)?learning[s]?[:\-]?\s+(.+)",
    flags=re.IGNORECASE,
)


# ── Private helpers ───────────────────────────────────────────────────────────


def _compact_text(value: Any, limit: int = 1200) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return ""
    return normalized if len(normalized) <= limit else f"{normalized[:limit].rstrip()}..."


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_comparable(value: Any) -> str:
    return str(value or "").strip().lower()


def _strip_markdown_edge_bold(value: str) -> str:
    return re.sub(r"^\*{1,2}|\*{1,2}$", "", value).strip()


def _example_relevance_score(example: Any, draft: Any) -> int:
    """Higher score = more relevant to this draft.

    Port of _exampleRelevanceScore from Node.
    """
    score = 0
    asset_type = _normalize_comparable(getattr(draft, "deliverable_metadata", {}) or {})
    # For Python rebuild, asset_type and channel live in deliverable_metadata.
    meta: dict[str, Any] = {}
    if hasattr(draft, "deliverable_metadata") and isinstance(draft.deliverable_metadata, dict):
        meta = draft.deliverable_metadata
    draft_asset_type = _normalize_comparable(meta.get("assetType") or meta.get("asset_type") or "")
    draft_channel = _normalize_comparable(meta.get("channel") or "")
    del asset_type  # shadow above; use derived values

    ex_asset_type = _normalize_comparable(getattr(example, "asset_type", None) or "")
    ex_channel = _normalize_comparable(getattr(example, "channel", None) or "")

    if draft_asset_type and ex_asset_type == draft_asset_type:
        score += 3
    if draft_channel and ex_channel == draft_channel:
        score += 3
    if not ex_asset_type and not ex_channel:
        score += 1
    return score


def _build_runtime_context(
    draft: Any, *, has_attachments: bool, has_linked_google_doc: bool
) -> str:
    """Build the runtime-constraint block injected near the top of the system prompt.

    Ported from _buildWritingRuntimeContext in Node.

    GUARDRAIL: The bullet about not inventing source text is the anti-fabrication
    guardrail Jon requires.  It is explicit: the model may NOT invent efficacy
    claims, impact statistics, or outcomes not present in the provided source
    material.
    """
    meta: dict[str, Any] = {}
    if hasattr(draft, "deliverable_metadata") and isinstance(draft.deliverable_metadata, dict):
        meta = draft.deliverable_metadata
    draft_title: str = meta.get("title") or meta.get("externalTitle") or "this draft"

    lines = [
        "Runtime context for this Writing Studio turn:",
        "- You only have access to the context included in this prompt.",
        "- Do not assume direct access to repo files, uploaded hidden modules, or legacy .md documents unless their content is included below.",
        "- Treat approved Writing Studio rules/examples and any attached source notes in this prompt as the available source material.",
        (
            "- If information is missing, say what is missing or make the lightest reasonable assumption "
            "instead of inventing unseen source text.  NEVER fabricate efficacy claims, impact statistics, "
            "outcome numbers, or 'proof points' not present in the source material above."
        ),
        (
            "- A Google Doc may be linked to this draft, but it is only available here through the synced "
            "draft content and metadata included in this prompt."
            if has_linked_google_doc
            else "- No linked Google Doc content is available beyond the synced draft content in this prompt."
        ),
        (
            "- Attached source notes are included in this prompt as excerpted text, not as separately readable files."
            if has_attachments
            else "- No attachment excerpts were provided for this turn."
        ),
        f"- Active draft: {draft_title}.",
    ]
    return "\n".join(lines)


def _latest_draft_content(draft: Any) -> str:
    """Extract the most recent draft body.

    Composer Stage 1 introduces autosaved "live content" stored at
    ``deliverable_metadata['live_content']`` that does NOT cut a new version
    row (versions are only minted by the explicit Save-version button). When
    present, ``live_content`` is the authoritative latest body; otherwise
    fall back to ``versions[0].content``.
    """
    meta: dict[str, Any] = {}
    if hasattr(draft, "deliverable_metadata") and isinstance(draft.deliverable_metadata, dict):
        meta = draft.deliverable_metadata
    live = meta.get("live_content")
    if isinstance(live, str) and live:
        return live
    versions: list[Any] = meta.get("versions", [])
    if versions and isinstance(versions[0], dict):
        return str(versions[0].get("content", ""))
    return ""


# ── Public API ────────────────────────────────────────────────────────────────


def build_ruleset_grounding_block(
    profile: Any | None,
    rules: list[Any],
    examples: list[Any],
    *,
    asset_type: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Build the reusable rules/examples grounding block for a given profile.

    Used by both ``build_writing_memory_prompt`` (compose conversations) and
    ``agent_executor`` (first auto-draft in the deliverables pipeline) so both
    callers share one source of truth for the approved ruleset block and the
    anti-fabrication guardrail.

    Args:
        profile:    Active ``WritingProfile`` ORM object or None.
        rules:      All ``WritingRule`` rows to filter/rank.
        examples:   All ``WritingExample`` rows to filter/rank.
        asset_type: Optional asset_type hint for example relevance scoring
                    (mirrors deliverable_metadata["assetType"]).
        channel:    Optional channel hint for example relevance scoring.

    Returns:
        Dict with keys:
          ``system_prompt_grounding_block`` — formatted "Approved rules:" +
              "Relevant examples and templates:" text, profile-filtered,
              capped at PROMPT_RULE_LIMIT / PROMPT_EXAMPLE_LIMIT.
          ``anti_fabrication_guardrail`` — the guardrail text (ANTI_FABRICATION_GUARDRAIL).
          ``trace`` — dict with keys ``profile``, ``rules``, ``examples``
              (same shape as the corresponding sub-objects in
              ``build_writing_memory_prompt`` trace).

    Returns an empty dict ``{}`` when profile is None AND both lists are empty,
    signalling to callers that no grounding data is available.
    """
    profile_id: int | None = getattr(profile, "id", None)

    # ── Filter + rank rules ───────────────────────────────────────────────────
    prompt_rules = [
        r
        for r in rules
        if (profile_id is None or getattr(r, "profile_id", None) == profile_id)
        and (getattr(r, "status", "active") or "active") == "active"
    ][:PROMPT_RULE_LIMIT]

    # ── Filter + rank examples ────────────────────────────────────────────────
    # Build a minimal draft-like object for the relevance scorer when the
    # caller passed explicit asset_type / channel hints.
    class _HintDraft:
        deliverable_metadata: dict[str, Any] = {}

    if asset_type or channel:
        hint_draft = _HintDraft()
        hint_draft.deliverable_metadata = {
            "assetType": asset_type or "",
            "channel": channel or "",
        }
        draft_for_scoring: Any = hint_draft
    else:
        draft_for_scoring = _HintDraft()

    filtered_examples = [
        e for e in examples if profile_id is None or getattr(e, "profile_id", None) == profile_id
    ]
    filtered_examples.sort(
        key=lambda e: _example_relevance_score(e, draft_for_scoring),
        reverse=True,
    )
    prompt_examples = filtered_examples[:PROMPT_EXAMPLE_LIMIT]

    # Guard: nothing to inject
    if not prompt_rules and not prompt_examples and profile is None:
        return {}

    # ── Format the grounding block ────────────────────────────────────────────
    rules_text = (
        "\n".join(
            f"{i + 1}. [{getattr(r, 'rule_type', 'voice') or 'voice'}] {r.title}: {r.body}"
            for i, r in enumerate(prompt_rules)
        )
        if prompt_rules
        else "- No approved rules are available yet."
    )
    examples_text = (
        "\n\n".join(
            f"{i + 1}. {e.title} "
            f"({getattr(e, 'example_type', 'reference') or 'reference'}"
            f"{f', {e.asset_type}' if getattr(e, 'asset_type', None) else ''}"
            f"{f', {e.channel}' if getattr(e, 'channel', None) else ''})\n"
            f"{_compact_text(e.body, 1600)}"
            for i, e in enumerate(prompt_examples)
        )
        if prompt_examples
        else "- No reusable examples matched this context yet."
    )

    block_parts = [
        (
            "Use the approved Writing Studio memory bank below as explicit drafting context. "
            "Proposed training candidates are not durable memory and must not be treated as "
            "rules until a human approves them."
        ),
        "",
        "Approved rules:",
        rules_text,
        "",
        "Relevant examples and templates:",
        examples_text,
    ]
    grounding_block = "\n".join(block_parts)

    # ── Trace ─────────────────────────────────────────────────────────────────
    trace: dict[str, Any] = {
        "profile": (
            {
                "id": profile.id,
                "name": profile.name,
                "hasSystemPrompt": bool(getattr(profile, "system_prompt", None)),
            }
            if profile
            else None
        ),
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "type": getattr(r, "rule_type", "voice") or "voice",
                "sourceCandidateId": getattr(r, "source_candidate_id", None),
            }
            for r in prompt_rules
        ],
        "examples": [
            {
                "id": e.id,
                "title": e.title,
                "type": getattr(e, "example_type", "reference") or "reference",
                "assetType": getattr(e, "asset_type", None),
                "channel": getattr(e, "channel", None),
            }
            for e in prompt_examples
        ],
    }

    return {
        "system_prompt_grounding_block": grounding_block,
        "anti_fabrication_guardrail": ANTI_FABRICATION_GUARDRAIL,
        "trace": trace,
    }


def build_writing_memory_prompt(
    *,
    draft: Any,
    profile: Any | None,
    rules: list[Any],
    examples: list[Any],
    request: str | None = None,
    selected_text: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    prior_messages: list[Any] | None = None,
) -> dict[str, Any]:
    """Assemble system + user prompts for one Writing Studio compose turn.

    Returns a dict with keys:
      systemPrompt   — injected rules, examples, runtime constraints
      userPrompt     — the user's request + draft context
      priorMessages  — [{role, content}, ...] for conversation context
      trace          — metadata snapshot (profile, rules, examples used)

    Rules are filtered to the active profile and capped at PROMPT_RULE_LIMIT.
    Examples are ranked by asset_type/channel relevance and capped at PROMPT_EXAMPLE_LIMIT.
    Conversation history is carried forward so the model has context for follow-up turns.

    Port of buildWritingMemoryPrompt from server/writing-studio-invoke.js.
    Internally delegates rules/examples block assembly to build_ruleset_grounding_block.
    """
    meta: dict[str, Any] = {}
    if hasattr(draft, "deliverable_metadata") and isinstance(draft.deliverable_metadata, dict):
        meta = draft.deliverable_metadata
    brief: str | None = _optional_string(meta.get("brief") or "")
    latest_content = _latest_draft_content(draft)

    # ── Extract asset_type + channel hints from draft metadata ────────────────
    draft_asset_type: str | None = meta.get("assetType") or meta.get("asset_type") or None
    draft_channel: str | None = meta.get("channel") or None

    # ── Delegate rules/examples block to reusable helper ─────────────────────
    grounding = build_ruleset_grounding_block(
        profile,
        rules,
        examples,
        asset_type=draft_asset_type,
        channel=draft_channel,
    )
    # grounding may be empty dict if no profile/rules/examples — use a fallback block
    if grounding:
        grounding_block = grounding["system_prompt_grounding_block"]
        block_trace: dict[str, Any] = grounding["trace"]
    else:
        grounding_block = (
            "Use the approved Writing Studio memory bank below as explicit drafting context.\n\n"
            "Approved rules:\n- No approved rules are available yet.\n\n"
            "Relevant examples and templates:\n- No reusable examples matched this context yet."
        )
        block_trace = {"profile": None, "rules": [], "examples": []}

    # ── Attachments ───────────────────────────────────────────────────────────
    raw_attachments: list[dict[str, Any]] = [
        a for a in (attachments or []) if isinstance(a, dict) and (a.get("name") or a.get("text"))
    ][:4]
    prompt_attachments = [
        {
            "name": str(a.get("name") or "attached source"),
            "type": str(a.get("type") or "file"),
            "text": _compact_text(a.get("text") or "", PROMPT_ATTACHMENT_LIMIT),
        }
        for a in raw_attachments
    ]

    # ── Runtime context (includes anti-fabrication guardrail) ─────────────────
    runtime_context = _build_runtime_context(
        draft,
        has_attachments=bool(prompt_attachments),
        has_linked_google_doc=False,  # Python rebuild: no Google Doc sync yet
    )

    # ── System prompt ─────────────────────────────────────────────────────────
    system_parts: list[str] = [
        getattr(profile, "system_prompt", None)
        or "You are Artemis Writing Studio, a careful marketing writing partner.",
        "",
        runtime_context,
        "",
        grounding_block,
        "",
        COMPOSE_CHAT_PRESENTATION_DIRECTIVE,
    ]
    system_prompt = "\n".join(system_parts)

    # ── User prompt ───────────────────────────────────────────────────────────
    draft_title: str = meta.get("title") or meta.get("externalTitle") or "Untitled draft"
    campaign_id: str | None = draft.campaign_id if hasattr(draft, "campaign_id") else None

    user_parts: list[str] = [
        f"Writing action: {request or 'Continue shaping this draft.'}",
        "",
        "Draft context:",
        f"- Title: {draft_title}",
        f"- Asset type: {meta.get('assetType') or meta.get('asset_type') or 'Not set'}",
        f"- Audience: {meta.get('audience') or 'Not set'}",
        f"- Channel: {meta.get('channel') or 'Not set'}",
        f"- Campaign: {campaign_id or 'Not set'}",
        f"- Brief: {brief}" if brief else "- Brief: Not set",
        "- Linked Google Doc: Not linked",
        "",
        f"Selected passage:\n{selected_text}" if selected_text else "Selected passage: none",
        "",
        f"Current draft:\n{_compact_text(latest_content, PROMPT_CONTEXT_LIMIT) or '(No draft body yet.)'}",
    ]
    if prompt_attachments:
        user_parts += [
            "",
            "Attached source notes:",
            "\n\n".join(
                f"{i + 1}. {a['name']} ({a['type']})\n{a['text'] or '(No extracted text.)'}"
                for i, a in enumerate(prompt_attachments)
            ),
        ]
    user_parts += [
        "",
        (
            "Return useful writing output for the requested action. "
            "If you identify a reusable style rule or preference from this session, propose it at the end "
            'on a single labeled line in exactly this format: "Proposed learning: <the rule text>" '
            "— one rule only, no quotes around the rule text."
        ),
    ]
    user_prompt = "\n".join(filter(None, user_parts)) if False else "\n".join(user_parts)

    # ── Prior conversation messages for context ───────────────────────────────
    prior_turns: list[dict[str, str]] = []
    if prior_messages:
        for msg in prior_messages:
            role = getattr(msg, "role", "")
            content = getattr(msg, "content", "")
            if role in ("user", "assistant") and content:
                prior_turns.append({"role": role, "content": content})

    # ── Trace — merges helper trace with draft-specific fields ────────────────
    trace: dict[str, Any] = {
        **block_trace,
        "draft": {
            "id": getattr(draft, "id", None),
            "title": draft_title,
            "latestContentChars": len(latest_content),
            "selectedTextChars": len(selected_text or ""),
            "attachmentCount": len(prompt_attachments),
            "priorTurns": len(prior_turns),
        },
        "learningLifecycle": [
            "New guidance starts as a proposed candidate (returned as proposedCandidates).",
            "Proposed candidates are NOT persisted — Phase 3 wires persist + review.",
            "Only active rules and stored examples are included in Writing Studio prompt assembly.",
        ],
    }

    return {
        "systemPrompt": system_prompt,
        "userPrompt": user_prompt,
        "priorMessages": prior_turns,
        "trace": trace,
    }


def extract_proposed_learnings(response_text: str) -> list[str]:
    """Scan the assistant response for "Proposed learning: <text>" lines.

    Port of extractProposedLearnings from server/writing-studio-invoke.js.

    Rules:
    - Leading/trailing Markdown bold markers (**) are stripped.
    - The regex is case-insensitive; "reusable" qualifier is optional.
    - The extracted text must be ≥ 10 chars.
    - Multiple proposals are supported (the prompt only asks for one, but we
      accept more for robustness).
    """
    results: list[str] = []
    for line in response_text.split("\n"):
        stripped = _strip_markdown_edge_bold(line)
        match = _PROPOSED_LEARNING_RE.match(stripped)
        if match:
            text = match.group(1).strip().strip("\"'‘’“”")
            if len(text) >= 10:
                results.append(text)
    return results


def strip_proposed_learning_lines(response_text: str) -> str:
    """Remove visible proposed-learning lines while preserving the rest of the reply."""
    kept_lines: list[str] = []
    for line in response_text.split("\n"):
        stripped = _strip_markdown_edge_bold(line)
        if _PROPOSED_LEARNING_RE.match(stripped):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


def parse_draft_fence(response_text: str) -> tuple[str, str | None]:
    """Split an assistant response into (chat_message, deliverable).

    If the response contains a ``\\`\\`\\`artemis-draft ... \\`\\`\\``` fenced block:
      - ``deliverable`` = the text inside the fence (stripped).
      - ``chat_message`` = the response with the entire fence block removed,
        then whitespace-trimmed.  If nothing remains outside the fence,
        ``chat_message`` falls back to a short acknowledgment.

    If no fence is present:
      - ``deliverable`` = ``None``.
      - ``chat_message`` = the full response text (unchanged).

    This function never raises; a malformed/partial fence returns
    ``deliverable=None`` and the original text as ``chat_message``.
    """
    match = _ARTEMIS_DRAFT_FENCE_RE.search(response_text)
    if not match:
        return response_text, None

    deliverable = match.group(1).strip()
    if not deliverable:
        # Empty fence — treat as no deliverable.
        return response_text, None

    # Strip the entire fence block (opening + content + closing backticks).
    chat_message = _ARTEMIS_DRAFT_FENCE_RE.sub("", response_text).strip()
    if not chat_message:
        chat_message = "Here's the revised draft — applied to the document."

    return chat_message, deliverable
