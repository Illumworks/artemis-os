"""Slack message builders for pipeline approval DMs.

Builds rich Slack Block Kit messages for human-gate approval requests.
Interactive buttons (Approve / Reject) fire to /api/slack/pipeline-approval-callback.

Public API:
  build_approval_dm_blocks(pipeline_name, node_label, run_id, node_id, context)
  build_escalation_dm_blocks(pipeline_name, node_label, run_id, node_id, context, original_approvers)
  build_plain_approval_text(pipeline_name, node_label, run_id, node_id)  # fallback
"""

from __future__ import annotations

from typing import Any

_CALLBACK_ACTION_ID_PREFIX = "pipeline_approval"


def build_approval_dm_blocks(
    *,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
    context: dict[str, Any] | None = None,
    app_base_url: str = "",
) -> list[dict[str, Any]]:
    """Return Slack Block Kit blocks for a pipeline gate approval DM.

    Includes:
    - Header with pipeline name + gate label
    - Context section (signal evidence or draft preview extracted from *context*)
    - Approve / Reject action buttons
    - Deep-link to in-app Approval Queue
    """
    ctx = context or {}
    district = ctx.get("district", "")
    reason_code = ctx.get("reason_code", "")
    evidence = ctx.get("evidence", "")
    urgency = ctx.get("urgency", "")
    approval_kind = ctx.get("approval_kind", "signal")
    deliverable_ids = ctx.get("deliverable_ids")
    primary_deliverable_id = None
    if isinstance(deliverable_ids, list) and deliverable_ids:
        candidate = deliverable_ids[0]
        if candidate is not None and str(candidate).strip():
            primary_deliverable_id = str(candidate)

    header_text = f":bell: *{pipeline_name}* — {node_label}"

    context_lines: list[str] = []
    if district:
        context_lines.append(f"*District:* {district}")
    if reason_code:
        context_lines.append(f"*Reason Code:* `{reason_code}`")
    if urgency:
        badge = ":fire:" if str(urgency).upper() in ("HOT", "CRITICAL") else ":large_yellow_circle:"
        context_lines.append(f"*Urgency:* {badge} {urgency}")
    if evidence:
        truncated = evidence[:280] + "…" if len(evidence) > 280 else evidence
        context_lines.append(f'*Evidence:* "{truncated}"')

    if approval_kind == "content_draft":
        subject_text = "You have a *content draft* awaiting your approval."
    else:
        subject_text = "You have a *signal* awaiting your review."

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{pipeline_name} — {node_label}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text + "\n" + subject_text},
        },
    ]

    if context_lines:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(context_lines)},
            }
        )

    # Action buttons — value encodes run_id:node_id:decision
    approve_value = f"{run_id}:{node_id}:approved"
    reject_value = f"{run_id}:{node_id}:rejected"
    view_url = f"{app_base_url}/approvals" if app_base_url else "/approvals"
    edit_url = (
        f"{app_base_url}/#writing-studio?draft={primary_deliverable_id}"
        if approval_kind == "content_draft" and app_base_url and primary_deliverable_id
        else None
    )

    action_elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Approve", "emoji": True},
            "style": "primary",
            "value": approve_value,
            "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_approve",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Reject", "emoji": True},
            "style": "danger",
            "value": reject_value,
            "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_reject",
        },
    ]
    if edit_url:
        action_elements.append(
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "Edit in Writing Studio",
                    "emoji": True,
                },
                "url": edit_url,
                "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_edit_draft",
            }
        )
    action_elements.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "View in Artemis →", "emoji": True},
            "url": view_url,
            "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_view",
        }
    )

    blocks.append(
        {
            "type": "actions",
            "elements": action_elements,
        }
    )

    return blocks


def build_escalation_dm_blocks(
    *,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
    context: dict[str, Any] | None = None,
    original_approvers: list[str] | None = None,
    timeout_hours: int = 72,
    app_base_url: str = "",
) -> list[dict[str, Any]]:
    """Return Slack Block Kit blocks for an escalation DM."""
    approver_list = ", ".join(original_approvers or []) or "original approvers"
    escalation_note = (
        f":warning: *Escalated:* {approver_list} did not respond "
        f"within {timeout_hours}h. Please review."
    )
    blocks = build_approval_dm_blocks(
        pipeline_name=pipeline_name,
        node_label=node_label,
        run_id=run_id,
        node_id=node_id,
        context=context,
        app_base_url=app_base_url,
    )
    # Prepend escalation notice after header
    blocks.insert(
        1,
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": escalation_note},
        },
    )
    return blocks


def build_plain_approval_text(
    *,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
) -> str:
    """Plain-text fallback for when Block Kit is not available."""
    return (
        f"[Artemis] {pipeline_name} — {node_label}\n"
        f"A decision is required. Run ID: {run_id}, Node: {node_id}\n"
        f"Open Artemis to approve or reject: /approvals"
    )
