"""Vanilla Forums API v2 client for the Champions community. Read-only.

Nothing here writes back into the community. The token is an admin one, so
that restraint lives in this module rather than in the credential.

**Incremental, not full history.** Hannah's version re-pulls everything on every
run, which is most of why hers is slow. Discussions and comments both support
``dateInserted`` filtering, so a run reads only what is new since the watermark.

**Author email needs a second call.** The join key for the whole pod mapping is
the author's email domain, and ``expand=insertUser`` does NOT include email --
only userID, name, photo and rank. Email is on ``/users`` alone. So a run builds
a userID -> email map and joins locally.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from artemis.config import settings

logger = logging.getLogger(__name__)

#: Vanilla caps page size at 500 for most collections; 100 is the documented
#: default maximum for /users. Kept conservative -- the whole corpus is ~478
#: items, so pagination is about correctness, not throughput.
PAGE_SIZE = 100

AMIRA_STAFF_DOMAIN = "amiralearning.com"


class VanillaError(RuntimeError):
    """A Vanilla API call failed in a way the caller cannot paper over."""


@dataclass(frozen=True)
class VanillaItem:
    """One community item, normalised across the three endpoint shapes."""

    external_id: str
    item_type: str  # discussion | comment | article
    title: str | None
    body: str | None
    url: str | None
    category: str | None
    posted_at: datetime
    author_user_id: int | None
    author_name: str | None
    parent_discussion_id: int | None = None
    post_type: str | None = None


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


class VanillaClient:
    def __init__(self, *, token: str | None = None, base_url: str | None = None) -> None:
        self._token = token if token is not None else settings.vanilla_token
        self._base = (base_url or settings.vanilla_base_url).rstrip("/")
        if not self._token:
            raise VanillaError(
                "No Vanilla API token configured. Set ARTEMIS_VANILLA_TOKEN "
                "(the value lives in ~/.artemis/secrets.env as VANILLA_TOKEN)."
            )

    async def _get(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        # Redirects are followed deliberately. On 2026-09-25 the API host moved
        # from amiralearning.vanillacommunities.com to champions.amiralearning.com
        # and every endpoint began answering 302; without this, a host move is a
        # hard failure rather than a redirect we quietly survive. The base URL
        # points at the new host, so this is the safety net, not the mechanism.
        resp = await client.get(
            f"{self._base}/{path.lstrip('/')}",
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise VanillaError(f"GET /{path} -> {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if not isinstance(payload, list):
            raise VanillaError(f"GET /{path} returned {type(payload).__name__}, expected a list")
        return payload

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        page = 1
        while True:
            batch = await self._get(client, path, {**params, "page": page, "limit": PAGE_SIZE})
            if not batch:
                return
            for row in batch:
                yield row
            # A short page is the last page. Vanilla's Link header would be more
            # correct, but a short page is unambiguous and needs no parsing.
            if len(batch) < PAGE_SIZE:
                return
            page += 1
            if page > 200:  # pragma: no cover - corpus is ~478 items
                logger.warning("champions: /%s exceeded 200 pages, stopping", path)
                return

    async def fetch_post_type_names(self, client: httpx.AsyncClient) -> dict[str, str]:
        """postTypeID -> display name, e.g. ``tip`` -> ``Tip``.

        Hannah's sheet has a "Post Type" column and this is where it comes from:
        Vanilla classifies every post and the author picks it when posting. No
        reason to have a model guess at something the source already knows.
        """
        names: dict[str, str] = {}
        async for row in self._paginate(client, "post-types", {}):
            pid, name = row.get("postTypeID"), row.get("name")
            if isinstance(pid, str) and isinstance(name, str):
                names[pid] = name
        return names

    async def fetch_category_names(self, client: httpx.AsyncClient) -> dict[str, str]:
        """categoryID -> name. A bare id in the sheet is useless to a reader."""
        names: dict[str, str] = {}
        async for row in self._paginate(client, "categories", {}):
            cid, name = row.get("categoryID"), row.get("name")
            if cid is not None and isinstance(name, str):
                names[str(cid)] = name
        return names

    async def fetch_users(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Every account, with the fields the Users tab needs."""
        return [row async for row in self._paginate(client, "users", {})]

    async def fetch_user_emails(self, client: httpx.AsyncClient) -> dict[int, str]:
        """userID -> email, for the domain join.

        Fetched wholesale rather than per-author: the community has a few
        hundred users and one paginated sweep costs less than N lookups.
        """
        emails: dict[int, str] = {}
        async for row in self._paginate(client, "users", {}):
            uid, email = row.get("userID"), row.get("email")
            if isinstance(uid, int) and isinstance(email, str) and "@" in email:
                emails[uid] = email.strip().lower()
        return emails

    async def fetch_items(
        self,
        client: httpx.AsyncClient,
        *,
        since: datetime | None = None,
    ) -> list[VanillaItem]:
        """Discussions, comments and KB articles, newest-first filtering applied.

        ``since`` is exclusive, matched on the item's own insert date.
        """
        items: list[VanillaItem] = []
        # Vanilla's dateInserted filter takes a range expression; ">=" plus a
        # local strict comparison keeps the boundary item from being re-read
        # forever while never skipping one that landed in the same second.
        date_filter = {"dateInserted": f">={since.isoformat()}"} if since else {}

        async for row in self._paginate(
            client, "discussions", {**date_filter, "expand": "insertUser"}
        ):
            item = self._discussion(row)
            if item and (since is None or item.posted_at > since):
                items.append(item)

        async for row in self._paginate(
            client, "comments", {**date_filter, "expand": "insertUser"}
        ):
            item = self._comment(row)
            if item and (since is None or item.posted_at > since):
                items.append(item)

        # Knowledge-base articles live on a different addon and do not accept the
        # same filter, so they are filtered locally.
        try:
            async for row in self._paginate(client, "articles", {}):
                item = self._article(row)
                if item and (since is None or item.posted_at > since):
                    items.append(item)
        except VanillaError as exc:
            # The KB is a separate addon. Its absence must not lose the
            # discussions and comments already collected.
            logger.warning("champions: knowledge-base articles unavailable: %s", exc)

        return items

    # ── per-type normalisation ────────────────────────────────────────────

    def _discussion(self, row: dict[str, Any]) -> VanillaItem | None:
        posted = _parse_dt(row.get("dateInserted"))
        did = row.get("discussionID")
        if posted is None or did is None:
            return None
        user = row.get("insertUser") or {}
        return VanillaItem(
            external_id=f"d-{did}",
            item_type="discussion",
            title=row.get("name"),
            body=row.get("body"),
            url=row.get("url") or row.get("canonicalUrl"),
            category=str(row.get("categoryID")) if row.get("categoryID") is not None else None,
            posted_at=posted,
            author_user_id=row.get("insertUserID"),
            author_name=user.get("name"),
            parent_discussion_id=did if isinstance(did, int) else None,
            post_type=str(row["postTypeID"]) if row.get("postTypeID") else None,
        )

    def _comment(self, row: dict[str, Any]) -> VanillaItem | None:
        posted = _parse_dt(row.get("dateInserted"))
        cid = row.get("commentID")
        if posted is None or cid is None:
            return None
        user = row.get("insertUser") or {}
        return VanillaItem(
            external_id=f"c-{cid}",
            item_type="comment",
            title=None,
            body=row.get("body"),
            url=row.get("url"),
            category=str(row.get("categoryID")) if row.get("categoryID") is not None else None,
            posted_at=posted,
            author_user_id=row.get("insertUserID"),
            author_name=user.get("name"),
            parent_discussion_id=(
                row.get("discussionID") if isinstance(row.get("discussionID"), int) else None
            ),
        )

    def _article(self, row: dict[str, Any]) -> VanillaItem | None:
        posted = _parse_dt(row.get("dateInserted"))
        aid = row.get("articleID")
        if posted is None or aid is None:
            return None
        return VanillaItem(
            external_id=f"a-{aid}",
            item_type="article",
            title=row.get("name"),
            body=row.get("body"),
            url=row.get("url"),
            category=str(row.get("knowledgeCategoryID"))
            if row.get("knowledgeCategoryID") is not None
            else None,
            posted_at=posted,
            author_user_id=row.get("insertUserID"),
            author_name=None,
        )


#: The member-facing host, and since 2026-09-25 the API host too. Rows stored
#: before that carry URLs on the old host, so the rewrite below still matters:
#: 525 of the 529 stored URLs are on amiralearning.vanillacommunities.com.
#: Rewritten at render time rather than at ingest, so the stored value stays
#: exactly what the API said when it was read.
PUBLIC_HOST = "champions.amiralearning.com"
#: The host the API used to answer on. It now 302s to PUBLIC_HOST, but 518 rows
#: were stored with URLs on it and those still need rewriting for display.
API_HOST = "amiralearning.vanillacommunities.com"


def public_url(url: str | None) -> str:
    """The link a Champion would recognise."""
    if not url:
        return ""
    return url.replace(f"//{API_HOST}/", f"//{PUBLIC_HOST}/", 1)


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


def is_amira_staff(domain: str | None) -> bool:
    return domain == AMIRA_STAFF_DOMAIN
