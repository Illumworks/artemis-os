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
from artemis.champions.pods import load_domain_directory, resolve
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
    # RETURNING fires on an update too, so novelty is decided before the write.
    seen_before = set((await session.execute(select(ChampionsItem.external_id))).scalars())
    new_ids: list[str] = []
    for item in items:
        email = emails.get(item.author_user_id or -1)
        domain = email_domain(email)
        base = pg_insert(ChampionsItem).values(
            external_id=item.external_id,
            item_type=item.item_type,
            url=item.url,
            category=item.category,
            post_type=item.post_type,
            title=item.title,
            posted_at=item.posted_at,
            parent_discussion_id=item.parent_discussion_id,
            author_user_id=item.author_user_id,
            author_name=item.author_name,
            author_email_domain=domain,
            is_amira_staff=is_amira_staff(domain),
            body=item.body,
        )
        # Vanilla owns these columns, so a re-read refreshes them -- that is what
        # lets `--full` repair data rather than only add rows. Our own derived
        # columns (summary, theme, the flags, the pod join) are NOT in this list
        # and are never touched: re-reading the source must not discard a
        # judgment or a human's correction.
        stmt = base.on_conflict_do_update(
            index_elements=["external_id"],
            set_={
                "url": base.excluded.url,
                "category": base.excluded.category,
                "post_type": base.excluded.post_type,
                "title": base.excluded.title,
                "body": base.excluded.body,
                "author_name": base.excluded.author_name,
                "author_email_domain": base.excluded.author_email_domain,
                "is_amira_staff": base.excluded.is_amira_staff,
                "parent_discussion_id": base.excluded.parent_discussion_id,
                "updated_at": datetime.now(UTC),
            },
        ).returning(ChampionsItem.external_id)
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result is not None and item.external_id not in seen_before:
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


async def resolve_pods(session: AsyncSession, *, only_unresolved: bool = True) -> tuple[int, int]:
    """Fill district / state / pod / CSM from the D1 pod directory.

    Separate from ingest, and re-runnable: the directory is corrected by hand in
    /pods/admin over time, so a domain that could not be placed today is placed
    on a later pass without re-reading Vanilla. Returns (placed, unplaced).
    """
    stmt = select(ChampionsItem)
    if only_unresolved:
        stmt = stmt.where(ChampionsItem.pod_resolved_at.is_(None))
    rows = list((await session.execute(stmt)).scalars())

    async with httpx.AsyncClient(timeout=60) as http:
        directory = await load_domain_directory(http)

    placed = unplaced = 0
    now = datetime.now(UTC)
    for row in rows:
        match = resolve(row.author_email_domain, directory)
        # pod_resolved_at records that the question was ASKED, whatever the
        # answer. Without it an unplaceable domain is indistinguishable from one
        # never looked up, and the needs-a-decision bucket cannot be counted.
        row.pod_resolved_at = now
        if match is None:
            unplaced += 1
            continue
        # Take whatever the directory can say. An ambiguous domain is ambiguous
        # about the DISTRICT; its state and pod may still be unanimous across
        # every claiming account, and the digest sorts by state. Discarding a
        # fact all claimants agree on is not caution, it is data loss.
        row.district = match.district
        row.state = match.state
        row.pod = match.pod_name or match.pod_slug
        row.csm_email = match.csm_email
        if match.needs_decision:
            unplaced += 1
        else:
            placed += 1

    await session.flush()
    return placed, unplaced


async def mark_amira_replies(session: AsyncSession) -> int:
    """Set replied_by_amira across each thread.

    It is a property of the THREAD, not the item: a discussion counts as replied
    when any comment on it came from an amiralearning.com address, and every
    comment in that thread inherits the same answer. Computed here rather than at
    ingest because a reply can arrive long after the post it answers.
    """
    replied_threads = set(
        (
            await session.execute(
                select(ChampionsItem.parent_discussion_id)
                .where(ChampionsItem.item_type == "comment")
                .where(ChampionsItem.is_amira_staff.is_(True))
                .where(ChampionsItem.parent_discussion_id.is_not(None))
            )
        ).scalars()
    )

    rows = list((await session.execute(select(ChampionsItem))).scalars())
    touched = 0
    for row in rows:
        if row.item_type == "discussion":
            thread = (
                int(row.external_id.removeprefix("d-")) if row.external_id[2:].isdigit() else None
            )
        else:
            thread = row.parent_discussion_id
        value = thread is not None and thread in replied_threads
        if row.replied_by_amira != value:
            row.replied_by_amira = value
            touched += 1
    await session.flush()
    return touched


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
