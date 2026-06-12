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
        samples_block = f"\n\nCharacteristic phrases (calibrate register, never quote verbatim):\n{samples_lines}"

    return f"""{persona}{samples_block}

You are writing a SHORT Slack message to Jon Fila, your operator.

Voice: dry-witty British chief of staff. Think Jarvis. Confident, economical, talks TO Jon not AT a room.
Short declarative sentences. Contractions natural. Light dry wit where it fits. Never formal or consultant-register.

Hard rules:
- NO labeled section headers ("Summary:", "Highlights:", "Priorities:", "Risks:", "OKR Status:", "Context:").
- NO bold labels followed by colons in casual replies (e.g. "*Status:*", "*Priority:*").
- NO tables. NO em-dashes. NO emojis.
- NO deck scaffolding, no numbered intro lists like "A few things worth noting:".
- Lead with the substance. Scannable. Every fact from the grounded data — nothing invented.
- If data has nothing substantive, say so in one sentence.
- SHORT: 3-8 lines for a brief, 4-10 lines for an OKR check-in.
- Slack bold (*text*) allowed for KR names and objective names only.
- Output ONLY the Slack message — no preamble, no sign-off, no "Here is:".
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
    kr_snapshot: list[dict[str, Any]] | None = None,
    digest: dict[str, Any] | None = None,
) -> str:
    """Build the user prompt for the OKR check-in voice pass.

    When ``digest`` is provided (p2-okr-opener-digest), the prompt instructs
    the LLM to write a MINIMAL opener using only the digest KRs (in_motion +
    slipping), then ask what Jon moved.  The full kr_snapshot is not dumped
    into the opener — it is available for reference during the reconcile pass
    only.

    When ``digest`` is None (backward-compat), the older full-snapshot prompt
    is used.
    """
    is_friday = delivery_date.weekday() == 4  # Monday=0, Friday=4
    if is_friday:
        date_str = f"Friday {delivery_date.strftime('%B')} {delivery_date.day}"
        checkin_label = "Friday OKR check-in"
    else:
        date_str = (
            f"{delivery_date.strftime('%A')} {delivery_date.strftime('%B')} {delivery_date.day}"
        )
        checkin_label = "OKR check-in"

    parts: list[str] = [
        f"Today is {date_str} ({checkin_label}).",
        "",
    ]

    if digest is not None:
        # New digest-based prompt: minimal, activity-grounded opener.
        in_motion = list(digest.get("in_motion") or [])
        slipping = list(digest.get("slipping") or [])

        parts += [
            "Here is the activity-grounded digest for the check-in opener.",
            "Your job: write 2-5 SHORT sentences in Artemis's voice. NO reciting all KRs.",
            "",
        ]

        if in_motion:
            parts.append("KRs with recent activity (what Jon's been pushing on):")
            for e in in_motion:
                parts.append(f"  *{e['kr_title']}* ({e['prog']}%) — recent OKR activity recorded")
            parts.append("")

        if slipping:
            parts.append(
                "KRs that are low-progress and stalled (no recent activity — needs his attention):"
            )
            for s in slipping:
                parts.append(f"  *{s['kr_title']}* at {s['prog']}% — stalled")
            parts.append("")

        if not in_motion and not slipping:
            parts.append(
                "No grounded activity yet. Keep the opener to one sentence acknowledging "
                "you haven't seen activity log yet."
            )
            parts.append("")

        parts += [
            "After the digest, close with: 'What did you move this week? I'll map it. "
            "Nothing updates until you say go.'",
            "",
            "Rules:",
            "- Name ONLY the KRs listed above. Do NOT mention KRs not in this list.",
            "- Show the real % for each KR you mention.",
            "- No em-dashes. No emojis. No labeled section headers.",
            "- Do NOT assert what Jon accomplished — you are grounding from activity logs.",
            "- Aim 2-5 short sentences total.",
            "",
            "Write the Slack message now.",
        ]
    else:
        # Backward-compat: full-snapshot prompt (used when digest not yet provided).
        parts += [
            "Here is the grounded OKR data. Your job:",
            "1. Lead with where his KRs currently stand (show the numbers from the snapshot below).",
            "2. For any KR with evidence this week, present it as CONTEXT (not claimed accomplishment).",
            "3. ASK Jon what HE actually moved this week — his answer is the source of truth.",
            "4. Close with: nothing updates until he says go.",
            "",
        ]

        # Part C: all-KR snapshot
        if kr_snapshot:
            parts.append("All active KRs (show these numbers):")
            for entry in kr_snapshot:
                obj_title = str(entry.get("objective_title") or "").strip()
                kr_title = str(entry.get("kr_title") or "").strip()
                prog = int(entry.get("prog") or 0)
                target = str(entry.get("target_text") or "").strip()
                target_str = f" (target: {target})" if target else ""
                parts.append(f"  *{obj_title}* > *{kr_title}* — {prog}%{target_str}")
            parts.append("")

        parts.append("Evidence found this week (context only, not claimed accomplishments):")
        if not proposals:
            parts.append(
                "  (No grounded evidence this week — no OKR activity, no matching Jira, no meeting items.)"
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
            "Now write the Slack message. Conversational, direct, dry-witty chief-of-staff voice. "
            "No labeled section headers. No tables. No em-dashes. No emojis. "
            "Show the actual KR numbers from the snapshot. "
            "Do NOT assert what Jon accomplished — only present evidence as context and ask."
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
    kr_snapshot: list[dict[str, Any]] | None = None,
    digest: dict[str, Any] | None = None,
) -> str | None:
    """Return a voice-rendered Slack string for the Friday OKR check-in.

    Returns None if the LLM call fails or produces empty output —
    the caller should fall back to the plain-text rendering.

    Guarantees:
    - Output is lint-clean.
    - When ``digest`` is provided, the opener names ONLY KRs in
      ``digest["in_motion"]`` and ``digest["slipping"]`` — at most 4-5 KRs.
    - Output does NOT assert Jon did X — presents evidence as context or questions.

    Args:
        proposals: Grounded KR proposals for this week (used in reconcile,
            also available to the voice prompt as context).
        delivery_date: The delivery date.
        session_id: Used for deterministic voice sample selection.
        kr_snapshot: All active KR state (kept for backward-compat; not dumped
            into the opener when ``digest`` is provided).
        digest: Activity-grounded digest from ``build_checkin_digest``.  When
            provided, the voice prompt instructs the LLM to use ONLY the
            digest KRs for the opener — no full-KR recitation.
    """
    from artemis.floating_artemis.personality import load_agent_profile, select_voice_samples

    profile = load_agent_profile("artemis")
    session_seed = session_id or delivery_date.isoformat()
    voice_samples = select_voice_samples(session_seed, k=4, voice_corpus=profile.voice_corpus)

    system = _build_voice_system_prompt(voice_samples)
    user_prompt = _build_checkin_voice_prompt(
        proposals,
        delivery_date,
        kr_snapshot=kr_snapshot,
        digest=digest,
    )

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
