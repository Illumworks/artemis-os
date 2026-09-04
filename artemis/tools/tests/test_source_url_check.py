"""Rejecting a signal whose source URL is not a real page.

A scout is an LLM agent that emits signals through a tool. Nothing checked that
the URL it supplied pointed at anything, so a run whose feeds came back empty
could invent plausible items instead of reporting zero. That produced 149
fabricated signals between 2026-08-10 and 2026-09-02, 52 approved past Gate 1,
one of which told Josh and Angela that Illinois had announced a partnership with
Amira. Julie opened the links and found nothing behind them.

The severity split is the design, and both halves are pinned here: a definitive
4xx rejects, while anything ambiguous is allowed through as unverified. Dropping
real intelligence because a state site was briefly slow would be its own failure.
"""

from __future__ import annotations

import pytest

from artemis.tools import _source_url_check as mod
from artemis.tools._source_url_check import verify_source_url


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


def _patch(monkeypatch: pytest.MonkeyPatch, *, status: int | None = None, boom: bool = False):
    """Stub BOTH hops so no test touches the network or DNS."""

    async def _ok(_url: str, **_kw: object) -> None:
        return None

    class _Client:
        def __init__(self, *a: object, **k: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, _url: str, **_kw: object) -> _Resp:
            if boom:
                raise TimeoutError("slow site")
            assert status is not None
            return _Resp(status)

    monkeypatch.setattr("artemis.egress_guard.async_validate_url", _ok)
    monkeypatch.setattr("artemis.scouts._http.ScoutHttpClient", _Client)


@pytest.mark.asyncio
async def test_no_url_is_allowed_and_marked_unverified() -> None:
    """Some signals legitimately carry no URL; absence is not a lie."""
    verdict = await verify_source_url("")
    assert verdict.ok
    assert not verdict.verified


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(mod._DEFINITIVE_MISS))
async def test_a_definitive_miss_rejects(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """400 is in this set because it is exactly what Google News returns for an
    invented article id — the case this exists to catch."""
    _patch(monkeypatch, status=status)

    verdict = await verify_source_url("https://news.google.com/rss/articles/illinois-amira-pilot")

    assert not verdict.ok
    assert verdict.verified
    assert str(status) in verdict.reason


@pytest.mark.asyncio
async def test_a_live_page_is_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, status=200)

    verdict = await verify_source_url("https://www.cde.ca.gov/")

    assert verdict.ok
    assert verdict.verified


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_an_ambiguous_response_is_allowed_but_unverified(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A bot filter or a struggling state site must not cost us real intel."""
    _patch(monkeypatch, status=status)

    verdict = await verify_source_url("https://www.michigan.gov/mde/some-page")

    assert verdict.ok, "ambiguous must never reject"
    assert not verdict.verified
    assert "could not confirm" in verdict.reason


@pytest.mark.asyncio
async def test_a_network_failure_is_allowed_but_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, boom=True)

    verdict = await verify_source_url("https://www.tea.texas.gov/")

    assert verdict.ok
    assert not verdict.verified


@pytest.mark.asyncio
async def test_a_blocked_address_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The URL comes from a model, so SSRF is a live risk, not a hypothetical."""
    from artemis.egress_guard import EgressBlockedError

    async def _blocked(_url: str, **_kw: object) -> None:
        raise EgressBlockedError("blocked egress to non-public address")

    monkeypatch.setattr("artemis.egress_guard.async_validate_url", _blocked)

    verdict = await verify_source_url("http://169.254.169.254/latest/meta-data/")

    assert not verdict.ok
    assert "egress" in verdict.reason
