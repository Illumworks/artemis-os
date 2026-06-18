"""Async Jira REST API v3 client.

Basic auth (email:api_token) for all operations — OAuth is out of scope.
All HTTP via httpx.AsyncClient; a single shared client is used per method
to allow asyncio.gather without per-coroutine client churn on concurrent calls.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any
from urllib.parse import quote

import httpx

_DEFAULT_COLUMN_MAP: dict[str, list[str]] = {
    "todo": ["New", "To Do", "Open"],
    "prog": ["In Progress"],
    "blocked": ["Blocked"],
    "review": ["In Review"],
    "done": ["Done", "Closed", "Resolved"],
}

_OVERVIEW_FIELDS = [
    "summary",
    "assignee",
    "status",
    "priority",
    "labels",
    "created",
    "updated",
    "comment",
    "worklog",
    "attachment",
    "customfield_10020",
]

_ISSUE_FIELDS = [
    "summary",
    "description",
    "status",
    "priority",
    "assignee",
    "labels",
    "created",
    "updated",
    "comment",
    "worklog",
    "attachment",
    "issuetype",
    "project",
]


class JiraAPIError(Exception):
    def __init__(self, operation: str, status: int, detail: str = "") -> None:
        super().__init__(f"Jira {operation} failed ({status}): {detail[:200]}")
        self.operation = operation
        self.status = status
        self.detail = detail


# ── ADF helpers ───────────────────────────────────────────────────────────────


def adf_to_text(node: dict[str, Any] | None) -> str:
    """Flatten an Atlassian Document Format node to plain text."""
    if not node:
        return ""
    ntype = node.get("type", "")
    if ntype == "text":
        return str(node.get("text", ""))
    if ntype == "hardBreak":
        return "\n"
    if ntype == "mention":
        attrs: dict[str, Any] = node.get("attrs") or {}
        return f"@{attrs.get('text') or attrs.get('id', 'someone')}"
    if ntype == "emoji":
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or "")
    if ntype == "inlineCard":
        return str((node.get("attrs") or {}).get("url") or "")
    if ntype == "rule":
        return "\n---\n"
    content = node.get("content")
    if isinstance(content, list):
        if ntype == "codeBlock":
            lang = str((node.get("attrs") or {}).get("language") or "")
            lang_str = f"({lang}) " if lang else ""
            return f"```{lang_str}\n{''.join(adf_to_text(c) for c in content)}\n```\n"
        if ntype == "blockquote":
            inner = "".join(adf_to_text(c) for c in content)
            return "\n".join(f"> {line}" for line in inner.split("\n")) + "\n"
        if ntype in ("tableCell", "tableHeader"):
            return "".join(adf_to_text(c) for c in content).replace("\n", " ").rstrip() + " | "
        if ntype == "tableRow":
            return "| " + "".join(adf_to_text(c) for c in content) + "\n"
        if ntype == "table":
            return "".join(adf_to_text(c) for c in content) + "\n"
        inner = "".join(adf_to_text(c) for c in content)
        if ntype in ("paragraph", "heading", "listItem", "bulletList", "orderedList"):
            return inner + "\n"
        return inner
    return ""


def _wrap_adf(text: str) -> dict[str, Any]:
    return {
        "version": 1,
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(text)}]}],
    }


def _build_adf(text: str, mentions: list[dict[str, str]]) -> dict[str, Any]:
    """Build ADF doc, substituting proper mention nodes for @name references."""
    if not mentions:
        return _wrap_adf(text)
    sorted_m = sorted(mentions, key=lambda m: -len(m.get("name", "")))
    pattern = "|".join(re.escape(m["name"]) for m in sorted_m if m.get("name"))
    if not pattern:
        return _wrap_adf(text)
    re_ = re.compile(rf"@@?({pattern})")
    nodes: list[dict[str, Any]] = []
    last = 0
    for match in re_.finditer(text):
        if match.start() > last:
            nodes.append({"type": "text", "text": text[last : match.start()]})
        mention = next((m for m in sorted_m if m.get("name") == match.group(1)), None)
        if mention:
            nodes.append(
                {"type": "mention", "attrs": {"id": mention["id"], "text": f"@{match.group(1)}"}}
            )
        last = match.end()
    if last < len(text):
        nodes.append({"type": "text", "text": text[last:]})
    return {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": nodes}]}


def _description_to_adf(text: str) -> dict[str, Any]:
    """Convert multi-paragraph plain text to ADF (for issue description updates)."""
    if not text.strip():
        return {"type": "doc", "version": 1, "content": []}
    paragraphs = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": para.replace("\n", " ").strip()}],
        }
        for para in re.split(r"\n{2,}", text)
        if para.replace("\n", " ").strip()
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


def _extract_sprint_name(field: Any) -> str:
    if not isinstance(field, list) or not field:
        return ""
    active = next(
        (s for s in field if isinstance(s, dict) and s.get("state") == "active"), field[-1]
    )
    return str(active.get("name", "")) if isinstance(active, dict) else ""


def _status_in_clause(statuses: list[str]) -> str:
    return ", ".join(f'"{s}"' for s in statuses)


def _map_column_item(issue: dict[str, Any]) -> dict[str, Any]:
    f = issue.get("fields") or {}
    worklogs: list[dict[str, Any]] = (f.get("worklog") or {}).get("worklogs") or []
    total_seconds = sum(int(w.get("timeSpentSeconds") or 0) for w in worklogs)
    return {
        "key": issue["key"],
        "title": f.get("summary") or issue["key"],
        "assignee": (f.get("assignee") or {}).get("displayName") or "",
        "assigneeId": (f.get("assignee") or {}).get("accountId") or "",
        "status": (f.get("status") or {}).get("name") or "",
        "priority": (f.get("priority") or {}).get("name") or "",
        "labels": f.get("labels") or [],
        "created": f.get("created") or "",
        "updated": f.get("updated") or "",
        "commentCount": (f.get("comment") or {}).get("total") or 0,
        "worklogTotal": round(total_seconds / 3600 * 10) / 10,
        "attachmentCount": len(f.get("attachment") or []),
        "sprint": _extract_sprint_name(f.get("customfield_10020")),
    }


# ── Client ────────────────────────────────────────────────────────────────────


class JiraClient:
    def __init__(self, site_url: str, email: str, api_token: str) -> None:
        self._base = site_url.rstrip("/")
        raw = f"{email}:{api_token}".encode()
        self._auth_header = f"Basic {base64.b64encode(raw).decode()}"

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Accept-Language": "en",
        }
        if content_type:
            h["Content-Type"] = content_type
        return h

    async def _fetch_column(
        self, client: httpx.AsyncClient, jql: str, max_items: int
    ) -> list[dict[str, Any]]:
        resp = await client.post(
            f"{self._base}/rest/api/3/search/jql",
            headers=self._headers("application/json"),
            json={"jql": jql, "maxResults": max_items, "fields": _OVERVIEW_FIELDS},
        )
        if not resp.is_success:
            raise JiraAPIError("search/jql", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return [_map_column_item(issue) for issue in (data.get("issues") or [])]

    async def get_overview(
        self,
        *,
        project_key: str = "",
        max_items: int = 20,
        column_map: dict[str, list[str]] | None = None,
        assignee_filter: list[str] | None | str = "me",
    ) -> dict[str, Any]:
        """Fetch a swimlane overview of the board.

        ``assignee_filter`` controls which tickets are returned:

        * ``"me"`` (default) — only the authenticated user's tickets
          (``assignee = currentUser()``).  The morning brief uses this path so
          Jon only sees his own work.
        * A non-empty ``list[str]`` of Jira account IDs — tickets assigned to
          any of those people OR unassigned
          (``assignee IN (...) OR assignee IS EMPTY``).  The board uses this
          when ``team_members`` is configured.
        * ``None`` — no assignee clause; returns the whole project.  Used when
          the board has no team members configured.
        """
        cm: dict[str, list[str]] = {**_DEFAULT_COLUMN_MAP, **(column_map or {})}
        project_clause = f' AND project = "{project_key}"' if project_key else ""

        if assignee_filter == "me":
            # Brief path: only the authenticated user's own tickets.
            assignee_clause = " AND assignee = currentUser()"
        elif isinstance(assignee_filter, list) and assignee_filter:
            # Board path with a configured team: show the team + the owner
            # (currentUser()) + unassigned.  The team_members list may not
            # include the board owner, so we always add currentUser() so that
            # the owner's swim lane always appears alongside the team.
            ids_joined = ", ".join(f'"{aid}"' for aid in assignee_filter)
            assignee_clause = (
                f" AND (assignee IN ({ids_joined})"
                f" OR assignee = currentUser()"
                f" OR assignee IS EMPTY)"
            )
        else:
            # None or empty list: whole project, no assignee restriction.
            assignee_clause = ""

        async with httpx.AsyncClient(timeout=20) as client:
            me_resp = await client.get(f"{self._base}/rest/api/3/myself", headers=self._headers())
            if not me_resp.is_success:
                raise JiraAPIError(
                    "myself",
                    me_resp.status_code,
                    me_resp.text[:200] or "credentials rejected",
                )
            me: dict[str, Any] = me_resp.json()

            todo, prog, blocked, review = await asyncio.gather(
                self._fetch_column(
                    client,
                    f"status IN ({_status_in_clause(cm['todo'])}){project_clause}{assignee_clause} ORDER BY updated DESC",
                    max_items,
                ),
                self._fetch_column(
                    client,
                    f"status IN ({_status_in_clause(cm['prog'])}){project_clause}{assignee_clause} ORDER BY updated DESC",
                    max_items,
                ),
                self._fetch_column(
                    client,
                    f"status IN ({_status_in_clause(cm['blocked'])}){project_clause}{assignee_clause} ORDER BY updated DESC",
                    max_items,
                ),
                self._fetch_column(
                    client,
                    f"status IN ({_status_in_clause(cm['review'])}){project_clause}{assignee_clause} ORDER BY updated DESC",
                    max_items,
                ),
            )

        return {
            "connected": True,
            "authMethod": "basic",
            "hasOauth": False,
            "siteUrl": self._base,
            "configPath": "db",
            "currentUser": {
                "accountId": me.get("accountId") or "",
                "displayName": me.get("displayName") or "",
                "emailAddress": me.get("emailAddress") or "",
            },
            "columns": [
                {"label": "To Do", "key": "todo", "items": todo},
                {"label": "In Progress", "key": "prog", "items": prog},
                {"label": "Blocked", "key": "blocked", "items": blocked},
                {"label": "In Review", "key": "review", "items": review},
            ],
        }

    async def list_projects(self) -> list[dict[str, Any]]:
        """List all projects accessible to the authenticated user.

        Used by the team-members picker when no project_key is configured —
        enumerates so we can merge assignables across the user's accessible
        scope. Each entry has at least {key, name}.
        """
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{self._base}/rest/api/3/project/search",
                headers=self._headers(),
                params={"maxResults": 100},
            )
            if not resp.is_success:
                raise JiraAPIError("project/search", resp.status_code, resp.text[:200])
            data = resp.json()
            values = data.get("values") if isinstance(data, dict) else None
            return list(values or [])

    async def get_issue(self, key: str) -> dict[str, Any]:
        enc = quote(key, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            issue_resp, trans_resp = await asyncio.gather(
                client.get(
                    f"{self._base}/rest/api/3/issue/{enc}",
                    headers=self._headers(),
                    params={"fields": ",".join(_ISSUE_FIELDS)},
                ),
                client.get(
                    f"{self._base}/rest/api/3/issue/{enc}/transitions",
                    headers=self._headers(),
                ),
            )

        if not issue_resp.is_success:
            raise JiraAPIError("get_issue", issue_resp.status_code, issue_resp.text[:200])
        issue: dict[str, Any] = issue_resp.json()

        transitions: list[dict[str, Any]] = []
        if trans_resp.is_success:
            td: dict[str, Any] = trans_resp.json()
            transitions = [
                {"id": t["id"], "name": t["name"], "to": (t.get("to") or {}).get("name") or ""}
                for t in (td.get("transitions") or [])
            ]

        f = issue.get("fields") or {}
        return {
            "key": issue["key"],
            "url": f"{self._base}/browse/{issue['key']}",
            "projectKey": (f.get("project") or {}).get("key")
            or "-".join(issue["key"].split("-")[:-1]),
            "title": f.get("summary") or issue["key"],
            "description": adf_to_text(f.get("description")).strip(),
            "status": (f.get("status") or {}).get("name") or "",
            "priority": (f.get("priority") or {}).get("name") or "",
            "issueType": (f.get("issuetype") or {}).get("name") or "",
            "assignee": (f.get("assignee") or {}).get("displayName") or "",
            "assigneeId": (f.get("assignee") or {}).get("accountId") or "",
            "labels": f.get("labels") or [],
            "created": f.get("created") or "",
            "updated": f.get("updated") or "",
            "comments": [
                {
                    "id": c["id"],
                    "author": (c.get("author") or {}).get("displayName") or "",
                    "body": adf_to_text(c.get("body")).strip(),
                    "created": c.get("created"),
                    "updated": c.get("updated"),
                }
                for c in (f.get("comment") or {}).get("comments") or []
            ],
            "worklogs": [
                {
                    "id": w["id"],
                    "author": (w.get("author") or {}).get("displayName") or "",
                    "timeSpentSeconds": w.get("timeSpentSeconds"),
                    "comment": adf_to_text(w.get("comment")).strip(),
                    "started": w.get("started"),
                }
                for w in (f.get("worklog") or {}).get("worklogs") or []
            ],
            "attachments": [
                {
                    "id": a["id"],
                    "filename": a.get("filename"),
                    "mimeType": a.get("mimeType"),
                    "size": a.get("size"),
                    "url": a.get("content"),
                    "created": a.get("created"),
                }
                for a in (f.get("attachment") or [])
            ],
            "transitions": transitions,
        }

    async def search_issues(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        escaped = query.replace('"', '\\"')
        jql = f'text ~ "{escaped}" ORDER BY updated DESC'
        async with httpx.AsyncClient(timeout=20) as client:
            items = await self._fetch_column(client, jql, max_results)
        return [
            {
                "key": item["key"],
                "summary": item["title"],
                "status": item["status"],
                "assignee": item["assignee"] or None,
                "priority": item["priority"] or None,
                "labels": item["labels"],
                "url": f"{self._base}/browse/{item['key']}",
            }
            for item in items
        ]

    async def get_assignable_users(
        self, project_key: str, team_filter: list[str] | None = None
    ) -> list[dict[str, Any]]:
        page_size = 200
        all_users: list[dict[str, Any]] = []
        start_at = 0
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                resp = await client.get(
                    f"{self._base}/rest/api/3/user/assignable/search",
                    headers=self._headers(),
                    params={
                        "project": project_key,
                        "maxResults": page_size,
                        "startAt": start_at,
                    },
                )
                if not resp.is_success:
                    raise JiraAPIError("assignable_users", resp.status_code, resp.text[:200])
                page: list[dict[str, Any]] = resp.json() if isinstance(resp.json(), list) else []
                all_users.extend(page)
                if len(page) < page_size:
                    break
                start_at += len(page)
        if team_filter:
            filter_set = set(team_filter)
            all_users = [u for u in all_users if u.get("accountId") in filter_set]
        return [
            {
                "accountId": u["accountId"],
                "displayName": u.get("displayName") or "",
                "avatarUrl": (u.get("avatarUrls") or {}).get("48x48")
                or (u.get("avatarUrls") or {}).get("32x32")
                or "",
                "emailAddress": u.get("emailAddress") or "",
            }
            for u in all_users
        ]

    async def add_comment(
        self, key: str, text: str, mentions: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        enc = quote(key, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self._base}/rest/api/3/issue/{enc}/comment",
                headers=self._headers("application/json"),
                json={"body": _build_adf(text, mentions or [])},
            )
        if not resp.is_success:
            raise JiraAPIError("add_comment", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return {
            "id": data["id"],
            "author": (data.get("author") or {}).get("displayName") or "",
            "body": adf_to_text(data.get("body")).strip(),
            "created": data.get("created"),
        }

    async def add_worklog(self, key: str, hours: float, note: str | None = None) -> dict[str, Any]:
        enc = quote(key, safe="")
        time_spent = round(float(hours) * 3600)
        if time_spent <= 0:
            raise ValueError("hours must be > 0")
        payload: dict[str, Any] = {"timeSpentSeconds": time_spent}
        if note:
            payload["comment"] = _wrap_adf(note)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self._base}/rest/api/3/issue/{enc}/worklog",
                headers=self._headers("application/json"),
                json=payload,
            )
        if not resp.is_success:
            raise JiraAPIError("add_worklog", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return {
            "id": data["id"],
            "author": (data.get("author") or {}).get("displayName") or "",
            "timeSpentSeconds": data.get("timeSpentSeconds"),
            "started": data.get("started"),
        }

    async def upload_attachment(
        self, key: str, content: bytes, filename: str, mimetype: str
    ) -> dict[str, Any]:
        enc = quote(key, safe="")
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self._base}/rest/api/3/issue/{enc}/attachments",
                headers=headers,
                files={"file": (filename, content, mimetype)},
            )
        if not resp.is_success:
            raise JiraAPIError("upload_attachment", resp.status_code, resp.text[:200])
        data: Any = resp.json()
        att: dict[str, Any] = data[0] if isinstance(data, list) and data else data
        return {
            "id": att.get("id"),
            "filename": att.get("filename"),
            "mimeType": att.get("mimeType"),
            "size": att.get("size"),
            "url": att.get("content"),
        }

    async def set_assignee(self, key: str, account_id: str | None) -> dict[str, bool]:
        enc = quote(key, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.put(
                f"{self._base}/rest/api/3/issue/{enc}/assignee",
                headers=self._headers("application/json"),
                json={"accountId": account_id or None},
            )
        if not resp.is_success:
            raise JiraAPIError("set_assignee", resp.status_code, resp.text[:200])
        return {"ok": True}

    async def transition_issue(self, key: str, transition_id: str) -> dict[str, bool]:
        enc = quote(key, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self._base}/rest/api/3/issue/{enc}/transitions",
                headers=self._headers("application/json"),
                json={"transition": {"id": str(transition_id)}},
            )
        if not resp.is_success:
            raise JiraAPIError("transition_issue", resp.status_code, resp.text[:200])
        return {"ok": True}

    async def update_description(self, key: str, description_text: str) -> dict[str, bool]:
        enc = quote(key, safe="")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.put(
                f"{self._base}/rest/api/3/issue/{enc}",
                headers=self._headers("application/json"),
                json={"fields": {"description": _description_to_adf(description_text)}},
            )
        if not resp.is_success:
            raise JiraAPIError("update_description", resp.status_code, resp.text[:200])
        return {"ok": True}

    async def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description: str = "",
        assignee_account_id: str | None = None,
        priority_name: str = "Medium",
        labels: list[str] | None = None,
        issue_type_name: str = "Task",
    ) -> dict[str, Any]:
        if not project_key:
            raise ValueError("project_key required")
        if not summary:
            raise ValueError("summary required")
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type_name},
            "priority": {"name": priority_name or "Medium"},
            "labels": [lbl for lbl in (labels or []) if lbl],
        }
        if description:
            fields["description"] = _wrap_adf(description)
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self._base}/rest/api/3/issue",
                headers=self._headers("application/json"),
                json={"fields": fields},
            )
        if not resp.is_success:
            raise JiraAPIError("create_issue", resp.status_code, resp.text[:200])
        data: dict[str, Any] = resp.json()
        return {"key": data["key"], "id": data["id"]}

    async def get_attachment(self, attachment_id: str) -> tuple[bytes, str]:
        """Fetch attachment content, following redirects. Returns (bytes, content_type)."""
        enc = quote(attachment_id, safe="")
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(
                f"{self._base}/rest/api/3/attachment/content/{enc}",
                headers=self._headers(),
            )
        if not resp.is_success:
            raise JiraAPIError("get_attachment", resp.status_code, resp.text[:200])
        content_type = resp.headers.get("content-type") or "application/octet-stream"
        return resp.content, content_type
