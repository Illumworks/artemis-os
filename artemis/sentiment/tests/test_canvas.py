"""Tests for the Brand Signals standing canvas.

The discovery tests matter most: the first live attempt looked for the canvas
under ``properties.canvas``, could not see the one that existed, and so created
a SECOND canvas tab on the channel. Both halves of that bug are pinned here.
"""

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from artemis.sentiment.canvas import (
    CANVAS_TITLE,
    compose_canvas_markdown,
    ensure_channel_canvas,
    find_canvas_tabs,
    update_standing_canvas,
)
from artemis.sentiment.themes import THEME_INSTITUTIONAL_REJECTION

NOW = datetime(2026, 8, 25, 8, 0, tzinfo=UTC)


def _row(title, *, link="https://x/1", themes=None, amira=False, state="US", day=20):
    return {
        "id": 1,
        "lane": "vendor" if amira else "category",
        "title": title,
        "link": link,
        "themes": themes or [],
        "amira": amira,
        "published": datetime(2026, 8, day, tzinfo=UTC),
        "state": state,
    }


def _picture(**over):
    base = {
        "total": 2,
        "amira": 1,
        "states": [("NM", 2), ("FL", 1)],
        "themes": [("parent_objection", 2)],
        "corpus": 47,
    }
    base.update(over)
    return base


class TestFindCanvasTabs:
    def test_reads_tabs_not_a_canvas_key(self) -> None:
        """The live shape. Looking under properties.canvas found nothing and
        caused a duplicate canvas to be created."""
        info = {
            "channel": {
                "properties": {
                    "tabs": [{"type": "canvas", "data": {"file_id": "F1", "shared_ts": "100"}}]
                }
            }
        }
        assert find_canvas_tabs(info) == ["F1"]

    def test_orders_oldest_first_so_the_target_is_deterministic(self) -> None:
        info = {
            "channel": {
                "properties": {
                    "tabs": [
                        {"type": "canvas", "data": {"file_id": "Fnew", "shared_ts": "200"}},
                        {"type": "canvas", "data": {"file_id": "Fold", "shared_ts": "100"}},
                    ]
                }
            }
        }
        assert find_canvas_tabs(info) == ["Fold", "Fnew"]

    def test_ignores_non_canvas_tabs(self) -> None:
        info = {
            "channel": {
                "properties": {
                    "tabs": [
                        {"type": "files", "data": {"file_id": "F9"}},
                        {"type": "canvas", "data": {"file_id": "F1", "shared_ts": "1"}},
                    ]
                }
            }
        }
        assert find_canvas_tabs(info) == ["F1"]

    @pytest.mark.parametrize(
        "info",
        [
            {},
            {"channel": {}},
            {"channel": {"properties": {}}},
            {"channel": {"properties": {"tabs": []}}},
            {"channel": {"properties": {"tabs": [{"type": "canvas"}]}}},
            {"channel": {"properties": {"tabs": ["not-a-dict"]}}},
        ],
    )
    def test_missing_or_malformed_shapes_are_empty_not_an_error(self, info) -> None:
        assert find_canvas_tabs(info) == []


class _FakeClient:
    def __init__(self, info: dict[str, Any], created: str = "Fnew") -> None:
        self._info = info
        self._created = created
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def api_call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == "conversations.info":
            return self._info
        if method == "conversations.canvases.create":
            return {"ok": True, "canvas_id": self._created}
        return {"ok": True}


def _info(*file_ids):
    return {
        "channel": {
            "properties": {
                "tabs": [
                    {"type": "canvas", "data": {"file_id": f, "shared_ts": str(i)}}
                    for i, f in enumerate(file_ids)
                ]
            }
        }
    }


@pytest.mark.asyncio
class TestEnsureChannelCanvas:
    async def test_reuses_an_existing_canvas_and_creates_nothing(self) -> None:
        """conversations.canvases.create is NOT idempotent -- calling it when one
        already exists spawns a second tab. This is the guard against that."""
        client = _FakeClient(_info("F1"))
        assert await ensure_channel_canvas(client, "C1") == "F1"
        assert [m for m, _ in client.calls] == ["conversations.info"]

    async def test_creates_only_when_there_is_none(self) -> None:
        client = _FakeClient(_info())
        assert await ensure_channel_canvas(client, "C1") == "Fnew"
        assert "conversations.canvases.create" in [m for m, _ in client.calls]

    async def test_picks_the_oldest_when_duplicates_exist(self) -> None:
        client = _FakeClient(_info("Foldest", "Fdupe"))
        assert await ensure_channel_canvas(client, "C1") == "Foldest"

    async def test_create_without_an_id_returns_none(self) -> None:
        class _Bad(_FakeClient):
            async def api_call(self, method, **kwargs):
                if method == "conversations.info":
                    return _info()
                return {"ok": True}

        assert await ensure_channel_canvas(_Bad(_info()), "C1") is None


@pytest.mark.asyncio
class TestUpdateStandingCanvas:
    async def test_replaces_the_whole_document(self) -> None:
        """`replace` with no section_id swaps the entire canvas, which is what
        makes 'rewrite it every morning' one call."""
        client = _FakeClient(_info("F1"))
        assert await update_standing_canvas(client, "C1", "# Hi") == "F1"
        edit = [kw for m, kw in client.calls if m == "canvases.edit"][0]
        assert edit["canvas_id"] == "F1"
        change = cast("list[dict[str, Any]]", edit["changes"])[0]
        assert change["operation"] == "replace"
        assert "section_id" not in change
        assert change["document_content"]["markdown"] == "# Hi"

    async def test_no_canvas_means_no_edit(self) -> None:
        class _NoId(_FakeClient):
            async def api_call(self, method, **kwargs):
                self.calls.append((method, kwargs))
                return _info() if method == "conversations.info" else {"ok": True}

        client = _NoId(_info())
        assert await update_standing_canvas(client, "C1", "# Hi") is None
        assert "canvases.edit" not in [m for m, _ in client.calls]


class TestCanvasMarkdown:
    def test_has_a_title_and_an_updated_stamp(self) -> None:
        md = compose_canvas_markdown([_row("A")], _picture(), now=NOW)
        assert md.startswith(f"# {CANVAS_TITLE}")
        assert "Updated Tuesday, August 25" in md
        assert "47 tracked" in md

    def test_renders_state_and_theme_tables(self) -> None:
        md = compose_canvas_markdown([_row("A")], _picture(), now=NOW)
        assert "| State | Stories |" in md
        assert "| NM | 2 |" in md
        assert "| Theme | Stories |" in md

    def test_links_are_canvas_markdown_not_slack_syntax(self) -> None:
        md = compose_canvas_markdown([_row("A", link="https://x/1")], _picture(), now=NOW)
        assert "[A](https://x/1)" in md
        assert "<https://x/1|" not in md

    def test_amira_section_states_absence_rather_than_vanishing(self) -> None:
        md = compose_canvas_markdown([_row("A")], _picture(amira=0), now=NOW)
        assert "Amira by name (0)" in md
        assert "category-level, not aimed at us" in md

    def test_institutional_section_appears_when_present(self) -> None:
        rows = [_row("District rejects it", themes=[THEME_INSTITUTIONAL_REJECTION])]
        md = compose_canvas_markdown(rows, _picture(), now=NOW)
        assert "Institutional action (1)" in md
        assert "a board vote is a contract" in md.replace(
            "district or board decision is a contract", "a board vote is a contract"
        )

    def test_empty_corpus_says_so_without_fake_sections(self) -> None:
        md = compose_canvas_markdown([], _picture(total=0, states=[], themes=[]), now=NOW)
        assert "Nothing in the window" in md
        assert "not an outage" in md
        assert "| State |" not in md

    def test_attribution_caveat_is_stated(self) -> None:
        md = compose_canvas_markdown([_row("A")], _picture(), now=NOW)
        assert "a wrong state is worse than none" in md

    def test_scope_caveat_is_always_present(self) -> None:
        md = compose_canvas_markdown([_row("A")], _picture(), now=NOW)
        assert "Facebook parent groups" in md

    def test_a_pipe_in_a_headline_cannot_break_a_row(self) -> None:
        md = compose_canvas_markdown([_row("A | B")], _picture(), now=NOW)
        assert "A - B" in md

    def test_each_story_appears_once(self) -> None:
        rows = [
            _row("Amira one", link="https://x/1", amira=True),
            _row("District two", link="https://x/2", themes=[THEME_INSTITUTIONAL_REJECTION]),
            _row("Plain three", link="https://x/3"),
        ]
        md = compose_canvas_markdown(rows, _picture(), now=NOW)
        for n in (1, 2, 3):
            assert md.count(f"https://x/{n}") == 1
