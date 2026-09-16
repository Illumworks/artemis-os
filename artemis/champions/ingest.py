"""Phase 1 ingest: pull, classify, store. Delivers nothing.

Nothing here emails or posts to Slack. That is the point: this runs alongside
Hannah's existing setup for a week so the two can be diffed, and a comparison is
only honest if the new pipeline cannot also be sending.

The pod join is NOT done here. It needs the Cloudflare D1 pod directory, and
this machine has no Cloudflare credentials. ``author_email_domain`` is stored on
every row so the join can be backfilled without re-reading Vanilla.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.champions.classify import classify_item, escalation_hits, strip_html
from artemis.champions.models import ChampionsItem, ChampionsRun
from artemis.champions.vanilla import VanillaClient, email_domain, is_amira_staff

logger = logging.getLogger(__name__)

#: claude-code runs as a subprocess per call. Three concurrent marketing runs
#: have produced 408s before now, so the classifier stays below that.
CLASSIFY_CONCURRENCY = 3


@dataclass
class IngestResult:
    run_id: int
    items_seen: int
    items_new: int
    items_classified: int
    items_flagged: int
    escalations: int
    errors: list[str]


async def _watermark(session: AsyncSession) -> datetime | None:
    """Newest item already stored. Null means a full backfill."""
    return (await session.execute(select(func.max(ChampionsItem.posted_at)))).scalar_one_or_none()


async def ingest(
    session: AsyncSession,
    *,
    full: bool = False,
    classify: bool = True,
    limit: int | None = None,
) -> IngestResult:
    """One ingest pass.

    ``full=True`` re-reads the whole community. The default is incremental from
    the newest stored item, which is the difference between a run that takes
    seconds and Hannah's, which re-pulls everything every time.
    """
    errors: list[str] = []
    since = None if full else await _watermark(session)

    run = ChampionsRun(watermark=since, status="running")
    session.add(run)
    await session.flush()

    client = VanillaClient()
    async with httpx.AsyncClient(timeout=60) as http:
        emails = await client.fetch_user_emails(http)
        items = await client.fetch_items(http, since=since)

    if limit is not None:
        items = sorted(items, key=lambda i: i.posted_at, reverse=True)[:limit]

    # ── store first, classify second ──────────────────────────────────────
    # Deliberate: a classifier failure must not lose the item. An unclassified
    # row is picked up by the next run; an item never stored is invisible.
    new_ids: list[str] = []
    for item in items:
        email = emails.get(item.author_user_id or -1)
        domain = email_domain(email)
        stmt = (
            pg_insert(ChampionsItem)
            .values(
                external_id=item.external_id,
                item_type=item.item_type,
                url=item.url,
                category=item.category,
                title=item.title,
                posted_at=item.posted_at,
                author_user_id=item.author_user_id,
                author_name=item.author_name,
                author_email_domain=domain,
                is_amira_staff=is_amira_staff(domain),
                body=item.body,
            )
            .on_conflict_do_nothing(index_elements=["external_id"])
            .returning(ChampionsItem.external_id)
        )
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            new_ids.append(item.external_id)
    await session.flush()

    classified = flagged = escalated = 0
    if classify:
        classified, flagged, escalated, errs = await classify_pending(session)
        errors.extend(errs)

    run.items_seen = len(items)
    run.items_new = len(new_ids)
    run.items_classified = classified
    run.items_flagged = flagged
    run.escalations = escalated
    run.errors = errors or None
    run.status = "ok" if not errors else "partial"
    run.finished_at = datetime.now(UTC)
    await session.flush()

    return IngestResult(
        run_id=run.id,
        items_seen=len(items),
        items_new=len(new_ids),
        items_classified=classified,
        items_flagged=flagged,
        escalations=escalated,
        errors=errors,
    )


async def classify_pending(
    session: AsyncSession, *, limit: int | None = None
) -> tuple[int, int, int, list[str]]:
    """Classify every stored row that has no classification yet.

    Separate from ingest so a prompt change can be re-applied to the corpus
    without re-reading Vanilla -- which will happen, because the flagging rules
    have already changed once.
    """
    stmt = (
        select(ChampionsItem)
        .where(ChampionsItem.classified_at.is_(None))
        .order_by(ChampionsItem.posted_at.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = list((await session.execute(stmt)).scalars())

    errors: list[str] = []
    sem = asyncio.Semaphore(CLASSIFY_CONCURRENCY)

    async def one(row: ChampionsItem) -> tuple[ChampionsItem, object | None]:
        async with sem:
            try:
                return row, await classify_item(
                    title=row.title, body=row.body, item_type=row.item_type
                )
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the run
                errors.append(f"{row.external_id}: {type(exc).__name__}: {exc}"[:200])
                return row, None

    classified = flagged = escalated = 0
    for row, result in await asyncio.gather(*(one(r) for r in rows)):
        # An item with no extractable prose -- an embedded image, an empty
        # comment -- can never be classified, and would otherwise sit in the
        # work queue being retried on every run forever. Image posts are normal
        # in this community, so that set only grows. Marked as SEEN, not judged:
        # classified_at stops the churn while summary and both flags stay null,
        # so nothing downstream reads silence as a verdict of "no problem".
        if result is None and not (row.title or strip_html(row.body)):
            row.classified_at = datetime.now(UTC)
            row.classifier_model = "skipped:no-text"
            continue

        # The escalation tripwire is independent of the LLM and runs even when
        # classification failed -- it is the safety net, not a nice-to-have.
        hits = escalation_hits(f"{row.title or ''} {strip_html(row.body)}")
        if hits and not row.is_amira_staff:
            row.escalation = True
            row.escalation_terms = hits
            escalated += 1

        if result is None:
            continue
        row.summary = result.summary  # type: ignore[attr-defined]
        row.theme = result.theme  # type: ignore[attr-defined]
        # Amira staff posts are never flagged. "Monthly News" tripping a keyword
        # was one of the original bugs, and the exclusion is kept deliberately.
        row.product_issue = (
            False if row.is_amira_staff else bool(result.product_issue)  # type: ignore[attr-defined]
        )
        row.adoption_friction = (
            False if row.is_amira_staff else bool(result.adoption_friction)  # type: ignore[attr-defined]
        )
        row.classified_at = datetime.now(UTC)
        row.classifier_model = "claude-code"
        classified += 1
        if row.product_issue:
            flagged += 1

    await session.flush()
    return classified, flagged, escalated, errors
