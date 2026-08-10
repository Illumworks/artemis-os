"""Consolidated system-health report across every store that records activity.

Read the module docstring in `artemis.ops` for why this exists.

Everything here is READ-ONLY.  It is safe to run against production at any
time, and it deliberately depends only on Postgres so it still works when the
app itself is down.
"""

import asyncio
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis import db as _db

# Named agents that have a Slack presence.  Ordered for display.
AGENTS: tuple[str, ...] = ("artemis", "callie", "kai", "ares")

# How stale each recurring signal may get before we flag it.  These encode the
# *intended* cadence, so a breach is a real finding rather than a curiosity.
STALENESS_BUDGET: dict[str, timedelta] = {
    "morning_brief": timedelta(days=4),  # weekdays only; a long weekend is fine
    "callie_signal_push": timedelta(days=3),
    "signal_collection": timedelta(days=3),
    "gate_approval": timedelta(days=14),
}

# A pipeline run this old that still counts as in-flight is a wedge, not work.
STUCK_RUN_AFTER = timedelta(days=1)


@dataclass(frozen=True)
class Finding:
    """A single problem worth a human's attention."""

    severity: str  # "warn" | "stuck"
    message: str


@dataclass(frozen=True)
class AgentActivity:
    """One named agent, seen through every path it can write to.

    Judging liveness from `last_conversation` alone is the specific mistake
    this module exists to prevent -- an agent can be silent in conversation for
    weeks while faithfully delivering its scheduled work every day.
    """

    agent: str
    last_conversation: datetime | None
    last_trace: datetime | None
    last_proactive: datetime | None
    last_push: datetime | None

    @property
    def freshest(self) -> datetime | None:
        stamps = [
            self.last_conversation,
            self.last_trace,
            self.last_proactive,
            self.last_push,
        ]
        return max((s for s in stamps if s is not None), default=None)


@dataclass(frozen=True)
class Bucket:
    """A count-and-newest roll-up (signal statuses, candidate stages)."""

    label: str
    count: int
    newest: datetime | None


@dataclass(frozen=True)
class StuckRun:
    run_id: str
    pipeline: str
    status: str
    since: datetime | None


@dataclass
class Funnel:
    signal_states: list[Bucket] = field(default_factory=list)
    candidate_stages: list[Bucket] = field(default_factory=list)
    signals_7d: int = 0
    signals_7d_via_pipeline: int = 0

    def status(self, label: str) -> Bucket | None:
        return next((b for b in self.signal_states if b.label == label), None)


@dataclass
class Report:
    generated_at: datetime
    service: dict[str, str] = field(default_factory=dict)
    agents: list[AgentActivity] = field(default_factory=list)
    funnel: Funnel = field(default_factory=Funnel)
    stuck_runs: list[StuckRun] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


# ── formatting ────────────────────────────────────────────────────────────────


def _age(when: datetime | None) -> timedelta | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when


def _fmt_duration(when: datetime | None) -> str:
    """Bare duration, for use inside a sentence ("idle for 55d")."""
    age = _age(when)
    if age is None:
        return "never"
    total = int(age.total_seconds())
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _fmt_age(when: datetime | None) -> str:
    """Relative age, for standalone display in a column."""
    if when is None:
        return "never"
    return f"{_fmt_duration(when)} ago"


def _fmt_when(when: datetime | None) -> str:
    if when is None:
        return "-"
    return when.astimezone().strftime("%Y-%m-%d %H:%M")


def _is_stale(when: datetime | None, budget: timedelta) -> bool:
    age = _age(when)
    return age is not None and age > budget


# ── service layer (does not need the app to be up) ────────────────────────────


def _shell(cmd: str) -> str:
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:  # pragma: no cover - best-effort diagnostics only
        return ""


def _leading_pid(listing: str) -> str:
    head = listing.split()
    return head[0] if head and head[0].isdigit() else ""


def collect_service() -> dict[str, str]:
    """launchd / process / health facts, gathered without touching the DB."""
    service: dict[str, str] = {}

    pid = _leading_pid(_shell("launchctl list | grep me.artemisos.app"))
    service["app_pid"] = pid or "not running"
    if pid:
        service["app_uptime"] = _shell(f"ps -o etime= -p {pid}") or "?"
        service["app_started"] = _shell(f"ps -o lstart= -p {pid}") or "?"

    code = _shell('curl -s -o /dev/null -w "%{http_code}" -m 5 http://127.0.0.1:8000/healthz')
    service["healthz"] = code or "unreachable"

    tunnel_pid = _leading_pid(_shell("launchctl list | grep me.artemisos.tunnel"))
    service["tunnel_pid"] = tunnel_pid or "not running"
    service["watchdog_last"] = _shell("tail -1 ~/Library/Logs/artemisos/watchdog.log") or "-"

    return service


# ── database layer ────────────────────────────────────────────────────────────


async def _timestamp(session: AsyncSession, sql: str, **params: object) -> datetime | None:
    value = (await session.execute(text(sql), params)).scalar()
    return value if isinstance(value, datetime) else None


async def _count(session: AsyncSession, sql: str, **params: object) -> int:
    value = (await session.execute(text(sql), params)).scalar()
    return int(value) if isinstance(value, int) else 0


async def _buckets(session: AsyncSession, sql: str) -> list[Bucket]:
    rows = (await session.execute(text(sql))).all()
    return [
        Bucket(
            label=str(row[0]) if row[0] is not None else "(none)",
            count=int(row[1]),
            newest=row[2] if isinstance(row[2], datetime) else None,
        )
        for row in rows
    ]


async def collect_agents(session: AsyncSession) -> list[AgentActivity]:
    """Per-agent activity across ALL of its write paths, not just conversation."""
    activity: list[AgentActivity] = []

    for agent in AGENTS:
        # 1. Conversational turns (inbound Slack -> handle_turn -> reply).
        last_conversation = await _timestamp(
            session,
            """
            SELECT max(created_at) FROM floating_artemis_messages
            WHERE session_id LIKE :prefix AND role = 'assistant'
            """,
            prefix=f"slack-{agent}%",
        )
        # 2. Agent run traces (any provider call made on this agent's behalf).
        last_trace = await _timestamp(
            session,
            "SELECT max(created_at) FROM agent_traces WHERE agent_id = :agent",
            agent=agent,
        )
        # 3. Proactive scheduled deliveries (morning brief, OKR check-in).
        last_proactive: datetime | None = None
        if agent == "artemis":
            last_proactive = await _timestamp(
                session,
                """
                SELECT max(delivered_at) FROM morning_brief_deliveries
                WHERE status = 'sent'
                """,
            )
        # 4. Autonomous pushes (Callie's top-tier signal cards).  These live in
        #    the memory store rather than a table of their own, which is exactly
        #    why they get overlooked.
        last_push: datetime | None = None
        if agent == "callie":
            last_push = await _timestamp(
                session,
                """
                SELECT max(created_at) FROM memory_observations
                WHERE category = 'callie_signal_push'
                """,
            )

        activity.append(
            AgentActivity(
                agent=agent,
                last_conversation=last_conversation,
                last_trace=last_trace,
                last_proactive=last_proactive,
                last_push=last_push,
            )
        )

    return activity


async def collect_funnel(session: AsyncSession) -> Funnel:
    """Marketing funnel: collection is one system, approval is another."""
    signal_states = await _buckets(
        session,
        """
        SELECT signal_status, count(*), max(created_at)
        FROM signal_queue GROUP BY 1 ORDER BY 2 DESC
        """,
    )
    candidate_stages = await _buckets(
        session,
        """
        SELECT stage, count(*), max(created_at)
        FROM campaign_candidates GROUP BY 1 ORDER BY 2 DESC
        """,
    )
    # Scouts write signals directly; the pipeline is a separate path.  Showing
    # which one produced recent rows heads off both "the pipeline is wedged so
    # nothing works" (wrong) and "signals are flowing so all is well" (also
    # wrong -- the approval half can be dead at the same time).
    signals_7d = await _count(
        session,
        "SELECT count(*) FROM signal_queue WHERE created_at > now() - interval '7 days'",
    )
    via_pipeline = await _count(
        session,
        """
        SELECT count(pipeline_run_id) FROM signal_queue
        WHERE created_at > now() - interval '7 days'
        """,
    )

    return Funnel(
        signal_states=signal_states,
        candidate_stages=candidate_stages,
        signals_7d=signals_7d,
        signals_7d_via_pipeline=via_pipeline,
    )


async def collect_stuck_runs(session: AsyncSession) -> list[StuckRun]:
    """Pipeline runs the scheduler will treat as in-flight forever.

    A run suspended at a human gate blocks every future scheduled run of the
    same pipeline.  Nothing errors and nothing alerts -- the pipeline just
    silently stops running, which is how `marketing.main` went two months
    without anyone noticing.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, pipeline_id, status, started_at, created_at
                FROM pipeline_runs
                WHERE status IN ('awaiting_approval', 'running', 'suspended')
                ORDER BY COALESCE(started_at, created_at) ASC
                """
            )
        )
    ).all()
    return [
        StuckRun(
            run_id=str(row[0]),
            pipeline=str(row[1]),
            status=str(row[2]),
            since=row[3] if isinstance(row[3], datetime) else row[4],
        )
        for row in rows
    ]


def derive_findings(report: Report) -> list[Finding]:
    """Turn the raw numbers into the short list of things that need a human."""
    findings: list[Finding] = []

    if report.service.get("healthz") != "200":
        findings.append(Finding("stuck", f"healthz returned {report.service.get('healthz')!r}"))

    for agent in report.agents:
        if agent.freshest is None:
            findings.append(Finding("warn", f"{agent.agent}: no recorded activity on any path"))
        if _is_stale(agent.last_proactive, STALENESS_BUDGET["morning_brief"]):
            findings.append(
                Finding(
                    "warn",
                    f"{agent.agent}: last scheduled delivery was "
                    f"{_fmt_duration(agent.last_proactive)} ago",
                )
            )
        if _is_stale(agent.last_push, STALENESS_BUDGET["callie_signal_push"]):
            findings.append(
                Finding(
                    "warn",
                    f"{agent.agent}: last autonomous push was {_fmt_duration(agent.last_push)} ago",
                )
            )

    for run in report.stuck_runs:
        if _is_stale(run.since, STUCK_RUN_AFTER):
            findings.append(
                Finding(
                    "stuck",
                    f"{run.pipeline} has been {run.status} for "
                    f"{_fmt_duration(run.since)} ({run.run_id[:8]}) -- this BLOCKS "
                    "every future scheduled run of that pipeline",
                )
            )

    collected = report.funnel.status("qualified") or report.funnel.status("pending_qualification")
    if collected and _is_stale(collected.newest, STALENESS_BUDGET["signal_collection"]):
        findings.append(
            Finding("warn", f"no new signals collected for {_fmt_duration(collected.newest)}")
        )

    approved = report.funnel.status("approved")
    if approved and _is_stale(approved.newest, STALENESS_BUDGET["gate_approval"]):
        findings.append(
            Finding(
                "warn",
                f"nothing approved past Gate 1 for {_fmt_duration(approved.newest)} -- "
                "collection is working; the approval half of the funnel is idle",
            )
        )

    return findings


async def build_report() -> Report:
    report = Report(generated_at=datetime.now(UTC), service=collect_service())
    async with _db.SessionLocal() as session:
        report.agents = await collect_agents(session)
        report.funnel = await collect_funnel(session)
        report.stuck_runs = await collect_stuck_runs(session)
    report.findings = derive_findings(report)
    return report


# ── rendering ─────────────────────────────────────────────────────────────────


def render(report: Report) -> str:
    out: list[str] = []
    add = out.append

    add("=" * 78)
    add(f"ARTEMIS OS HEALTH  ({report.generated_at.astimezone():%Y-%m-%d %H:%M %Z})")
    add("=" * 78)

    service = report.service
    add("")
    add("SERVICE")
    add(f"  app        pid {service.get('app_pid')}  up {service.get('app_uptime', '-')}")
    add(f"  started    {service.get('app_started', '-')}")
    add(f"  healthz    {service.get('healthz')}")
    add(f"  tunnel     pid {service.get('tunnel_pid')}")
    add(f"  watchdog   {service.get('watchdog_last')}")

    add("")
    add("NAMED AGENTS -- an agent is alive if ANY column is recent")
    add(f"  {'agent':<9} {'conversation':<15} {'trace':<15} {'scheduled':<15} {'push':<15}")
    for agent in report.agents:
        add(
            f"  {agent.agent:<9} "
            f"{_fmt_age(agent.last_conversation):<15} "
            f"{_fmt_age(agent.last_trace):<15} "
            f"{_fmt_age(agent.last_proactive):<15} "
            f"{_fmt_age(agent.last_push):<15}"
        )
    add("  conversation = replied to an inbound Slack message")
    add("  trace        = made any provider call")
    add("  scheduled    = delivered a cron-driven brief / check-in")
    add("  push         = posted an unprompted signal card")

    funnel = report.funnel
    add("")
    add("MARKETING FUNNEL")
    add(
        f"  signals last 7d: {funnel.signals_7d} "
        f"({funnel.signals_7d_via_pipeline} via pipeline, rest written directly by scouts)"
    )
    add("  signals by status:")
    for bucket in funnel.signal_states:
        add(f"    {bucket.label:<24} {bucket.count:>6}   newest {_fmt_when(bucket.newest)}")
    add("  campaign candidates by stage:")
    if not funnel.candidate_stages:
        add("    (none)")
    for bucket in funnel.candidate_stages:
        add(f"    {bucket.label:<24} {bucket.count:>6}   newest {_fmt_when(bucket.newest)}")

    add("")
    add("IN-FLIGHT PIPELINE RUNS (a stale one blocks its pipeline's schedule)")
    if not report.stuck_runs:
        add("  none")
    for run in report.stuck_runs:
        add(
            f"  {run.run_id[:8]}  {run.pipeline:<34} {run.status:<18} "
            f"since {_fmt_when(run.since)} ({_fmt_age(run.since)})"
        )

    add("")
    add("FINDINGS")
    if not report.findings:
        add("  OK -- nothing needs attention")
    for finding in report.findings:
        marker = "!!" if finding.severity == "stuck" else " -"
        add(f"  {marker} {finding.message}")
    add("")

    return "\n".join(out)


def main() -> int:
    report = asyncio.run(build_report())
    print(render(report))
    return 1 if any(f.severity == "stuck" for f in report.findings) else 0
