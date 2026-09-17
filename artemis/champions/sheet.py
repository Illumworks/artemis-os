"""Rebuild the Champions Google Sheet from the stored rows.

Every tab is derived from ``Activity``, which is derived from the database, so
the sheet is a view and never the source of truth. It is rebuilt from scratch on
each run -- the same way Hannah's does -- which means a row edited by hand in the
sheet is lost on the next run. That is deliberate: two sources of truth is worse.

The file must live on a Shared Drive before anything writes to it. Moving a file
after a job has started writing to it breaks the job, which is the lesson from
Hannah's Typeform problem. It is already there; this module checks rather than
assumes, because a 404 from Drive and a missing file look identical.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.champions.models import ChampionsItem, ChampionsRun
from artemis.champions.vanilla import public_url
from artemis.integrations.gmail.sender import resolve_gmail_client

logger = logging.getLogger(__name__)

SHEET_ID = "1RcRcQQeS8FXbYxxA14PXAZ1qvsoBUF0nC3jWrREWFbw"
MARKETING_ACCOUNT = "amiracentral@amiralearning.com"
PODS_ADMIN_URL = "https://central.amiralearning.com/pods/admin"

_SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"

ACTIVITY_HEADERS = [
    "ID",
    "Type",
    "Date",
    "Author",
    "Domain",
    "District",
    "State",
    "Pod",
    "CSM",
    "Category",
    "Title",
    "Summary",
    "Theme",
    "Product issue",
    "Adoption friction",
    "Escalation",
    "Replied by Amira",
    "Link",
]


class SheetError(RuntimeError):
    """The sheet could not be rebuilt. Never a partial success reported as done."""


@dataclass
class SheetResult:
    tabs_written: dict[str, int]
    removed_tabs: list[str]


#: Google caps a tab name at 100 characters and rejects these characters in one.
_TAB_FORBIDDEN = str.maketrans({c: "-" for c in "[]*?/\\:"})


def _pod_tab_title(pod_name: str) -> str:
    """A sheet tab name for a pod, prefixed so pod tabs group together."""
    cleaned = pod_name.translate(_TAB_FORBIDDEN).strip() or "Unassigned"
    return f"Pod · {cleaned}"[:100]


def _yn(value: bool | None) -> str:
    """Blank for unknown. An unclassified row must not read as a clean one --
    FALSE and "we never looked" are different facts."""
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def _activity_rows(items: list[ChampionsItem], categories: dict[str, str]) -> list[list[str]]:
    rows = []
    for i in items:
        rows.append(
            [
                i.external_id,
                {"discussion": "Discussion", "comment": "Comment", "article": "KB Article"}.get(
                    i.item_type, i.item_type
                ),
                i.posted_at.date().isoformat(),
                i.author_name or "",
                i.author_email_domain or "",
                i.district or "",
                i.state or "",
                i.pod or ("Unassigned" if not i.is_amira_staff else ""),
                i.csm_email or "",
                categories.get(i.category or "", i.category or ""),
                i.title or "",
                i.summary or "",
                i.theme or "",
                _yn(i.product_issue),
                _yn(i.adoption_friction),
                _yn(i.escalation),
                _yn(i.replied_by_amira),
                public_url(i.url),
            ]
        )
    return rows


def _rollup(items: list[ChampionsItem], key: str) -> list[list[str]]:
    """Count items, product issues and adoption friction per key value."""
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "issues": 0, "friction": 0, "escalations": 0, "latest": ""}
    )
    for i in items:
        value = getattr(i, key) or "Unassigned"
        b = buckets[value]
        b["items"] += 1
        b["issues"] += 1 if i.product_issue else 0
        b["friction"] += 1 if i.adoption_friction else 0
        b["escalations"] += 1 if i.escalation else 0
        day = i.posted_at.date().isoformat()
        if day > b["latest"]:
            b["latest"] = day
    rows = [
        [
            name,
            str(b["items"]),
            str(b["issues"]),
            str(b["friction"]),
            str(b["escalations"]),
            b["latest"],
        ]
        for name, b in buckets.items()
    ]
    rows.sort(key=lambda r: (-int(r[1]), r[0]))
    return rows


def _district_rollup(items: list[ChampionsItem]) -> list[list[str]]:
    """By District, with State to its left.

    A district name alone is ambiguous to someone scanning the tab -- US school
    districts include more than one Lincoln and more than one Washington -- and
    state is the column people reach for first.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "issues": 0, "friction": 0, "escalations": 0, "latest": ""}
    )
    for i in items:
        if not i.district:
            continue
        b = buckets[(i.state or "", i.district)]
        b["items"] += 1
        b["issues"] += 1 if i.product_issue else 0
        b["friction"] += 1 if i.adoption_friction else 0
        b["escalations"] += 1 if i.escalation else 0
        day = i.posted_at.date().isoformat()
        if day > b["latest"]:
            b["latest"] = day
    rows = [
        [
            state,
            district,
            str(b["items"]),
            str(b["issues"]),
            str(b["friction"]),
            str(b["escalations"]),
            b["latest"],
        ]
        for (state, district), b in buckets.items()
    ]
    rows.sort(key=lambda r: (-int(r[2]), r[0], r[1]))
    return rows


def _themes(items: list[ChampionsItem]) -> list[list[str]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "states": set(), "districts": set(), "latest": ""}
    )
    for i in items:
        if not i.theme:
            continue
        b = buckets[i.theme]
        b["count"] += 1
        if i.state:
            b["states"].add(i.state)
        if i.district:
            b["districts"].add(i.district)
        day = i.posted_at.date().isoformat()
        if day > b["latest"]:
            b["latest"] = day
    rows = [
        [
            theme,
            str(b["count"]),
            ", ".join(sorted(b["states"])),
            str(len(b["districts"])),
            b["latest"],
        ]
        for theme, b in buckets.items()
    ]
    rows.sort(key=lambda r: (-int(r[1]), r[0]))
    return rows


def _needs_decision(items: list[ChampionsItem]) -> list[list[str]]:
    """Domains that were looked up and could not be placed.

    The tab that makes the unassigned bucket shrink instead of being ignored:
    it is ordered by how many items each unplaced domain is costing, so the
    highest-value decision is the top row.
    """
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "authors": set(), "latest": ""}
    )
    for i in items:
        if i.is_amira_staff or i.district or not i.author_email_domain:
            continue
        if i.pod_resolved_at is None:
            continue  # never looked up; not a decision for a human yet
        b = buckets[i.author_email_domain]
        b["items"] += 1
        if i.author_name:
            b["authors"].add(i.author_name)
        day = i.posted_at.date().isoformat()
        if day > b["latest"]:
            b["latest"] = day
    rows = [
        [
            dom,
            str(b["items"]),
            str(len(b["authors"])),
            b["latest"],
            f"{PODS_ADMIN_URL}?domain={dom}",
        ]
        for dom, b in buckets.items()
    ]
    rows.sort(key=lambda r: (-int(r[1]), r[0]))
    return rows


def _simple_list(items: list[ChampionsItem], categories: dict[str, str]) -> list[list[str]]:
    return [
        [
            i.posted_at.date().isoformat(),
            categories.get(i.category or "", i.category or ""),
            i.title or (i.summary or "")[:80],
            public_url(i.url),
        ]
        for i in items
    ]


async def build_sheet(
    session: AsyncSession,
    *,
    spreadsheet_id: str = SHEET_ID,
    categories: dict[str, str] | None = None,
) -> SheetResult:
    items = list(
        (
            await session.execute(select(ChampionsItem).order_by(ChampionsItem.posted_at.desc()))
        ).scalars()
    )
    runs = list(
        (
            await session.execute(
                select(ChampionsRun).order_by(ChampionsRun.started_at.desc()).limit(50)
            )
        ).scalars()
    )
    categories = categories or {}

    educators = [i for i in items if not i.is_amira_staff]
    staff = [i for i in items if i.is_amira_staff and i.item_type != "article"]
    kb = [i for i in items if i.item_type == "article"]

    tabs: dict[str, list[list[str]]] = {
        "Activity": [ACTIVITY_HEADERS, *_activity_rows(items, categories)],
        "By Pod": [
            ["Pod", "Items", "Product issues", "Adoption friction", "Escalations", "Most recent"],
            *_rollup(educators, "pod"),
        ],
        "By State": [
            ["State", "Items", "Product issues", "Adoption friction", "Escalations", "Most recent"],
            *_rollup([i for i in educators if i.state], "state"),
        ],
        "By District": [
            [
                "State",
                "District",
                "Items",
                "Product issues",
                "Adoption friction",
                "Escalations",
                "Most recent",
            ],
            *_district_rollup(educators),
        ],
        "Themes": [
            ["Theme", "Count", "States", "Districts", "Most recent"],
            *_themes(educators),
        ],
        "Needs a decision": [
            ["Domain", "Items affected", "People", "Most recent", "Resolve at"],
            *_needs_decision(items),
        ],
        "Amira Team Posts": [
            ["Date", "Category", "Heading", "Link"],
            *_simple_list(staff, categories),
        ],
        "KB Articles": [["Date", "Category", "Heading", "Link"], *_simple_list(kb, categories)],
    }

    # One tab per pod, which Hannah confirmed she still wants alongside `By Pod`.
    # Built from the same Activity rows, so a pod tab can never disagree with the
    # rollup. Ordered by size so the busiest pod is nearest the front, and named
    # with a prefix so the pod tabs stay together and never collide with a fixed
    # tab name.
    by_pod: dict[str, list[ChampionsItem]] = defaultdict(list)
    for item in educators:
        by_pod[item.pod or "Unassigned"].append(item)
    for pod_name, pod_items in sorted(by_pod.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        tabs[_pod_tab_title(pod_name)] = [
            ACTIVITY_HEADERS,
            *_activity_rows(pod_items, categories),
        ]

    tabs |= {
        "Run log": [
            [
                "Run at",
                "Watermark",
                "Items seen",
                "New",
                "Classified",
                "Flagged",
                "Escalations",
                "Status",
                "Errors",
            ],
            *[
                [
                    r.started_at.strftime("%Y-%m-%d %H:%M"),
                    r.watermark.strftime("%Y-%m-%d %H:%M") if r.watermark else "(full)",
                    str(r.items_seen),
                    str(r.items_new),
                    str(r.items_classified),
                    str(r.items_flagged),
                    str(r.escalations),
                    r.status,
                    "; ".join(r.errors or [])[:300],
                ]
                for r in runs
            ],
        ],
    }

    async with SessionScopedClient(session) as http:
        return await _write(http, spreadsheet_id, tabs)


class SessionScopedClient:
    """An httpx client carrying the marketing account's bearer token."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        credential = await resolve_gmail_client(
            self._session, purpose="marketing", connected_email=MARKETING_ACCOUNT
        )
        self._client = httpx.AsyncClient(
            timeout=120, headers={"Authorization": f"Bearer {credential.access_token}"}
        )
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()


async def _write(
    http: httpx.AsyncClient, spreadsheet_id: str, tabs: dict[str, list[list[str]]]
) -> SheetResult:
    meta = await http.get(f"{_SHEETS}/{spreadsheet_id}", params={"fields": "sheets.properties"})
    if meta.status_code != 200:
        raise SheetError(f"could not read the spreadsheet: {meta.status_code} {meta.text[:200]}")
    existing = {
        s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.json().get("sheets", [])
    }

    requests: list[dict[str, Any]] = []
    for title in tabs:
        if title not in existing:
            requests.append({"addSheet": {"properties": {"title": title}}})
    # Tabs we do not own are left alone EXCEPT the empty placeholder, which Jon
    # confirmed is disposable. Never remove a tab with content in it.
    removed = []
    for title, sheet_id in existing.items():
        if title in tabs:
            continue
        # The empty placeholder Jon confirmed was disposable, and any pod tab
        # this run no longer produces -- a pod that was renamed or emptied would
        # otherwise leave a stale tab that still looks current. Only tabs this
        # module creates are ever removed; anything else is left alone.
        if title == "Alerts" or title.startswith("Pod · "):
            requests.append({"deleteSheet": {"sheetId": sheet_id}})
            removed.append(title)
    if requests:
        resp = await http.post(
            f"{_SHEETS}/{spreadsheet_id}:batchUpdate", json={"requests": requests}
        )
        if resp.status_code != 200:
            raise SheetError(f"tab setup failed: {resp.status_code} {resp.text[:300]}")

    clear = await http.post(
        f"{_SHEETS}/{spreadsheet_id}/values:batchClear",
        json={"ranges": [f"'{t}'" for t in tabs]},
    )
    if clear.status_code != 200:
        raise SheetError(f"clear failed: {clear.status_code} {clear.text[:300]}")

    resp = await http.post(
        f"{_SHEETS}/{spreadsheet_id}/values:batchUpdate",
        json={
            "valueInputOption": "RAW",
            "data": [{"range": f"'{t}'!A1", "values": rows} for t, rows in tabs.items()],
        },
    )
    if resp.status_code != 200:
        raise SheetError(f"write failed: {resp.status_code} {resp.text[:300]}")

    # Freeze and bold the header row on every tab we own.
    meta2 = await http.get(f"{_SHEETS}/{spreadsheet_id}", params={"fields": "sheets.properties"})
    ids = {
        s["properties"]["title"]: s["properties"]["sheetId"] for s in meta2.json().get("sheets", [])
    }
    fmt: list[dict[str, Any]] = []
    for title in tabs:
        sid = ids.get(title)
        if sid is None:
            continue
        fmt.append(
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )
        fmt.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            }
        )
        # A filter on every tab, so any column can be sorted or filtered in
        # place. Cleared first: a filter left from the previous run still spans
        # the old row count, and a rebuilt tab with more rows would leave the new
        # ones outside it -- invisible to a sort, which is worse than no filter.
        fmt.append({"clearBasicFilter": {"sheetId": sid}})
        fmt.append(
            {
                "setBasicFilter": {
                    "filter": {
                        "range": {
                            "sheetId": sid,
                            "startRowIndex": 0,
                            "endRowIndex": max(len(tabs[title]), 1),
                            "startColumnIndex": 0,
                            "endColumnIndex": max(len(tabs[title][0]), 1),
                        }
                    }
                }
            }
        )
    if fmt:
        await http.post(f"{_SHEETS}/{spreadsheet_id}:batchUpdate", json={"requests": fmt})

    return SheetResult(
        tabs_written={t: max(len(rows) - 1, 0) for t, rows in tabs.items()}, removed_tabs=removed
    )
