"""The Brand Signals standing picture, as a Slack channel canvas.

Why a canvas and not a message
------------------------------
The brief has two halves that want different surfaces. What is NEW is news: a
few lines, read once, in the channel. The standing picture -- every story in the
window, the state and theme rollups, the competitor escalation ladder -- is a
reference document. Posting a document as a chat message is what produced the
original complaint: one message plus two threaded replies, re-posted daily.

A channel canvas is updated IN PLACE, so there is exactly one permanent link
that is always current and never repeats itself. It also renders tables, which
the rollups want and Slack messages cannot do.

Mechanics worth knowing
-----------------------
* ``canvases.edit`` with a ``replace`` change and NO ``section_id`` replaces the
  whole document. Verified against the live API; that is what makes "rewrite the
  standing picture every morning" a single call.
* The canvas id is DISCOVERED from ``conversations.info`` rather than stored in
  config, so deleting and recreating the canvas cannot leave us writing to an
  id that no longer exists.
* ``canvases:read``/``canvases:write`` were already on Callie's grant, so this
  needed no re-authorization. ``files:read`` is NOT granted, which is why
  nothing here tries to read the canvas back.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from artemis.screentime.national_news import NATIONAL
from artemis.sentiment.report import (
    LOOKBACK_DAYS,
    THEME_LABELS,
    _peer_pattern_section,
)
from artemis.sentiment.themes import THEME_INSTITUTIONAL_REJECTION

_log = logging.getLogger(__name__)

CANVAS_TITLE = "Brand Signals — standing picture"


def _md_line(row: dict[str, Any]) -> str:
    """One story as a canvas-markdown bullet."""
    when = row["published"].strftime("%b %-d") if row.get("published") else "undated"
    where = "" if row["state"] == NATIONAL else f" · {row['state']}"
    labels = ", ".join(THEME_LABELS.get(t, t) for t in row.get("themes") or [])
    tail = f" — _{labels}_" if labels else ""
    # Escape the pipe: an unescaped one inside a table cell or link text breaks
    # the row. Cheaper to neutralise than to detect context.
    title = row["title"].replace("|", "-")
    return f"* **{when}**{where} — [{title}]({row['link']}){tail}"


def _table(header: tuple[str, str], rows: Sequence[tuple[str, int]]) -> list[str]:
    if not rows:
        return []
    out = [f"| {header[0]} | {header[1]} |", "| --- | --- |"]
    out += [f"| {label} | {count} |" for label, count in rows]
    return out


def compose_canvas_markdown(
    findings: list[dict[str, Any]],
    picture: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Render the standing picture. Pure — no I/O, so the wording is testable."""
    now = now or datetime.now().astimezone()
    parts: list[str] = [f"# {CANVAS_TITLE}"]
    parts.append(
        f"_Updated {now.strftime('%A, %B %-d at %-I:%M %p')} · "
        f"{picture['total']} stories in the last {LOOKBACK_DAYS} days · "
        f"{picture['corpus']} tracked since we started keeping them._"
    )

    if not findings:
        parts.append(
            "\n## Nothing in the window\n\nBoth the vendor lane and the "
            "category sweep ran and returned nothing. This is a real result, "
            "not an outage."
        )
        return "\n".join(parts)

    named = [r for r in findings if r["amira"]]
    institutional = [
        r
        for r in findings
        if THEME_INSTITUTIONAL_REJECTION in (r.get("themes") or []) and not r["amira"]
    ]

    parts.append("\n## Where it is")
    if picture["states"]:
        parts += _table(("State", "Stories"), picture["states"])
        parts.append(
            "\n_States are only listed when the coverage names them, or names a "
            "district prominent enough to identify without one. Stories we cannot "
            "place are counted in the total but not attributed — a wrong state is "
            "worse than none._"
        )
    else:
        parts.append("_No story in the window could be placed in a single state._")

    if picture["themes"]:
        parts.append("\n## What is being said")
        parts += _table(
            ("Theme", "Stories"),
            [(THEME_LABELS.get(t, t).capitalize(), n) for t, n in picture["themes"]],
        )

    parts.append(f"\n## Amira by name ({len(named)})")
    if named:
        parts += [_md_line(r) for r in named]
    else:
        parts.append(
            "_Nothing in the window names Amira. The backlash is currently "
            "category-level, not aimed at us._"
        )

    if institutional:
        parts.append(f"\n## Institutional action ({len(institutional)})")
        parts.append(
            "_The commercially severe half. A parent complaint is sentiment; a "
            "district or board decision is a contract._"
        )
        parts += [_md_line(r) for r in institutional]

    # Reuse the peer ladder from the Slack composer so the two surfaces cannot
    # disagree about which competitor is escalating fastest.
    shown = {r["link"] for r in named} | {r["link"] for r in institutional}
    ladder = _peer_pattern_section(findings, shown)
    if ladder:
        parts.append("")
        # Convert the Slack-flavoured lines to canvas markdown.
        for line in ladder:
            converted = line.replace("*Pattern to watch", "## Pattern to watch")
            converted = converted.replace("•", "*").replace("*Litigation*", "**Litigation**")
            converted = converted.replace("*Contract*", "**Contract**")
            converted = converted.replace("*Board Action*", "**Board Action**")
            parts.append(converted.lstrip("\n"))

    rest = [r for r in findings if r["link"] not in shown]
    if rest:
        parts.append(f"\n## Everything else in the window ({len(rest)})")
        parts += [_md_line(r) for r in rest]

    parts.append("\n## What this does not cover")
    parts.append(
        "News coverage only. Facebook parent groups are closed to any automated "
        "read. Reddit access is submitted and awaiting review. Vista Social is "
        "pending. Until those land this under-reports parent-voice chatter, which "
        "is where the specific narratives (voice recordings, training AI on "
        'children, "it\'s just a chatbot") actually live.'
    )
    return "\n".join(parts)


def find_canvas_tabs(channel_info: dict[str, Any]) -> list[str]:
    """Canvas file ids attached to a channel, OLDEST FIRST.

    Two things learned the hard way against the live API:

    * The canvas is exposed under ``properties.tabs`` (entries with
      ``type == "canvas"``), NOT under ``properties.canvas``. Looking in the
      wrong place made an existing canvas invisible.
    * ``conversations.canvases.create`` is NOT idempotent -- calling it on a
      channel that already has a canvas creates a SECOND one. Combined with the
      point above, a create-if-not-found that cannot see the existing canvas
      spawns a new tab on every run.

    Ordering by ``shared_ts`` makes the choice deterministic: whatever happens,
    every run writes to the same, oldest canvas rather than picking whichever
    the API happened to list first.
    """
    tabs = ((channel_info.get("channel") or {}).get("properties") or {}).get("tabs") or []
    found: list[tuple[str, str]] = []
    for tab in tabs:
        if not isinstance(tab, dict) or tab.get("type") != "canvas":
            continue
        data = tab.get("data") or {}
        file_id = data.get("file_id")
        if isinstance(file_id, str) and file_id:
            found.append((str(data.get("shared_ts") or ""), file_id))
    return [file_id for _ts, file_id in sorted(found)]


async def ensure_channel_canvas(client: Any, channel: str) -> str | None:
    """Return the channel's canvas id, creating one only if there is none.

    Discovered rather than configured, because the id is created at runtime and
    a stored copy would silently point at a deleted canvas.
    """
    info = await client.api_call("conversations.info", channel=channel)
    existing = find_canvas_tabs(info)
    if existing:
        if len(existing) > 1:
            _log.warning(
                "brand_signals: channel %s has %d canvas tabs; writing to the "
                "oldest (%s). Extra tabs need removing by hand.",
                channel,
                len(existing),
                existing[0],
            )
        return existing[0]

    created = await client.api_call(
        "conversations.canvases.create",
        channel_id=channel,
        document_content={"type": "markdown", "markdown": f"# {CANVAS_TITLE}\n"},
    )
    new_id = created.get("canvas_id")
    return new_id if isinstance(new_id, str) else None


async def update_standing_canvas(client: Any, channel: str, markdown: str) -> str | None:
    """Rewrite the channel canvas in place. Returns the canvas id, or None."""
    canvas_id = await ensure_channel_canvas(client, channel)
    if not canvas_id:
        _log.warning("brand_signals: no canvas id for channel %s", channel)
        return None
    await client.api_call(
        "canvases.edit",
        canvas_id=canvas_id,
        changes=[
            {
                "operation": "replace",
                "document_content": {"type": "markdown", "markdown": markdown},
            }
        ],
    )
    return canvas_id
