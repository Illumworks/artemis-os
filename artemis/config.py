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

    enablement_library_channel_id: str = Field(
        default="C0BB17EJLKC",
        validation_alias=AliasChoices(
            "ARTEMIS_ENABLEMENT_LIBRARY_CHANNEL_ID",
            "ENABLEMENT_LIBRARY_CHANNEL_ID",
        ),
        description=(
            "Slack channel ID for #enablement-library, where Kai answers and where "
            "flag_catalog_gap posts. Empty = the flag tool is disabled (fail-closed)."
        ),
    )
    kai_action_authorized_user_ids: str = Field(
        default="U09F3EPJXSQ,U07CHT0S7UK",
        validation_alias=AliasChoices(
            "ARTEMIS_KAI_ACTION_AUTHORIZED_USER_IDS",
            "KAI_ACTION_AUTHORIZED_USER_IDS",
        ),
        description=(
            "Comma-separated Slack user IDs permitted to trigger Kai's ONE "
            "side-effecting tool (flag_catalog_gap). Owner decision 2026-08-10: "
            "Jon Fila (U09F3EPJXSQ) and Missy Dahlberg (U07CHT0S7UK) only; everyone "
            "else is information-only. Resolved server-side from the Slack event's "
            "user id, never from message text. Empty = nobody is authorized "
            "(fail-closed)."
        ),
    )
    kai_catalog_owner_user_ids: str = Field(
        default="U07926XP0FR,U07CHT0S7UK",
        validation_alias=AliasChoices(
            "ARTEMIS_KAI_CATALOG_OWNER_USER_IDS",
            "KAI_CATALOG_OWNER_USER_IDS",
        ),
        description=(
            "Comma-separated Slack user IDs @-mentioned on a flag_catalog_gap post: "
            "Sara Erickson (U07926XP0FR) and Missy Dahlberg (U07CHT0S7UK), who own "
            "the enablement catalog. Empty = the gap posts without tagging anyone."
        ),
    )
    callie_dm_requester_emails: str = Field(
        default=(
            "jon.fila@amiralearning.com,"
            "angela.miata@amiralearning.com,"
            "joshua.mukai@amiralearning.com"
        ),
        validation_alias=AliasChoices(
            "ARTEMIS_CALLIE_DM_REQUESTER_EMAILS",
            "CALLIE_DM_REQUESTER_EMAILS",
        ),
        description=(
            "Comma-separated emails permitted to ask Callie's send_guarded_dm (CALLIE-1) "
            "to message someone on their behalf. Owner decision, Jon, 2026-08-12: Jon, "
            "Angela, and Josh only. This is the important half of the guard -- the risk "
            "named was proxying ('Callie, DM Sara and tell her X'), which is a WHO-MAY-ASK "
            "problem, not a who-may-receive one. Resolved server-side from the verified "
            "Slack event's user id via users.info (SlackClient.lookup_user_email), NEVER "
            "from anything in the message text or tool input. Empty = nobody is authorized "
            "(fail-closed), matching kai_action_authorized_user_ids.\n\n"
            "All five addresses across this setting and callie_dm_recipient_emails were "
            "verified against Slack's own records via users.lookupByEmail on 2026-08-12, "
            "not inferred from the naming convention. Josh is joshUA.mukai@ (U07NYLNJY79); "
            "josh.mukai@ resolves to nothing and would have failed him closed. Getting this "
            "exactly right matters more than usual here because the workspace contains a "
            "SECOND Josh -- Josh Smith, josh.smith@amiralearning.com -- who is the person "
            "Callie wrongly fuzzy-matched to when asked about Josh Mukai (2026-08-12). "
            "Authorization requires an exact match against this list, never a fuzzy one, so "
            "a wrong address fails its owner closed and can never grant access to the wrong "
            "person; re-verify with users.lookupByEmail rather than guessing when editing."
        ),
    )
    callie_dm_recipient_emails: str = Field(
        default=(
            "jon.fila@amiralearning.com,"
            "angela.miata@amiralearning.com,"
            "joshua.mukai@amiralearning.com,"
            "hannah.slater@amiralearning.com,"
            "jaclyn.wright@amiralearning.com"
        ),
        validation_alias=AliasChoices(
            "ARTEMIS_CALLIE_DM_RECIPIENT_EMAILS",
            "CALLIE_DM_RECIPIENT_EMAILS",
        ),
        description=(
            "Comma-separated emails Callie's send_guarded_dm (CALLIE-1) may deliver to: "
            "Jon, Angela, Josh, Hannah, and Jaclyn. Checked INDEPENDENTLY of "
            "callie_dm_requester_emails -- an authorized requester naming an unlisted "
            "recipient is still refused. Resolved to a Slack user id at send time via "
            "users.lookupByEmail (see artemis.floating_artemis.tools.callie_dm), never via "
            "directory_people -- that cache had NULL slack_user_id for every real approver "
            "on the adjacent crisis-content pipeline this week and silently took down every "
            "approval; this tool skips it entirely rather than repeat that failure. Empty = "
            "nobody can receive (fail-closed). Every address here was verified against Slack "
            "on 2026-08-12 -- see callie_dm_requester_emails above, including why the second "
            "Josh in the workspace makes exact matching load-bearing."
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
        default="0 11 * * *",
        description=(
            "Cron expression for the Screen-Time Watch national sweep (default daily "
            "11:00 UTC — collection only, no auto-digest/alerting). Used by the "
            "dedicated runner (register_screentime_schedule) + reflected on the seeded "
            "display pipeline row. No numeric day-of-week is used here (this repo's "
            "APScheduler cron day-of-week bug: 0=Mon, not Sun) — a bare '* * *' day/month/dow "
            "with only hour+minute set sidesteps it entirely."
        ),
    )
    screentime_cron_tz: str = Field(
        default="UTC",
        description="Timezone for screentime_cron.",
    )
    screentime_board_sweep_cron: str = Field(
        default="0 12 * * sun",
        description=(
            "Cron expression for the SEPARATE, weekly board-peer-validation sweep "
            "(BoardDocs + an LLM call per district — too slow for the daily fast "
            "sweep, decoupled 2026-07-11). Registered by "
            "``artemis.screentime.runner.register_board_sweep_schedule``, its own "
            "job id, silent (no Slack). Uses the day-of-week NAME 'sun' rather than "
            "a numeric field — this repo's APScheduler cron day-of-week gotcha "
            "(numeric 0=Mon, not Sun) is sidestepped by never using a numeric dow. "
            "Uses screentime_cron_tz for its timezone."
        ),
    )
    screentime_digest_cron: str = Field(
        default="0 13 * * mon-fri",
        description=(
            "Cron for the DAILY situational read posted by Callie to the market-"
            "signals channel. Runs two hours after the 11:00 UTC collection sweep "
            "so the digest reports the morning's findings rather than yesterday's. "
            "Weekdays only, and day-of-week is given by NAME — never numeric — per "
            "this repo's APScheduler gotcha (numeric 0=Mon, not Sun), which once "
            "made the morning brief fire Tue-Sat. Registered by "
            "``artemis.screentime.runner.register_digest_schedule``. Dormant unless "
            "``screentime_report_channel`` is set."
        ),
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

    crisis_content_poll_interval_minutes: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_POLL_INTERVAL_MINUTES",
            "CRISIS_CONTENT_POLL_INTERVAL_MINUTES",
        ),
        description=(
            "Poll cadence, in minutes, for the crisis-comms content-approval doc "
            "poller (CCA4 -- see docs/crisis-content-approval-pipeline.md and "
            "artemis/crisis_content/poller.py)."
        ),
    )
    crisis_content_notify_destination: Literal["live", "dm_jon"] = Field(
        default="live",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_NOTIFY_DESTINATION",
            "CRISIS_CONTENT_NOTIFY_DESTINATION",
        ),
        description=(
            "Routing for Callie's crisis-content review cards (CCA6 -- see "
            "docs/crisis-content-approval-pipeline.md 'Routing' and "
            "artemis/crisis_content/notify.py).\n\n"
            "'live' (default, Jon has asked for full functionality): the real "
            "routing table. 'asset' -> 'Ready' DMs Jon (he is the only asset "
            "approver). 'copy' -> 'Ready' posts to "
            "crisis_content_copy_notify_channel, @-mentioning the copy "
            "approvers (crisis_content_copy_approver_emails minus Jon's "
            "authorization-backstop entry -- see that field's docstring; the "
            "card is still addressed to Angela/Hannah/Jaclyn only). No "
            "'Testing' footer on either route under this value.\n\n"
            "'dm_jon': ROLLBACK OVERRIDE. Sends EVERYTHING -- both routes -- to "
            "Jon as a DM, exactly like the pre-CCA6 behavior, and restores the "
            "'Testing -- routed to you only' footer so the rollback path stays "
            "honest about what it is. Flip to this value -- no deploy required, "
            "just an env var / restart -- if the channel routing misbehaves, "
            "e.g. at 9pm on a Friday, to instantly restore known-good behavior. "
            "Any value other than these two literals fails Settings validation "
            "at startup rather than silently doing something unintended."
        ),
    )
    crisis_content_copy_notify_channel: str = Field(
        default="C0BM9TL63TL",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_COPY_NOTIFY_CHANNEL",
            "CRISIS_CONTENT_COPY_NOTIFY_CHANNEL",
        ),
        description=(
            "Slack channel ID where copy-route 'Ready' cards post under live "
            "routing (CCA6 -- see artemis/crisis_content/notify.py). Callie's "
            "bot user must already be a member, or chat.postMessage fails with "
            "'not_in_channel' and the notification retries every poll tick "
            "until she is invited."
        ),
    )
    crisis_content_asset_approver_emails: str = Field(
        default="jon.fila@amiralearning.com",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_ASSET_APPROVER_EMAILS",
            "CRISIS_CONTENT_ASSET_APPROVER_EMAILS",
        ),
        description=(
            "Comma-separated emails permitted to decide the 'asset' route on a "
            "crisis-content card (CCA5 -- see "
            "docs/crisis-content-approval-pipeline.md 'Routing' and "
            "artemis/crisis_content/authorization.py). Jon only, by design: he "
            "approves visuals; the copy approvers below are deliberately "
            "rejected on this route. A pipeline-scoped allowlist -- does not "
            "touch or widen artemis.enablement.actions' Jon-and-Missy "
            "authorization helper. Empty = nobody is authorized (fail-closed)."
        ),
    )
    crisis_content_copy_approver_emails: str = Field(
        default=(
            "angela.miata@amiralearning.com,"
            "hannah.slater@amiralearning.com,"
            "jaclyn.wright@amiralearning.com,"
            "jon.fila@amiralearning.com"
        ),
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_COPY_APPROVER_EMAILS",
            "CRISIS_CONTENT_COPY_APPROVER_EMAILS",
        ),
        description=(
            "Comma-separated emails permitted to decide the 'copy' route on a "
            "crisis-content card (CCA5). Angela, Hannah, and Jaclyn -- any ONE "
            "is sufficient (quorum-of-one). Empty = nobody is authorized "
            "(fail-closed).\n\n"
            "Jon is on this list as a deliberate REDUNDANCY, added 2026-08-11 at "
            "his request: during a crisis push, copy must not sit unapproved "
            "because all three primary approvers happen to be unavailable. He is "
            "a backstop, not a routine approver -- docs/crisis-content-approval-"
            "pipeline.md 'Routing' still assigns copy to the three of them, and "
            "cards are addressed to them. Note this is the only overlap between "
            "the two routes; the asset route remains Jon-only."
        ),
    )
    crisis_content_writeback_jen_emails: str = Field(
        default="jen@justrightstrategy.com,jen@digigeeks.com",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_WRITEBACK_JEN_EMAILS",
            "CRISIS_CONTENT_WRITEBACK_JEN_EMAILS",
        ),
        description=(
            "Comma-separated emails to @mention (Drive comment) and email "
            "(Gmail backup) on the crisis-content write-back (CCA7 -- see "
            "docs/crisis-content-approval-pipeline.md and "
            "artemis/crisis_content/writeback.py). Jen has two addresses on "
            "the doc -- justrightstrategy.com (the owner) and digigeeks.com (a "
            "writer) -- and it is not obvious which she watches, so both are "
            "notified by default. A settings value rather than an inline "
            "literal so the list is a config change, not a code edit, matching "
            "crisis_content_copy_approver_emails above."
        ),
    )
    crisis_content_writeback_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_WRITEBACK_ENABLED",
            "CRISIS_CONTENT_WRITEBACK_ENABLED",
        ),
        description=(
            "Kill switch for the CCA7 write-back that runs after a crisis-content "
            "decision lands: the Google Doc line insert, the Drive @mention "
            "comment, and the Gmail backup. Decisions still record normally in "
            "crisis_content_decisions either way; this only gates the three "
            "notification side effects, never the decision itself.\n\n"
            "**Enabled 2026-08-11 on Jon's explicit authorisation.** It shipped "
            "defaulting False for one evening because turning it on means writing "
            "into a document owned by an external vendor while she is editing it, "
            "and emailing her -- an owner decision, not something to infer from "
            "'ship it'. Jon then asked for it on, describing it as the fallback "
            "path so Jen hears a decision even if she misses the doc.\n\n"
            "Set False to stop all three side effects immediately with no deploy. "
            "That is the emergency-off path if a write ever looks wrong against "
            "her live document -- reach for it before debugging. See the safety "
            "section of briefs/cca7-writeback-and-notify-jen.md ('treat every "
            "write as the riskiest line of code in this repo')."
        ),
    )
    crisis_content_image_link_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_IMAGE_LINK_ENABLED",
            "CRISIS_CONTENT_IMAGE_LINK_ENABLED",
        ),
        description=(
            "Kill switch for CCA10 -- linking a thread-attached image into "
            "Jen's doc as a text line (see docs/crisis-content-approval-"
            "pipeline.md and artemis/crisis_content/image_link.py). Thread "
            "notes still record ``has_attachment``/``file_count`` normally "
            "either way; this only gates the doc write + Slack confirmation "
            "reply.\n\n"
            "**Enabled on merge (2026-08-12).** The worker shipped it False, "
            "mirroring how crisis_content_writeback_enabled (CCA7) originally "
            "shipped, on the reasoning that writing into an externally-owned "
            "document is an owner decision. Correct instinct, but the "
            "authorisation already exists here and CCA7's did not: Jon asked "
            "for this feature in as many words -- 'if someone uploads the "
            "images to slack they get pushed to the doc' -- and separately "
            "switched the write-back on. Leaving it off would have withheld "
            "the thing he asked for.\n\n"
            "Set False at any time to stop the doc write immediately with "
            "no deploy."
        ),
    )
    crisis_content_jen_slack_user_id: str = Field(
        default="U016P00LP08",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_JEN_SLACK_USER_ID",
            "CRISIS_CONTENT_JEN_SLACK_USER_ID",
        ),
        description=(
            "Jen's Slack user id, for the real ``<@…>`` mention CCA9's "
            "change-request notification will send (CCA8 -- see "
            "artemis/crisis_content/notify.py's ``jen_mention()``). "
            "Ready-for-review cards never use this; they say the plain word "
            "'Jen' instead (she is pinged only when she must genuinely act).\n\n"
            "**CONFIGURED, NOT RESOLVED -- do not 'fix' this into an email "
            "lookup.** Verified live 2026-08: ``users.lookupByEmail`` returns "
            "nothing for either of Jen's addresses "
            "(jen@digigeeks.com, jen@justrightstrategy.com -- see "
            "crisis_content_writeback_jen_emails above). She is an external "
            "Slack Connect user on a different team (``TUQ6KJT0V``), and "
            "email lookup only sees users in our own workspace, so it will "
            "silently return None forever no matter how many times it's "
            "retried. ``users.info`` on this id independently confirms it is "
            "jen@digigeeks.com on that external team. The default is that "
            "confirmed id.\n\n"
            "Empty string = no known id; ``jen_mention()`` falls back to the "
            "plain word 'Jen' rather than emitting a broken ``<@>`` mention."
        ),
    )
    crisis_content_test_tab_marker: str = Field(
        default="TESTING",
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_TEST_TAB_MARKER",
            "CRISIS_CONTENT_TEST_TAB_MARKER",
        ),
        description=(
            "Case-insensitive substring that marks a Google Docs tab as Jon's test "
            "lane (CCA13 -- see docs/crisis-content-approval-pipeline.md and "
            "artemis/crisis_content/tab_resolution.py). A card whose live table "
            "lives on a tab whose title contains this marker (e.g. 'Content To "
            "Review (TESTING)') sets Transition.is_test, which "
            "artemis/crisis_content/notify.py already honors: DM to Jon only, the "
            "'Testing' footer restored, regardless of the destination setting "
            "below.\n\n"
            "The Jen write-back (Drive @mention + Gmail, artemis/crisis_content/"
            "writeback.py) and the Writing Studio harvest (later slices) are "
            "SUPPOSED to also honor is_test but do not yet -- both live entirely "
            "in artemis/crisis_content/slack_actions.py and writeback.py, which "
            "were out of file scope for CCA13 (the brief that introduced this "
            "setting). See that brief's report for why: is_test is not persisted "
            "anywhere a decision-time click handler can read it. Wiring the "
            "suppression is the very next piece of work here, not done.\n\n"
            "A tab whose title does NOT contain this marker is a real tab by "
            "default, with no per-tab configuration needed -- a new monthly tab "
            "just works. Empty string would match every tab (matching an empty "
            "substring is always True), which would silently make the whole doc a "
            "test lane; Settings does not block that value, so do not set it "
            "empty."
        ),
    )
    crisis_content_rule_mining_threshold: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_RULE_MINING_THRESHOLD",
            "CRISIS_CONTENT_RULE_MINING_THRESHOLD",
        ),
        description=(
            "How many times a normalized (deleted, inserted) suggestion pair must "
            "recur across Angela/Hannah's Google Docs suggesting-mode edits before "
            "artemis/crisis_content/rule_mining.py (CCA15) proposes it as a "
            "candidate writing_rules row. Default 3, per the brief's own worked "
            "example ('child' -> 'student', seen three times). Below threshold the "
            "pair is counted (crisis_content_rule_mining_pairs) and nothing is "
            "proposed -- a single edit is a judgment about one sentence, not a "
            "rule. Never write below 2: at 1, every one-off suggestion would "
            "become a candidate, which is exactly the noise this slice exists to "
            "keep out of Angela's review queue."
        ),
    )
    crisis_content_rule_mining_interval_minutes: int = Field(
        default=60,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_RULE_MINING_INTERVAL_MINUTES",
            "CRISIS_CONTENT_RULE_MINING_INTERVAL_MINUTES",
        ),
        description=(
            "Minimum minutes between rule-mining passes. Mining needs its own "
            "documents.get (it reads suggestions, so it cannot share tab "
            "resolution's PREVIEW_WITHOUT_SUGGESTIONS fetch), and the poll tick "
            "runs every 2 minutes -- mining every tick would tripleize our API "
            "calls against Jen's doc to watch for editorial edits that arrive a "
            "few times a day. 0 disables mining entirely; the notification path "
            "is unaffected either way."
        ),
    )
    crisis_content_rule_mining_max_words: int = Field(
        default=6,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_RULE_MINING_MAX_WORDS",
            "CRISIS_CONTENT_RULE_MINING_MAX_WORDS",
        ),
        description=(
            "Longest either side of a mined (deleted, inserted) pair may be, in "
            "whitespace-separated words, before artemis/crisis_content/"
            "rule_mining.py (CCA16) refuses to propose it as a "
            "writing_training_candidates row -- even after it reaches "
            "crisis_content_rule_mining_threshold. The pair keeps counting "
            "occurrences past the guard (crisis_content_rule_mining_pairs); only "
            "the proposal step is withheld, forever, for as long as it stays "
            "over the guard. Default 6: CCA16 coalesces run-level fragments "
            "into one span-level pair per rewritten sentence (see that slice's "
            "brief), so a genuine house rule -- 'child' -> 'student' -- stays "
            "short and clears this guard easily, while a whole rewritten "
            "sentence recurring under it is boilerplate that belongs in front "
            "of a human, not auto-surfaced as if it were a style rule."
        ),
    )
    crisis_content_harvest_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ARTEMIS_CRISIS_CONTENT_HARVEST_ENABLED",
            "CRISIS_CONTENT_HARVEST_ENABLED",
        ),
        description=(
            "Kill switch for CCA14 -- harvesting approved crisis-content copy into "
            "Writing Studio's writing_examples table, under a dedicated 'Amira "
            "Social' profile (see docs/crisis-content-approval-pipeline.md 'Slice "
            "D' and artemis/crisis_content/harvest.py). Decisions still record "
            "normally either way; this only gates the harvest INSERT that follows "
            "an 'approved' decision.\n\n"
            "Defaults True (unlike crisis_content_writeback_enabled's original "
            "False default): this write never leaves the app -- no external "
            "vendor doc, no email, no Slack post -- it is an internal DB insert "
            "into our own Writing Studio corpus, so the 'writing into someone "
            "else's live document is an owner decision' reasoning that justified "
            "writeback's cautious default does not apply here. Test cards "
            "(CrisisContentCard.is_test) are never harvested regardless of this "
            "setting.\n\n"
            "Set False to stop the harvest immediately with no deploy, e.g. if a "
            "bad channel mapping is writing bad rows into the corpus."
        ),
    )

    argus_claim_poll_interval_seconds: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "ARTEMIS_ARGUS_CLAIM_POLL_INTERVAL_SECONDS",
            "ARGUS_CLAIM_POLL_INTERVAL_SECONDS",
        ),
        description=(
            "Poll cadence, in seconds, for the Argus research-request claimer "
            "(ARGUS-1 -- see artemis/floating_artemis/tools/argus_tools.py "
            "run_claim_tick). This is a DB poll (SELECT ... FOR UPDATE SKIP "
            "LOCKED against argus_research_requests), not an outbound API call, "
            "so a short interval is cheap; 15s keeps the gap between "
            "dispatch_research enqueueing a row and Argus actually starting "
            "small without polling so often it shows up in DB load."
        ),
    )
    argus_claim_stale_minutes: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "ARTEMIS_ARGUS_CLAIM_STALE_MINUTES",
            "ARGUS_CLAIM_STALE_MINUTES",
        ),
        description=(
            "A 'running' argus_research_requests row older than this (by "
            "claimed_at) is presumed orphaned by a crash mid-research and is "
            "re-claimable by the next poll tick. Long enough that a normal "
            "research pass (several fetchers + one LLM synthesis call) is never "
            "mistaken for stale and reclaimed out from under itself -- research "
            "does not hold the row locked while running, so a too-short window "
            "would not cause a live double-run, but it would waste an attempt "
            "off the attempts cap for work that was still genuinely in flight."
        ),
    )
    argus_claim_retry_backoff_minutes: int = Field(
        default=5,
        validation_alias=AliasChoices(
            "ARTEMIS_ARGUS_CLAIM_RETRY_BACKOFF_MINUTES",
            "ARGUS_CLAIM_RETRY_BACKOFF_MINUTES",
        ),
        description=(
            "How long a FAILED argus_research_requests row waits before the "
            "claimer will retry it. A failure releases the row to 'pending' with "
            "claimed_at stamped, so this is measured from the failure.\n\n"
            "Exists because the ARGUS-1 live smoke burned all three attempts in "
            "52 seconds: a claim tick drains until nothing is claimable, and a "
            "just-failed row was immediately claimable again. Retries are for "
            "outlasting transient conditions, and three inside a minute outlast "
            "nothing -- one Slack blip would permanently fail a district. Only "
            "retries wait: a never-attempted row (attempts == 0) is always "
            "claimable at once, so new work never queues behind someone else's "
            "backoff. 0 restores the old immediate-retry behaviour."
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
