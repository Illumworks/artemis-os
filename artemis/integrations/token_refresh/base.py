"""Refresher protocol + result types shared by all per-provider refreshers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class RefreshOutcome(Enum):
    """Outcome of a single refresh attempt.

    - REFRESHED: got new tokens, caller should persist `new_creds`.
    - STILL_VALID: no refresh was needed (caller skipped early by leeway check).
    - NO_REFRESH_TOKEN: provider issues non-expiring tokens (e.g. Slack xoxb);
      scheduler skips this row forever.
    - REFRESH_TOKEN_EXPIRED: provider rejected the refresh_token; the
      integration is wedged and needs reauth.
    - TRANSIENT_FAILURE: network/5xx; log + retry next tick.
    """

    REFRESHED = "refreshed"
    STILL_VALID = "still_valid"
    NO_REFRESH_TOKEN = "no_refresh_token"
    REFRESH_TOKEN_EXPIRED = "refresh_token_expired"
    TRANSIENT_FAILURE = "transient_failure"


@dataclass
class RefreshResult:
    outcome: RefreshOutcome
    new_creds: dict[str, object] | None = None
    error: str | None = None


class TokenRefresher(Protocol):
    """Stateless per-provider refresher.

    Implementations should be safe to call concurrently — they hold no shared
    state. Refresh failures are surfaced as `RefreshResult` outcomes, never
    raised, so the scheduler can keep iterating other integrations.
    """

    provider: str

    async def refresh(self, creds: dict[str, object]) -> RefreshResult: ...
