"""Runtime configuration loaded from environment.

Single source of truth for all env-driven settings. Imported wherever config is needed.
Never read os.environ directly elsewhere — go through `settings`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ARTEMIS_",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    app_base_url: str = Field(
        default="",
        description="Absolute Artemis app base URL used for Slack deep-links.",
    )
    outbound_send_enabled: bool = Field(
        default=False,
        description="Enable CMP-SEND-2 outbound send surfaces and enqueue-on-approve hook.",
    )
    marketing_campaigns_slack_channel: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_MARKETING_CAMPAIGNS_SLACK_CHANNEL",
            "MARKETING_CAMPAIGNS_SLACK_CHANNEL",
        ),
        description=(
            "Slack channel ID (e.g. C0B8QE17DGQ) where marketing approval gates post a "
            "review notification, in addition to approver DMs. Empty = no channel post."
        ),
    )
    marketing_content_review_channel_id: str = Field(
        default="C0BAJV9A2UX",
        validation_alias=AliasChoices(
            "ARTEMIS_MARKETING_CONTENT_REVIEW_CHANNEL_ID",
            "MARKETING_CONTENT_REVIEW_CHANNEL_ID",
        ),
        description=(
            "Slack channel ID for the 'Marketing Content Review' channel (C0BAJV9A2UX). "
            "Used by Callie when a Writing Studio draft is marked ready-for-review but is "
            "NOT attached to a campaign. Campaign-attached drafts continue to use "
            "marketing_campaigns_slack_channel."
        ),
    )
    approval_notify_override: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_APPROVAL_NOTIFY_OVERRIDE",
            "APPROVAL_NOTIFY_OVERRIDE",
        ),
        description=(
            "TEST/STAGING: if set to an email, ALL human-gate approval DMs route only to this "
            "person instead of the configured approvers (channel post is unaffected). Empty = "
            "normal routing to the gate's approvers."
        ),
    )

    db_url: str = Field(
        default="postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_os",
        description="SQLAlchemy async URL for Postgres. Use 127.0.0.1, not "
        "localhost: localhost resolves to ::1 first and IPv6 loopback to "
        "Postgres hangs on this machine (4s+ connect stall before IPv4 fallback).",
    )
    db_pool_size: int = Field(
        default=5,
        description="SQLAlchemy pool_size (number of persistent connections). "
        "Keep lean in dev/worktrees; production overrides via ARTEMIS_DB_POOL_SIZE.",
    )
    db_max_overflow: int = Field(
        default=10,
        description="SQLAlchemy max_overflow (burst connections above pool_size). "
        "Keep lean in dev/worktrees; production overrides via ARTEMIS_DB_MAX_OVERFLOW.",
    )
    db_pool_timeout: int = Field(
        default=10,
        description="Seconds to wait for a connection from the pool before raising. "
        "10s is a safe improvement over SQLAlchemy's 30s default.",
    )
    db_pool_recycle: int = Field(
        default=1800,
        description="Seconds after which a connection is recycled to avoid stale "
        "server-side timeouts (30 min is a safe default).",
    )

    token: str | None = Field(default=None, description="Shared-account auth token; None disables.")
    cf_access_enabled: bool = Field(
        default=False,
        description="When true, trust and verify Cloudflare Access JWT headers for identity.",
    )
    cf_access_team_domain: str = Field(
        default="",
        description="Cloudflare Access team domain, e.g. example.cloudflareaccess.com.",
    )
    cf_access_aud: str = Field(
        default="",
        description="Cloudflare Access application audience tag (AUD).",
    )
    google_client_id: str = Field(
        default="",
        description="Google OAuth client id for per-user Docs/Drive access.",
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth client secret for per-user Docs/Drive access.",
    )
    google_redirect_uri: str = Field(
        default="https://app.artemisos.me/api/google/oauth/callback",
        description="Google OAuth redirect URI for the Artemis app.",
    )
    writing_studio_docs_folder_id: str = Field(
        default="19Dxp0xTwz_owGorQAc_BwSXmCJO-pPeP",
        validation_alias=AliasChoices(
            "ARTEMIS_WRITING_STUDIO_DOCS_FOLDER_ID",
            "WRITING_STUDIO_DOCS_FOLDER_ID",
        ),
        description=(
            "Google Drive folder ID where newly created Writing Studio docs are moved after "
            "creation. Empty string = leave docs in My Drive root (default behavior). "
            "Requires drive.file scope; if the move is rejected the doc is still returned "
            "at the root (graceful fallback)."
        ),
    )

    embedding_provider: str = Field(
        default="minilm",
        description="Embedding backend. Only 'minilm' supported in V1.",
    )

    enablement_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_ENABLEMENT_WEBHOOK_SECRET",
            "ENABLEMENT_WEBHOOK_SECRET",
        ),
        description=(
            "Shared secret the enablement indexing Apps Script must send in the "
            "X-Enablement-Token header to POST /api/enablement/ingest. Empty = the "
            "ingest endpoint is disabled (fail-closed). This is the app-layer auth; "
            "Cloudflare Access in front of the app must separately allow the Apps "
            "Script through via a service token (see the Apps Script deploy runbook)."
        ),
    )

    # M1: lossless memory — archive + backup paths and parameters
    archive_dir: Path = Field(
        default=Path.home() / ".artemis" / "archive",
        description="Root directory for cold-tier raw_inputs archives.",
    )
    memory_eval_dir: Path = Field(
        default=Path.home() / ".artemis" / "memory-eval",
        description="Directory for persisted retrieval-eval QA sets and reports.",
    )
    backup_dir: Path = Field(
        default=Path.home() / ".artemis" / "backups",
        description="Directory for nightly pg_dump backups.",
    )
    backup_pg_host: str = Field(default="localhost", description="Postgres host for pg_dump.")
    backup_pg_port: int = Field(default=5432, description="Postgres port for pg_dump.")
    backup_pg_user: str = Field(default="artemis", description="Postgres user for pg_dump.")
    backup_pg_dbname: str = Field(default="artemis_os", description="Database name to back up.")
    backup_pg_bindir: str = Field(
        default="/opt/homebrew/opt/postgresql@17/bin",
        description="Directory containing pg_dump, pg_restore, createdb binaries.",
    )
    backup_retain_days: int = Field(default=30, description="Days to retain pg_dump files.")
    archive_age_days: int = Field(
        default=90, description="Archive raw_inputs rows older than this many days."
    )
    # NOTE: use day NAMES (mon-fri/fri/mon), NOT numbers, for the day-of-week field.
    # APScheduler's CronTrigger.from_crontab reads numeric day-of-week as 0=Mon..6=Sun,
    # so "1-5" fired Tue-Sat (ran Saturday, skipped Monday) — not Mon-Fri. Day names map
    # unambiguously. (Bug fixed 2026-06-20.)
    morning_brief_cron: str = Field(
        default="0 8 * * mon-fri",
        description="Cron expression for the scheduled Slack morning brief (default: weekdays Mon-Fri at 08:00).",
    )
    morning_brief_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the scheduled Slack morning brief.",
    )
    okr_checkin_cron: str = Field(
        default="0 16 * * fri",
        description="Cron expression for the Friday 4pm OKR check-in (default: Fri 16:00).",
    )
    review_escalation_cron: str = Field(
        default="0 17 * * *",
        description=(
            "Cron expression for the daily stale-review escalation sweep "
            "(default: every day at 17:00)."
        ),
    )
    review_escalation_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the stale-review escalation sweep.",
    )
    review_escalation_age_hours: int = Field(
        default=24,
        description="Minimum age in hours before a ready-for-review draft is escalated.",
    )
    commitments_followup_cron: str = Field(
        default="30 9 * * mon-fri",
        description="Cron expression for the commitments follow-up sweep (default: weekdays Mon-Fri at 09:30).",
    )
    commitments_followup_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the commitments follow-up sweep.",
    )
    commitments_due_soon_hours: int = Field(
        default=48,
        description="How far ahead the follow-up sweep treats commitments as due soon.",
    )
    commitments_renotify_hours: int = Field(
        default=24,
        description="Minimum hours between repeat follow-ups for the same commitment.",
    )
    commitments_default_snooze_hours: int = Field(
        default=48,
        description="Default snooze duration when a follow-up reply omits an explicit window.",
    )
    commitments_proposals_digest_cron: str = Field(
        default="0 9 * * mon-fri",
        description=(
            "Cron expression for the daily proposals digest sweep "
            "(default: weekdays Mon-Fri at 09:00). Fires only when proposed commitments exist."
        ),
    )
    commitments_proposals_digest_tz: str = Field(
        default="America/New_York",
        description="IANA timezone for the commitment proposals digest sweep.",
    )
    hub_escalation_cron: str = Field(
        default="0 * * * *",
        description=(
            "Cron expression for the hub agent-escalation sweep "
            "(default: every hour). Finds pending asks unresolved after ~1 day "
            "and has Artemis post a terminal comment + DM Jon."
        ),
    )
    pre_meeting_prep_cron: str = Field(
        default="*/30 7-19 * * mon-fri",
        description=(
            "Cron expression for the pre-meeting prep sweep "
            "(default: every 30 min, weekdays 7am-7pm). Sends a prep DM for "
            "any calendar event starting within the next 30 min."
        ),
    )
    commitment_urgency_nudge_cron: str = Field(
        default="0 */2 * * mon-fri",
        description=(
            "Cron expression for the commitment urgency-nudge sweep "
            "(default: every 2 hours on weekdays). Fires an interrupt-bar DM "
            "for commitments due within the next 12 hours that haven't been nudged."
        ),
    )
    post_meeting_scheduling_cron: str = Field(
        default="*/20 8-18 * * mon-fri",
        description=(
            "Cron expression for the post-meeting scheduling sweep "
            "(default: every 20 min, weekdays 8am-6pm). Scans recent meeting "
            "action items, detects scheduling requests, and PROPOSES candidate "
            "times to Jon via DM. Never auto-creates events — creation goes "
            "through the agency gate on Jon's confirmation."
        ),
    )
    commitment_urgency_hours: int = Field(
        default=12,
        description=(
            "Commitments due within this many hours trigger an urgency nudge DM "
            "(above the daily digest cadence)."
        ),
    )
    directory_sync_cron: str = Field(
        default="0 6 * * mon",
        description=(
            "Cron expression for the weekly name→email directory sync from Slack "
            "(default: Monday at 06:00). Refreshes the directory_people roster cache."
        ),
    )
    uvicorn_workers: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "ARTEMIS_UVICORN_WORKERS",
            "UVICORN_WORKERS",
            "WEB_CONCURRENCY",
        ),
        description=(
            "Configured uvicorn worker count. Writing Studio collab is single-process in v1 and "
            "logs a startup warning if this is >1."
        ),
    )
    lm_studio_base_url: str = Field(
        default="http://127.0.0.1:1234",
        validation_alias=AliasChoices("ARTEMIS_LM_STUDIO_BASE_URL", "LM_STUDIO_BASE_URL"),
        description=(
            "Base URL for the OpenAI-compatible local LLM server (LM Studio/Ollama). "
            "Point at the Mac Studio's Tailscale IP/hostname to use it as the LLM box. Use an "
            "explicit address, never 'localhost' (localhost resolves to ::1 first → IPv6 hang)."
        ),
    )

    # ── Callie proactive top-tier signal push (event-driven, not scheduler) ────
    callie_proactive_min_score: float = Field(
        default=0.7,
        validation_alias=AliasChoices(
            "ARTEMIS_CALLIE_PROACTIVE_MIN_SCORE",
            "CALLIE_PROACTIVE_MIN_SCORE",
        ),
        description=(
            "Minimum qualification fit score (0–1) before Callie posts a top-tier signal "
            "to the marketing channel unprompted. Default 0.7. Jon can lower to 0.5 for "
            "more signal or raise to 0.85 to stay quiet until only the clearest wins."
        ),
    )
    callie_proactive_daily_cap: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "ARTEMIS_CALLIE_PROACTIVE_DAILY_CAP",
            "CALLIE_PROACTIVE_DAILY_CAP",
        ),
        description=(
            "Maximum number of proactive Callie top-tier signal pushes per UTC calendar day. "
            "Default 3. Set to 0 to disable all proactive pushes. Raise to let more through "
            "on heavy intake days."
        ),
    )
    callie_proactive_channel: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_CALLIE_PROACTIVE_CHANNEL",
            "CALLIE_PROACTIVE_CHANNEL",
        ),
        description=(
            "Slack channel ID for Callie's proactive top-tier signal posts. "
            "Empty = feature OFF (no proactive pushes). There is intentionally NO "
            "fallback to marketing_campaigns_slack_channel — signal alerts must never "
            "leak into the approval-gate channel. Set a dedicated signals channel to enable."
        ),
    )

    # Multi-account Claude Code support.
    # Maps an account name (e.g. "marketing", "personal") to a CLAUDE_CONFIG_DIR path.
    # Parsed from ARTEMIS_CLAUDE_ACCOUNT_CONFIG_DIRS as a JSON object.
    # Empty default means no per-account isolation (single ambient login).
    claude_account_config_dirs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps a Claude account name to the CLAUDE_CONFIG_DIR path for that account. "
            'Example JSON: {"marketing": "/Users/artemis/.claude-marketing", '
            '"personal": "/Users/artemis/.claude-personal"}. '
            "Set via ARTEMIS_CLAUDE_ACCOUNT_CONFIG_DIRS env var. Empty = single ambient login."
        ),
    )
    # Maps agent_id (exact) or agent_id prefix (e.g. "marketing.") to an account name.
    # Parsed from ARTEMIS_CLAUDE_AGENT_ACCOUNTS as a JSON object.
    # Exact match wins over prefix match; longest prefix wins among prefix matches.
    # Empty default means all agents use the ambient login (unchanged behavior).
    claude_agent_accounts: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps agent_id or agent_id prefix to an account name in claude_account_config_dirs. "
            'Example JSON: {"marketing.": "marketing", "personal": "personal"}. '
            "Exact match wins; longest prefix wins otherwise. "
            "Set via ARTEMIS_CLAUDE_AGENT_ACCOUNTS env var. Empty = all agents use ambient login."
        ),
    )

    # ------------------------------------------------------------------ #
    # Screen-Time Watch — national policy intelligence pipeline (Brief 1) #
    # ------------------------------------------------------------------ #
    # Additive, isolated namespace. Nothing here touches the marketing
    # campaign pipeline. The stance rules live in screentime_stance_config
    # (DB) with this settings blob as the code-side fallback default, so
    # Angela can re-tune favorable/unfavorable without a deploy.
    screentime_cron: str = Field(
        default="0 7 * * *",
        description=(
            "Cron expression for the Screen-Time Watch national sweep (default daily "
            "07:00). Used by the dedicated runner + reflected on the seeded pipeline row."
        ),
    )
    screentime_cron_tz: str = Field(
        default="America/Chicago",
        description="Timezone for screentime_cron.",
    )
    screentime_window_days: int = Field(
        default=30,
        description=(
            "Rolling discovery window in days — signals published older than this are "
            "dropped during a sweep (the 'kept current ~30 days' requirement)."
        ),
    )
    screentime_retention_days: int = Field(
        default=60,
        description=(
            "Retention window in days for stored screentime_signals. The runner / purge "
            "helper auto-expires signals whose discovered_at is older than this. 0 = keep forever."
        ),
    )
    screentime_states: str = Field(
        default="",
        description=(
            "Comma-separated 2-letter state codes to sweep. EMPTY = all 50 states + DC "
            "(national, the intended default). Decoupled from the campaign's target states. "
            "Set only to scope a manual/test run."
        ),
    )
    screentime_stance_rules: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Code-side fallback for the tunable stance rules. EMPTY = use the v1 default "
            "baked into artemis/screentime/stance_config.py. The DB row "
            "screentime_stance_config(name='default') overrides this when present. "
            "Set via ARTEMIS_SCREENTIME_STANCE_RULES (JSON) only to override without a DB row."
        ),
    )
    screentime_topic_rules: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Code-side fallback for the tunable TOPIC-relevance gate (require/exclude "
            "terms + LLM tie-break toggle). EMPTY = use the default baked into "
            "artemis/screentime/topic_config.py. The DB row "
            "screentime_stance_config(name='topic') overrides this when present. "
            "Set via ARTEMIS_SCREENTIME_TOPIC_RULES (JSON) to override without a deploy. "
            "This gate runs BEFORE store/classify and drops generic ed-policy noise "
            "(literacy, reading retention, curriculum approvals, test scores) so only "
            "genuine instructional/student screen-time items survive."
        ),
    )
    screentime_topic_llm_tiebreak: bool = Field(
        default=False,
        description=(
            "When True, keyword-ambiguous items (pass require-terms but also hit an "
            "exclude-term, i.e. mixed signal) get a cheap tool-less LLM relevance check "
            "(codex→claude-code) instead of being dropped on keywords alone. OFF by "
            "default to keep the gate deterministic + free; the deterministic gate is "
            "always the fallback."
        ),
    )

    # ── Screen-Time Watch — Callie reporting to #policy-watch (Brief 2) ─────────
    # Additive, dormant-until-set (same pattern as callie_proactive_channel).
    # A NEW consumer of the screentime_signals table — does NOT change Callie's
    # campaign push behavior. Empty channel = feature OFF (no posts at all).
    screentime_report_channel: str = Field(
        default="",
        validation_alias=AliasChoices(
            "ARTEMIS_SCREENTIME_REPORT_CHANNEL",
            "SCREENTIME_REPORT_CHANNEL",
        ),
        description=(
            "Slack channel ID for Callie's Screen-Time Watch reports (#policy-watch). "
            "Empty = feature OFF — no digest, no big-move alerts. Dedicated channel, "
            "separate from callie_proactive_channel (campaign signals) and the "
            "approval-gate channel. Set to enable both the weekly digest and immediate "
            "big-move alerts."
        ),
    )
    screentime_bigmove_states: str = Field(
        default="CA,TX,FL,NY,PA,IL,OH,GA,NC,MI",
        validation_alias=AliasChoices(
            "ARTEMIS_SCREENTIME_BIGMOVE_STATES",
            "SCREENTIME_BIGMOVE_STATES",
        ),
        description=(
            "Comma-separated 2-letter codes for the 'large' states whose moves count "
            "as big. A passed UNFAVORABLE move (blanket restriction) in one of these "
            "states fires an immediate alert. Empty = no state is treated as large "
            "(so the large-state rule never fires)."
        ),
    )
    screentime_bigmove_statuses: str = Field(
        default="passed,amended",
        validation_alias=AliasChoices(
            "ARTEMIS_SCREENTIME_BIGMOVE_STATUSES",
            "SCREENTIME_BIGMOVE_STATUSES",
        ),
        description=(
            "Comma-separated signal statuses that count as a 'real move that landed' "
            "for the big-move alert (vs. merely proposed). Default 'passed,amended'."
        ),
    )
    screentime_bigmove_favorable_alert: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ARTEMIS_SCREENTIME_BIGMOVE_FAVORABLE_ALERT",
            "SCREENTIME_BIGMOVE_FAVORABLE_ALERT",
        ),
        description=(
            "When True, a passed FAVORABLE move with an evidence-based carve-out fires "
            "an immediate alert in ANY state (a new carve-out is strategically useful "
            "everywhere). Set False to alert only on large-state unfavorable moves."
        ),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


class ProductionAuthConfigError(RuntimeError):
    """Raised at startup when a production deploy would boot unauthenticated.

    ``cf_access_enabled`` defaults to ``False``; if a production deploy forgets
    to set it, ``resolve_request_identity`` silently falls back to the dev-shim
    identity and every endpoint runs UNAUTHENTICATED. This error makes that
    misconfiguration a refusal-to-boot instead of a silent fail-open.
    """


def assert_production_auth_config(cfg: Settings | None = None) -> None:
    """Refuse to boot a production deploy whose identity layer would fall open.

    No-op unless ``env == "production"`` — development and test boots are
    intentionally unaffected (the dev shim is the designed behavior there).
    Called from the app lifespan before anything else starts.
    """
    active = cfg if cfg is not None else settings
    if active.env != "production":
        return
    problems: list[str] = []
    if not active.cf_access_enabled:
        problems.append("ARTEMIS_CF_ACCESS_ENABLED must be true")
    if not active.cf_access_team_domain.strip():
        problems.append("ARTEMIS_CF_ACCESS_TEAM_DOMAIN is not set")
    if not active.cf_access_aud.strip():
        problems.append("ARTEMIS_CF_ACCESS_AUD is not set")
    if problems:
        raise ProductionAuthConfigError(
            "Refusing to start: ARTEMIS_ENV=production but Cloudflare Access identity "
            "is not fully configured — the app would serve every request with the "
            "unauthenticated dev-shim identity. Fix: " + "; ".join(problems)
        )
