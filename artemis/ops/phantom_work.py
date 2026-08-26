"""Detect an agent describing work that was never started.

The failure this catches has happened twice and cost differently each time.

2026-08-12: ``dispatch_research`` returned ``{"status": "dispatched"}`` from a
path that persisted nothing. Callie relayed it in good faith for five weeks;
``argus_research_requests`` was empty the whole time. That was a plumbing bug and
is fixed.

2026-08-26: Callie told Josh to hold the Prince George's sequence "until Argus
clarifies who's actually in the seat" -- and never called ``dispatch_research``.
Nothing malfunctioned. She described a dependency that did not exist, and a real
sequence stalled on research nobody had started.

No plumbing fix catches the second kind, because nothing is broken. The only
reliable signal is the mismatch between what an agent SAID and which tools it
actually called -- which ``agent_traces.tools_used`` has recorded since OBS-1.
This is the "assert the effect, never the transcript" rule pointed at the
transcript itself.

Deliberately narrow. It looks for named delegations with a known tool, so it
reports something actionable rather than a vague suspicion. A miss is acceptable;
a flood of false positives would get the whole health report ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Phrase -> the tool that must appear in the SAME trace for the claim to be true.
#: Patterns require a forward-looking framing ("waiting on", "until", "asked") so
#: that merely NAMING Argus ("Argus found X last week") is not flagged.
_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "dispatch_research",
        re.compile(
            r"(wait(ing)?\s+(on|for)\s+argus"
            r"|until\s+argus"
            r"|argus\s+(is|will|can)\s+\w+"
            r"|(ask|asked|asking|have|had|having)\s+argus"
            r"|argus\s+(to\s+)?(look|check|confirm|clarif|research|dig|verif))",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True)
class PhantomClaim:
    """An agent described work in flight without the tool call to back it."""

    trace_id: int
    agent_id: str
    created_at: datetime
    claimed_tool: str
    excerpt: str

    def describe(self) -> str:
        return (
            f"{self.agent_id} described {self.claimed_tool} as in flight but never "
            f"called it ({self.created_at:%Y-%m-%d %H:%M}): “{self.excerpt}”"
        )


def _excerpt(body: str, match: re.Match[str], width: int = 90) -> str:
    start = max(0, match.start() - width // 3)
    end = min(len(body), match.end() + width)
    text_out = " ".join(body[start:end].split())
    return ("…" if start else "") + text_out + ("…" if end < len(body) else "")


async def find_phantom_claims(
    session: AsyncSession, *, hours: int = 24, limit: int = 200
) -> list[PhantomClaim]:
    """Return recent turns whose text promised work the trace shows never started.

    A trace with an EMPTY ``tools_used`` is skipped, not flagged: rows written
    before OBS-1 populated that column read ``[]`` for every agent and every
    turn, so treating empty as "called nothing" would resurface the entire
    archive as false positives.
    """
    rows = list(
        await session.execute(
            text(
                """
                SELECT id, agent_id, created_at, output_summary, tools_used
                FROM agent_traces
                WHERE created_at > now() - make_interval(hours => :hours)
                  AND output_summary IS NOT NULL
                  AND tools_used IS NOT NULL
                  AND jsonb_array_length(tools_used) > 0
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            {"hours": hours, "limit": limit},
        )
    )

    found: list[PhantomClaim] = []
    for row in rows:
        body = str(row.output_summary or "")
        if not body:
            continue
        # A failed call reads "<name>:error" (OBS-1), which still proves the
        # attempt was made -- the agent did not invent it. Prefix-match so those
        # count as called.
        called = {str(t).split(":", 1)[0] for t in (row.tools_used or [])}
        for tool_name, pattern in _CLAIMS:
            if tool_name in called:
                continue
            match = pattern.search(body)
            if match:
                found.append(
                    PhantomClaim(
                        trace_id=int(row.id),
                        agent_id=str(row.agent_id or "?"),
                        created_at=row.created_at,
                        claimed_tool=tool_name,
                        excerpt=_excerpt(body, match),
                    )
                )
    return found
