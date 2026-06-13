"""Stage 7 backend tests — per-user Google Docs connect/import/export."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.google_docs.models  # noqa: F401
import artemis.identity.models  # noqa: F401
import artemis.marketing.models  # noqa: F401
import artemis.pipelines.models  # noqa: F401
import artemis.writing_rules.models  # noqa: F401
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.google_docs.models import GoogleCredential
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.repository import create_campaign_candidate_from_signal, create_signal

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_gdoc",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_DB_URL!r} is not a safe test database."
    )

_ENGINE = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_ENGINE)
db_module.engine = _ENGINE
db_module.SessionLocal = async_sessionmaker(
    bind=_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "google_credentials, "
    "writing_draft_thread_messages, "
    "templates, "
    "claims, "
    "writing_sources, "
    "writing_examples, "
    "writing_rules, "
    "writing_profiles, "
    "campaign_state_transitions, "
    "approvals, "
    "campaign_sends, "
    "campaign_deliverables, "
    "content_asset_links, "
    "content_assets, "
    "campaign_briefs, "
    "campaign_candidate_signals, "
    "campaign_candidates, "
    "scout_runs, "
    "qualifier_rule_applications, "
    "skipped_signals, "
    "district_contacts, "
    "districts, "
    "district_tier_bands, "
    "district_data_meta, "
    "signal_queue, "
    "rulesets, "
    "territory_config, "
    "signal_reason_codes, "
    "users "
    "RESTART IDENTITY CASCADE"
)


class FakeGoogleApis:
    def __init__(self) -> None:
        self.authorization_code_access_token = "access-from-code"
        self.refresh_access_token = "access-from-refresh"
        self.refresh_token = "refresh-secret"
        self.connected_email = "writer@amiralearning.com"
        self.import_document_id = "doc-import-123"
        self.import_title = "Imported District Messaging"
        self.export_document_id = "doc-export-789"
        self.last_docs_auth_header: str | None = None
        self.refresh_calls = 0
        self.batch_updates: list[dict[str, object]] = []
        self.created_documents: list[dict[str, object]] = []
        self.drive_renames: list[dict[str, object]] = []
        self.revoked_tokens: list[str] = []

    def transport(self) -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith("https://oauth2.googleapis.com/token"):
                body = parse_qs(request.content.decode("utf-8"))
                grant_type = body.get("grant_type", [""])[0]
                if grant_type == "authorization_code":
                    return httpx.Response(
                        200,
                        json={
                            "access_token": self.authorization_code_access_token,
                            "refresh_token": self.refresh_token,
                            "expires_in": 3600,
                            "scope": " ".join(
                                [
                                    "https://www.googleapis.com/auth/drive.file",
                                    "https://www.googleapis.com/auth/documents",
                                ]
                            ),
                            "token_type": "Bearer",
                        },
                    )
                self.refresh_calls += 1
                return httpx.Response(
                    200,
                    json={
                        "access_token": self.refresh_access_token,
                        "expires_in": 3600,
                        "scope": " ".join(
                            [
                                "https://www.googleapis.com/auth/drive.file",
                                "https://www.googleapis.com/auth/documents",
                            ]
                        ),
                        "token_type": "Bearer",
                    },
                )

            if url.startswith("https://www.googleapis.com/oauth2/v2/userinfo"):
                auth = request.headers.get("Authorization", "")
                assert auth == f"Bearer {self.authorization_code_access_token}"
                return httpx.Response(200, json={"email": self.connected_email})

            if url.startswith("https://oauth2.googleapis.com/revoke"):
                token = dict(request.url.params).get("token", "")
                self.revoked_tokens.append(token)
                return httpx.Response(200, json={})

            if url.startswith("https://docs.googleapis.com/v1/documents/") and url.endswith(
                ":batchUpdate"
            ):
                self.last_docs_auth_header = request.headers.get("Authorization")
                self.batch_updates.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(200, json={"replies": []})

            if url == "https://docs.googleapis.com/v1/documents":
                self.last_docs_auth_header = request.headers.get("Authorization")
                self.created_documents.append(json.loads(request.content.decode("utf-8")))
                return httpx.Response(
                    200,
                    json={"documentId": self.export_document_id, "title": "Exported Draft"},
                )

            if url.startswith("https://www.googleapis.com/drive/v3/files/"):
                self.last_docs_auth_header = request.headers.get("Authorization")
                payload = json.loads(request.content.decode("utf-8"))
                self.drive_renames.append(payload)
                document_id = url.rstrip("/").split("/")[-1]
                return httpx.Response(200, json={"id": document_id, "name": payload["name"]})

            if url.startswith("https://docs.googleapis.com/v1/documents/"):
                self.last_docs_auth_header = request.headers.get("Authorization")
                document_id = url.rstrip("/").split("/")[-1]
                if document_id == self.import_document_id:
                    return httpx.Response(200, json=self._import_document_payload(document_id))
                return httpx.Response(
                    200,
                    json={
                        "documentId": document_id,
                        "title": "Existing Linked Draft",
                        "body": {"content": [{"endIndex": 8}]},
                    },
                )

            raise AssertionError(f"Unexpected Google API request: {request.method} {url}")

        return httpx.MockTransport(handler)

    def _import_document_payload(self, document_id: str) -> dict[str, object]:
        return {
            "documentId": document_id,
            "title": self.import_title,
            "body": {
                "content": [
                    {
                        "endIndex": 1,
                        "paragraph": {
                            "elements": [{"textRun": {"content": "\n"}}],
                        },
                    },
                    {
                        "endIndex": 20,
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Staffing Pressure\n"}}],
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        },
                    },
                    {
                        "endIndex": 45,
                        "paragraph": {
                            "elements": [{"textRun": {"content": "State funding gap\n"}}],
                            "bullet": {"listId": "list-1", "nestingLevel": 0},
                        },
                    },
                    {
                        "endIndex": 95,
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "District leaders need support.\n",
                                    }
                                }
                            ],
                        },
                    },
                ]
            },
            "lists": {
                "list-1": {
                    "listProperties": {
                        "nestingLevels": [{"glyphType": "BULLET_DISC_CIRCLE_SQUARE"}]
                    }
                }
            },
        }


@pytest.fixture(autouse=True)
def _configure_google_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "google-client-secret")
    monkeypatch.setattr(
        settings,
        "google_redirect_uri",
        "https://app.artemisos.me/api/google/oauth/callback",
    )
    monkeypatch.setattr(settings, "cf_access_enabled", False)
    monkeypatch.setattr(settings, "token", None)


@pytest.fixture(autouse=True)
def _reset_router_state() -> AsyncIterator[None]:
    from artemis.google_integration import clear_google_oauth_states

    clear_google_oauth_states()
    yield
    clear_google_oauth_states()


@pytest.fixture
def fake_google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogleApis:
    from artemis.google_docs import client as google_client

    fake = FakeGoogleApis()
    monkeypatch.setattr(
        google_client,
        "_make_http_client",
        lambda timeout=15.0: httpx.AsyncClient(
            transport=fake.transport(),
            timeout=timeout,
        ),
    )
    return fake


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_ENGINE, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_deliverable(db: AsyncSession, *, title: str, content: str) -> CampaignDeliverable:
    signal = await create_signal(
        db,
        headline="Stage 7 test signal",
        campaign_family="obc",
        source_type="manual",
        summary="stage 7 test",
        discovered_by="test",
    )
    candidate: CampaignCandidate = await create_campaign_candidate_from_signal(
        db,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )
    deliverable = CampaignDeliverable(
        candidate_id=candidate.id,
        deliverable_id="deliverable-stage7",
        campaign_id=str(candidate.id),
        status="generating",
        deliverable_metadata={
            "title": title,
            "versions": [
                {
                    "id": "v1",
                    "version_number": 1,
                    "content": content,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ],
        },
    )
    db.add(deliverable)
    await db.flush()
    await db.refresh(deliverable)
    await db.commit()
    return deliverable


async def _connect_google(client: AsyncClient, *, purpose: str = "personal") -> str:
    start = await client.get(
        "/api/google/oauth/start",
        params={"purpose": purpose},
        follow_redirects=False,
    )
    assert start.status_code == 302
    location = start.headers["location"]
    parsed = urlparse(location)
    state = parse_qs(parsed.query)["state"][0]
    callback = await client.get(
        "/api/google/oauth/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    return location


async def test_google_status_initially_disconnected(client: AsyncClient) -> None:
    response = await client.get("/api/google/status")

    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert response.json()["purpose"] == "personal"


async def test_google_marketing_oauth_uses_separate_purpose_and_overview(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
) -> None:
    await _connect_google(client, purpose="marketing")

    status = await client.get("/api/google/status", params={"purpose": "marketing"})
    assert status.status_code == 200
    assert status.json()["connected"] is True
    assert status.json()["purpose"] == "marketing"
    overview = await client.get("/api/google/overview")
    assert overview.status_code == 200
    assert overview.json()["connected"] is True
    assert overview.json()["purpose"] == "marketing"

    stored = (
        await db_session.execute(
            select(GoogleCredential).where(
                GoogleCredential.user_id == 1,
                GoogleCredential.purpose == "marketing",
            )
        )
    ).scalar_one_or_none()
    assert stored is not None


async def test_google_oauth_connect_stores_credential_and_status(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    location = await _connect_google(client)

    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert "documents" in query["scope"][0]
    assert "drive.file" in query["scope"][0]

    status = await client.get("/api/google/status")
    assert status.status_code == 200
    assert status.json() == {
        "connected": True,
        "email": fake_google.connected_email,
        "purpose": "personal",
        "hasDriveScope": True,
        "docsImportReady": True,
        "docsExportReady": True,
        "hasCalendarScope": False,
        "hasGmailReadScope": False,
    }

    stored = (
        await db_session.execute(
            select(GoogleCredential).where(
                GoogleCredential.user_id == 1,
                GoogleCredential.purpose == "personal",
            )
        )
    ).scalar_one_or_none()
    assert stored is not None
    assert stored.user_id == 1
    assert stored.purpose == "personal"
    assert stored.access_token == fake_google.authorization_code_access_token
    assert stored.refresh_token == fake_google.refresh_token
    assert stored.connected_email == fake_google.connected_email

    serialized_response = json.dumps(status.json())
    assert fake_google.authorization_code_access_token not in serialized_response
    assert fake_google.refresh_token not in serialized_response
    assert fake_google.authorization_code_access_token not in caplog.text
    assert fake_google.refresh_token not in caplog.text


async def test_google_status_and_import_are_per_user(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
) -> None:
    from artemis.main import app

    await _connect_google(client)
    draft = await _make_deliverable(db_session, title="Per-user draft", content="Original content.")

    other_user = User(email="teammate@amiralearning.com", name="Teammate")
    db_session.add(other_user)
    await db_session.commit()
    await db_session.refresh(other_user)

    async def _override_user() -> User:
        return other_user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        status = await client.get("/api/google/status")
        assert status.status_code == 200
        assert status.json()["connected"] is False
        assert status.json()["purpose"] == "personal"

        response = await client.post(
            f"/api/writing-studio/drafts/{draft.id}/google-doc/import",
            json={
                "docUrl": f"https://docs.google.com/document/d/{fake_google.import_document_id}/edit"
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "google_not_connected"
    finally:
        app.dependency_overrides.clear()


async def test_google_import_updates_draft_content_and_links_doc(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _connect_google(client, purpose="marketing")
    draft = await _make_deliverable(
        db_session,
        title="Imported draft",
        content="Original content.",
    )

    response = await client.post(
        f"/api/writing-studio/drafts/{draft.id}/google-doc/import",
        json={
            "docUrl": f"https://docs.google.com/document/d/{fake_google.import_document_id}/edit"
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["linkedDocId"] == fake_google.import_document_id
    assert body["importedContent"] == (
        "# Staffing Pressure\n\n- State funding gap\n\nDistrict leaders need support."
    )
    assert body["googleDoc"]["title"] == fake_google.import_title
    assert (
        fake_google.last_docs_auth_header == f"Bearer {fake_google.authorization_code_access_token}"
    )

    draft_get = await client.get(f"/api/writing-studio/drafts/{draft.id}")
    assert draft_get.status_code == 200
    draft_body = draft_get.json()
    assert draft_body["content"] == body["importedContent"]
    assert draft_body["metadata"]["googleDoc"]["documentId"] == fake_google.import_document_id
    assert draft_body["metadata"]["googleDoc"]["previewText"] == (
        "# Staffing Pressure - State funding gap District leaders need support."
    )

    serialized = json.dumps(body)
    assert fake_google.authorization_code_access_token not in serialized
    assert fake_google.refresh_token not in serialized
    assert fake_google.authorization_code_access_token not in caplog.text
    assert fake_google.refresh_token not in caplog.text


async def test_google_export_creates_new_doc_and_links_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _connect_google(client, purpose="marketing")
    draft = await _make_deliverable(
        db_session,
        title="Michigan email",
        content="Fresh export copy.",
    )

    response = await client.post(f"/api/writing-studio/drafts/{draft.id}/google-doc/export")

    assert response.status_code == 200
    body = response.json()
    assert body["linkedDocId"] == fake_google.export_document_id
    assert (
        body["docUrl"]
        == f"https://docs.google.com/document/d/{fake_google.export_document_id}/edit"
    )
    assert body["created"] is True
    assert fake_google.created_documents == [{"title": "Michigan email"}]
    assert fake_google.batch_updates[0]["requests"] == [
        {
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": 7,
                }
            }
        },
        {
            "insertText": {
                "location": {"index": 1},
                "text": "Fresh export copy.",
            }
        },
    ]

    refreshed = await client.get(f"/api/writing-studio/drafts/{draft.id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["metadata"]["googleDoc"]["documentId"] == fake_google.export_document_id
    assert refreshed.json()["metadata"]["googleDoc"]["url"] == body["docUrl"]

    serialized = json.dumps(body)
    assert fake_google.authorization_code_access_token not in serialized
    assert fake_google.refresh_token not in serialized
    assert fake_google.authorization_code_access_token not in caplog.text
    assert fake_google.refresh_token not in caplog.text


async def test_google_import_refreshes_expired_access_token(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
) -> None:
    await client.get("/api/me")
    await db_session.merge(
        GoogleCredential(
            user_id=1,
            purpose="marketing",
            access_token="expired-access-token",
            refresh_token=fake_google.refresh_token,
            expiry=datetime.now(UTC) - timedelta(minutes=5),
            scope="https://www.googleapis.com/auth/documents",
            connected_email=fake_google.connected_email,
        )
    )
    await db_session.commit()
    draft = await _make_deliverable(
        db_session,
        title="Refresh import draft",
        content="Original content.",
    )

    response = await client.post(
        f"/api/writing-studio/drafts/{draft.id}/google-doc/import",
        json={
            "docUrl": f"https://docs.google.com/document/d/{fake_google.import_document_id}/edit"
        },
    )

    assert response.status_code == 200
    assert fake_google.refresh_calls == 1
    assert fake_google.last_docs_auth_header == f"Bearer {fake_google.refresh_access_token}"

    stored = (
        await db_session.execute(
            select(GoogleCredential).where(
                GoogleCredential.user_id == 1,
                GoogleCredential.purpose == "marketing",
            )
        )
    ).scalar_one_or_none()
    assert stored is not None
    assert stored.access_token == fake_google.refresh_access_token
    assert stored.refresh_token == fake_google.refresh_token


async def test_google_disconnect_revokes_and_clears_current_user_token(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleApis,
) -> None:
    await _connect_google(client)

    response = await client.post("/api/google/disconnect")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "connected": False, "purpose": "personal"}
    assert fake_google.revoked_tokens == [fake_google.authorization_code_access_token]

    stored = (
        await db_session.execute(
            select(GoogleCredential).where(
                GoogleCredential.user_id == 1,
                GoogleCredential.purpose == "personal",
            )
        )
    ).scalar_one_or_none()
    assert stored is None
