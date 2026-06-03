from __future__ import annotations

from artemis.integrations.slack.messages import build_approval_dm_blocks


def test_content_draft_dm_includes_edit_in_writing_studio_button() -> None:
    blocks = build_approval_dm_blocks(
        pipeline_name="PIPE4",
        node_label="Gate 2 approval",
        run_id="run-123",
        node_id="gate-2",
        context={
            "approval_kind": "content_draft",
            "deliverable_ids": [42],
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
        labels_to_urls["Edit in Writing Studio"]
        == "https://artemis.example.com/#writing-studio?draft=42"
    )
    assert labels_to_urls["View in Artemis →"] == "https://artemis.example.com/approvals"
