"""Tests for the Slack approval card builders in artemis.integrations.slack.messages.

Covers:
- signal_brief card: headline-based title, evidence snippets, Approve + View, NO Reject
- content_draft card: campaign/type/district title, full draft body chunked, Approve + Edit
                      deep-linked to deliverable id, NO Reject, NO View
- generic card: Approve + Reject + View still present for other gate kinds
- long draft body chunks across multiple section blocks
- plain-text fallback for each kind
"""

from __future__ import annotations

from artemis.integrations.slack.messages import (
    build_approval_dm_blocks,
    build_plain_approval_text,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _actions(blocks: list[dict]) -> dict:
    """Return the first actions block."""
    return next(b for b in blocks if b["type"] == "actions")


def _button_labels(blocks: list[dict]) -> list[str]:
    actions = _actions(blocks)
    return [el["text"]["text"] for el in actions["elements"] if el.get("type") == "button"]


def _button_urls(blocks: list[dict]) -> dict[str, str | None]:
    actions = _actions(blocks)
    return {
        el["text"]["text"]: el.get("url")
        for el in actions["elements"]
        if el.get("type") == "button"
    }


def _all_text(blocks: list[dict]) -> str:
    """Concatenate all mrkdwn / plain_text in the block list."""
    parts: list[str] = []
    for b in blocks:
        t = b.get("text") or {}
        if isinstance(t, dict) and t.get("text"):
            parts.append(t["text"])
        for el in b.get("elements", []):
            et = el.get("text") or {}
            if isinstance(et, dict) and et.get("text"):
                parts.append(et["text"])
    return "\n".join(parts)


def _section_texts(blocks: list[dict]) -> list[str]:
    return [
        b["text"]["text"]
        for b in blocks
        if b["type"] == "section" and isinstance(b.get("text"), dict)
    ]


# ── signal_brief card ─────────────────────────────────────────────────────────


SIGNAL_CTX = {
    "approval_kind": "signal_brief",
    "headline": "Houston ISD Purchases New Reading Program",
    "district_label": "Houston ISD (TX)",
    "urgency": "HOT",
    "reason_codes": ["rfp_issued", "budget_approved"],
    "score": 0.87,
    "signal_count": 3,
    "brief_body": "This signal is HOT: the board approved an RFP last week.",
    "brief_preview": "Board approved RFP.",
    "evidence_quote": "Board approved an RFP for reading intervention.",
    "evidence_snippets": [
        "Board approved an RFP for reading intervention.",
        "District set aside $1.2M in the FY26 budget.",
    ],
}


def test_signal_card_header_uses_headline_not_pipeline_name() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="Marketing Pipeline — Gate 1 Signals Inbox",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    header = next(b for b in blocks if b["type"] == "header")
    assert "Houston ISD Purchases New Reading Program" in header["text"]["text"]
    assert "Houston ISD (TX)" in header["text"]["text"]
    # Old static title must NOT appear
    assert "Marketing Pipeline" not in header["text"]["text"]
    assert "Gate 1 Signals Inbox" not in header["text"]["text"]


def test_signal_card_has_approve_and_view_but_no_reject() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    labels = _button_labels(blocks)
    assert "Approve" in labels
    assert "View in Artemis →" in labels
    assert "Reject" not in labels
    assert "Edit in Writing Studio" not in labels


def test_signal_card_shows_urgency_and_reason_codes() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "HOT" in text
    assert "rfp_issued" in text
    assert "budget_approved" in text


def test_signal_card_shows_brief_body() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "board approved an RFP last week" in text


def test_signal_card_shows_multiple_evidence_snippets() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "Board approved an RFP for reading intervention" in text
    assert "$1.2M" in text


def test_signal_card_shows_group_size_when_multiple_signals() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "3" in text


def test_signal_card_view_url_points_to_approvals() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    urls = _button_urls(blocks)
    assert urls.get("View in Artemis →") == "https://app.example.com/approvals"


def test_signal_card_score_present() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "0.87" in text


# ── content_draft card ────────────────────────────────────────────────────────


CONTENT_CTX = {
    "approval_kind": "content_draft",
    "campaign_name": "ENRICH1 Skip-List Follow-Up",
    "campaign_family": "enrich1",
    "deliverable_type_slug": "outreach_email",
    "district_label": "Houston ISD (TX)",
    "draft_title": "Re: Reading Intervention — Your Students Need This",
    "draft_body": (
        "Hi Angela,\n\n"
        "I wanted to follow up on our conversation about Amira Learning's "
        "reading intervention platform.\n\n"
        "Best,\nJon"
    ),
    "draft_summary": "Follow-up outreach email for Houston ISD.",
    "deliverable_ids": [42],
    "deliverable_count": 1,
    "ready_deliverable_count": 1,
}


def test_content_card_header_uses_campaign_and_district() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
        app_base_url="https://app.example.com",
    )
    header = next(b for b in blocks if b["type"] == "header")
    text = header["text"]["text"]
    assert "ENRICH1 Skip-List Follow-Up" in text
    assert "Houston ISD (TX)" in text
    assert "Outreach Email" in text
    # Old static title must NOT appear
    assert "Gate 2 Approval Drawer" not in text
    assert "content draft awaiting" not in text


def test_content_card_has_approve_and_edit_but_no_reject_and_no_view() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
        app_base_url="https://app.example.com",
    )
    labels = _button_labels(blocks)
    assert "Approve" in labels
    assert any("Edit in Writing Studio" in lbl for lbl in labels)
    assert "Reject" not in labels
    assert "View in Artemis →" not in labels


def test_content_card_edit_button_deep_links_to_deliverable_id() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
        app_base_url="https://app.example.com",
    )
    urls = _button_urls(blocks)
    edit_label = next(lbl for lbl in urls if "Edit" in lbl)
    assert urls[edit_label] == "https://app.example.com/#writing-studio?draft=42"


def test_content_card_shows_subject_line() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "Re: Reading Intervention — Your Students Need This" in text


def test_content_card_shows_full_draft_body() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "reading intervention platform" in text


def test_content_card_long_body_chunked_into_multiple_section_blocks() -> None:
    """A draft body > 3 000 chars must produce multiple section blocks, each <= 3 000 chars."""
    long_body = "A" * 7_500
    ctx = {**CONTENT_CTX, "draft_body": long_body}
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=ctx,
        app_base_url="https://app.example.com",
    )
    section_texts = [
        b["text"]["text"]
        for b in blocks
        if b["type"] == "section" and isinstance(b.get("text"), dict)
    ]
    # All section block texts must be <= 3 000 chars
    for st in section_texts:
        assert len(st) <= 3_000, f"Section block too long: {len(st)} chars"
    # The draft text must be fully present across chunks
    body_chunks = [st for st in section_texts if st.startswith("A")]
    assert "".join(body_chunks) == long_body


def test_content_card_truncated_body_adds_note() -> None:
    """A body at the executor cap (10 000 chars) must add the 'full draft in Writing Studio' note."""
    capped_body = "B" * 10_000
    ctx = {**CONTENT_CTX, "draft_body": capped_body}
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=ctx,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "full draft in Writing Studio" in text


def test_content_card_no_draft_body_shows_fallback() -> None:
    """When draft_body is absent, the card falls back gracefully to draft_summary."""
    ctx = {**CONTENT_CTX, "draft_body": None, "draft_title": None}
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=ctx,
        app_base_url="https://app.example.com",
    )
    text = _all_text(blocks)
    assert "Houston ISD" in text  # header still present
    # Should show fallback summary or a placeholder
    assert "Follow-up outreach email" in text or "Writing Studio" in text


# ── generic card (other gate kinds) ──────────────────────────────────────────


def test_generic_card_has_approve_reject_and_view() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="My Pipeline",
        node_label="Finance Gate",
        run_id="r3",
        node_id="n3",
        context={"approval_kind": "finance_approval"},
        app_base_url="https://app.example.com",
    )
    labels = _button_labels(blocks)
    assert "Approve" in labels
    assert "Reject" in labels
    assert "View in Artemis →" in labels


def test_generic_card_empty_context_has_approve_reject_view() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="My Pipeline",
        node_label="Some Gate",
        run_id="r4",
        node_id="n4",
        context=None,
        app_base_url="",
    )
    labels = _button_labels(blocks)
    assert "Approve" in labels
    assert "Reject" in labels
    assert "View in Artemis →" in labels


# ── plain-text fallback ───────────────────────────────────────────────────────


def test_plain_text_signal_brief() -> None:
    text = build_plain_approval_text(
        pipeline_name="PIPE4",
        node_label="Gate 1",
        run_id="r1",
        node_id="n1",
        context=SIGNAL_CTX,
    )
    assert "Houston ISD Purchases New Reading Program" in text
    assert "HOT" in text
    assert "rfp_issued" in text
    assert "Board approved an RFP for reading intervention" in text
    assert "r1" in text


def test_plain_text_content_draft() -> None:
    text = build_plain_approval_text(
        pipeline_name="PIPE4",
        node_label="Gate 2",
        run_id="r2",
        node_id="n2",
        context=CONTENT_CTX,
    )
    assert "ENRICH1 Skip-List Follow-Up" in text
    assert "Houston ISD (TX)" in text
    assert "Re: Reading Intervention" in text
    assert "writing-studio?draft=42" in text


def test_plain_text_generic() -> None:
    text = build_plain_approval_text(
        pipeline_name="My Pipeline",
        node_label="Finance Gate",
        run_id="r5",
        node_id="n5",
        context={"approval_kind": "finance_approval"},
    )
    assert "My Pipeline" in text
    assert "Finance Gate" in text


# ── legacy test (updated) ─────────────────────────────────────────────────────


def test_content_draft_dm_includes_edit_in_writing_studio_button() -> None:
    """Retained from prior test suite — edit button still deep-links to the deliverable."""
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2 approval",
        run_id="run-123",
        node_id="gate-2",
        context={
            "approval_kind": "content_draft",
            "deliverable_ids": [42],
            "campaign_name": "ENRICH1",
            "district_label": "Austin ISD (TX)",
            "draft_title": "Subject line",
            "draft_body": "Body text of the draft.",
        },
        app_base_url="https://artemis.example.com",
    )

    actions = next(block for block in blocks if block["type"] == "actions")
    labels_to_urls = {
        element["text"]["text"]: element.get("url")
        for element in actions["elements"]
        if element.get("type") == "button"
    }

    assert (
        labels_to_urls.get("Edit in Writing Studio →")
        == "https://artemis.example.com/#writing-studio?draft=42"
    )
    # The old "View in Artemis →" button is removed from content_draft cards.
    assert "View in Artemis →" not in labels_to_urls
