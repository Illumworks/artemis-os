"""Tests for artemis.scouts._linkedin_scraper — third-party LinkedIn client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from artemis.scouts._linkedin_scraper import LinkedInScraperClient


def _mock_http(json_data: dict[str, Any] | None = None, status_code: int = 200) -> MagicMock:
    """Return a mock ScoutHttpClient whose .get() returns the given JSON."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    http = MagicMock()
    http.get = AsyncMock(return_value=resp)
    return http


# ---------------------------------------------------------------------------
# no-op when API key is unset
# ---------------------------------------------------------------------------


async def test_fetch_posts_no_key_returns_empty() -> None:
    client = LinkedInScraperClient(api_key="")
    result = await client.fetch_posts("https://linkedin.com/in/someone")
    assert result == []


async def test_check_profile_delta_no_key_returns_none() -> None:
    client = LinkedInScraperClient(api_key="")
    result = await client.check_profile_delta("https://linkedin.com/in/someone")
    assert result is None


async def test_fetch_company_followers_always_noop() -> None:
    client = LinkedInScraperClient(api_key="real-key", http=_mock_http())
    result = await client.fetch_company_followers("amira-learning")
    assert result == []


# ---------------------------------------------------------------------------
# fetch_posts with a key
# ---------------------------------------------------------------------------


async def test_fetch_posts_calls_http_get() -> None:
    http = _mock_http({"posts": []})
    client = LinkedInScraperClient(api_key="key", http=http)
    await client.fetch_posts("https://linkedin.com/in/someone")
    http.get.assert_called_once()


async def test_fetch_posts_parses_posts() -> None:
    posts_payload = {
        "posts": [
            {
                "urn": "urn:li:activity:123",
                "text": "Excited about literacy!",
                "posted_at": "2026-05-16T10:00:00Z",
                "url": "https://linkedin.com/posts/123",
                "is_authored": True,
            }
        ]
    }
    http = _mock_http(posts_payload)
    client = LinkedInScraperClient(api_key="key", http=http)
    result = await client.fetch_posts("https://linkedin.com/in/someone")
    assert len(result) == 1
    assert result[0]["post_id"] == "urn:li:activity:123"
    assert result[0]["text"] == "Excited about literacy!"
    assert result[0]["is_authored"] is True


async def test_fetch_posts_passes_since_param() -> None:
    http = _mock_http({"posts": []})
    client = LinkedInScraperClient(api_key="key", http=http)
    since = datetime(2026, 5, 1, tzinfo=UTC)
    await client.fetch_posts("https://linkedin.com/in/someone", since=since)
    call_kwargs = http.get.call_args[1]
    assert "since" in call_kwargs.get("params", {})


async def test_fetch_posts_returns_empty_on_http_error() -> None:
    http = MagicMock()
    http.get = AsyncMock(side_effect=Exception("connection refused"))
    client = LinkedInScraperClient(api_key="key", http=http)
    result = await client.fetch_posts("https://linkedin.com/in/someone")
    assert result == []


async def test_from_env_uses_env_var() -> None:
    with patch.dict("os.environ", {"LINKEDIN_SCRAPER_API_KEY": "test-key-123"}):
        client = LinkedInScraperClient.from_env()
    assert client._api_key == "test-key-123"


async def test_from_env_empty_when_var_unset() -> None:
    with patch.dict("os.environ", {}, clear=True):
        # ensure var is absent
        import os

        os.environ.pop("LINKEDIN_SCRAPER_API_KEY", None)
        client = LinkedInScraperClient.from_env()
    assert client._api_key == ""
