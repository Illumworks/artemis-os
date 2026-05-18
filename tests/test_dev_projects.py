from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from artemis.db import SessionLocal, engine
from artemis.dev_projects.loop_runner import decide_permission, run_turn
from artemis.ws.manager import ws_manager

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def clean_dev_projects() -> AsyncIterator[None]:
    await engine.dispose()
    async with SessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE dev_annotations, dev_messages, dev_sessions, dev_projects "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield
    await engine.dispose()


async def _create_project(client: AsyncClient, path: Path) -> dict[str, Any]:
    response = await client.post(
        "/api/dev-projects/projects",
        json={"name": "Test Project", "path": str(path)},
    )
    assert response.status_code == 201
    return response.json()


async def _create_session(client: AsyncClient, project_id: int) -> dict[str, Any]:
    response = await client.post(
        f"/api/dev-projects/projects/{project_id}/sessions",
        json={"provider": "claude-code", "model": "default"},
    )
    assert response.status_code == 201
    return response.json()


async def _messages(session_id: int) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = await db.execute(
            text("SELECT id, role, content FROM dev_messages WHERE session_id=:sid ORDER BY id"),
            {"sid": session_id},
        )
        return [dict(row._mapping) for row in rows]


async def _wait_for_tool_id(session_id: int) -> str:
    for _ in range(50):
        for message in await _messages(session_id):
            for block in message["content"]:
                if block.get("type") == "tool_use":
                    return str(block["id"])
        await asyncio.sleep(0.02)
    raise AssertionError("tool_use did not appear")


async def test_empty_project_list(client: AsyncClient) -> None:
    response = await client.get("/api/dev-projects/projects")
    assert response.status_code == 200
    assert response.json() == {"projects": []}


async def test_project_session_message_annotation_happy_path(
    client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])

    detail = await client.get(f"/api/dev-projects/sessions/{session['id']}")
    assert detail.status_code == 200
    assert detail.json()["session"]["project_id"] == project["id"]

    note = await client.post(
        f"/api/dev-projects/sessions/{session['id']}/annotations",
        json={"url": "http://localhost:3000", "note": "this looks broken"},
    )
    assert note.status_code == 201
    assert note.json()["note"] == "this looks broken"

    annotations = await client.get(f"/api/dev-projects/sessions/{session['id']}/annotations")
    assert annotations.status_code == 200
    assert len(annotations.json()["annotations"]) == 1


async def test_permission_denied_mid_loop(client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])

    task = asyncio.create_task(run_turn(session["id"], "list the files in this directory"))
    permission_id = await _wait_for_tool_id(session["id"])
    assert await decide_permission(
        session_id=session["id"], permission_id=permission_id, approved=False
    )
    await task

    messages = await _messages(session["id"])
    assert any(
        block.get("type") == "tool_result" and block.get("is_error") is True
        for message in messages
        for block in message["content"]
    )
    assert "denied" in messages[-1]["content"][0]["text"].lower()


async def test_permission_approved_resumes_loop(client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])

    task = asyncio.create_task(run_turn(session["id"], "list the files in this directory"))
    permission_id = await _wait_for_tool_id(session["id"])
    assert await decide_permission(
        session_id=session["id"], permission_id=permission_id, approved=True
    )
    await task

    messages = await _messages(session["id"])
    assert any("app.py" in str(message["content"]) for message in messages)
    assert messages[-1]["role"] == "assistant"


async def test_fork_at_message_copies_history(client: AsyncClient, tmp_path: Path) -> None:
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])
    post = await client.post(
        f"/api/dev-projects/sessions/{session['id']}/annotations",
        json={"note": "anchor"},
    )
    assert post.status_code == 201
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO dev_messages(session_id, role, content) "
                'VALUES (:sid, \'assistant\', \'[{"type":"text","text":"hello"}]\'::jsonb)'
            ),
            {"sid": session["id"]},
        )
        await db.commit()
    messages = await client.get(f"/api/dev-projects/sessions/{session['id']}/messages")
    message_id = messages.json()["messages"][0]["id"]

    fork = await client.post(
        f"/api/dev-projects/sessions/{session['id']}/fork",
        json={"at_message_id": message_id},
    )
    assert fork.status_code == 201
    fork_detail = await client.get(f"/api/dev-projects/sessions/{fork.json()['id']}")
    assert len(fork_detail.json()["messages"]) == 1


async def test_delete_active_session_archives(client: AsyncClient, tmp_path: Path) -> None:
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])
    response = await client.delete(f"/api/dev-projects/sessions/{session['id']}")
    assert response.status_code == 204
    sessions = await client.get(f"/api/dev-projects/projects/{project['id']}/sessions")
    archived = sessions.json()["sessions"][0]
    assert archived["archived_at"] is not None


async def test_provider_switch_mid_session(client: AsyncClient, tmp_path: Path) -> None:
    project = await _create_project(client, tmp_path)
    session = await _create_session(client, project["id"])
    response = await client.patch(
        f"/api/dev-projects/sessions/{session['id']}",
        json={"provider": "codex", "model": "gpt-5.4"},
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "codex"
    assert response.json()["model"] == "gpt-5.4"


async def test_project_model_defaults_apply_to_new_sessions(
    client: AsyncClient, tmp_path: Path
) -> None:
    project = await _create_project(client, tmp_path)
    update = await client.patch(
        f"/api/dev-projects/projects/{project['id']}",
        json={"metadata": {"default_provider": "codex", "default_model": "gpt-5.4"}},
    )
    assert update.status_code == 200

    response = await client.post(
        f"/api/dev-projects/projects/{project['id']}/sessions",
        json={},
    )
    assert response.status_code == 201
    assert response.json()["provider"] == "codex"
    assert response.json()["model"] == "gpt-5.4"


async def test_project_file_search(client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")
    project = await _create_project(client, tmp_path)
    response = await client.get(f"/api/dev-projects/projects/{project['id']}/files?q=main")
    assert response.status_code == 200
    assert response.json()["files"][0]["path"] == "src/main.py"


class _FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.events: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: Any) -> None:
        self.events.append(data)


async def test_ws_reconnect_mid_stream_receives_later_events() -> None:
    first = _FakeWebSocket()
    second = _FakeWebSocket()
    await ws_manager.connect("dev-projects:99", first)
    await ws_manager.broadcast("dev-projects:99", {"type": "dev_projects.token", "token": "a"})
    await ws_manager.disconnect("dev-projects:99", first)
    await ws_manager.connect("dev-projects:99", second)
    await ws_manager.broadcast("dev-projects:99", {"type": "dev_projects.token", "token": "b"})
    await ws_manager.disconnect("dev-projects:99", second)

    assert first.events == [{"type": "dev_projects.token", "token": "a"}]
    assert second.events == [{"type": "dev_projects.token", "token": "b"}]
