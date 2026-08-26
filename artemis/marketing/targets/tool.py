"""Callie's tool for absorbing an updated target list.

Josh will re-post the list as it changes. Reading the file is not enough: the
extraction the files layer puts in front of an agent is a SUMMARY -- columns,
row count, and 50 sample rows -- so the model cannot reconstruct 1,287 rows from
its own context. This tool re-fetches the original bytes by Slack file id and
imports them.

**Identity-gated, and layer 2 rather than 3.** A layer-3 confirmation in a shared
Slack channel is answered by whoever replies next (see the note on
``send_slack_dm`` in ``artemis/floating_artemis/tool_registry.py``), so it would
imply a safety that is not there. The real gate is the speaker id, bound as a
closure at registration so tool input can never spoof the requester -- the same
construction CALLIE-1 uses for her guarded DM.

Replacing the target universe is the most consequential write in this package:
get it wrong and Josh is either shown accounts he does not sell into, or shown
nothing at all. Two protections, therefore, not one -- the identity gate here,
and ``import_target_accounts``'s refusal to accept a file that is not a target
list.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from artemis.agent.types import Tool

logger = logging.getLogger(__name__)

IMPORT_TARGET_ACCOUNTS = Tool(
    name="import_target_accounts",
    description=(
        "Replace the new-business TARGET ACCOUNT list from a spreadsheet someone "
        "just posted in Slack. Use this ONLY when explicitly asked to update, "
        "refresh or replace the target account list, and only with the id of a "
        "file from this conversation. Accounts missing from the new file stop "
        "being treated as live targets. Restricted to Jon and Josh. [layer:2]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": (
                    "The Slack file id of the posted spreadsheet (e.g. 'F0BTF3J3HB2'). "
                    "Take it from the attached-file context; never guess one."
                ),
            }
        },
        "required": ["file_id"],
    },
)


def authorized_importer_ids() -> frozenset[str]:
    """Who may replace the target list: Jon and Josh.

    Reuses the vision allowlist rather than adding a third setting to keep in
    sync -- it already means "people trusted to hand this system data", and it
    is currently exactly those two.
    """
    from artemis.files.authorization import vision_user_ids

    return vision_user_ids()


def _make_import_target_accounts(
    speaker_id: str | None,
) -> Callable[[dict[str, Any]], Awaitable[str]]:
    """Bind the requester's identity so tool input cannot spoof it."""

    async def _run(inp: dict[str, Any]) -> str:
        if not speaker_id or speaker_id not in authorized_importer_ids():
            # Names the reason precisely: this is a permissions answer, not a
            # "something went wrong" answer, and the difference is what stops
            # people debugging the wrong problem.
            return (
                "Not permitted: replacing the target account list is restricted to Jon "
                "and Josh. Tell the requester that, and do not attempt it another way."
            )

        file_id = str(inp.get("file_id") or "").strip()
        if not file_id:
            return "import_target_accounts needs the Slack file_id of the posted spreadsheet."

        try:
            import httpx

            import artemis.db as _db
            from artemis.files.extract.text import decode_bytes
            from artemis.marketing.targets.ingest import (
                TargetListError,
                import_target_accounts,
                resolve_target_districts,
            )

            token, name, url = await _resolve_slack_file(file_id)
            if not url:
                return (
                    f"Could not find a downloadable Slack file with id {file_id!r}. "
                    "Check the id came from the attached-file context."
                )

            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if response.status_code >= 400:
                return (
                    f"Slack refused the download of {name} (HTTP {response.status_code}). "
                    "This is a permissions problem, not a problem with the file."
                )

            payload, _note = decode_bytes(response.content, filename=name)
            delimiter = "\t" if name.lower().endswith((".tsv", ".tab")) else ","

            async with _db.SessionLocal() as session:
                try:
                    report = await import_target_accounts(
                        session,
                        payload,
                        source_file_id=file_id,
                        imported_by=speaker_id,
                        delimiter=delimiter,
                    )
                except TargetListError as exc:
                    # Nothing was written: the import validates before it mutates.
                    return f"Did not import {name}: {exc.reason}"
                counts = await resolve_target_districts(session)
                await session.commit()

            linked = counts.get("exact", 0) + counts.get("normalized", 0)
            total = sum(counts.values()) or 1
            lines = [
                f"Target account list updated from {name}: {report.summary()}.",
                f"{linked} of {total} accounts ({100 * linked / total:.0f}%) are linked to a "
                "district record; the rest are matched by name instead.",
            ]
            if report.departed:
                lines.append(
                    f"{report.departed} account(s) were on the previous list and are not on "
                    "this one -- they will no longer be surfaced as live targets."
                )
            if report.skipped:
                lines.append(f"{len(report.skipped)} row(s) skipped: {report.skipped[0]}")
            return " ".join(lines)
        except Exception as exc:
            logger.exception("import_target_accounts failed for file_id=%s", file_id)
            return f"import_target_accounts failed: {exc}"

    return _run


async def _resolve_slack_file(file_id: str) -> tuple[str, str, str]:
    """Look up a Slack file's name and private URL. Returns (token, name, url)."""
    import httpx
    from sqlalchemy import text

    import artemis.db as _db
    from artemis.integrations.crypto import decrypt_credentials

    async with _db.SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT encrypted_credentials FROM integrations "
                    "WHERE provider='slack' AND agent_id='callie' AND status='active' LIMIT 1"
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return "", file_id, ""

    creds = decrypt_credentials(bytes(row)) or {}
    token = str(creds.get("access_token") or "")
    if not token:
        return "", file_id, ""

    async with httpx.AsyncClient(timeout=30.0) as client:
        info = await client.post(
            "https://slack.com/api/files.info",
            headers={"Authorization": f"Bearer {token}"},
            data={"file": file_id},
        )
    body = info.json()
    if not body.get("ok"):
        return token, file_id, ""
    obj = body.get("file") or {}
    name = str(obj.get("name") or file_id)
    url = str(obj.get("url_private_download") or obj.get("url_private") or "")
    return token, name, url


def register_target_admin_tools(registry: Any, *, speaker_id: str | None) -> None:
    """Register the target-list import. Call ONLY for agent_id == 'callie'."""
    registry.register(
        IMPORT_TARGET_ACCOUNTS,
        _make_import_target_accounts(speaker_id),
        layer=2,
    )
