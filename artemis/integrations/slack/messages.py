"""Slack message builders for pipeline approval DMs.

Builds rich Slack Block Kit messages for human-gate approval requests.
Interactive buttons (Approve) fire to /api/slack/pipeline-approval-callback.
Reject is intentionally omitted from marketing cards — rejections happen
in-app for agent training.  Non-marketing gate kinds fall back to the
generic card which still includes Reject.

Public API:
  build_approval_dm_blocks(pipeline_name, node_label, run_id, node_id, context, app_base_url)
  build_escalation_dm_blocks(...)
  build_plain_approval_text(pipeline_name, node_label, run_id, node_id, context)  # fallback
"""

from __future__ import annotations

from typing import Any

_CALLBACK_ACTION_ID_PREFIX = "pipeline_approval"

# Slack section block text cap is 3 000 chars per block.
_SLACK_SECTION_MAX = 3_000

# Marketing gate approval_kind values that get specialised cards.
_MARKETING_KINDS = frozenset({"signal_brief", "content_draft"})


# ── helpers ─────────────────────────────────────────────────────────────────


def _urgency_badge(urgency: str) -> str:
    return ":fire:" if str(urgency).upper() in ("HOT", "CRITICAL") else ":large_yellow_circle:"


def _split_into_section_blocks(text: str, prefix: str = "") -> list[dict[str, Any]]:
    """Split *text* into one or more mrkdwn section blocks, each <= _SLACK_SECTION_MAX chars.

    An optional *prefix* string is prepended to the first chunk only.
    """
    full = (prefix + text) if prefix else text
    if not full:
        return []
    chunks: list[str] = []
    while full:
        chunks.append(full[:_SLACK_SECTION_MAX])
        full = full[_SLACK_SECTION_MAX:]
    return [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in chunks]


# ── signal_brief card ────────────────────────────────────────────────────────


def _build_signal_card(
    *,
    run_id: str,
    node_id: str,
    ctx: dict[str, Any],
    app_base_url: str,
) -> list[dict[str, Any]]:
    """Rich approval card for approval_kind == 'signal_brief'."""
    # Title: signal headline + district (beat the old "Marketing Pipeline — Gate 1 Signals Inbox")
    headline = ctx.get("headline") or "New Signal"
    district_label = ctx.get("district_label") or ""
    title_plain = f"{headline} — {district_label}" if district_label else headline

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": title_plain,
                "emoji": True,
            },
        },
    ]

    # Body section lines
    body_lines: list[str] = []

    # Brief body / "why it matters" from the brief_composer's write
    brief_body = ctx.get("brief_body") or ctx.get("brief_preview")
    if brief_body:
        body_lines.append(f":memo: {brief_body}")

    urgency = ctx.get("urgency")
    if urgency:
        badge = _urgency_badge(urgency)
        body_lines.append(f"*Urgency:* {badge} {urgency}")

    reason_codes: list[str] = ctx.get("reason_codes") or []
    if reason_codes:
        body_lines.append(f"*Reason codes:* `{'`, `'.join(reason_codes)}`")

    signal_count: int = ctx.get("signal_count") or 1
    if signal_count > 1:
        body_lines.append(
            f":busts_in_silhouette: Part of a group of *{signal_count}* related signals"
        )

    score = ctx.get("score")
    if score is not None:
        body_lines.append(f"*Fit score:* {float(score):.2f}")

    if body_lines:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(body_lines)},
            }
        )

    # Evidence snippets — show all available (one per signal), no 280-char truncation.
    evidence_snippets: list[str] = ctx.get("evidence_snippets") or []
    if not evidence_snippets:
        single = ctx.get("evidence_quote")
        if single:
            evidence_snippets = [single]
    for i, snippet in enumerate(evidence_snippets):
        label = "*Evidence:*" if i == 0 else "*Also:*"
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f'{label} "{snippet}"'},
            }
        )

    # Divider before actions
    blocks.append({"type": "divider"})

    # Action buttons: Approve + View in Artemis. NO Reject.
    approve_value = f"{run_id}:{node_id}:approved"
    view_url = f"{app_base_url}/approvals" if app_base_url else "/approvals"

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "value": approve_value,
                    "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View in Artemis →", "emoji": True},
                    "url": view_url,
                    "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_view",
                },
            ],
        }
    )

    return blocks


# ── content_draft card ───────────────────────────────────────────────────────


def _build_content_card(
    *,
    run_id: str,
    node_id: str,
    ctx: dict[str, Any],
    app_base_url: str,
) -> list[dict[str, Any]]:
    """Rich approval card for approval_kind == 'content_draft'."""
    campaign_name = ctx.get("campaign_name") or ctx.get("campaign_family") or "Campaign"
    type_slug = ctx.get("deliverable_type_slug") or ""
    district_label = ctx.get("district_label") or ""

    # Header: "ENRICH1 Skip-List Follow-Up · Outreach Email · Houston ISD (TX)"
    type_human = type_slug.replace("_", " ").replace("-", " ").title() if type_slug else ""
    title_parts = [campaign_name]
    if type_human:
        title_parts.append(type_human)
    if district_label:
        title_parts.append(district_label)
    title_plain = " · ".join(title_parts)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": title_plain,
                "emoji": True,
            },
        },
    ]

    # Subject line (= draftTitle)
    draft_title = ctx.get("draft_title") or ctx.get("draft_summary")
    if draft_title:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Subject:* {draft_title}"},
            }
        )

    blocks.append({"type": "divider"})

    # Full draft body — chunked across multiple section blocks
    draft_body = ctx.get("draft_body")
    if draft_body:
        # Detect if we hit the stored cap; if so, add a truncation note.
        # _DRAFT_BODY_MAX is 10_000 in the executor; check length rather than import.
        executor_body_max = 10_000
        truncated = len(draft_body) >= executor_body_max
        body_blocks = _split_into_section_blocks(draft_body)
        blocks.extend(body_blocks)
        if truncated:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "_…(full draft in Writing Studio)_",
                    },
                }
            )
    else:
        # Fallback when draft body not yet available (e.g. legacy context path)
        fallback = ctx.get("draft_summary") or ctx.get("brief_preview")
        if fallback:
            blocks.extend(_split_into_section_blocks(fallback, prefix="*Draft preview:* "))
        else:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "_Draft body not yet available — open Writing Studio to review._",
                    },
                }
            )

    blocks.append({"type": "divider"})

    # Action buttons: Approve + Edit in Writing Studio (deep-linked). NO Reject, NO View.
    approve_value = f"{run_id}:{node_id}:approved"
    deliverable_ids = ctx.get("deliverable_ids")
    primary_deliverable_id: str | None = None
    if isinstance(deliverable_ids, list) and deliverable_ids:
        candidate = deliverable_ids[0]
        if candidate is not None and str(candidate).strip():
            primary_deliverable_id = str(candidate)

    edit_url = (
        f"{app_base_url}/#writing-studio?draft={primary_deliverable_id}"
        if app_base_url and primary_deliverable_id
        else f"/#writing-studio?draft={primary_deliverable_id}"
        if primary_deliverable_id
        else (f"{app_base_url}/#writing-studio" if app_base_url else "/#writing-studio")
    )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "value": approve_value,
                    "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_approve",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Edit in Writing Studio →",
                        "emoji": True,
                    },
                    "url": edit_url,
                    "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_edit_draft",
                },
            ],
        }
    )

    return blocks


# ── generic card (non-marketing gate kinds) ──────────────────────────────────


def _build_generic_card(
    *,
    pipeline_name: str,
    node_label: str,
    run_id: str,
    node_id: str,
    ctx: dict[str, Any],
    app_base_url: str,
) -> list[dict[str, Any]]:
    """Generic approval card for gate kinds other than signal_brief / content_draft."""
    district = ctx.get("district", "")
    reason_code = ctx.get("reason_code", "")
    evidence = ctx.get("evidence", "")
    urgency = ctx.get("urgency", "")

    header_text = f":bell: *{pipeline_name}* — {node_label}"
    approval_kind = ctx.get("approval_kind", "signal")

    context_lines: list[str] = []
    if district:
        context_lines.append(f"*District:* {district}")
    if reason_code:
        context_lines.append(f"*Reason Code:* `{reason_code}`")
    if urgency:
        badge = _urgency_badge(urgency)
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

    approve_value = f"{run_id}:{node_id}:approved"
    reject_value = f"{run_id}:{node_id}:rejected"
    view_url = f"{app_base_url}/approvals" if app_base_url else "/approvals"

    blocks.append(
        {
            "type": "actions",
            "elements": [
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
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View in Artemis →",
                        "emoji": True,
                    },
                    "url": view_url,
                    "action_id": f"{_CALLBACK_ACTION_ID_PREFIX}_view",
                },
            ],
        }
    )

    return blocks


# ── public API ───────────────────────────────────────────────────────────────


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

    For marketing gate kinds (signal_brief, content_draft) the card is specialised:
    - signal_brief:  headline-based title, full evidence snippets, Approve + View
    - content_draft: campaign/type/district title, full draft body chunked into section
                     blocks, Approve + Edit (deep-linked to the deliverable)

    All other gate kinds receive the generic card (Approve + Reject + View).
    """
    ctx = context or {}
    approval_kind = ctx.get("approval_kind", "")

    if approval_kind == "signal_brief":
        return _build_signal_card(
            run_id=run_id,
            node_id=node_id,
            ctx=ctx,
            app_base_url=app_base_url,
        )

    if approval_kind == "content_draft":
        return _build_content_card(
            run_id=run_id,
            node_id=node_id,
            ctx=ctx,
            app_base_url=app_base_url,
        )

    return _build_generic_card(
        pipeline_name=pipeline_name,
        node_label=node_label,
        run_id=run_id,
        node_id=node_id,
        ctx=ctx,
        app_base_url=app_base_url,
    )


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
    context: dict[str, Any] | None = None,
) -> str:
    """Plain-text fallback for when Block Kit is not available."""
    ctx = context or {}
    approval_kind = ctx.get("approval_kind", "")

    if approval_kind == "signal_brief":
        headline = ctx.get("headline") or "New Signal"
        district = ctx.get("district_label") or ""
        urgency = ctx.get("urgency") or ""
        codes = ", ".join(ctx.get("reason_codes") or [])
        evidence = ctx.get("evidence_quote") or ""
        title = f"{headline} — {district}" if district else headline
        parts = [f"[Artemis] Gate 1 Signal: {title}"]
        if urgency:
            parts.append(f"Urgency: {urgency}")
        if codes:
            parts.append(f"Reason codes: {codes}")
        if evidence:
            parts.append(f'Evidence: "{evidence[:200]}"')
        parts.append(f"Approve at: /approvals  (Run: {run_id})")
        return "\n".join(parts)

    if approval_kind == "content_draft":
        campaign = ctx.get("campaign_name") or ctx.get("campaign_family") or "Campaign"
        type_slug = ctx.get("deliverable_type_slug") or ""
        district = ctx.get("district_label") or ""
        draft_title = ctx.get("draft_title") or ctx.get("draft_summary") or ""
        deliverable_ids = ctx.get("deliverable_ids") or []
        did = str(deliverable_ids[0]) if deliverable_ids else ""
        type_human = type_slug.replace("_", " ").replace("-", " ").title() if type_slug else ""
        title_parts = [campaign]
        if type_human:
            title_parts.append(type_human)
        if district:
            title_parts.append(district)
        title = " · ".join(title_parts)
        parts = [f"[Artemis] Gate 2 Draft Ready: {title}"]
        if draft_title:
            parts.append(f"Subject: {draft_title}")
        if did:
            parts.append(f"Edit at: /#writing-studio?draft={did}")
        parts.append(f"Run: {run_id}")
        return "\n".join(parts)

    return (
        f"[Artemis] {pipeline_name} — {node_label}\n"
        f"A decision is required. Run ID: {run_id}, Node: {node_id}\n"
        f"Open Artemis to approve or reject: /approvals"
    )
