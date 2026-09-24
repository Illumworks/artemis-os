"""Run a Champions ingest by hand.

    uv run python -m artemis.champions              # incremental, classify
    uv run python -m artemis.champions --full       # re-read the whole community
    uv run python -m artemis.champions --no-classify  # store only, no model calls
    uv run python -m artemis.champions --classify-only  # label what is already stored

Phase 1 sends nothing. There is no flag here that would email or post.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx

from artemis.champions.deliver import DIGEST_RECIPIENTS, post_slack, send_email
from artemis.champions.digest import collect
from artemis.champions.ingest import classify_pending, ingest, mark_amira_replies, resolve_pods
from artemis.champions.pods import load_domain_directory
from artemis.champions.sheet import build_sheet
from artemis.champions.users import build_user_rows, to_grid
from artemis.champions.vanilla import VanillaClient
from artemis.db import SessionLocal, engine


async def _run(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        if args.deliver:
            digest = await collect(session, days=args.days)
            # An empty week is not a failure, but it is not worth a send either:
            # a digest with nothing in it teaches people to ignore the next one.
            if not digest.items:
                print(f"no educator posts in the last {args.days} days -- nothing sent")
                await session.commit()
                await engine.dispose()
                return
            to = list(args.to) if args.to else list(DIGEST_RECIPIENTS)
            print(
                f"window {digest.window_label} | {len(digest.items)} items | "
                f"{len(digest.flagged)} flagged"
            )
            print(f"email -> {', '.join(to)}")
            message_id = await send_email(session, digest, to=to)
            print(f"  sent id={message_id}")
            if args.slack:
                sent = await post_slack(session, digest, channels=list(args.slack))
                print(f"slack -> {sent}")
            await session.commit()
        elif args.replies:
            touched = await mark_amira_replies(session)
            await session.commit()
            print(f"replied_by_amira set on {touched} row(s)")
        elif args.sheet:
            async with httpx.AsyncClient(timeout=60) as http:
                client = VanillaClient()
                categories = await client.fetch_category_names(http)
                post_types = await client.fetch_post_type_names(http)
                directory = await load_domain_directory(http)
                users, never = await build_user_rows(http, directory, client=client)
            sheet_result = await build_sheet(
                session,
                categories=categories,
                post_types=post_types,
                user_tabs=(to_grid(users), to_grid(never)),
            )
            await session.commit()
            for tab, n in sheet_result.tabs_written.items():
                print(f"  {tab:20} {n:5} rows")
            if sheet_result.removed_tabs:
                print(f"  removed: {', '.join(sheet_result.removed_tabs)}")
        elif args.resolve_pods:
            placed, unplaced = await resolve_pods(session, only_unresolved=not args.full)
            await session.commit()
            total = placed + unplaced
            pct = (100 * placed / total) if total else 0
            print(f"pods: placed {placed}/{total} ({pct:.0f}%) | needs a decision {unplaced}")
        elif args.classify_only:
            classified, flagged, escalated, errors = await classify_pending(
                session, limit=args.limit
            )
            await session.commit()
            print(
                f"classified {classified} | product issues {flagged} | "
                f"escalations {escalated} | errors {len(errors)}"
            )
        else:
            result = await ingest(
                session,
                full=args.full,
                classify=not args.no_classify,
                limit=args.limit,
            )
            await session.commit()
            print(
                f"run {result.run_id}: seen {result.items_seen} | new {result.items_new} | "
                f"classified {result.items_classified} | product issues {result.items_flagged} | "
                f"escalations {result.escalations} | errors {len(result.errors)}"
            )
            for err in result.errors[:5]:
                print(f"  ! {err}")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Champions community ingest (Phase 1)")
    parser.add_argument("--full", action="store_true", help="re-read the whole community")
    parser.add_argument("--no-classify", action="store_true", help="store only, no model calls")
    parser.add_argument("--classify-only", action="store_true", help="label rows already stored")
    parser.add_argument(
        "--resolve-pods", action="store_true", help="fill district/state/pod/CSM from D1"
    )
    parser.add_argument(
        "--replies", action="store_true", help="recompute replied_by_amira across threads"
    )
    parser.add_argument(
        "--deliver", action="store_true", help="send the digest (email, and Slack with --slack)"
    )
    parser.add_argument("--to", action="append", help="override the recipient list; repeatable")
    parser.add_argument(
        "--slack", action="append", help="Slack channel or user id to post to; repeatable"
    )
    parser.add_argument("--days", type=int, default=7, help="digest window in days (default 7)")
    parser.add_argument("--sheet", action="store_true", help="rebuild the Google Sheet")
    parser.add_argument("--limit", type=int, help="cap items processed (for a smoke run)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
