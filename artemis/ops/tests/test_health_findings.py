"""Tests for the health report's judgement layer.

`derive_findings` is pure, so these pin down the reasoning without a DB.  The
case that matters most is `test_agent_alive_on_scheduled_path_only`: it encodes
the mistake that prompted this module -- concluding an agent is dead because its
conversation column is stale, while its scheduled deliveries are landing daily.
"""

from datetime import UTC, datetime, timedelta

from artemis.ops.health import (
    AgentActivity,
    Bucket,
    Funnel,
    Report,
    StuckRun,
    derive_findings,
    render,
)


def _ago(**kwargs: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


def _report(**overrides: object) -> Report:
    base: dict[str, object] = {
        "generated_at": datetime.now(UTC),
        "service": {"healthz": "200"},
        "agents": [],
        "funnel": Funnel(),
        "stuck_runs": [],
    }
    base.update(overrides)
    return Report(**base)  # type: ignore[arg-type]


def test_healthy_system_produces_no_findings() -> None:
    report = _report(
        agents=[
            AgentActivity("artemis", _ago(days=30), _ago(days=30), _ago(hours=3), None),
            AgentActivity("kai", _ago(hours=1), _ago(hours=1), None, None),
        ],
        funnel=Funnel(signal_states=[Bucket("qualified", 100, _ago(hours=20))]),
    )
    assert derive_findings(report) == []


def test_agent_alive_on_scheduled_path_only() -> None:
    """A 20-day-silent conversation column must NOT read as a dead agent.

    This is the exact false alarm the module exists to prevent: Artemis had not
    held a Slack conversation since 2026-07-21 while delivering the morning
    brief every single morning.
    """
    artemis = AgentActivity(
        agent="artemis",
        last_conversation=_ago(days=20),
        last_trace=_ago(days=20),
        last_proactive=_ago(hours=3),
        last_push=None,
    )
    assert artemis.freshest is not None
    findings = derive_findings(_report(agents=[artemis]))
    assert findings == [], "a fresh scheduled delivery means the agent is alive"


def test_agent_with_no_activity_anywhere_is_flagged() -> None:
    dead = AgentActivity("ares", None, None, None, None)
    messages = [f.message for f in derive_findings(_report(agents=[dead]))]
    assert any("no recorded activity on any path" in m for m in messages)


def test_stalled_scheduled_delivery_is_flagged() -> None:
    stalled = AgentActivity("artemis", _ago(days=1), _ago(days=1), _ago(days=9), None)
    findings = derive_findings(_report(agents=[stalled]))
    assert any("last scheduled delivery" in f.message for f in findings)
    assert all(f.severity == "warn" for f in findings)


def test_stalled_push_is_flagged() -> None:
    stalled = AgentActivity("callie", _ago(days=1), _ago(days=1), None, _ago(days=8))
    findings = derive_findings(_report(agents=[stalled]))
    assert any("last autonomous push" in f.message for f in findings)


def test_stale_in_flight_run_is_a_stuck_finding() -> None:
    report = _report(
        stuck_runs=[StuckRun("d5c28647-aaaa", "marketing.main", "awaiting_approval", _ago(days=64))]
    )
    findings = derive_findings(report)
    assert len(findings) == 1
    assert findings[0].severity == "stuck"
    assert "BLOCKS every future scheduled run" in findings[0].message
    assert "d5c28647" in findings[0].message


def test_fresh_in_flight_run_is_not_flagged() -> None:
    """A run that started minutes ago is working, not wedged."""
    report = _report(stuck_runs=[StuckRun("abc123", "marketing.main", "running", _ago(minutes=10))])
    assert derive_findings(report) == []


def test_idle_approval_half_is_flagged_while_collection_is_healthy() -> None:
    report = _report(
        funnel=Funnel(
            signal_states=[
                Bucket("qualified", 1693, _ago(hours=20)),
                Bucket("approved", 44, _ago(days=55)),
            ]
        )
    )
    messages = [f.message for f in derive_findings(report)]
    assert any("approval half of the funnel is idle" in m for m in messages)
    assert not any("no new signals" in m for m in messages)


def test_stalled_collection_is_flagged() -> None:
    report = _report(funnel=Funnel(signal_states=[Bucket("qualified", 10, _ago(days=5))]))
    assert any("no new signals collected" in f.message for f in derive_findings(report))


def test_non_200_healthz_is_stuck() -> None:
    findings = derive_findings(_report(service={"healthz": "unreachable"}))
    assert findings[0].severity == "stuck"


def test_render_is_stable_and_mentions_each_agent() -> None:
    report = _report(
        service={"healthz": "200", "app_pid": "1244", "tunnel_pid": "926"},
        agents=[AgentActivity("artemis", None, None, _ago(hours=3), None)],
        funnel=Funnel(signal_states=[Bucket("qualified", 5, _ago(hours=1))], signals_7d=5),
    )
    report.findings = derive_findings(report)
    text = render(report)

    assert "ARTEMIS OS HEALTH" in text
    assert "artemis" in text
    assert "never" in text, "missing timestamps should read 'never', not crash"
    assert "OK -- nothing needs attention" in text


# ── inbound-with-no-replies (the silent-outage detector) ──────────────────────
#
# 2026-07-20: Sara asked Kai three questions over 19 hours during a Claude CLI
# auth outage. Every turn 401'd, so no assistant row was ever written -- and
# because `floating_artemis_messages` only records turns that SUCCEEDED, the
# miss left no trace anywhere. Sara wrote "Looks like Kai fell asleep at the
# wheel here"; the human noticed, the system did not. Nothing in this report
# detected it until now.


def test_unanswered_direct_question_is_flagged() -> None:
    report = _report(
        agents=[
            AgentActivity(
                "kai",
                last_conversation=_ago(days=14),
                last_trace=_ago(days=14),
                last_proactive=None,
                last_push=None,
                last_direct_inbound=_ago(hours=17),
            )
        ]
    )
    findings = derive_findings(report)
    assert any("has NOT" in f.message and "kai" in f.message for f in findings)
    assert any(f.severity == "stuck" for f in findings)


def test_reply_after_the_question_clears_it() -> None:
    """Answered is answered, however old the exchange is."""
    activity = AgentActivity(
        "kai",
        last_conversation=_ago(hours=2),
        last_trace=_ago(hours=2),
        last_proactive=None,
        last_push=None,
        last_direct_inbound=_ago(hours=3),
    )
    assert activity.unanswered_for is None
    assert not any("has NOT" in f.message for f in derive_findings(_report(agents=[activity])))


def test_recent_question_is_not_yet_a_finding() -> None:
    """Normal latency is ~3 minutes; a few minutes outstanding is just in flight."""
    activity = AgentActivity(
        "kai",
        last_conversation=_ago(hours=5),
        last_trace=_ago(hours=5),
        last_proactive=None,
        last_push=None,
        last_direct_inbound=_ago(minutes=4),
    )
    assert activity.unanswered_for is not None  # outstanding
    assert not any("has NOT" in f.message for f in derive_findings(_report(agents=[activity])))


def test_agent_that_never_replied_at_all_is_flagged() -> None:
    activity = AgentActivity(
        "kai",
        last_conversation=None,
        last_trace=None,
        last_proactive=None,
        last_push=None,
        last_direct_inbound=_ago(hours=6),
    )
    assert activity.unanswered_for is not None
    assert any("has NOT" in f.message for f in derive_findings(_report(agents=[activity])))


def test_no_inbound_means_nothing_outstanding() -> None:
    """A quiet channel is not a broken agent."""
    activity = AgentActivity("ares", _ago(days=52), _ago(days=52), None, None, None)
    assert activity.unanswered_for is None


def test_waiting_column_renders_the_outstanding_question() -> None:
    report = _report(
        agents=[
            AgentActivity("kai", _ago(days=14), _ago(days=14), None, None, _ago(hours=17)),
            AgentActivity("ares", _ago(days=52), _ago(days=52), None, None, None),
        ]
    )
    out = render(report)
    assert "waiting" in out
    assert "!! 17h" in out
