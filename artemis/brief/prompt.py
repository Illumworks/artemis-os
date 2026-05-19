"""Context builder and prompt builder for daily brief.

Ports _buildContextString and _buildPrompt from the Node reference verbatim
in structure — same section headers, same truncation limits.
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
            priorities = prev.get("priorities") or []
            if priorities:
                lines.append("Suggested priorities:")
                for i, p in enumerate(priorities):
                    ticket = p.get("ticket")
                    suffix = f" ({ticket})" if ticket else ""
                    lines.append(f"  {i + 1}. {p.get('title', '')}{suffix}")
            if prev.get("defer"):
                lines.append(f"Suggested to defer: {prev['defer']}")
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
                lines.append(f"  - [{t.get('key', '?')}] {t.get('summary', '')} ({priority})")
        if review:
            lines.append("In review:")
            for t in review[:3]:
                lines.append(f"  - [{t.get('key', '?')}] {t.get('summary', '')}")
        if blocked:
            lines.append("Blocked:")
            for t in blocked[:2]:
                lines.append(f"  - [{t.get('key', '?')}] {t.get('summary', '')}")

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
    return f"""You are Artemis, a personal work intelligence assistant. Based on the context below, generate a concise daily brief for Jon.

{context_string}

Generate a JSON object with this exact structure (no other text, valid JSON only):
{{
  "headline": "One sentence capturing the most important thing about today",
  "priorities": [
    {{ "rank": 1, "title": "short action title", "why": "1-2 sentence reason grounded in the data above", "ticket": "JIRA-KEY or null" }},
    {{ "rank": 2, "title": "...", "why": "...", "ticket": null }},
    {{ "rank": 3, "title": "...", "why": "...", "ticket": null }}
  ],
  "continuity": "1-2 sentences comparing what was suggested previously vs what sessions show actually happened. null if no previous brief.",
  "context": "1 sentence of key context Jon should hold in mind today (meetings, blockers, deadlines)",
  "defer": "One specific thing to explicitly not worry about today",
  "slackUrgency": "low|medium|high",
  "calendarNote": "brief note about today's schedule or null if no meetings"
}}

Rules:
- Be direct and opinionated. Say "Focus on X first" not "You might consider X"
- Ground every priority in actual data from the context (ticket numbers, KR names, session titles)
- If previous brief exists, acknowledge explicitly whether the plan was followed or interrupted
- Keep "why" under 30 words each
- Return ONLY the JSON object, nothing else"""
