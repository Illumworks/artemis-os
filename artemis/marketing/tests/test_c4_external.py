"""Phase C4 tests — ExternalWritingStudio: Stub, Real, factory.

Tests:
  - StubWritingStudio deterministic IDs
  - StubWritingStudio create/get/submit lifecycle
  - RealWritingStudio construction but no real HTTP
  - get_writing_studio() env-var detection
  - get_writing_studio() returns Stub when env unset
  - get_writing_studio() returns Real when both env vars set
  - ExternalDraft + ExternalApproval dataclass defaults
"""

from __future__ import annotations

import pytest

from artemis.marketing.writing_studio.external import (
    ExternalApproval,
    ExternalDraft,
    RealWritingStudio,
    StubWritingStudio,
    get_writing_studio,
)

# ── StubWritingStudio ─────────────────────────────────────────────────────────


class TestStubWritingStudioDraft:
    async def test_create_draft_returns_external_draft(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("Test Title")
        assert isinstance(draft, ExternalDraft)
        assert draft.title == "Test Title"

    async def test_create_draft_deterministic_ids(self) -> None:
        stub = StubWritingStudio()
        d1 = await stub.create_draft("First")
        d2 = await stub.create_draft("Second")
        assert d1.external_id == "stub-draft-1"
        assert d2.external_id == "stub-draft-2"

    async def test_create_draft_counter_increments(self) -> None:
        stub = StubWritingStudio()
        for i in range(1, 6):
            d = await stub.create_draft(f"Draft {i}")
            assert d.external_id == f"stub-draft-{i}"

    async def test_create_draft_initial_status_is_draft(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("Title")
        assert draft.status == "draft"

    async def test_create_draft_metadata_stored(self) -> None:
        stub = StubWritingStudio()
        meta = {"key": "value", "num": 42}
        draft = await stub.create_draft("Meta Draft", metadata=meta)
        assert draft.metadata == meta

    async def test_create_draft_no_metadata_defaults_empty(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("No Meta")
        assert draft.metadata == {}

    async def test_get_draft_returns_created_draft(self) -> None:
        stub = StubWritingStudio()
        created = await stub.create_draft("Fetchable")
        fetched = await stub.get_draft(created.external_id)
        assert fetched.external_id == created.external_id
        assert fetched.title == "Fetchable"

    async def test_get_draft_not_found_raises(self) -> None:
        stub = StubWritingStudio()
        with pytest.raises(ValueError, match="not found"):
            await stub.get_draft("does-not-exist")

    async def test_instances_are_independent(self) -> None:
        stub1 = StubWritingStudio()
        stub2 = StubWritingStudio()
        d1 = await stub1.create_draft("A")
        d2 = await stub2.create_draft("B")
        # Both start at counter 1 — independent state
        assert d1.external_id == d2.external_id == "stub-draft-1"


class TestStubWritingStudioSubmit:
    async def test_submit_for_review_returns_approval(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("For Review")
        approval = await stub.submit_for_review(draft.external_id)
        assert isinstance(approval, ExternalApproval)
        assert approval.kind == "writing_gate_2"
        assert approval.status == "pending"

    async def test_submit_approval_id_deterministic(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("Title")
        a1 = await stub.submit_for_review(draft.external_id)
        a2 = await stub.submit_for_review(draft.external_id)
        assert a1.external_id == "stub-approval-1"
        assert a2.external_id == "stub-approval-2"

    async def test_submit_sets_draft_status_ready_for_review(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("Title")
        await stub.submit_for_review(draft.external_id)
        fetched = await stub.get_draft(draft.external_id)
        assert fetched.status == "ready_for_review"

    async def test_submit_links_approval_to_draft(self) -> None:
        stub = StubWritingStudio()
        draft = await stub.create_draft("Title")
        approval = await stub.submit_for_review(draft.external_id)
        assert approval.draft_id == draft.external_id


# ── RealWritingStudio construction ────────────────────────────────────────────


class TestRealWritingStudio:
    def test_construction_stores_url_and_token(self) -> None:
        real = RealWritingStudio(base_url="https://example.com", token="tok")
        assert real._base_url == "https://example.com"
        assert real._token == "tok"

    def test_construction_strips_trailing_slash(self) -> None:
        real = RealWritingStudio(base_url="https://example.com/", token="tok")
        assert real._base_url == "https://example.com"

    def test_headers_contain_bearer_token(self) -> None:
        real = RealWritingStudio(base_url="https://example.com", token="secret")
        headers = real._headers()
        assert headers["Authorization"] == "Bearer secret"
        assert headers["Content-Type"] == "application/json"


# ── get_writing_studio() factory ──────────────────────────────────────────────


class TestGetWritingStudio:
    def test_returns_stub_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARTEMIS_WRITING_STUDIO_URL", raising=False)
        monkeypatch.delenv("ARTEMIS_WRITING_STUDIO_TOKEN", raising=False)
        result = get_writing_studio()
        assert isinstance(result, StubWritingStudio)

    def test_returns_stub_when_only_url_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_URL", "https://example.com")
        monkeypatch.delenv("ARTEMIS_WRITING_STUDIO_TOKEN", raising=False)
        result = get_writing_studio()
        assert isinstance(result, StubWritingStudio)

    def test_returns_stub_when_only_token_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ARTEMIS_WRITING_STUDIO_URL", raising=False)
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_TOKEN", "tok")
        result = get_writing_studio()
        assert isinstance(result, StubWritingStudio)

    def test_returns_real_when_both_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_URL", "https://ws.example.com")
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_TOKEN", "mytoken")
        result = get_writing_studio()
        assert isinstance(result, RealWritingStudio)

    def test_returns_stub_when_vars_are_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_URL", "   ")
        monkeypatch.setenv("ARTEMIS_WRITING_STUDIO_TOKEN", "   ")
        result = get_writing_studio()
        assert isinstance(result, StubWritingStudio)


# ── Dataclass defaults ────────────────────────────────────────────────────────


class TestDataclasses:
    def test_external_draft_metadata_defaults_empty(self) -> None:
        d = ExternalDraft(external_id="x", title="t", status="draft")
        assert d.metadata == {}

    def test_external_approval_fields(self) -> None:
        a = ExternalApproval(
            external_id="ea-1",
            draft_id="draft-1",
            kind="writing_gate_2",
            status="pending",
        )
        assert a.kind == "writing_gate_2"
        assert a.status == "pending"
