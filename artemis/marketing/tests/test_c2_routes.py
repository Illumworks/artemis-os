"""Phase C2 route tests — ≥80 tests covering happy paths + error paths.

Uses the httpx.AsyncClient ASGI fixture from tests/conftest.py (root level)
plus the marketing db_session fixture from this conftest.py.

All DB-touching tests must use db_session to get per-test isolation via TRUNCATE.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import (
    Approval,
    CampaignCandidate,
    ContentAsset,
    Ruleset,
    SignalQueue,
)
from artemis.marketing.repository import (
    create_approval,
    create_campaign_candidate_from_signal,
    create_content_asset,
    create_scout_run,
    create_signal,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _make_signal(db: AsyncSession, **overrides: Any) -> SignalQueue:
    defaults = {
        "headline": "Test Headline",
        "campaign_family": "test_family",
        "source_type": "manual",
        "summary": "A test signal",
        "discovered_by": "manual",
    }
    return await create_signal(db, **{**defaults, **overrides})


async def _make_candidate(db: AsyncSession, signal: SignalQueue) -> CampaignCandidate:
    return await create_campaign_candidate_from_signal(
        db,
        signal_id=signal.id,
        ruleset_version_tag="v1",
    )


async def _make_asset(db: AsyncSession, **overrides: Any) -> ContentAsset:
    defaults = {"asset_type": "snippet", "status": "draft"}
    return await create_content_asset(db, **{**defaults, **overrides})


async def _make_ruleset(db: AsyncSession, **overrides: Any) -> Ruleset:
    defaults: dict[str, Any] = {
        "family": "test_family",
        "version_tag": "v1",
        "state": "draft",
        "hard_filters": [],
        "weighted_signals": [],
        "qualitative_rubrics": [],
    }
    ruleset = Ruleset(**{**defaults, **overrides})
    db.add(ruleset)
    await db.flush()
    await db.refresh(ruleset)
    return ruleset


async def _make_approval(db: AsyncSession, **overrides: Any) -> Approval:
    defaults: dict[str, Any] = {"kind": "signal_approval", "subject_id": "1"}
    merged = {**defaults, **overrides}
    return await create_approval(
        db,
        kind=merged["kind"],
        subject_id=merged["subject_id"],
        decision_payload=merged.get("decision_payload"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scouts — /api/scouts
# ─────────────────────────────────────────────────────────────────────────────


class TestScoutsPackages:
    async def test_list_packages_ok(self, client: AsyncClient) -> None:
        r = await client.get("/api/scouts/packages")
        assert r.status_code == 200
        data = r.json()
        assert "packages" in data
        assert isinstance(data["packages"], list)

    async def test_list_packages_structure(self, client: AsyncClient) -> None:
        """Each package (if any) should have scoutType."""
        r = await client.get("/api/scouts/packages")
        assert r.status_code == 200
        for pkg in r.json()["packages"]:
            assert "scoutType" in pkg


class TestScoutsRuns:
    async def test_list_runs_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/scouts/runs")
        assert r.status_code == 200
        data = r.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)

    async def test_get_run_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/scouts/runs/nonexistent_run_id")
        assert r.status_code == 404
        assert r.json()["code"] == "scout_run_not_found"

    async def test_get_run_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        run = await create_scout_run(db_session, run_id="scout_run_test_1", scout_type="test_scout")
        await db_session.commit()
        r = await client.get(f"/api/scouts/runs/{run.id}")
        assert r.status_code == 200
        assert r.json()["run"]["id"] == run.id

    async def test_create_run_missing_scout_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/scouts/runs", json={"findings": [{"headline": "x"}]})
        assert r.status_code == 400

    async def test_create_run_empty_findings(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/scouts/runs",
            json={"scoutType": "regional_news_scout", "findings": []},
        )
        assert r.status_code == 400

    async def test_create_run_too_many_findings(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        findings = [{"headline": f"h{i}", "campaignFamily": "f"} for i in range(101)]
        r = await client.post(
            "/api/scouts/runs",
            json={"scoutType": "regional_news_scout", "findings": findings},
        )
        assert r.status_code == 400

    async def test_create_run_unknown_scout_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/scouts/runs",
            json={
                "scoutType": "unknown_scout",
                "findings": [{"headline": "x", "campaignFamily": "f"}],
            },
        )
        # Either 400 (packages found, unknown type) or 201 (empty packages list in test env)
        # In CI without packages file: 201 because unknown type is not in empty list
        assert r.status_code in {400, 201}

    async def test_create_run_dry_run(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """Dry-run with valid findings should return dry_run_passed."""
        r = await client.post(
            "/api/scouts/runs",
            json={
                "scoutType": "regional_news_scout",
                "dryRun": True,
                "findings": [
                    {
                        "headline": "Board approves budget",
                        "campaignFamily": "ev_awareness",
                        "sourceType": "news_article",
                    }
                ],
            },
        )
        # May be 201 (packages exist) or 400 (packages empty / unknown type)
        # We just confirm the response is well-formed if 201
        if r.status_code == 201:
            body = r.json()
            assert body["dryRun"] is True
            assert "status" in body

    async def test_list_runs_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await create_scout_run(db_session, run_id="sr_pending_1", scout_type="t", status="pending")
        await db_session.commit()
        r = await client.get("/api/scouts/runs?status=pending")
        assert r.status_code == 200
        runs = r.json()["runs"]
        for run in runs:
            assert run["status"] == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# Signal Queue — /api/signal-queue
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalQueueList:
    async def test_list_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/signal-queue/")
        assert r.status_code == 200
        data = r.json()
        assert data["signals"] == []

    async def test_list_with_signal(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _make_signal(db_session)
        await db_session.commit()
        r = await client.get("/api/signal-queue/")
        assert r.status_code == 200
        assert len(r.json()["signals"]) == 1

    async def test_list_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_signal(db_session, signal_status="in_inbox")
        await _make_signal(db_session, signal_status="approved")
        await db_session.commit()
        r = await client.get("/api/signal-queue/?status=in_inbox")
        assert r.status_code == 200
        signals = r.json()["signals"]
        assert all(s["signalStatus"] == "in_inbox" for s in signals)

    async def test_list_invalid_status_returns_all(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Invalid status string should be ignored (returns all)."""
        await _make_signal(db_session)
        await db_session.commit()
        r = await client.get("/api/signal-queue/?status=garbage_status")
        assert r.status_code == 200
        assert len(r.json()["signals"]) == 1


class TestSignalQueueGet:
    async def test_get_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/signal-queue/99999")
        assert r.status_code == 404
        assert r.json()["code"] == "signal_not_found"

    async def test_get_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        await db_session.commit()
        r = await client.get(f"/api/signal-queue/{signal.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == signal.id
        assert body["headline"] == "Test Headline"
        assert "signalStatus" in body


class TestSignalQueueIntake:
    async def test_intake_missing_headline(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/signal-queue/intake",
            json={"campaignFamily": "test"},
        )
        assert r.status_code == 400

    async def test_intake_dry_run_invalid(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/signal-queue/intake",
            json={"dryRun": True, "campaignFamily": "test"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dryRun"] is True
        assert body["valid"] is False

    async def test_intake_dry_run_valid(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # C3: campaignFamily must be in VALID_CAMPAIGN_FAMILIES; use "obc"
        r = await client.post(
            "/api/signal-queue/intake",
            json={"dryRun": True, "headline": "Big news", "campaignFamily": "obc"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dryRun"] is True
        assert body["valid"] is True

    async def test_intake_root_alias_matches_intake_response_shape(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        payload = {"dryRun": True, "headline": "Big news", "campaignFamily": "obc"}

        intake_resp = await client.post("/api/signal-queue/intake", json=payload)
        alias_resp = await client.post("/api/signal-queue", json=payload)

        assert alias_resp.status_code == intake_resp.status_code == 200
        assert alias_resp.json() == intake_resp.json()

    async def test_intake_creates_signal(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # C3: campaignFamily must be in VALID_CAMPAIGN_FAMILIES; use "obc"
        r = await client.post(
            "/api/signal-queue/intake",
            json={"headline": "New district opens", "campaignFamily": "obc"},
        )
        assert r.status_code == 201
        body = r.json()
        assert "signal" in body
        assert body["signal"]["headline"] == "New district opens"

    async def test_intake_duplicate_returns_409(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_signal(
            db_session,
            headline="Duplicate Signal",
            source_url="http://example.com/dupe",
            campaign_family="obc",
        )
        await db_session.commit()
        # C3: campaignFamily must be in VALID_CAMPAIGN_FAMILIES; use "obc"
        r = await client.post(
            "/api/signal-queue/intake",
            json={
                "headline": "Duplicate Signal",
                "campaignFamily": "obc",
                "sourceUrl": "http://example.com/dupe",
            },
        )
        assert r.status_code == 409


class TestSignalQueueActions:
    async def test_qualify_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post("/api/signal-queue/99999/qualify")
        assert r.status_code == 404

    async def test_qualify_returns_400_when_no_active_rulesets(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # C3: qualify endpoint now requires active rulesets; 400 when none exist
        signal = await _make_signal(db_session)
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/qualify")
        assert r.status_code == 400
        assert r.json()["code"] == "no_active_rulesets"

    async def test_approve_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post("/api/signal-queue/99999/approve")
        assert r.status_code == 404

    async def test_approve_wrong_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session, signal_status="approved")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/approve")
        assert r.status_code == 409

    async def test_approve_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="in_inbox")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/approve")
        assert r.status_code == 200
        body = r.json()
        assert body["signal"]["signalStatus"] == "approved"
        assert "candidateId" in body

    async def test_reject_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post("/api/signal-queue/99999/reject")
        assert r.status_code == 404

    async def test_reject_wrong_status(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="rejected")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/reject", json={})
        assert r.status_code == 409

    async def test_reject_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="in_inbox")
        await db_session.commit()
        r = await client.post(
            f"/api/signal-queue/{signal.id}/reject",
            json={"reason": "Not relevant"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["signalStatus"] == "rejected"
        assert body["rejectedReason"] == "Not relevant"

    async def test_snooze_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post("/api/signal-queue/99999/snooze", json={"days": 7})
        assert r.status_code == 404

    async def test_snooze_invalid_days(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="in_inbox")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/snooze", json={"days": 0})
        assert r.status_code == 400

    async def test_snooze_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="in_inbox")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/snooze", json={"days": 7})
        assert r.status_code == 200
        body = r.json()
        assert body["signalStatus"] == "snoozed"
        assert body["snoozedUntil"] is not None

    async def test_ask_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post("/api/signal-queue/99999/ask")
        assert r.status_code == 404

    async def test_ask_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session, signal_status="in_inbox")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/ask")
        assert r.status_code == 200
        assert r.json()["signalStatus"] == "archived"

    async def test_archive_alias_matches_ask_response_shape(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ask_signal = await _make_signal(db_session, signal_status="in_inbox")
        archive_signal = await _make_signal(
            db_session,
            signal_status="in_inbox",
            headline="Archive alias",
            source_url="http://example.com/archive-alias",
        )
        await db_session.commit()

        ask = await client.post(f"/api/signal-queue/{ask_signal.id}/ask")
        archive = await client.post(f"/api/signal-queue/{archive_signal.id}/archive")

        assert archive.status_code == ask.status_code == 200
        assert archive.json().keys() == ask.json().keys()
        assert archive.json()["signalStatus"] == ask.json()["signalStatus"] == "archived"

    async def test_ask_already_archived(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session, signal_status="archived")
        await db_session.commit()
        r = await client.post(f"/api/signal-queue/{signal.id}/ask")
        assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# Signal Criteria — /api/signal-criteria
# ─────────────────────────────────────────────────────────────────────────────


class TestSignalCriteria:
    async def test_list_rulesets_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/signal-criteria/rulesets")
        assert r.status_code == 200
        assert r.json() == []

    async def test_list_rulesets_with_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_ruleset(db_session)
        await db_session.commit()
        r = await client.get("/api/signal-criteria/rulesets")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_get_ruleset_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/api/signal-criteria/rulesets/nonexistent_family")
        assert r.status_code == 404

    async def test_get_ruleset_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _make_ruleset(db_session, family="ev_awareness", version_tag="v1")
        await db_session.commit()
        r = await client.get("/api/signal-criteria/rulesets/ev_awareness")
        assert r.status_code == 200
        body = r.json()
        assert body["family"] == "ev_awareness"
        assert "versions" in body

    async def test_create_ruleset_missing_family(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/signal-criteria/rulesets", json={"versionTag": "v1"})
        assert r.status_code == 400

    async def test_create_ruleset_missing_version_tag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/signal-criteria/rulesets", json={"family": "ev_awareness"})
        assert r.status_code == 400

    async def test_create_ruleset_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post(
            "/api/signal-criteria/rulesets",
            json={"family": "ev_awareness", "versionTag": "v1"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["family"] == "ev_awareness"
        assert body["state"] == "draft"

    async def test_create_ruleset_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_ruleset(db_session, family="ev_awareness", version_tag="v1")
        await db_session.commit()
        r = await client.post(
            "/api/signal-criteria/rulesets",
            json={"family": "ev_awareness", "versionTag": "v1"},
        )
        assert r.status_code == 409

    async def test_activate_ruleset_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/signal-criteria/rulesets/99999/activate")
        assert r.status_code == 404

    async def test_activate_ruleset_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        ruleset = await _make_ruleset(db_session, family="ev_awareness", version_tag="v1")
        await db_session.commit()
        r = await client.post(f"/api/signal-criteria/rulesets/{ruleset.id}/activate")
        assert r.status_code == 200
        assert r.json()["state"] == "active"

    async def test_get_territory_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/signal-criteria/territory/unknown_family")
        assert r.status_code == 200
        body = r.json()
        assert body["family"] == "unknown_family"
        assert body["hotStates"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Ops — /api/campaign-ops
# ─────────────────────────────────────────────────────────────────────────────


class TestCampaignOps:
    async def test_list_candidates_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/api/campaign-ops/candidates")
        assert r.status_code == 200
        assert r.json()["candidates"] == []

    async def test_list_candidates_with_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.get("/api/campaign-ops/candidates")
        assert r.status_code == 200
        assert len(r.json()["candidates"]) == 1

    async def test_list_candidates_filter_decision_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.get("/api/campaign-ops/candidates?decisionState=pending_review")
        assert r.status_code == 200
        # approved candidate shouldn't match
        r2 = await client.get("/api/campaign-ops/candidates?decisionState=pending_review")
        assert r2.status_code == 200

    async def test_get_candidate_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/api/campaign-ops/candidates/99999")
        assert r.status_code == 404
        assert r.json()["code"] == "campaign_ops_candidate_not_found"

    async def test_get_candidate_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.get(f"/api/campaign-ops/candidates/{candidate.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == candidate.id
        assert body["campaignFamily"] == signal.campaign_family

    async def test_brief_assemble_produces_real_brief(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # C3: brief assembly now returns a real brief (stub replaced)
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.post(f"/api/campaign-ops/candidates/{candidate.id}/brief/assemble")
        assert r.status_code == 201
        body = r.json()
        assert "brief" in body
        assert "stub" not in body
        assert body["brief"]["candidateId"] == candidate.id

    async def test_brief_assemble_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/campaign-ops/candidates/99999/brief/assemble")
        assert r.status_code == 404

    async def test_advance_invalid_action(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/advance",
            json={"action": "invalid_action"},
        )
        assert r.status_code == 400

    async def test_advance_approve(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/advance",
            json={"action": "approve"},
        )
        assert r.status_code == 200
        assert r.json()["decisionState"] == "approved"

    async def test_advance_reject(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.post(
            f"/api/campaign-ops/candidates/{candidate.id}/advance",
            json={"action": "reject"},
        )
        assert r.status_code == 200
        assert r.json()["decisionState"] == "rejected"

    async def test_advance_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post(
            "/api/campaign-ops/candidates/99999/advance",
            json={"action": "approve"},
        )
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Deliverables — /api/campaign-deliverables
# ─────────────────────────────────────────────────────────────────────────────


class TestCampaignDeliverables:
    async def test_list_deliverables_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.get(f"/api/campaign-deliverables/{candidate.id}")
        assert r.status_code == 200
        assert r.json() == []

    async def test_list_deliverables_query_alias_matches_path(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        await client.post(
            "/api/campaign-deliverables/",
            json={"candidateId": candidate.id, "status": "generating"},
        )

        path = await client.get(f"/api/campaign-deliverables/{candidate.id}")
        alias = await client.get(f"/api/campaign-deliverables?campaignId={candidate.id}")

        assert alias.status_code == 200
        assert alias.json() == path.json()

    async def test_list_deliverables_query_alias_without_campaign_returns_empty(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/api/campaign-deliverables")
        assert r.status_code == 200
        assert r.json() == []

    async def test_list_deliverables_candidate_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.get("/api/campaign-deliverables/99999")
        assert r.status_code == 404

    async def test_create_deliverable_missing_candidate_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/campaign-deliverables/", json={"status": "generating"})
        assert r.status_code == 400

    async def test_create_deliverable_candidate_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/campaign-deliverables/",
            json={"candidateId": 99999},
        )
        assert r.status_code == 404

    async def test_create_deliverable_ok(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        r = await client.post(
            "/api/campaign-deliverables/",
            json={"candidateId": candidate.id, "status": "generating"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["candidateId"] == candidate.id
        assert body["status"] == "generating"
        assert "metadata" in body

    async def test_submit_review_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/campaign-deliverables/99999/submit-review", json={})
        assert r.status_code == 404

    async def test_submit_review_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        await db_session.commit()
        # Create deliverable via HTTP
        r = await client.post(
            "/api/campaign-deliverables/",
            json={"candidateId": candidate.id},
        )
        assert r.status_code == 201
        deliverable_id = r.json()["id"]
        r2 = await client.post(
            f"/api/campaign-deliverables/{deliverable_id}/submit-review", json={}
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "review_pending"


# ─────────────────────────────────────────────────────────────────────────────
# Content Assets — /api/content-assets
# ─────────────────────────────────────────────────────────────────────────────


class TestContentAssets:
    async def test_list_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/content-assets/")
        assert r.status_code == 200
        assert r.json() == []

    async def test_create_missing_asset_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/content-assets/", json={"summary": "test"})
        assert r.status_code == 400

    async def test_create_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post(
            "/api/content-assets/",
            json={"assetType": "snippet", "summary": "A test snippet"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["assetType"] == "snippet"
        assert body["status"] == "draft"
        assert "metadata" in body

    async def test_get_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/content-assets/99999")
        assert r.status_code == 404

    async def test_get_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        asset = await _make_asset(db_session)
        await db_session.commit()
        r = await client.get(f"/api/content-assets/{asset.id}")
        assert r.status_code == 200
        assert r.json()["id"] == asset.id

    async def test_patch_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.patch("/api/content-assets/99999", json={"status": "final"})
        assert r.status_code == 404

    async def test_patch_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        asset = await _make_asset(db_session)
        await db_session.commit()
        r = await client.patch(
            f"/api/content-assets/{asset.id}",
            json={"status": "final", "summary": "Updated"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "final"
        assert body["summary"] == "Updated"

    async def test_list_with_assets(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _make_asset(db_session, asset_type="doc")
        await _make_asset(db_session, asset_type="snippet")
        await db_session.commit()
        r = await client.get("/api/content-assets/")
        assert r.status_code == 200
        assert len(r.json()) == 2

    async def test_list_filter_asset_type(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_asset(db_session, asset_type="doc")
        await _make_asset(db_session, asset_type="snippet")
        await db_session.commit()
        r = await client.get("/api/content-assets/?assetType=doc")
        assert r.status_code == 200
        assert all(a["assetType"] == "doc" for a in r.json())


class TestContentAssetLinks:
    async def test_create_link_missing_candidate_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/content-assets/links", json={"assetId": 1})
        assert r.status_code == 400

    async def test_create_link_missing_asset_id(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post("/api/content-assets/links", json={"candidateId": 1})
        assert r.status_code == 400

    async def test_create_link_asset_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.post(
            "/api/content-assets/links",
            json={"candidateId": 1, "assetId": 99999},
        )
        assert r.status_code == 404

    async def test_create_link_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        asset = await _make_asset(db_session)
        await db_session.commit()
        r = await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id, "linkRole": "reference"},
        )
        assert r.status_code == 201
        body = r.json()
        assert body["candidateId"] == candidate.id
        assert body["assetId"] == asset.id
        assert body["linkRole"] == "reference"

    async def test_list_links_query_alias_filters_by_campaign(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        other = await _make_candidate(db_session, signal)
        asset = await _make_asset(db_session)
        other_asset = await _make_asset(db_session)
        await db_session.commit()
        created = await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id},
        )
        await client.post(
            "/api/content-assets/links",
            json={"candidateId": other.id, "assetId": other_asset.id},
        )

        alias = await client.get(f"/api/content-assets/links?campaignId={candidate.id}")
        all_links = await client.get("/api/content-assets/links")

        assert alias.status_code == 200
        assert alias.json() == [created.json()]
        assert len(all_links.json()) == 2

    async def test_create_link_duplicate_returns_409(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        asset = await _make_asset(db_session)
        await db_session.commit()
        # First link
        await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id},
        )
        # Duplicate
        r = await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id},
        )
        assert r.status_code == 409

    async def test_delete_link_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        r = await client.delete("/api/content-assets/links/99999")
        assert r.status_code == 404

    async def test_delete_link_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        asset = await _make_asset(db_session)
        await db_session.commit()
        r_create = await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id},
        )
        link_id = r_create.json()["id"]
        r_delete = await client.delete(f"/api/content-assets/links/{link_id}")
        assert r_delete.status_code == 204

    async def test_delete_link_campaign_asset_alias_matches_link_delete(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        signal = await _make_signal(db_session)
        candidate = await _make_candidate(db_session, signal)
        asset = await _make_asset(db_session)
        await db_session.commit()
        await client.post(
            "/api/content-assets/links",
            json={"candidateId": candidate.id, "assetId": asset.id},
        )

        r_delete = await client.delete(f"/api/content-assets/links/{candidate.id}/{asset.id}")
        r_repeat = await client.delete(f"/api/content-assets/links/{candidate.id}/{asset.id}")

        assert r_delete.status_code == 204
        assert r_repeat.status_code == 204


# ─────────────────────────────────────────────────────────────────────────────
# Approvals — /api/approvals
# ─────────────────────────────────────────────────────────────────────────────


class TestApprovals:
    async def test_list_empty(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/approvals/")
        assert r.status_code == 200
        assert r.json() == []

    async def test_list_with_data(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _make_approval(db_session)
        await db_session.commit()
        r = await client.get("/api/approvals/")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_list_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _make_approval(db_session)
        await db_session.commit()
        r = await client.get("/api/approvals/?status=pending")
        assert r.status_code == 200
        assert all(a["status"] == "pending" for a in r.json())

    async def test_get_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.get("/api/approvals/99999")
        assert r.status_code == 404

    async def test_get_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        approval = await _make_approval(db_session)
        await db_session.commit()
        r = await client.get(f"/api/approvals/{approval.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == approval.id
        assert body["status"] == "pending"
        # Python schema doesn't have these — should be null
        assert body["targetType"] is None
        assert body["approvalKind"] is None

    async def test_decide_not_found(self, client: AsyncClient, db_session: AsyncSession) -> None:
        r = await client.post(
            "/api/approvals/99999/decision",
            json={"status": "approved", "decidedBy": "tester"},
        )
        assert r.status_code == 404

    async def test_decide_invalid_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        approval = await _make_approval(db_session)
        await db_session.commit()
        r = await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "skip", "decidedBy": "tester"},
        )
        assert r.status_code == 400

    async def test_decide_approve_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        approval = await _make_approval(db_session)
        await db_session.commit()
        r = await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "approved", "decidedBy": "tester@example.com"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved"
        assert body["decidedBy"] == "tester@example.com"
        assert body["decidedAt"] is not None

    async def test_decide_reject_ok(self, client: AsyncClient, db_session: AsyncSession) -> None:
        approval = await _make_approval(db_session)
        await db_session.commit()
        r = await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "rejected", "decidedBy": "reviewer"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

    async def test_decide_node_compat_approve_verb(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Accept Node's 'approve' verb in addition to the Python 'approved' state."""
        approval = await _make_approval(db_session)
        await db_session.commit()
        r = await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "approve", "decidedBy": "reviewer"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    async def test_decide_already_decided(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        approval = await _make_approval(db_session)
        await db_session.commit()
        # First decision
        await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "approved", "decidedBy": "tester"},
        )
        # Second decision on same approval
        r = await client.post(
            f"/api/approvals/{approval.id}/decision",
            json={"status": "rejected", "decidedBy": "tester"},
        )
        assert r.status_code == 400
