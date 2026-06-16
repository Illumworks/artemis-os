"""Context builder and prompt builder for daily brief.

`_build_context_string` mirrors the Node reference's section layout
(same section headers, same truncation limits). `_build_prompt` emits
the trimmed DailyBrief schema:
  - summary:        1-2 sentence day overview
  - top_priorities: up to 3 actionable items (merged priorities + next actions)
  - waiting_on_you: people/threads waiting on Jon
  - okr_at_risk:    at-risk KRs only (null if none)
  - confidence:     quality signal

Removed sections vs. old schema:
  - highlights  (redundant with top_priorities)
  - next_actions (merged into top_priorities)
  - risks        (fold real risks into summary or okr_at_risk)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _build_context_string(sources: dict[str, Any]) -> str:
    lines: list[str] = []
    now = datetime.now(UTC)
    today = now.strftime("%A, %B %-d")
    lines.append(f"Today is {today}.")

    # Previous brief + what was suggested
    if sources.get("previousBrief") is not None:
        prev_snapshot = sources["previousBrief"]
        try:
            prev: dict[str, Any] = (
                prev_snapshot.brief_json if isinstance(prev_snapshot.brief_json, dict) else {}
            )
            generated_at = prev_snapshot.generated_at
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            hours_ago = round((now - generated_at).total_seconds() / 3600)
            lines.append(f"\n## Yesterday's brief (generated {hours_ago}h ago)")
            # Support both old schema (priorities) and new schema (top_priorities)
            priorities = prev.get("top_priorities") or prev.get("priorities") or []
            if priorities:
                lines.append("Suggested priorities:")
                for i, p in enumerate(priorities):
                    urgency = p.get("urgency")
                    suffix = f" ({urgency})" if urgency else ""
                    lines.append(f"  {i + 1}. {p.get('item', '')}{suffix}")
            if prev.get("summary"):
                lines.append(f"Summary: {prev['summary']}")
        except Exception:
            pass

    # Recent sessions (what was actually worked on)
    sessions = sources.get("sessions") or []
    if sessions:
        lines.append("\n## Recently worked on (sessions)")
        for s in sessions[:6]:
            if isinstance(s, dict):
                title = s.get("title") or "Untitled"
                last_used = s.get("last_used_at")
                if last_used:
                    try:
                        when = datetime.fromtimestamp(float(last_used), tz=UTC).strftime(
                            "%a, %b %-d"
                        )
                    except (ValueError, OSError):
                        when = "unknown"
                else:
                    when = "unknown"
                lines.append(f'  - "{title}" ({when})')

    # Jira
    jira = sources.get("jira")
    if jira and jira.get("connected") and jira.get("columns"):
        lines.append("\n## Jira")
        columns = jira["columns"]
        in_prog: list[Any] = next(
            (c.get("items", []) for c in columns if c.get("key") == "prog"), []
        )
        review: list[Any] = next(
            (c.get("items", []) for c in columns if c.get("key") == "review"), []
        )
        blocked: list[Any] = next(
            (c.get("items", []) for c in columns if c.get("key") == "blocked"), []
        )
        if in_prog:
            lines.append("In progress:")
            for t in in_prog[:4]:
                priority = t.get("priority") or "–"
                title = t.get("title") or t.get("summary") or ""
                lines.append(f"  - [{t.get('key', '?')}] {title} ({priority})")
        if review:
            lines.append("In review:")
            for t in review[:3]:
                title = t.get("title") or t.get("summary") or ""
                lines.append(f"  - [{t.get('key', '?')}] {title}")
        if blocked:
            lines.append("Blocked:")
            for t in blocked[:2]:
                title = t.get("title") or t.get("summary") or ""
                lines.append(f"  - [{t.get('key', '?')}] {title}")

    # Calendar
    calendar = sources.get("calendar")
    if calendar:
        lines.append("\n## Calendar today")
        events = calendar.get("todayEvents") or calendar.get("events") or []
        if events:
            for e in events[:5]:
                time_label = e.get("startTime") or e.get("start") or ""
                title = e.get("title") or e.get("summary") or "Event"
                prefix = f"{time_label} " if time_label else ""
                lines.append(f"  - {prefix}{title}")
        else:
            lines.append("  No meetings today.")

    # Slack signals (J8 — may not be available)
    slack = sources.get("slack")
    if slack and slack.get("connected"):
        lines.append("\n## Slack")
        mentions = slack.get("missedMentions") or 0
        dms = slack.get("unreadDMs") or 0
        threads = slack.get("replyNeededThreads") or 0
        lines.append(
            f"  {mentions} missed mentions, {dms} unread DMs, {threads} threads needing reply"
        )

    # OKR
    okr = sources.get("okr")
    if okr and okr.get("objectives"):
        lines.append("\n## OKR status")
        for obj in okr["objectives"][:2]:
            progress = obj.get("progress") or "?"
            lines.append(f"  Objective: {obj.get('title', '')} ({progress}%)")
            krs = obj.get("krs") or obj.get("keyResults") or []
            for kr in krs[:3]:
                status = kr.get("status") or "–"
                if status == "atrisk":
                    status_label = "at risk"
                elif status == "done":
                    status_label = "done"
                else:
                    status_label = status
                prog = kr.get("prog") or kr.get("progress") or 0
                lines.append(f"    KR: {kr.get('title', '')} — {status_label} ({prog}%)")

    # Memory context
    memory = sources.get("memory") or []
    if memory:
        lines.append("\n## Relevant memory / context")
        for m in memory[:4]:
            if hasattr(m, "content"):
                text = m.content or ""
            elif isinstance(m, dict):
                text = m.get("text") or m.get("content") or ""
            else:
                text = str(m)
            if text:
                lines.append(f"  - {text[:120]}")

    return "\n".join(lines)


def _build_prompt(context_string: str) -> str:
    return f"""You are Artemis, a personal work intelligence assistant. Based on the context below, generate a scannable daily brief for Jon.

{context_string}

Generate a JSON object matching this exact schema (no other text, valid JSON only):
{{
  "summary": "1-2 sentences: overall shape of the day and whether yesterday's plan held (if previous brief exists)",
  "top_priorities": [
    {{ "item": "most urgent action grounded in actual tickets/KRs/meetings", "rationale": "why now, under 25 words or null", "urgency": "high" }},
    {{ "item": "second priority", "rationale": "...", "urgency": "medium" }},
    {{ "item": "third priority", "rationale": "...", "urgency": "medium" }}
  ],
  "waiting_on_you": [
    {{ "who": "person or thread name", "context": "one line on what they need or null" }}
  ],
  "okr_at_risk": "1-2 lines naming only KRs that are at-risk or stalled, with their % — null if nothing at risk",
  "confidence": "high"
}}

Rules:
- top_priorities replaces both "Priorities" and "Next Actions" — merge them into one ranked list of max 3 actionable items. No duplication.
- waiting_on_you: use Slack unread DM senders, threads needing reply, or any person explicitly waiting. Empty array if nothing actionable.
- okr_at_risk: only KRs with status "atrisk" or very low progress. Null if all KRs are on track. Do NOT dump full OKR status here.
- summary: 1-2 sentences only. Fold any critical risk into the summary if it doesn't fit elsewhere. Do not list every item.
- Be direct and opinionated. "Focus on X first" not "You might consider X".
- Ground every item in actual data from the context (ticket names, KR names, session titles, meeting names).
- CRITICAL: Never use bare Jira keys (e.g. "MT-456") alone. Always include the ticket title: "MT-456 Fix login redirect". The context already shows the title — use it.
- Keep each "rationale" under 25 words or set to null.
- "urgency" and "confidence" accept only "high", "medium", or "low".
- Return ONLY the JSON object, nothing else."""
