import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builders import repository as repo


def _agent_payload(agent_id: str = "provider-agent") -> dict[str, str]:
    return {
        "agentId": agent_id,
        "name": "Provider Agent",
        "provider": "claude-code",
        "model": "sonnet",
        "fallbackProvider": "codex",
        "fallbackModel": "gpt-5.4",
    }


@pytest.mark.asyncio
async def test_post_without_fallback_provider_returns_422(client: AsyncClient) -> None:
    payload = _agent_payload("missing-fallback")
    payload.pop("fallbackProvider")
    resp = await client.post("/api/agents/", json=payload)
    assert resp.status_code == 422
    assert "fallback_provider" in resp.text


@pytest.mark.asyncio
async def test_patch_null_fallback_provider_returns_422(client: AsyncClient) -> None:
    await client.post("/api/agents/", json=_agent_payload("null-fallback"))
    resp = await client.patch("/api/agents/null-fallback", json={"fallbackProvider": None})
    assert resp.status_code == 422
    assert "fallback_provider" in resp.text


@pytest.mark.asyncio
async def test_patch_legacy_null_fallback_requires_population(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id="legacy-null",
            name="Legacy Null",
            provider="claude-code",
            model="sonnet",
        )
    resp = await client.patch("/api/agents/legacy-null", json={"name": "Touched"})
    assert resp.status_code == 422
    assert "fallback_provider" in resp.text
