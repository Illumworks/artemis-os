"""M3 identity-aware scope enforcement — comprehensive test suite.

Tests:
1. Unit: allowed_scopes_for / allowance resolver for all identity types
2. HTTP integration: scope enforcement at /api/memory/* endpoints
3. Agent path: Callie and Artemis scope enforcement via _enforce_agent_scope_set
4. Floating assistant (D11): server-side agent_id resolution
5. Fail-closed: unknown/unresolved identity → deny
6. Regression: marketing access to WS/signals/marketing memory still works

DB setup:
  Uses ARTEMIS_TEST_DB_URL (defaults to artemis_test).
  Seeds personal and marketing observations, tests access by different identities.
"""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
from artemis.db import attach_pgvector_codec

DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

if "artemis_test" not in DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={DB_URL!r} is not a safe test database."
    )

_ENGINE = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_ENGINE)
db_module.engine = _ENGINE
db_module.SessionLocal = async_sessionmaker(
    bind=_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    "TRUNCATE memory_observations, memory_drawers, memory_evidence, "
    "memory_conflicts, floating_artemis_sessions, floating_artemis_messages, "
    "floating_artemis_page_context, users RESTART IDENTITY CASCADE"
)

OWNER_EMAIL = "amiracentral@amiralearning.com"
MARKETING_EMAIL = "marketer@amiralearning.com"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Fresh AsyncSession per test, truncated before each test.

    Uses a fresh engine per test to avoid asyncio event-loop / asyncpg
    connection conflicts when running many tests in sequence.
    """
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from artemis.db import attach_pgvector_codec

    engine = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    session_factory = async_sessionmaker(
        bind=engine, expire_on_commit=False, class_=AsyncSession
    )
    # Also patch the module-level SessionLocal so the app uses the same DB.
    old_session_local = db_module.SessionLocal
    old_engine = db_module.engine
    db_module.SessionLocal = session_factory
    db_module.engine = engine

    async with session_factory() as session:
        await session.execute(_TRUNCATE)
        await session.commit()
        yield session

    db_module.SessionLocal = old_session_local
    db_module.engine = old_engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_session):
    """ASGI test client with identity shim overrideable via state."""
    from httpx import ASGITransport, AsyncClient
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_observation(
    db_session: AsyncSession,
    scope_kind: str,
    scope_id: str,
    content: str,
) -> int:
    """Insert a memory observation directly and return its id."""
    result = await db_session.execute(
        text(
            "INSERT INTO memory_observations "
            "(scope_kind, scope_id, category, content, content_hash, score, "
            " hit_count, source_quality, user_confirmed, wing) "
            "VALUES (:sk, :sid, 'test', :content, md5(:content), 1.0, "
            "        0, 1.0, FALSE, 'durable') "
            "RETURNING id"
        ),
        {
            "sk": scope_kind,
            "sid": scope_id,
            "content": content,
        },
    )
    row = result.fetchone()
    await db_session.commit()
    return row[0]


async def _seed_user(db_session: AsyncSession, email: str, name: str = "Test User") -> int:
    """Upsert a user row and return their id."""
    from artemis.identity.repository import get_or_create_user
    user = await get_or_create_user(db_session, email, name)
    await db_session.commit()
    return user.id


def _make_identity_headers(email: str) -> dict[str, str]:
    """Return request headers that will pass identity as the given email.

    In dev mode (cf_access_enabled=False) there's no real JWT; we override the
    identity resolution by patching the dev shim email for the test.  This is
    done via the app's state override mechanism in conftest.
    """
    # We rely on the dev shim path — the test monkey-patches the dev email.
    return {}


# ── 1. Unit tests: resolver ───────────────────────────────────────────────────


class TestResolver:
    def test_owner_gets_all(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email, OWNER_EMAIL
        a = allowed_scopes_for_email(OWNER_EMAIL, 1)
        assert a.allow_all is True
        assert not a.denied

    def test_owner_permits_personal(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email, OWNER_EMAIL
        a = allowed_scopes_for_email(OWNER_EMAIL, 1)
        assert a.permits("personal", "1")
        assert a.permits("personal", "9999")

    def test_owner_permits_agent_artemis(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email, OWNER_EMAIL
        a = allowed_scopes_for_email(OWNER_EMAIL, 1)
        assert a.permits("agent", "artemis")
        assert a.permits("agent", "floating-artemis")
        assert a.permits("agent", "callie")

    def test_marketing_human_cannot_read_owner_personal(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email, OWNER_EMAIL
        owner_id = 1
        marketing_user_id = 42
        owner_allowance = allowed_scopes_for_email(OWNER_EMAIL, owner_id)
        mktg_allowance = allowed_scopes_for_email(MARKETING_EMAIL, marketing_user_id)
        # Owner's personal scope — marketing should be denied
        assert not mktg_allowance.permits("personal", str(owner_id))
        # Owner's personal scope — owner should be allowed
        assert owner_allowance.permits("personal", str(owner_id))

    def test_marketing_human_reads_own_personal(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        mktg_user_id = 42
        a = allowed_scopes_for_email(MARKETING_EMAIL, mktg_user_id)
        assert a.permits("personal", str(mktg_user_id))

    def test_marketing_human_cannot_read_agent_artemis(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        a = allowed_scopes_for_email(MARKETING_EMAIL, 42)
        assert not a.permits("agent", "artemis")
        assert not a.permits("agent", "floating-artemis")

    def test_marketing_human_can_read_agent_callie(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        a = allowed_scopes_for_email(MARKETING_EMAIL, 42)
        assert a.permits("agent", "callie")

    def test_marketing_human_reads_marketing_scopes(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        a = allowed_scopes_for_email(MARKETING_EMAIL, 42)
        assert a.permits("workspace", "marketing")
        assert a.permits("campaign_family", "any-family")
        assert a.permits("global", "global")

    def test_callie_agent_cannot_read_personal(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("callie")
        assert not a.permits("personal", "1")
        assert not a.permits("personal", "42")

    def test_callie_agent_cannot_read_agent_artemis(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("callie")
        assert not a.permits("agent", "artemis")
        assert not a.permits("agent", "floating-artemis")

    def test_callie_agent_reads_marketing(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("callie")
        assert a.permits("agent", "callie")
        assert a.permits("workspace", "marketing")
        assert a.permits("campaign_family", "any")
        assert a.permits("global", "global")

    def test_artemis_agent_gets_all(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("artemis")
        assert a.allow_all is True
        assert a.permits("personal", "1")
        assert a.permits("agent", "artemis")

    def test_floating_artemis_alias_gets_all(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("floating-artemis")
        assert a.allow_all is True

    def test_unknown_agent_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_agent
        a = allowed_scopes_for_agent("unknown-bot")
        assert a.denied is True
        assert not a.permits("agent", "callie")

    def test_unknown_identity_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        # Blank email → deny
        a = allowed_scopes_for_email("", 1)
        assert a.denied is True

    def test_invalid_user_id_denied(self):
        from artemis.identity.scope_policy import allowed_scopes_for_email
        # Non-owner email with bad user_id → deny
        a = allowed_scopes_for_email(MARKETING_EMAIL, -1)
        assert a.denied is True
        a2 = allowed_scopes_for_email(MARKETING_EMAIL, 0)
        assert a2.denied is True

    def test_d11_owner_gets_artemis_agent(self):
        from artemis.identity.scope_policy import resolve_agent_id_from_email, OWNER_EMAIL
        assert resolve_agent_id_from_email(OWNER_EMAIL) == "artemis"

    def test_d11_marketing_gets_callie_agent(self):
        from artemis.identity.scope_policy import resolve_agent_id_from_email
        assert resolve_agent_id_from_email(MARKETING_EMAIL) == "callie"

    def test_d11_blank_email_gets_callie(self):
        from artemis.identity.scope_policy import resolve_agent_id_from_email
        assert resolve_agent_id_from_email("") == "callie"

    def test_d11_none_email_gets_callie(self):
        from artemis.identity.scope_policy import resolve_agent_id_from_email
        assert resolve_agent_id_from_email(None) == "callie"  # type: ignore[arg-type]

    def test_allowance_denied_permits_nothing(self):
        from artemis.identity.scope_policy import allowance_denied
        a = allowance_denied()
        assert not a.permits("global", "global")
        assert not a.permits("personal", "1")
        assert not a.permits("agent", "callie")
        assert not a.permits("workspace", "marketing")


# ── 2. Agent path: _enforce_agent_scope_set ───────────────────────────────────


class TestAgentScopeEnforcement:
    def test_callie_cannot_read_agent_artemis(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        result = _enforce_agent_scope_set("callie", [
            Scope(scope_kind="agent", scope_id="artemis"),
        ])
        assert result == []

    def test_callie_cannot_read_personal(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        result = _enforce_agent_scope_set("callie", [
            Scope(scope_kind="personal", scope_id="1"),
            Scope(scope_kind="personal", scope_id="42"),
        ])
        assert result == []

    def test_callie_can_read_callie_scope(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        result = _enforce_agent_scope_set("callie", [
            Scope(scope_kind="agent", scope_id="callie"),
        ])
        assert len(result) == 1
        assert result[0].scope_id == "callie"

    def test_callie_mixed_scope_filters_correctly(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        result = _enforce_agent_scope_set("callie", [
            Scope(scope_kind="agent", scope_id="callie"),
            Scope(scope_kind="agent", scope_id="artemis"),   # should be dropped
            Scope(scope_kind="personal", scope_id="1"),      # should be dropped
            Scope(scope_kind="workspace", scope_id="marketing"),  # OK
        ])
        scopes_str = [(s.scope_kind, s.scope_id) for s in result]
        assert ("agent", "artemis") not in scopes_str
        assert ("personal", "1") not in scopes_str
        assert ("agent", "callie") in scopes_str
        assert ("workspace", "marketing") in scopes_str

    def test_artemis_gets_all_scopes(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        scopes = [
            Scope(scope_kind="agent", scope_id="floating-artemis"),
            Scope(scope_kind="personal", scope_id="1"),
            Scope(scope_kind="agent", scope_id="callie"),
        ]
        result = _enforce_agent_scope_set("artemis", scopes)
        assert result == scopes

    def test_unknown_agent_gets_empty(self):
        from artemis.floating_artemis.memory import _enforce_agent_scope_set
        from artemis.memory.schemas import Scope
        result = _enforce_agent_scope_set("unknown-bot", [
            Scope(scope_kind="agent", scope_id="callie"),
        ])
        assert result == []


# ── 3. HTTP integration tests ─────────────────────────────────────────────────
#
# These tests patch the dev shim identity to simulate different callers.
# The dev shim is used because CF Access is disabled in test mode.


async def _with_identity(app_client, email: str, user_id_cache: dict):
    """Context helper: monkey-patch the dev shim identity for the next requests."""
    import artemis.identity.dependencies as id_dep
    import artemis.config as cfg_mod

    # Override the dev-shim email
    original_email = id_dep._DEV_USER_EMAIL
    id_dep._DEV_USER_EMAIL = email
    try:
        yield
    finally:
        id_dep._DEV_USER_EMAIL = original_email


@pytest.mark.asyncio
async def test_http_owner_sees_all_scopes(db_session, client):
    """Owner can see personal and agent:artemis observations."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    personal_obs_id = await _seed_observation(
        db_session, "personal", str(owner_id), "Owner personal note"
    )
    artemis_obs_id = await _seed_observation(
        db_session, "agent", "artemis", "Artemis agent memory"
    )
    mktg_obs_id = await _seed_observation(
        db_session, "workspace", "marketing", "Marketing observation"
    )

    # Simulate owner identity
    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = OWNER_EMAIL
        resp = await client.get("/api/memory/observations", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    obs_ids = {o["id"] for o in body["observations"]}
    assert personal_obs_id in obs_ids, "Owner must see personal obs"
    assert artemis_obs_id in obs_ids, "Owner must see agent:artemis obs"
    assert mktg_obs_id in obs_ids, "Owner must see marketing obs"


@pytest.mark.asyncio
async def test_http_marketing_cannot_see_owner_personal(db_session, client):
    """Marketing user gets ZERO personal:<owner> and ZERO agent:artemis rows."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    personal_obs_id = await _seed_observation(
        db_session, "personal", str(owner_id), "Owner personal note SECRET"
    )
    artemis_obs_id = await _seed_observation(
        db_session, "agent", "artemis", "Artemis agent memory SECRET"
    )
    mktg_obs_id = await _seed_observation(
        db_session, "workspace", "marketing", "Marketing observation PUBLIC"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get("/api/memory/observations", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    obs_ids = {o["id"] for o in body["observations"]}
    # HEADLINE ASSERTION: zero personal and zero agent:artemis rows
    scope_kinds = {o["scope_kind"] for o in body["observations"]}
    assert "personal" not in scope_kinds, "Marketing must NOT see any personal: scope rows"
    assert personal_obs_id not in obs_ids, "Marketing must NOT see owner's personal obs"
    assert artemis_obs_id not in obs_ids, "Marketing must NOT see agent:artemis obs"
    # Marketing should see marketing obs
    assert mktg_obs_id in obs_ids, "Marketing should see workspace:marketing obs"


@pytest.mark.asyncio
async def test_http_marketing_cannot_widen_via_scope_filter(db_session, client):
    """Marketing user cannot widen access by supplying scope_kind=personal."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    personal_obs_id = await _seed_observation(
        db_session, "personal", str(owner_id), "Owner personal note SECRET"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        # Attempt to widen by requesting personal scope explicitly
        resp = await client.get(
            f"/api/memory/observations?scope_kind=personal&scope_id={owner_id}",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    # The personal filter is silently dropped (invalid for this caller), so the
    # server returns the caller's permitted scopes. The key assertion: personal
    # observations of the owner are NOT returned.
    obs_ids = {o["id"] for o in body["observations"]}
    assert personal_obs_id not in obs_ids, "Cannot widen via query param to owner personal scope"
    # All returned observations must be within the marketing user's allowance
    for obs in body["observations"]:
        assert obs["scope_kind"] != "personal" or obs["scope_id"] == str(
            mktg_id
        ), "Returned observations must only include caller's permitted scopes"


@pytest.mark.asyncio
async def test_http_marketing_reads_own_personal(db_session, client):
    """Marketing user can read their OWN personal:<user_id> observations."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    own_personal_obs_id = await _seed_observation(
        db_session, "personal", str(mktg_id), "Marketer personal note"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get("/api/memory/observations", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    obs_ids = {o["id"] for o in body["observations"]}
    assert own_personal_obs_id in obs_ids, "Marketing user must see their own personal obs"


@pytest.mark.asyncio
async def test_http_observation_detail_404_for_marketing_on_personal(db_session, client):
    """Marketing user gets 404 (not 403) for owner's personal observation."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    personal_obs_id = await _seed_observation(
        db_session, "personal", str(owner_id), "Owner personal note SECRET"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get(
            f"/api/memory/observations/{personal_obs_id}",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


@pytest.mark.asyncio
async def test_http_observation_detail_200_for_owner_on_personal(db_session, client):
    """Owner gets 200 for their personal observation."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")

    personal_obs_id = await _seed_observation(
        db_session, "personal", str(owner_id), "Owner personal note"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = OWNER_EMAIL
        resp = await client.get(
            f"/api/memory/observations/{personal_obs_id}",
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    assert body["observation"]["id"] == personal_obs_id


@pytest.mark.asyncio
async def test_http_scopes_endpoint_filtered_for_marketing(db_session, client):
    """Marketing user's /scopes response excludes personal and agent:artemis."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    await _seed_observation(db_session, "personal", str(owner_id), "Owner personal")
    await _seed_observation(db_session, "agent", "artemis", "Artemis mem")
    await _seed_observation(db_session, "workspace", "marketing", "Mktg mem")

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get("/api/memory/scopes", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    scopes = resp.json()
    scope_keys = {(s["scope_kind"], s["scope_id"]) for s in scopes}
    assert ("personal", str(owner_id)) not in scope_keys, "Marketing must NOT see personal scopes"
    assert ("agent", "artemis") not in scope_keys, "Marketing must NOT see agent:artemis scope"
    assert ("workspace", "marketing") in scope_keys, "Marketing must see workspace:marketing"


@pytest.mark.asyncio
async def test_http_scopes_endpoint_full_for_owner(db_session, client):
    """Owner sees all scopes including personal and agent:artemis."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")

    await _seed_observation(db_session, "personal", str(owner_id), "Owner personal")
    await _seed_observation(db_session, "agent", "artemis", "Artemis mem")
    await _seed_observation(db_session, "workspace", "marketing", "Mktg mem")

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = OWNER_EMAIL
        resp = await client.get("/api/memory/scopes", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    scopes = resp.json()
    scope_keys = {(s["scope_kind"], s["scope_id"]) for s in scopes}
    assert ("personal", str(owner_id)) in scope_keys, "Owner must see personal scope"
    assert ("agent", "artemis") in scope_keys, "Owner must see agent:artemis scope"


# ── 4. Fail-closed tests ──────────────────────────────────────────────────────


class TestFailClosed:
    def test_denied_allowance_returns_empty_filter_scopes(self):
        from artemis.identity.scope_policy import allowance_denied
        from artemis.memory.schemas import Scope

        a = allowance_denied()
        scopes = [
            Scope(scope_kind="global", scope_id="global"),
            Scope(scope_kind="workspace", scope_id="marketing"),
            Scope(scope_kind="personal", scope_id="1"),
        ]
        result = a.filter_scopes(scopes)
        assert result == [], "Denied allowance must filter all scopes"

    def test_denied_permits_nothing(self):
        from artemis.identity.scope_policy import allowance_denied
        a = allowance_denied()
        for sk in ["personal", "agent", "workspace", "global", "campaign_family"]:
            for sid in ["1", "callie", "artemis", "marketing", "global"]:
                assert not a.permits(sk, sid), f"Denied must not permit {sk}:{sid}"

    def test_resolver_error_returns_denied(self):
        """Passing bad args should return denied, not raise."""
        from artemis.identity.scope_policy import allowed_scopes_for_email, allowed_scopes_for_agent
        # None email
        a = allowed_scopes_for_email(None, 1)  # type: ignore[arg-type]
        assert a.denied is True
        # Bad agent_id
        b = allowed_scopes_for_agent(None)  # type: ignore[arg-type]
        assert b.denied is True

    pass  # class body; async tests moved to module level below


@pytest.mark.asyncio
async def test_http_blank_email_denied_no_personal_rows(db_session, client):
    """Blank email identity → deny → zero personal rows returned."""
    import artemis.identity.dependencies as id_dep

    owner_id = await _seed_user(db_session, OWNER_EMAIL, "Jon")
    await _seed_observation(db_session, "personal", str(owner_id), "Private")
    await _seed_observation(db_session, "workspace", "marketing", "Mktg")

    # Blank email → allowed_scopes_for_email("", ...) → denied allowance.
    # However the dev shim fills in _DEV_USER_EMAIL so we test via resolver directly.
    # Validate that a denied allowance returns empty via repository.
    from artemis.memory.repository import list_observations
    from artemis.identity.scope_policy import allowance_denied

    rows, total = await list_observations(db_session, allowance=allowance_denied())
    assert total == 0, "Denied allowance must return zero observations"
    assert rows == [], "Denied allowance must return empty rows"


# ── 5. D11 floating assistant: server-side agent resolution ──────────────────


@pytest.mark.asyncio
async def test_d11_owner_session_gets_artemis(db_session, client):
    """Owner creating a session gets agent_id=artemis in metadata."""
    import artemis.identity.dependencies as id_dep

    await _seed_user(db_session, OWNER_EMAIL, "Jon")

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = OWNER_EMAIL
        resp = await client.post(
            "/api/floating-artemis/sessions",
            json={"session_id": "test-owner-session-d11"},
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 201
    body = resp.json()
    assert body["metadata"]["agent_id"] == "artemis", (
        f"Owner must get artemis agent, got: {body['metadata'].get('agent_id')}"
    )


@pytest.mark.asyncio
async def test_d11_marketing_session_gets_callie(db_session, client):
    """Marketing user creating a session gets agent_id=callie (not artemis)."""
    import artemis.identity.dependencies as id_dep

    await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.post(
            "/api/floating-artemis/sessions",
            json={"session_id": "test-mktg-session-d11"},
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 201
    body = resp.json()
    assert body["metadata"]["agent_id"] == "callie", (
        f"Marketing must get callie agent, got: {body['metadata'].get('agent_id')}"
    )


@pytest.mark.asyncio
async def test_d11_marketing_cannot_override_to_artemis(db_session, client):
    """Marketing user requesting agent_id=artemis is still served callie."""
    import artemis.identity.dependencies as id_dep

    await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.post(
            "/api/floating-artemis/sessions",
            json={
                "session_id": "test-mktg-session-d11-override",
                "metadata": {"agent_id": "artemis"},  # client attempts to request artemis
            },
            headers={"Authorization": "Bearer test"},
        )
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 201
    body = resp.json()
    assert body["metadata"]["agent_id"] == "callie", (
        "Server must override client's agent_id=artemis request to callie for non-owner"
    )


# ── 6. Regression: marketing access still works ───────────────────────────────


@pytest.mark.asyncio
async def test_marketing_regression_can_read_workspace_marketing(db_session, client):
    """Marketing users retain access to workspace:marketing observations."""
    import artemis.identity.dependencies as id_dep

    await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    mktg_obs_id = await _seed_observation(
        db_session, "workspace", "marketing", "Marketing workspace obs"
    )
    campaign_obs_id = await _seed_observation(
        db_session, "campaign_family", "email-nurture", "Campaign family obs"
    )
    global_obs_id = await _seed_observation(
        db_session, "global", "global", "Global obs"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get("/api/memory/observations", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    obs_ids = {o["id"] for o in body["observations"]}
    assert mktg_obs_id in obs_ids, "Marketing must still see workspace:marketing"
    assert campaign_obs_id in obs_ids, "Marketing must still see campaign_family"
    assert global_obs_id in obs_ids, "Marketing must still see global"


@pytest.mark.asyncio
async def test_marketing_regression_can_read_agent_callie(db_session, client):
    """Marketing users retain access to agent:callie observations."""
    import artemis.identity.dependencies as id_dep

    await _seed_user(db_session, OWNER_EMAIL, "Jon")
    mktg_id = await _seed_user(db_session, MARKETING_EMAIL, "Marketer")

    callie_obs_id = await _seed_observation(
        db_session, "agent", "callie", "Callie marketing obs"
    )

    original = id_dep._DEV_USER_EMAIL
    try:
        id_dep._DEV_USER_EMAIL = MARKETING_EMAIL
        resp = await client.get("/api/memory/observations", headers={"Authorization": "Bearer test"})
    finally:
        id_dep._DEV_USER_EMAIL = original

    assert resp.status_code == 200
    body = resp.json()
    obs_ids = {o["id"] for o in body["observations"]}
    assert callie_obs_id in obs_ids, "Marketing must retain access to agent:callie"
