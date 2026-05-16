"""Tests for the observability-intent pre-router."""

from __future__ import annotations

import pytest

from artemis.floating_artemis.intent import IntentKind, classify_intent

pytestmark = pytest.mark.asyncio


# ── Active runs patterns ──────────────────────────────────────────────────────


def test_active_runs_what_is_running() -> None:
    result = classify_intent("what's running")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_what_is_currently_running() -> None:
    result = classify_intent("what is currently running")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_show_active() -> None:
    result = classify_intent("show me active runs")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_list_active() -> None:
    result = classify_intent("list active runs")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_any_agents_running() -> None:
    result = classify_intent("any agents currently running")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_any_workflows_running() -> None:
    result = classify_intent("any workflows running")
    assert result.kind == IntentKind.ACTIVE_RUNS


def test_active_runs_what_is_active() -> None:
    result = classify_intent("what's active right now")
    assert result.kind == IntentKind.ACTIVE_RUNS


# ── Recent failures patterns ──────────────────────────────────────────────────


def test_recent_failures_any_failures() -> None:
    result = classify_intent("any failures")
    assert result.kind == IntentKind.RECENT_FAILURES


def test_recent_failures_any_recent() -> None:
    result = classify_intent("any recent failures")
    assert result.kind == IntentKind.RECENT_FAILURES


def test_recent_failures_show_recent() -> None:
    result = classify_intent("show recent agent failures")
    assert result.kind == IntentKind.RECENT_FAILURES


def test_recent_failures_what_failed() -> None:
    result = classify_intent("what agents failed recently")
    assert result.kind == IntentKind.RECENT_FAILURES


# ── Health check patterns ─────────────────────────────────────────────────────


def test_health_check_bare() -> None:
    result = classify_intent("health check")
    assert result.kind == IntentKind.HEALTH_CHECK


def test_health_check_system() -> None:
    result = classify_intent("system health")
    assert result.kind == IntentKind.HEALTH_CHECK


def test_health_are_systems_ok() -> None:
    result = classify_intent("are all systems ok")
    assert result.kind == IntentKind.HEALTH_CHECK


def test_health_are_systems_nominal() -> None:
    result = classify_intent("are systems nominal")
    assert result.kind == IntentKind.HEALTH_CHECK


# ── Non-matches (should not trigger shortcuts) ────────────────────────────────


def test_no_match_open_ended_question() -> None:
    result = classify_intent("what do you think about running more agents?")
    assert result.kind == IntentKind.NONE


def test_no_match_casual_greeting() -> None:
    result = classify_intent("hey, how are you?")
    assert result.kind == IntentKind.NONE


def test_no_match_complex_query() -> None:
    result = classify_intent("can you help me write a workflow that runs every morning?")
    assert result.kind == IntentKind.NONE


def test_no_match_vague_status() -> None:
    result = classify_intent("how is the marketing pipeline going?")
    assert result.kind == IntentKind.NONE


def test_no_match_empty() -> None:
    result = classify_intent("")
    assert result.kind == IntentKind.NONE


def test_no_match_unrelated() -> None:
    result = classify_intent("I want to create a new signal qualification rule")
    assert result.kind == IntentKind.NONE


def test_no_match_partial_keyword() -> None:
    # "failures" in a complex sentence shouldn't always match
    result = classify_intent("how do we reduce failures in our marketing campaigns?")
    assert result.kind == IntentKind.NONE


# ── Confidence value ──────────────────────────────────────────────────────────


def test_match_has_confidence_one() -> None:
    result = classify_intent("what's running")
    assert result.confidence == 1.0


def test_no_match_has_confidence_zero() -> None:
    result = classify_intent("hello world")
    assert result.confidence == 0.0
