"""Write the missing catalog summaries by opening the assets themselves.

289 of 416 enablement assets (69%) have no summary. They still embed, but on
their title alone, so they lose every compound question — which is why Sara's
"customer facing tech requirement documents for rostering" returned a parent PDF
while the Technical Guide sat unretrieved.

**Why they were empty, and why that was correct.** ``generate_enrichment``
returns None rather than a filler summary, and the prompt told the model it could
not open the file. With 212 of the 289 carrying under 80 characters of record
text — barely more than the title — refusing was the honest outcome. A summary
generated from a title is a paraphrase of the title wearing a description's
clothes, and it would have made the catalog look complete while adding nothing to
retrieval.

**What changed.** We can now open them. The files layer reads PDFs, Google Docs
and web pages, and the marketing Google credential holds drive.readonly. So the
summary is written from the asset's real contents, and the prompt says which of
the two situations it is in.

**The bar.** An asset is only summarised when the fetch returns real text. Below
``MIN_CONTENT_CHARS`` the asset is SKIPPED and stays unsummarised, because no
summary is honest and a fabricated one is not. Videos and interactive demo links
mostly fall here, and that is the right outcome for them.

Everything written lands as ``summary_status='ai_draft'`` — never verified. Two
of 416 are ``enablement_verified`` today; that is a human's call, not this
module's.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.enablement.models import EnablementAsset

logger = logging.getLogger(__name__)

#: Below this many characters, a fetch has not told us what the asset is. The
#: record's own text averages under 80 characters for the assets in question, so
#: this must be comfortably above that to mean anything.
MIN_CONTENT_CHARS = 400

#: Hosts whose links are interactive apps or media players. Fetching returns page
#: chrome, not content. Skipped up front rather than burning a request to
#: discover it -- 63 walkthroughs and 103 videos sit behind these.
_UNFETCHABLE_HOSTS = ("storylane.io", "youtube.com", "youtu.be", "vimeo.com", "loom.com")

#: Media files, recognised by name. A Drive link to a video carries no extension
#: in its URL, so the host filter cannot see it: a 20-asset batch downloaded 20
#: .mp4 files from Drive purely to watch extraction fail on each one. Extraction
#: failing is the correct outcome, but "could not fetch" is the wrong reason to
#: record -- these are not documents and never will be. Catching them by name
#: also saves two Drive API calls apiece.
_MEDIA_EXTENSIONS = (
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".webm",
    ".mkv",
    ".mp3",
    ".wav",
    ".m4a",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
)


@dataclass
class BackfillReport:
    """What the run actually did. Skips are reported, never hidden."""

    considered: int = 0
    #: Excluded by the SQL filter before the limit was applied. Counted so that
    #: "0 summarised of 25" can never be mistaken for coverage of the whole
    #: backlog -- most of the catalog's unsummarised rows live here.
    excluded_unfetchable: int = 0
    summarised: int = 0
    too_little_content: int = 0
    unfetchable_host: int = 0
    fetch_failed: int = 0
    model_declined: int = 0
    skipped_titles: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.summarised} summarised of {self.considered} considered; "
            f"{self.excluded_unfetchable} interactive/media assets excluded before "
            f"the batch and still unsummarised. "
            f"Of the batch: {self.too_little_content} too little content, "
            f"{self.unfetchable_host} unfetchable host, "
            f"{self.fetch_failed} fetch failed, "
            f"{self.model_declined} declined by the model."
        )


def _is_unfetchable(url: str, title: str = "") -> bool:
    """True when the asset is a demo, a video or an image rather than a document."""
    lowered = (url or "").lower()
    if any(host in lowered for host in _UNFETCHABLE_HOSTS):
        return True
    named = (title or "").strip().lower()
    return named.endswith(_MEDIA_EXTENSIONS) or lowered.endswith(_MEDIA_EXTENSIONS)


#: The page reader wraps its result in a header, an optional truncation notice
#: and a trust footer. Anchoring on those exact sentinels rather than splitting
#: on "---" matters: the naive split leaves the truncation notice attached to the
#: body, so the model would be handed the tool's own words as if they were the
#: document's. It is also why two unrelated PDFs both fetched to exactly 20,034
#: characters -- the 20,000-char cap plus 34 characters of leaked footer.
_FRAMING = re.compile(
    r"^--- FETCHED FROM .*? ---\n(?:Notes:.*?\n)?(.*?)\n(?:\[Truncated at |--- end of )",
    re.DOTALL,
)


def _strip_tool_framing(raw: str) -> str:
    """Return only the page's own words, never the reader's."""
    match = _FRAMING.search(raw)
    return (match.group(1) if match else raw).strip()


async def _fetch_document_text(url: str) -> str:
    """Return readable text from an asset's link, or "" if there is none.

    Google Drive links go through the Drive/Docs path (the agent credential holds
    drive.readonly); everything else through the ordinary page reader, which
    already handles PDFs and strips page chrome.
    """
    try:
        if "drive.google.com" in url or "docs.google.com" in url:
            import httpx

            import artemis.db as _db
            from artemis.files.sources.google_files import (
                fetch_google_file,
                find_google_links,
                resolve_agent_google_token,
            )

            links = find_google_links(url)
            if not links:
                return ""
            async with _db.SessionLocal() as session:
                token = await resolve_agent_google_token(session)
                await session.commit()
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
                extracted = await fetch_google_file(links[0][1], token=token, client=client)
            return extracted.text

        from artemis.floating_artemis.tools.web import _read_web_page

        raw = await _read_web_page({"url": url})
        if raw.startswith(("Refusing", "Could not read", "read_web_page failed", "Fetched")):
            return ""
        return _strip_tool_framing(raw)
    except Exception:
        logger.debug("summary backfill: fetch failed for %s", url, exc_info=True)
        return ""


async def backfill_summaries(
    session: AsyncSession, *, limit: int = 25, dry_run: bool = False
) -> BackfillReport:
    """Summarise unsummarised assets from their own contents.

    ``dry_run`` fetches and generates but writes nothing, so a run can be
    inspected before it touches the catalog.
    """
    from artemis.enablement.enrichment import (
        AssetFacts,
        apply_enrichment,
        generate_enrichment,
        reembed,
    )

    # Unsummarised, not archived, and has a link to open.
    unsummarised = (
        or_(EnablementAsset.summary.is_(None), EnablementAsset.summary == ""),
        EnablementAsset.status.is_distinct_from("archived"),
        EnablementAsset.drive_link.isnot(None),
    )
    # Interactive demos and video players are excluded in SQL, not merely skipped
    # in the loop. Ordered by recency they crowd out every real candidate -- a
    # 25-row batch spent all 25 on Storylane walkthroughs and summarised nothing.
    # The limit should buy 25 attempts, not 25 rows.
    fetchable = and_(
        *[EnablementAsset.drive_link.notilike(f"%{host}%") for host in _UNFETCHABLE_HOSTS],
        *[EnablementAsset.title.notilike(f"%{ext}") for ext in _MEDIA_EXTENSIONS],
    )

    excluded = (
        await session.execute(
            select(func.count()).select_from(EnablementAsset).where(*unsummarised).where(~fetchable)
        )
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(EnablementAsset)
                .where(*unsummarised)
                .where(fetchable)
                .order_by(EnablementAsset.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    report = BackfillReport(considered=len(rows), excluded_unfetchable=int(excluded))

    for asset in rows:
        url = asset.drive_link or ""
        title = asset.title or asset.asset_name or f"id={asset.id}"

        if _is_unfetchable(url, title):
            report.unfetchable_host += 1
            report.skipped_titles.append(f"{title} (interactive/media, not a document)")
            continue

        text = await _fetch_document_text(url)
        if not text:
            report.fetch_failed += 1
            report.skipped_titles.append(f"{title} (could not fetch)")
            continue
        if len(text.strip()) < MIN_CONTENT_CHARS:
            # Honest refusal: too little to describe. Leaving it unsummarised is
            # the status quo and is truthful; inventing one is not.
            report.too_little_content += 1
            report.skipped_titles.append(f"{title} ({len(text.strip())} chars)")
            continue

        # Hand the fetched text to the generator without persisting it on the
        # row: it is prompt input for this one call, not catalog data.
        facts = replace(AssetFacts.from_row(asset), document_text=text)
        enrichment = await generate_enrichment(facts, session=session)
        if enrichment is None or not enrichment.summary:
            report.model_declined += 1
            report.skipped_titles.append(f"{title} (model declined)")
            continue

        if dry_run:
            report.summarised += 1
            logger.info("[dry-run] %s -> %s", title, enrichment.summary)
            continue

        # Reuse the existing writer rather than setting fields here: it lands
        # everything as ai_draft, clears any stale review state, and refuses to
        # overwrite a human-entered audience.
        apply_enrichment(asset, enrichment)

        # Re-embed, or the summary never reaches semantic search -- which is the
        # entire point of writing it. `reembed` never raises; a failure leaves
        # the old vector in place.
        await reembed(asset)

        report.summarised += 1

    if not dry_run:
        await session.flush()
    logger.info("enablement summary backfill: %s", report.summary())
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and generate but write nothing, so a run can be read before it lands",
    )
    args = parser.parse_args()

    async def _main() -> None:
        import artemis.db as _db

        async with _db.SessionLocal() as session:
            report = await backfill_summaries(session, limit=args.limit, dry_run=args.dry_run)
            if not args.dry_run:
                await session.commit()

        print(report.summary())
        if report.skipped_titles:
            # Skips are the honest half of the result and are always shown --
            # a run that only printed its successes would misreport coverage.
            print("\nskipped:")
            for line in report.skipped_titles:
                print(f"  - {line}")

    asyncio.run(_main())
