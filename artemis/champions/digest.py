"""Compose the weekly digest: an email organised by state, and a Slack summary.

Composition only. Nothing here sends -- the caller does that, so a digest can be
rendered and read without any risk of it going out, which is how a sample gets
reviewed before delivery is switched on.

The flagged items come first and everything else is context. A digest that
opens with counts trains people to skim past the part that needed them.
"""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.champions.models import ChampionsItem

SHEET_URL = "https://docs.google.com/spreadsheets/d/1RcRcQQeS8FXbYxxA14PXAZ1qvsoBUF0nC3jWrREWFbw"
PODS_ADMIN_URL = "https://central.amiralearning.com/pods/admin"


@dataclass
class Digest:
    since: datetime
    until: datetime
    items: list[ChampionsItem] = field(default_factory=list)
    staff: list[ChampionsItem] = field(default_factory=list)
    flagged: list[ChampionsItem] = field(default_factory=list)
    friction: list[ChampionsItem] = field(default_factory=list)
    escalations: list[ChampionsItem] = field(default_factory=list)
    unplaced_domains: Counter[str] = field(default_factory=Counter)

    @property
    def window_label(self) -> str:
        return f"{self.since.date().isoformat()} to {self.until.date().isoformat()}"


async def collect(session: AsyncSession, *, days: int = 7, until: datetime | None = None) -> Digest:
    until = until or datetime.now(UTC)
    since = until - timedelta(days=days)

    rows = list(
        (
            await session.execute(
                select(ChampionsItem)
                .where(ChampionsItem.posted_at >= since)
                .where(ChampionsItem.posted_at <= until)
                .order_by(ChampionsItem.posted_at.desc())
            )
        ).scalars()
    )

    # Educators are the digest; Amira's own posts are a separate list below it.
    educators = [r for r in rows if not r.is_amira_staff]
    digest = Digest(since=since, until=until, items=educators)
    digest.staff = [r for r in rows if r.is_amira_staff]
    rows = educators
    digest.flagged = [r for r in rows if r.product_issue]
    digest.friction = [r for r in rows if r.adoption_friction and not r.product_issue]
    digest.escalations = [r for r in rows if r.escalation]
    for r in rows:
        if not r.district and r.author_email_domain and r.pod_resolved_at is not None:
            digest.unplaced_domains[r.author_email_domain] += 1
    return digest


def _district(item: ChampionsItem) -> str:
    """The real district when the pod directory resolves it, the email domain
    when it does not.

    Falling back to the domain rather than blanking the cell is deliberate:
    Hannah's current digest shows the domain always, so a blank here would read
    as a regression. It also keeps a shared state-wide domain like `k12.nd.us`
    honest -- it shows as the domain rather than as one arbitrary district.
    """
    return item.district or item.author_email_domain or ""


def _where(item: ChampionsItem) -> str:
    if item.district and item.state:
        return f"{item.district} ({item.state})"
    return _district(item) or "unknown"


def _summary_cell(
    item: ChampionsItem, *, flag: str = "\U0001f6a9", esc: str = "\u26a0\ufe0f"
) -> str:
    """Summary with its markers prefixed.

    The markers live in this cell rather than in columns of their own because
    Slack is narrow and Summary is the column carrying the information.
    """
    prefix = ""
    if item.escalation:
        prefix += f"{esc} "
    if item.product_issue:
        prefix += f"{flag} "
    return prefix + (item.summary or "")


def _sorted_rows(items: list[ChampionsItem]) -> list[ChampionsItem]:
    """State then date, with unplaced last -- it is a data gap, not a state."""
    return sorted(
        items,
        key=lambda i: ((i.state or "").strip() == "", (i.state or "zz"), i.posted_at),
    )


def render_text(d: Digest) -> str:
    lines = [
        f"Champions community digest - {d.window_label}",
        "",
        f"{len(d.items)} new items" + (f" - {len(d.flagged)} flagged" if d.flagged else ""),
        "",
    ]
    width = 74
    for item in _sorted_rows(d.items):
        state = item.state or "--"
        lines.append(
            f"{state:<3} {item.posted_at.strftime('%m/%d/%y')}  {_district(item)[:26]:<26} "
            + ("[x] " if item.replied_by_amira else "    ")
            + _summary_cell(item, flag="[!]", esc="[!!]")[:width]
        )
    if d.staff:
        lines += ["", "AMIRA TEAM POSTS", ""]
        for item in sorted(d.staff, key=lambda i: i.posted_at):
            lines.append(
                f"  {item.posted_at.strftime('%m/%d/%y')}  "
                f"{(item.title or item.summary or '')[:90]}"
            )
    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        lines += [
            "",
            f"NEEDS A DECISION - {total} post(s) show an email domain because the pod",
            "directory could not place them:",
        ]
        for domain, n in d.unplaced_domains.most_common(5):
            lines.append(f"  {domain}: {n}")
        lines.append(f"  Resolve at {PODS_ADMIN_URL}")
    lines += ["", f"Full detail: {SHEET_URL}"]
    return "\n".join(lines)


def render_html(d: Digest) -> str:
    e = html.escape
    th = (
        'style="text-align:left;padding:6px 10px;border:1px solid #d0d5dd;'
        'background:#f9fafb;font-size:12px;letter-spacing:.02em;color:#475467"'
    )
    td = 'style="padding:6px 10px;border:1px solid #d0d5dd;vertical-align:top"'

    rows = []
    for item in _sorted_rows(d.items):
        summary = e(item.summary or "")
        if item.url:
            summary = (
                f'<a href="{e(item.url)}" style="color:#1a56db;text-decoration:none">{summary}</a>'
            )
        markers = ""
        if item.escalation:
            markers += '<span title="needs a human">&#9888;&#65039;</span> '
        if item.product_issue:
            markers += '<span title="product issue">&#128681;</span> '
        rows.append(
            f"<tr>"
            f"<td {td}>{e(item.state or '')}</td>"
            f'<td {td};white-space:nowrap">{item.posted_at.strftime("%m/%d/%y")}</td>'
            f"<td {td}>{e(_district(item))}</td>"
            f"<td {td}>{markers}{summary}</td>"
            f'<td {td};text-align:center">{"&#9989;" if item.replied_by_amira else ""}</td>'
            f"</tr>"
        )

    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'color:#101828;line-height:1.45">',
        '<h1 style="font-size:19px;margin:0 0 2px">Community Hub Digest</h1>',
        f'<div style="color:#667085;font-size:13px;margin-bottom:14px">{e(d.window_label)}</div>',
        f'<div style="font-size:14px;margin-bottom:10px"><strong>{len(d.items)}</strong> new items'
        + (f" &nbsp;·&nbsp; <strong>{len(d.flagged)}</strong> flagged" if d.flagged else "")
        + "</div>",
        '<table style="border-collapse:collapse;font-size:13px;width:100%;max-width:1000px">',
        f"<tr><th {th}>State</th><th {th}>Date</th><th {th}>District</th>"
        f"<th {th}>Summary</th><th {th}>Replied</th></tr>",
        *rows,
        "</table>",
    ]

    if d.staff:
        items = "".join(
            f'<li style="margin-bottom:4px">{item.posted_at.strftime("%m/%d/%y")} — '
            + (
                f'<a href="{e(item.url)}" style="color:#1a56db">{e(item.title or item.summary or "")}</a>'
                if item.url
                else e(item.title or item.summary or "")
            )
            + "</li>"
            for item in sorted(d.staff, key=lambda i: i.posted_at)
        )
        parts.append(
            '<h2 style="font-size:15px;margin:22px 0 8px">Amira team posts</h2>'
            f'<ul style="font-size:13px;padding-left:18px;margin:0">{items}</ul>'
        )

    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        listed = ", ".join(f"{e(dom)} ({n})" for dom, n in d.unplaced_domains.most_common(5))
        parts.append(
            f'<div style="margin-top:20px;font-size:12px;color:#667085">{total} post(s) show an '
            f"email domain above because the pod directory could not place them: {listed}. "
            f'<a href="{PODS_ADMIN_URL}" style="color:#1a56db">Resolve in the pod directory</a>.</div>'
        )

    parts.append(
        f'<div style="margin-top:18px;padding-top:12px;border-top:1px solid #eaecf0;font-size:13px">'
        f'<a href="{SHEET_URL}" style="color:#1a56db">Full detail in the sheet</a></div></div>'
    )
    return "".join(parts)


def render_slack(d: Digest) -> str:
    """Same content as the email, grouped by state.

    Slack mrkdwn has no table, and an ASCII one wraps unreadably on a phone, so
    each item is one line under a state heading. Every column survives: date,
    district, the markers, the summary and the replied tick.
    """
    lines = [
        f"*Community Hub Digest* — {d.window_label}",
        f"{len(d.items)} new items" + (f" · *{len(d.flagged)} flagged*" if d.flagged else ""),
    ]
    current: str | None = None
    for item in _sorted_rows(d.items):
        state = item.state or "Unplaced"
        if state != current:
            lines += ["", f"*{state}*"]
            current = state
        summary = _summary_cell(item)
        if item.url:
            summary = f"<{item.url}|{summary}>"
        tick = " :white_check_mark:" if item.replied_by_amira else ""
        lines.append(f"• {item.posted_at.strftime('%m/%d')} · {_district(item)} — {summary}{tick}")

    if d.staff:
        lines += ["", f"_Amira team posts this period: {len(d.staff)}_"]
    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        top = ", ".join(f"{dom} ({n})" for dom, n in d.unplaced_domains.most_common(3))
        lines += [
            "",
            f"_{total} post(s) show a domain instead of a district: {top}_ "
            f"_<{PODS_ADMIN_URL}|Resolve>_",
        ]
    lines += ["", f"<{SHEET_URL}|Full detail in the sheet>"]
    return "\n".join(lines)


def render_slack_blocks(d: Digest) -> list[dict[str, object]]:
    """Block Kit version — structure Slack will definitely render.

    Slack messages cannot contain tables, so the structure comes from headers,
    dividers and one grouped block per state. Items stay one line each so the
    five fields survive: date, district, markers, summary, replied.

    Blocks are capped at 50 and section text at 3000 characters, so a state with
    many posts is split rather than silently truncated by Slack.
    """
    blocks: list[dict[str, object]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Community Hub Digest", "emoji": True},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*{d.window_label}*  ·  {len(d.items)} new items"
                    + (
                        f"  ·  :triangular_flag_on_post: {len(d.flagged)} flagged"
                        if d.flagged
                        else ""
                    ),
                }
            ],
        },
        {"type": "divider"},
    ]

    def line(item: ChampionsItem) -> str:
        summary = _summary_cell(item)
        if item.url:
            summary = f"<{item.url}|{summary}>"
        tick = "  :white_check_mark:" if item.replied_by_amira else ""
        return f"`{item.posted_at.strftime('%m/%d')}`  *{_district(item)}* — {summary}{tick}"

    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        while buffer:
            chunk, size = [], 0
            while buffer and size + len(buffer[0]) < 2800:
                size += len(buffer[0]) + 1
                chunk.append(buffer.pop(0))
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(chunk)}})

    for item in _sorted_rows(d.items):
        state = item.state or "Unplaced"
        if state != current:
            flush()
            current = state
            buffer.append(f"*{state}*")
        buffer.append(line(item))
    flush()

    footer: list[str] = []
    if d.staff:
        footer.append(f"Amira team posts this period: {len(d.staff)}")
    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        top = ", ".join(f"{dom} ({n})" for dom, n in d.unplaced_domains.most_common(3))
        footer.append(
            f"{total} post(s) show a domain instead of a district: {top} · <{PODS_ADMIN_URL}|Resolve>"
        )
    footer.append(f"<{SHEET_URL}|Full detail in the sheet>")
    blocks.append({"type": "divider"})
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "  ·  ".join(footer)}]}
    )

    # Slack rejects the whole message above 50 blocks, so trim the middle rather
    # than lose the footer, and say so instead of silently dropping rows.
    if len(blocks) > 50:
        keep_head, keep_tail = blocks[:47], blocks[-2:]
        blocks = [
            *keep_head,
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": ":warning: truncated for Slack — full list in the sheet",
                    }
                ],
            },
            *keep_tail,
        ]
    return blocks
