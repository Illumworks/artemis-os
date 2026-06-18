"""Jira router — /api/jira.

Basic auth (email:api_token) for all operations. OAuth is out of scope for
this build — the Python backend uses the same Basic auth that works for reads
in the Node reference.

Config is stored encrypted in integration_configs (provider="jira") via
the existing provider-config repo, mirroring the Slack / GCal pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_jira_config
from artemis.integrations.jira.client import JiraAPIError, JiraClient
from artemis.marketing.routes._auth import require_owner, require_token

logger = logging.getLogger(__name__)

# Router-level require_owner gates every Jira endpoint (owner-only surface).
# Individual route stubs still carry require_token (kept for explicitness; the
# router-level dep runs first but both must pass).
router = APIRouter(
    prefix="/api/jira",
    tags=["jira"],
    dependencies=[Depends(require_token), Depends(require_owner)],
)

# camelCase keys the frontend sends → snake_case keys stored in DB
_CONFIG_FIELD_MAP: dict[str, str] = {
    "siteUrl": "site_url",
    "email": "email",
    "apiToken": "api_token",
    "projectKey": "project_key",
    "maxItemsPerColumn": "max_items_per_column",
    "columnMap": "column_map",
    "teamMembers": "team_members",
}


def _build_saved_config(raw: dict[str, object]) -> dict[str, object]:
    """Return the redacted config shape the frontend expects."""
    _tm = raw.get("team_members")
    return {
        "siteUrl": str(raw.get("site_url") or ""),
        "email": str(raw.get("email") or ""),
        "projectKey": str(raw.get("project_key") or ""),
        "teamMembers": list(_tm) if isinstance(_tm, list) else [],
        "apiTokenSet": bool(raw.get("api_token")),
        "oauth": {"clientId": "", "clientSecretSet": False},
    }


def _make_client(raw: dict[str, object]) -> JiraClient:
    return JiraClient(
        site_url=str(raw.get("site_url") or ""),
        email=str(raw.get("email") or ""),
        api_token=str(raw.get("api_token") or ""),
    )


# ── Config + overview ─────────────────────────────────────────────────────────


@router.get("/overview")
async def jira_overview(
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    *,
    me_only: bool = False,
) -> dict[str, Any]:
    """Return the Jira board overview.

    ``me_only=True`` forces ``assignee = currentUser()`` regardless of the
    configured team members.  The morning brief sets this so Jon only sees
    his own tickets even when the board is configured for a full team.
    """
    raw = await repo.get_provider_config(session, "jira") or {}
    saved_config = _build_saved_config(raw)

    site_url = str(raw.get("site_url") or "")
    email = str(raw.get("email") or "")
    api_token = str(raw.get("api_token") or "")

    if not (site_url and email and api_token):
        return {
            "connected": False,
            "authMethod": "basic",
            "hasOauth": False,
            "siteUrl": site_url,
            "configPath": "db",
            "savedConfig": saved_config,
        }

    client = _make_client(raw)
    if me_only:
        # Brief path: always scope to the authenticated user only.
        assignee_filter: list[str] | None | str = "me"
    else:
        # Board path: show all configured team members (+ unassigned).
        # Fall back to None (whole project) when no team is configured.
        _tm = raw.get("team_members")
        assignee_filter = list(_tm) if isinstance(_tm, list) and _tm else None
    try:
        result = await client.get_overview(
            project_key=str(raw.get("project_key") or ""),
            max_items=int(_m)
            if (_m := raw.get("max_items_per_column")) and isinstance(_m, (int, float, str))
            else 20,
            column_map=raw.get("column_map"),  # type: ignore[arg-type]
            assignee_filter=assignee_filter,
        )
        result["savedConfig"] = saved_config
        return result
    except JiraAPIError as exc:
        return {
            "connected": False,
            "authMethod": "basic",
            "hasOauth": False,
            "error": str(exc),
            "siteUrl": site_url,
            "configPath": "db",
            "savedConfig": saved_config,
        }


@router.post("/config")
async def jira_config(
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, bool]:
    payload = {snake: body[camel] for camel, snake in _CONFIG_FIELD_MAP.items() if camel in body}
    if not payload:
        raise HTTPException(status_code=400, detail="No valid fields provided")
    await repo.upsert_provider_config(session, "jira", payload)
    await session.commit()
    return {"ok": True}


# Note: Jira's "Connect" entry point lives at /api/integrations/jira/oauth/start
# (in artemis/routes/integrations.py) so the frontend pattern matches Slack/GCal.
# Jira is basic-auth — that route just verifies creds + creates the integration row.


# ── Read endpoints ────────────────────────────────────────────────────────────


@router.get("/attachment/{attachment_id}")
async def jira_attachment_proxy(
    attachment_id: str,
    inline: str | None = Query(default=None),
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> Response:
    raw = await repo.get_provider_config(session, "jira") or {}
    if not (raw.get("site_url") and raw.get("email") and raw.get("api_token")):
        raise HTTPException(status_code=400, detail="Jira not configured")
    client = _make_client(raw)
    try:
        content, content_type = await client.get_attachment(attachment_id)
    except JiraAPIError as exc:
        raise HTTPException(status_code=exc.status, detail="Attachment fetch failed") from exc
    headers: dict[str, str] = {}
    if inline == "1":
        headers["Content-Disposition"] = "inline"
    return Response(content=content, media_type=content_type, headers=headers)


@router.get("/search")
async def jira_search(
    q: str = Query(default=""),
    limit: int = Query(default=20),
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q is required")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.search_issues(q.strip(), max_results=limit)
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/issue/{key}")
async def jira_issue_detail(
    key: str,
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.get_issue(key)
    except JiraAPIError as exc:
        code = exc.status if exc.status >= 400 else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/assignable-users")
async def jira_assignable_users(
    project: str = Query(default=""),
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = project.strip() or cfg.project_key
    if not key:
        raise HTTPException(
            status_code=400, detail="project query param or projectKey config required"
        )
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.get_assignable_users(
            key, team_filter=list(cfg.team_members) if cfg.team_members else None
        )
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/team-members")
async def jira_get_team_members(
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    raw = await repo.get_provider_config(session, "jira") or {}
    site_url = str(raw.get("site_url") or "")
    email = str(raw.get("email") or "")
    api_token = str(raw.get("api_token") or "")
    _tm = raw.get("team_members")
    saved: list[str] = list(_tm) if isinstance(_tm, list) else []

    if not (site_url and email and api_token):
        return {"saved": saved, "all_assignable": []}

    client = JiraClient(site_url=site_url, email=email, api_token=api_token)
    project_key = str(raw.get("project_key") or "")

    try:
        if project_key:
            all_assignable = await client.get_assignable_users(project_key)
        else:
            # No project configured — enumerate all accessible projects and
            # merge their assignables. Dedupe on accountId.
            try:
                projects = await client.list_projects()
            except JiraAPIError:
                projects = []
            seen: dict[str, dict[str, Any]] = {}
            for proj in projects[:10]:  # cap at 10 projects to bound latency
                key = proj.get("key")
                if not key:
                    continue
                try:
                    for user in await client.get_assignable_users(key):
                        if acc_id := user.get("accountId"):
                            seen.setdefault(acc_id, user)
                except JiraAPIError:
                    continue  # skip projects we can't query
            all_assignable = list(seen.values())
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"saved": saved, "all_assignable": all_assignable}


@router.put("/team-members")
async def jira_put_team_members(
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    members_raw = body.get("members")
    if not isinstance(members_raw, list):
        raise HTTPException(status_code=400, detail="members must be a list of accountId strings")
    members: list[str] = [str(m) for m in members_raw if m]

    if members:
        raw = await repo.get_provider_config(session, "jira") or {}
        site_url = str(raw.get("site_url") or "")
        email = str(raw.get("email") or "")
        api_token = str(raw.get("api_token") or "")
        project_key = str(raw.get("project_key") or "")
        if site_url and email and api_token and project_key:
            client = JiraClient(site_url=site_url, email=email, api_token=api_token)
            try:
                all_users = await client.get_assignable_users(project_key)
            except JiraAPIError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            valid_ids = {u["accountId"] for u in all_users}
            unknown = [m for m in members if m not in valid_ids]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown accountIds (not assignable in project): {unknown}",
                )

    await repo.upsert_provider_config(session, "jira", {"team_members": members})
    await session.commit()
    return {"ok": True, "saved": members}


# ── Write endpoints ───────────────────────────────────────────────────────────


@router.post("/issue/{key}/comment")
async def jira_add_comment(
    key: str,
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    text = str(body.get("text") or "")
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    mentions = body.get("mentions")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.add_comment(key, text, mentions if isinstance(mentions, list) else [])
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/issue/{key}/worklog")
async def jira_add_worklog(
    key: str,
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    hours_raw = body.get("hours")
    if not hours_raw:
        raise HTTPException(status_code=400, detail="hours required")
    note = body.get("note")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.add_worklog(key, float(hours_raw), str(note) if note else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/issue/{key}/attachment")
async def jira_upload_attachment(
    key: str,
    file: UploadFile,
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    if not file:
        raise HTTPException(status_code=400, detail="file required")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    content = await file.read()
    filename = file.filename or "upload"
    mimetype = file.content_type or "application/octet-stream"
    try:
        return await client.upload_attachment(key, content, filename, mimetype)
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/issue/{key}/assignee")
async def jira_set_assignee(
    key: str,
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, bool]:
    account_id = body.get("accountId")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.set_assignee(key, str(account_id) if account_id else None)
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/issue/{key}/transition")
async def jira_transition_issue(
    key: str,
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, bool]:
    transition_id = body.get("transitionId")
    if not transition_id:
        raise HTTPException(status_code=400, detail="transitionId required")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.transition_issue(key, str(transition_id))
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/issue/{key}/description")
async def jira_update_description(
    key: str,
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, bool]:
    if "description" not in body:
        raise HTTPException(status_code=400, detail="description required")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    try:
        return await client.update_description(key, str(body["description"]))
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/issue")
async def jira_create_issue(
    body: dict[str, Any],
    _: None = Depends(require_token),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    summary = str(body.get("summary") or "")
    if not summary:
        raise HTTPException(status_code=400, detail="summary required")
    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = JiraClient(cfg.site_url, cfg.email, cfg.api_token)
    project_key = str(body.get("projectKey") or cfg.project_key or "")
    labels_raw = body.get("labels")
    try:
        return await client.create_issue(
            project_key=project_key,
            summary=summary,
            description=str(body.get("description") or ""),
            assignee_account_id=body.get("assigneeAccountId") or None,
            priority_name=str(body.get("priorityName") or "Medium"),
            labels=labels_raw if isinstance(labels_raw, list) else [],
            issue_type_name=str(body.get("issueTypeName") or "Task"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
