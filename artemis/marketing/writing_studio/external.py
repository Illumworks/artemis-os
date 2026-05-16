"""External Writing Studio client — Protocol + Stub + Real implementations.

StubWritingStudio (default): in-memory, deterministic IDs, no network.
RealWritingStudio: httpx client, activated only when env vars are set.

Usage:
    from artemis.marketing.writing_studio.external import get_writing_studio
    ws = get_writing_studio()  # returns Stub when env unset
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

# ── Data shapes returned by external ──────────────────────────────────────────


@dataclass
class ExternalDraft:
    """A draft created or fetched from the external Writing Studio."""

    external_id: str
    title: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExternalApproval:
    """An approval record created in the external Writing Studio."""

    external_id: str
    draft_id: str
    kind: str
    status: str


# ── Protocol ──────────────────────────────────────────────────────────────────


class ExternalWritingStudio(Protocol):
    """Interface to the external Writing Studio service."""

    async def create_draft(
        self,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalDraft:
        """Create a new draft in the external service.

        Returns an ExternalDraft with a stable external_id.
        """
        ...

    async def submit_for_review(
        self,
        draft_external_id: str,
    ) -> ExternalApproval:
        """Signal that a draft is ready for human review.

        Returns an ExternalApproval with kind='writing_gate_2'.
        """
        ...

    async def get_draft(self, draft_external_id: str) -> ExternalDraft:
        """Fetch current state of a draft from the external service."""
        ...


# ── Stub implementation (default, in-memory) ──────────────────────────────────


class StubWritingStudio:
    """In-memory stub — deterministic, no network. Default when env unset.

    IDs are deterministic counter-based strings: 'stub-draft-1', etc.
    """

    def __init__(self) -> None:
        self._draft_counter: int = 0
        self._approval_counter: int = 0
        self._drafts: dict[str, ExternalDraft] = {}
        self._approvals: dict[str, ExternalApproval] = {}

    async def create_draft(
        self,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalDraft:
        self._draft_counter += 1
        draft_id = f"stub-draft-{self._draft_counter}"
        draft = ExternalDraft(
            external_id=draft_id,
            title=title,
            status="draft",
            metadata=metadata or {},
        )
        self._drafts[draft_id] = draft
        return draft

    async def submit_for_review(self, draft_external_id: str) -> ExternalApproval:
        self._approval_counter += 1
        approval_id = f"stub-approval-{self._approval_counter}"
        approval = ExternalApproval(
            external_id=approval_id,
            draft_id=draft_external_id,
            kind="writing_gate_2",
            status="pending",
        )
        self._approvals[approval_id] = approval
        # Update draft status
        if draft_external_id in self._drafts:
            self._drafts[draft_external_id].status = "ready_for_review"
        return approval

    async def get_draft(self, draft_external_id: str) -> ExternalDraft:
        if draft_external_id not in self._drafts:
            raise ValueError(f"StubWritingStudio: draft not found: {draft_external_id}")
        return self._drafts[draft_external_id]


# ── Real implementation (httpx, inert until env is set) ───────────────────────


class RealWritingStudio:
    """Production httpx client for the external Writing Studio service.

    Reads ARTEMIS_WRITING_STUDIO_URL and ARTEMIS_WRITING_STUDIO_TOKEN from env.
    Only activated by get_writing_studio() when both env vars are set.
    Never calls real HTTP without explicit env config.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def create_draft(
        self,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalDraft:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/drafts",
                headers=self._headers(),
                json={"title": title, "metadata": metadata or {}},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        return ExternalDraft(
            external_id=str(data["id"]),
            title=data.get("title", title),
            status=data.get("status", "draft"),
            metadata=data.get("metadata") or {},
        )

    async def submit_for_review(self, draft_external_id: str) -> ExternalApproval:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/drafts/{draft_external_id}/submit-review",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        return ExternalApproval(
            external_id=str(data["id"]),
            draft_id=draft_external_id,
            kind=data.get("kind", "writing_gate_2"),
            status=data.get("status", "pending"),
        )

    async def get_draft(self, draft_external_id: str) -> ExternalDraft:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/drafts/{draft_external_id}",
                headers=self._headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        return ExternalDraft(
            external_id=str(data["id"]),
            title=data.get("title", ""),
            status=data.get("status", "draft"),
            metadata=data.get("metadata") or {},
        )


# ── Factory ───────────────────────────────────────────────────────────────────


def get_writing_studio() -> ExternalWritingStudio:
    """Return the appropriate ExternalWritingStudio implementation.

    Returns StubWritingStudio unless BOTH env vars are set:
      ARTEMIS_WRITING_STUDIO_URL
      ARTEMIS_WRITING_STUDIO_TOKEN

    Never calls real HTTP without explicit env config.
    """
    url = os.environ.get("ARTEMIS_WRITING_STUDIO_URL", "").strip()
    token = os.environ.get("ARTEMIS_WRITING_STUDIO_TOKEN", "").strip()
    if url and token:
        return RealWritingStudio(base_url=url, token=token)
    return StubWritingStudio()
