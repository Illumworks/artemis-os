"""The `Users` and `Never Logged In` tabs.

Requested by Hannah 2026-09-23/24 and first built on the MacBook as CSVs; this
is the same logic reading through Artemis so the daily job can write it straight
into the sheet. **Her column labels are kept verbatim, including "# of Logins",
even where Vanilla's field means something slightly different.** Renaming a
column she asked for by name is not a tidy-up.

Two counting rules earn their keep, and both were measured rather than assumed.

**"# of Posts" is countDiscussions, never countPosts.** Vanilla's ``countPosts``
is discussions PLUS comments -- verified across all 1,223 accounts -- so using it
would double-count every commenter beside its own Comments column.

**"Never logged in" is not ``countVisits == 0``.** Fourteen accounts have posted
or commented while showing zero visits, one as recently as 2026-09-08. Listing
those would send a CSM chasing somebody who is actively participating. The test
is no trace at all: no posts, no comments, no visits.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from artemis.champions.pods import PodMatch, resolve
from artemis.champions.vanilla import VanillaClient

logger = logging.getLogger(__name__)

#: Accounts belonging to Amira rather than to the community. The digest reports
#: team posts separately, and Hannah's sample has no staff rows -- she alone has
#: 48 discussions and 48 comments and would otherwise top the ranking.
STAFF_DOMAINS: tuple[str, ...] = ("amiralearning.com", "istation.com")

#: Vanilla's own bookkeeping accounts.
SYSTEM_NAMES: frozenset[str] = frozenset({"system", "stopforumspam", "akismet", "vanilla forums"})

USER_HEADERS: list[str] = [
    "State",
    "District",
    "Name",
    "# of Posts",
    "# of Comments",
    "# of Logins",
    "Most Recent Login",
    "Active",
]


def is_educator(user: dict[str, Any]) -> bool:
    email = str(user.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return False
    if str(user.get("name") or "").strip().lower() in SYSTEM_NAMES:
        return False
    return not email.endswith(STAFF_DOMAINS)


def _row(user: dict[str, Any], match: PodMatch | None) -> dict[str, Any]:
    posts = int(user.get("countDiscussions") or 0)
    comments = int(user.get("countComments") or 0)
    logins = int(user.get("countVisits") or 0)
    return {
        "State": (match.state if match else None) or "",
        # The real district name, blank when the directory cannot place it.
        # Deliberately NOT the email domain here: Hannah asked for district
        # names, and a shared domain resolves to no single district, so naming
        # one would be confidently wrong on a list a CSM acts from.
        "District": (match.district if match else None) or "",
        "Name": str(user.get("name") or ""),
        "# of Posts": posts,
        "# of Comments": comments,
        "# of Logins": logins,
        # Blank when there are no logins. Vanilla's dateLastActive is the
        # account's CREATION date for an account that never logged in -- it
        # equals dateInserted and dateFirstVisit for 668 of the 670 -- so
        # printing it under "Most Recent Login" tells a CSM that somebody logged
        # in two weeks ago on the very tab built to find people who never did.
        "Most Recent Login": str(user.get("dateLastActive") or "")[:10] if logins else "",
        # Not a column Hannah asked for. It exists because the obvious filter --
        # logins > 0 -- drops the fourteen accounts that have posted while
        # showing zero visits.
        "Active": "TRUE" if (posts or comments or logins) else "FALSE",
    }


async def build_user_rows(
    http: httpx.AsyncClient,
    directory: dict[str, PodMatch],
    *,
    client: VanillaClient | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (users, never_logged_in) as row dicts keyed by Hannah's headers."""
    vanilla = client or VanillaClient()
    users_raw = await vanilla.fetch_users(http)
    educators = [u for u in users_raw if is_educator(u)]

    rows = []
    for user in educators:
        domain = str(user.get("email") or "").rsplit("@", 1)[-1].strip().lower()
        rows.append(_row(user, resolve(domain, directory)))

    # Hannah's sample is ordered by comments, which floats the engaged members
    # to the top and leaves the long inactive tail below.
    users = sorted(
        rows,
        key=lambda r: (-r["# of Comments"], -r["# of Posts"], r["Name"].lower()),
    )

    # Rows a CSM can act on come first. Sorting plainly by state would float the
    # unidentifiable accounts -- auto-generated usernames on domains the
    # directory cannot place -- to the top of a list whose entire purpose is
    # "here are people to chase".
    never = sorted(
        (r for r in rows if r["Active"] == "FALSE"),
        key=lambda r: (
            not r["District"],
            r["State"] or "zz",
            r["District"],
            r["Name"].lower(),
        ),
    )
    logger.info(
        "champions: %d educator accounts, %d active, %d never logged in",
        len(rows),
        sum(1 for r in rows if r["Active"] == "TRUE"),
        len(never),
    )
    return users, never


def to_grid(rows: list[dict[str, Any]]) -> list[list[str]]:
    """Header row plus values, in Hannah's column order."""
    return [USER_HEADERS, *[[str(r[h]) for h in USER_HEADERS] for r in rows]]
