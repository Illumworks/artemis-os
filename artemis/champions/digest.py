"""Compose the weekly digest: an email organised by state, and a Slack summary.

Composition only. Nothing here sends -- the caller does that, so a digest can be
rendered and read without any risk of it going out, which is how a sample gets
reviewed before delivery is switched on.

The flagged items come first and everything else is context. A digest that
opens with counts trains people to skim past the part that needed them.
"""

from __future__ import annotations

import html
from collections import Counter, defaultdict
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
                .where(ChampionsItem.is_amira_staff.is_(False))
                .order_by(ChampionsItem.posted_at.desc())
            )
        ).scalars()
    )

    digest = Digest(since=since, until=until, items=rows)
    digest.flagged = [r for r in rows if r.product_issue]
    digest.friction = [r for r in rows if r.adoption_friction and not r.product_issue]
    digest.escalations = [r for r in rows if r.escalation]
    for r in rows:
        if not r.district and r.author_email_domain and r.pod_resolved_at is not None:
            digest.unplaced_domains[r.author_email_domain] += 1
    return digest


def _where(item: ChampionsItem) -> str:
    """District and state, or an honest admission that we do not know."""
    if item.district and item.state:
        return f"{item.district} ({item.state})"
    if item.district:
        return item.district
    if item.author_email_domain:
        return f"unplaced — {item.author_email_domain}"
    return "unknown"


def _by_state(items: list[ChampionsItem]) -> list[tuple[str, list[ChampionsItem]]]:
    buckets: dict[str, list[ChampionsItem]] = defaultdict(list)
    for i in items:
        buckets[i.state or "Unplaced"].append(i)
    # Unplaced sorts last: it is a data gap, not a state.
    return sorted(buckets.items(), key=lambda kv: (kv[0] == "Unplaced", -len(kv[1]), kv[0]))


def render_text(d: Digest) -> str:
    lines = [
        f"Champions community digest — {d.window_label}",
        "",
        f"{len(d.items)} posts from educators · {len(d.flagged)} product issues · "
        f"{len(d.friction)} adoption friction",
        "",
    ]

    if d.escalations:
        lines += ["NEEDS A HUMAN", ""]
        for i in d.escalations:
            lines += [f"  * {_where(i)} — {i.summary or ''}", f"    {i.url or ''}", ""]

    lines += ["PRODUCT ISSUES", ""]
    if not d.flagged:
        lines += ["  None this period.", ""]
    for i in d.flagged:
        lines += [
            f"  [!] {_where(i)}",
            f"      {i.summary or ''}",
            f"      {i.theme or ''} · {i.posted_at.date().isoformat()}",
            f"      {i.url or ''}",
            "",
        ]

    if d.friction:
        lines += ["ADOPTION FRICTION — real, but not a product fault", ""]
        for i in d.friction[:10]:
            lines += [f"  - {_where(i)}: {i.summary or ''}", ""]
        if len(d.friction) > 10:
            lines += [f"  ...and {len(d.friction) - 10} more in the sheet.", ""]

    lines += ["BY STATE", ""]
    for state, group in _by_state(d.items):
        issues = sum(1 for i in group if i.product_issue)
        mark = f" · {issues} product issue{'s' if issues != 1 else ''}" if issues else ""
        lines.append(f"  {state}: {len(group)} post{'s' if len(group) != 1 else ''}{mark}")
    lines.append("")

    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        lines += [
            f"NEEDS A DECISION — {total} post(s) could not be matched to a district",
            "",
        ]
        for domain, n in d.unplaced_domains.most_common(5):
            lines.append(f"  {domain}: {n}")
        lines += ["", f"  Resolve at {PODS_ADMIN_URL}", ""]

    lines += ["", f"Full detail: {SHEET_URL}"]
    return "\n".join(lines)


def render_html(d: Digest) -> str:
    e = html.escape

    def card(i: ChampionsItem, flagged: bool) -> str:
        border = "#b42318" if flagged else "#b8b8b8"
        return (
            f'<div style="border-left:3px solid {border};padding:2px 0 2px 12px;margin:0 0 14px 0">'
            f'<div style="font-weight:600;color:#101828">{e(_where(i))}</div>'
            f'<div style="color:#344054;margin:2px 0">{e(i.summary or "")}</div>'
            f'<div style="color:#667085;font-size:12px">{e(i.theme or "")} · '
            f"{i.posted_at.date().isoformat()}"
            + (f' · <a href="{e(i.url)}" style="color:#3538cd">view</a>' if i.url else "")
            + "</div></div>"
        )

    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:640px;margin:0 auto;color:#101828;line-height:1.5">',
        '<h1 style="font-size:20px;margin:0 0 4px">Champions community digest</h1>',
        f'<div style="color:#667085;font-size:13px;margin-bottom:18px">{e(d.window_label)}</div>',
        '<div style="background:#f9fafb;border-radius:8px;padding:12px 14px;margin-bottom:22px">'
        f"<strong>{len(d.items)}</strong> posts from educators &nbsp;·&nbsp; "
        f"<strong>{len(d.flagged)}</strong> product issues &nbsp;·&nbsp; "
        f"<strong>{len(d.friction)}</strong> adoption friction</div>",
    ]

    if d.escalations:
        parts.append('<h2 style="font-size:15px;margin:0 0 10px">Needs a human</h2>')
        parts += [card(i, True) for i in d.escalations]

    parts.append('<h2 style="font-size:15px;margin:22px 0 10px">Product issues</h2>')
    if d.flagged:
        parts += [card(i, True) for i in d.flagged]
    else:
        parts.append('<div style="color:#667085">None this period.</div>')

    if d.friction:
        parts.append(
            '<h2 style="font-size:15px;margin:22px 0 4px">Adoption friction</h2>'
            '<div style="color:#667085;font-size:13px;margin-bottom:10px">'
            "Real and worth knowing, but not a product fault.</div>"
        )
        parts += [card(i, False) for i in d.friction[:10]]
        if len(d.friction) > 10:
            parts.append(
                f'<div style="color:#667085;font-size:13px">…and {len(d.friction) - 10} '
                f"more in the sheet.</div>"
            )

    parts.append(
        '<h2 style="font-size:15px;margin:22px 0 10px">By state</h2><table '
        'style="border-collapse:collapse;font-size:14px">'
    )
    for state, group in _by_state(d.items):
        issues = sum(1 for i in group if i.product_issue)
        parts.append(
            f'<tr><td style="padding:3px 18px 3px 0;color:#344054">{e(state)}</td>'
            f'<td style="padding:3px 18px 3px 0">{len(group)}</td>'
            f'<td style="padding:3px 0;color:#b42318">'
            f"{(str(issues) + ' product issue' + ('s' if issues != 1 else '')) if issues else ''}"
            "</td></tr>"
        )
    parts.append("</table>")

    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        rows = "".join(
            f'<tr><td style="padding:3px 18px 3px 0;color:#344054">{e(dom)}</td>'
            f'<td style="padding:3px 0">{n}</td></tr>'
            for dom, n in d.unplaced_domains.most_common(5)
        )
        parts.append(
            '<h2 style="font-size:15px;margin:22px 0 4px">Needs a decision</h2>'
            f'<div style="color:#667085;font-size:13px;margin-bottom:10px">{total} post(s) '
            "could not be matched to a district, so they are missing from the state "
            "breakdown above.</div>"
            f'<table style="border-collapse:collapse;font-size:14px">{rows}</table>'
            f'<div style="margin-top:8px"><a href="{PODS_ADMIN_URL}" '
            'style="color:#3538cd;font-size:13px">Resolve in the pod directory</a></div>'
        )

    parts.append(
        f'<div style="margin-top:26px;padding-top:14px;border-top:1px solid #eaecf0">'
        f'<a href="{SHEET_URL}" style="color:#3538cd">Full detail in the sheet</a></div></div>'
    )
    return "".join(parts)


def render_slack(d: Digest) -> str:
    """Slack mirrors the email rather than replacing it: the same judgments,
    short enough to read in a channel."""
    lines = [
        f"*Champions community digest* — {d.window_label}",
        f"{len(d.items)} posts from educators · *{len(d.flagged)}* product issues · "
        f"{len(d.friction)} adoption friction",
    ]
    if d.escalations:
        lines.append("")
        lines.append(f":rotating_light: *{len(d.escalations)} item(s) need a human*")
    lines.append("")
    if d.flagged:
        lines.append("*Product issues*")
        for i in d.flagged:
            link = f" <{i.url}|view>" if i.url else ""
            lines.append(f"• *{_where(i)}* — {i.summary or ''}{link}")
    else:
        lines.append("*Product issues* — none this period.")
    if d.unplaced_domains:
        total = sum(d.unplaced_domains.values())
        top = ", ".join(f"{dom} ({n})" for dom, n in d.unplaced_domains.most_common(3))
        lines += [
            "",
            f"_{total} post(s) could not be matched to a district: {top}_",
            f"_<{PODS_ADMIN_URL}|Resolve in the pod directory>_",
        ]
    lines += ["", f"<{SHEET_URL}|Full detail in the sheet>"]
    return "\n".join(lines)
