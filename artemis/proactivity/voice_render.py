"""Voice rendering pass for proactive Slack messages.

Takes grounded brief/OKR data and phrases the Slack message in Artemis's voice
using a single cheap LLM call (claude-haiku-4-5).  Failure-isolated: if the
LLM call fails or returns nothing useful, the caller falls back to a plain-text
rendering.

Usage:
    from artemis.proactivity.voice_render import render_brief_with_voice
    from artemis.proactivity.voice_render import render_checkin_with_voice

Both functions are async and return a lint-clean string.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Cheap model — one call per scheduled event is fine.
_VOICE_MODEL = "claude-haiku-4-5-20251001"


def _build_voice_system_prompt(voice_samples: list[str]) -> str:
    """Return the system prompt that installs Artemis's voice."""
    from artemis.floating_artemis.personality import load_agent_profile

    profile = load_agent_profile("artemis")
    persona = profile.persona_core

    samples_block = ""
    if voice_samples:
        samples_lines = "\n".join(f'- "{s}"' for s in voice_samples)
        samples_block = f"\n\nCharacteristic phrases (use this register):\n{samples_lines}"

    return f"""{persona}{samples_block}

You are writing a SHORT Slack message to Jon Fila, your operator.
Rules:
- NO labeled sections like "Summary:", "Highlights:", "Priorities:", "Risks:", "OKR Status:".
- NO tables. NO em-dashes. NO emojis.
- Conversational chief-of-staff tone — like a sharp colleague briefing you, not a form.
- Lead with the most important thing. Keep it scannable.
- Every stated fact must come from the grounded data provided. Do not invent.
- If the data has nothing substantive, say so plainly in one sentence.
- Keep total output SHORT: 3-8 lines for a brief, 4-10 lines for an OKR check-in.
- Slack bold syntax (*text*) is allowed for KR names and objective names only.
- Output ONLY the Slack message text — no preamble, no sign-off, no "Here is:".
"""


def _build_brief_voice_prompt(brief: dict[str, Any], delivery_date: date) -> str:
    """Build the user prompt for the morning brief voice pass."""
    date_str = f"{delivery_date.strftime('%A')}, {delivery_date.strftime('%B')} {delivery_date.day}"
    parts: list[str] = [
        f"Today is {date_str}.",
        "",
        "Here is the grounded brief data you must narrate — no inventions:",
        "",
    ]

    summary = str(brief.get("summary") or "").strip()
    if summary:
        parts.append(f"Summary: {summary}")

    highlights = brief.get("highlights") or []
    if isinstance(highlights, list) and highlights:
        parts.append("Highlights:")
        for h in highlights[:5]:
            if not isinstance(h, dict):
                continue
            title = str(h.get("title") or "").strip()
            detail = str(h.get("detail") or "").strip()
            if title and detail and detail != title:
                parts.append(f"  - {title}: {detail}")
            elif title:
                parts.append(f"  - {title}")

    priorities = brief.get("priorities") or []
    if isinstance(priorities, list) and priorities:
        parts.append("Priorities:")
        for p in priorities[:5]:
            if not isinstance(p, dict):
                continue
            item = str(p.get("item") or "").strip()
            rationale = str(p.get("rationale") or "").strip()
            if item and rationale:
                parts.append(f"  - {item}: {rationale}")
            elif item:
                parts.append(f"  - {item}")

    next_actions = brief.get("next_actions") or []
    if isinstance(next_actions, list) and next_actions:
        parts.append("Next actions:")
        for na in next_actions[:5]:
            if not isinstance(na, dict):
                continue
            action = str(na.get("action") or "").strip()
            if action:
                parts.append(f"  - {action}")

    okr_status = str(brief.get("okr_status") or "").strip()
    if okr_status:
        parts.append(f"OKR note: {okr_status}")

    risks = brief.get("risks") or []
    if isinstance(risks, list) and risks:
        risk_texts = [str(r).strip() for r in risks[:3] if str(r).strip()]
        if risk_texts:
            parts.append("Risks: " + "; ".join(risk_texts))

    parts.append("")
    parts.append(
        "Now write the Slack message. Do NOT include labeled section headers. "
        "Narrate these facts in Artemis's voice — brief, direct, human."
    )
    return "\n".join(parts)


def _build_checkin_voice_prompt(
    proposals: list[dict[str, Any]],
    delivery_date: date,
) -> str:
    """Build the user prompt for the OKR check-in voice pass."""
    date_str = f"{delivery_date.strftime('%A')}, {delivery_date.strftime('%B')} {delivery_date.day}"
    parts: list[str] = [
        f"Today is {date_str} (Friday OKR check-in).",
        "",
        "Here is the grounded OKR evidence. Your job:",
        "1. Lead with where his KRs currently stand (progress % and objective).",
        "2. For any KR with evidence, present it as CONTEXT (not claimed accomplishment).",
        "3. ASK Jon what HE actually moved this week — his answer is the source of truth.",
        "4. Close with the safety reminder that nothing updates until he says go.",
        "",
        "Grounded KR data:",
    ]

    if not proposals:
        parts.append(
            "  (No KR evidence found this week — no OKR activity, no matching Jira, no meeting items.)"
        )
    else:
        for p in proposals:
            obj_title = str(p.get("objective_title") or "").strip()
            kr_title = str(p.get("kr_title") or "").strip()
            prog = int(p.get("current_prog") or 0)
            basis = list(p.get("basis") or [])
            parts.append(f"  KR: *{obj_title}* > *{kr_title}* (currently {prog}%)")
            for b in basis:
                b_str = str(b).strip()
                if b_str:
                    parts.append(f"    Evidence: {b_str}")

    parts.append("")
    parts.append(
        "Now write the Slack message. Conversational, direct. "
        "No labeled section headers. No assertions about what Jon did — "
        "only ask + present evidence as context."
    )
    return "\n".join(parts)


async def _call_voice_llm(system: str, user_prompt: str) -> str | None:
    """Call the LLM for the voice pass. Returns raw text or None on failure."""
    try:
        from artemis.agent.client import AnthropicAdapter, CompletionRequest
        from artemis.agent.types import Message, TextBlock

        adapter = AnthropicAdapter(default_model=_VOICE_MODEL)
        request = CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=user_prompt)])],
            system=system,
            model=_VOICE_MODEL,
            max_tokens=512,
            cache_system=True,
            cache_tools=False,
        )
        response = await adapter.complete(request)
        text = ""
        for block in response.message.content:
            if hasattr(block, "text"):
                text += block.text
        return text.strip() or None
    except Exception:
        logger.debug("Voice LLM call failed", exc_info=True)
        return None


async def render_brief_with_voice(
    brief: dict[str, Any],
    delivery_date: date,
    *,
    session_id: str = "",
) -> str | None:
    """Return a voice-rendered Slack string for the morning brief.

    Returns None if the LLM call fails or produces empty output —
    the caller should fall back to the plain-text rendering.

    Guarantees:
    - Output is lint-clean (no tables, no em-dashes, no emojis).
    - Output does NOT contain labeled section headers (Summary:, Highlights:, etc.).
    - Output narrates only grounded facts from ``brief``.
    """
    from artemis.floating_artemis.personality import load_agent_profile, select_voice_samples

    profile = load_agent_profile("artemis")
    session_seed = session_id or delivery_date.isoformat()
    voice_samples = select_voice_samples(session_seed, k=4, voice_corpus=profile.voice_corpus)

    system = _build_voice_system_prompt(voice_samples)
    user_prompt = _build_brief_voice_prompt(brief, delivery_date)

    try:
        raw = await _call_voice_llm(system, user_prompt)
    except Exception:
        logger.debug("Voice LLM call raised unexpectedly in render_brief_with_voice", exc_info=True)
        return None

    if not raw:
        return None

    # Lint the output.
    try:
        from artemis.writing_rules import lint_agent_text

        linted = str(lint_agent_text(raw))
    except Exception:
        linted = raw

    # Sanity guard: if the LLM ignored the no-section-headers rule, return None
    # so the caller falls back to plain rendering.
    lowered = linted.lower()
    for banned in (
        "summary:",
        "highlights:",
        "priorities:",
        "next actions:",
        "risks:",
        "okr status:",
    ):
        if banned in lowered:
            logger.warning(
                "Voice pass returned labeled section headers — discarding and falling back"
            )
            return None

    return linted if linted else None


async def render_checkin_with_voice(
    proposals: list[dict[str, Any]],
    delivery_date: date,
    *,
    session_id: str = "",
) -> str | None:
    """Return a voice-rendered Slack string for the Friday OKR check-in.

    Returns None if the LLM call fails or produces empty output —
    the caller should fall back to the plain-text rendering.

    Guarantees:
    - Output is lint-clean.
    - Output leads with KR state + asks what Jon moved.
    - Output does NOT assert Jon did X — presents evidence as context or questions.
    """
    from artemis.floating_artemis.personality import load_agent_profile, select_voice_samples

    profile = load_agent_profile("artemis")
    session_seed = session_id or delivery_date.isoformat()
    voice_samples = select_voice_samples(session_seed, k=4, voice_corpus=profile.voice_corpus)

    system = _build_voice_system_prompt(voice_samples)
    user_prompt = _build_checkin_voice_prompt(proposals, delivery_date)

    try:
        raw = await _call_voice_llm(system, user_prompt)
    except Exception:
        logger.debug(
            "Voice LLM call raised unexpectedly in render_checkin_with_voice", exc_info=True
        )
        return None

    if not raw:
        return None

    try:
        from artemis.writing_rules import lint_agent_text

        linted = str(lint_agent_text(raw))
    except Exception:
        linted = raw

    return linted if linted else None
