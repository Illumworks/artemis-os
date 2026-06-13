# Worker Brief — P2b + P2c: Commitments engine (closes P2)

**Owner:** Codex (backend). **Lead:** Artemis (Opus) verifies live + merges.
**Isolation (AGENTS.md rule 6):** isolated worktree, branch `worker/p2-commitments-engine`; **commit your work
on the branch before reporting**, then do-NOT-merge-report.
**Status:** READY. **Full design:** `docs/p2-proactivity-build-plan.md` (P2b/P2c sections — the decisions below
come from it). The stale-review escalation (already shipped) is the **narrow proof of the P2c pattern**;
this generalizes it to *all* commitments.

## Goal
Agents follow up on **open loops unprompted.** Extract commitments (start with meeting action-items), store
them with a lifecycle, and have the right agent proactively follow up before they go stale — Artemis for
personal/ops, Callie for marketing.

## Reuse (do NOT rebuild)
- **Extraction source already exists:** `meetings/summary_schemas.py:ActionItem` (text / owner / due) on
  Granola meeting summaries. Ingest these — don't build new extraction.
- **The proactivity scheduler** (`artemis/proactivity/scheduler.py`) — add a job alongside morning-brief /
  okr-checkin / stale-review-escalation. **Do NOT stand up a new scheduler stack.**
- **Per-agent Slack delivery** — Artemis DM + Callie's channel/DM (reuse the `send_callie_*` / FA DM paths +
  per-agent tokens). Named-agent output lint applies to all outbound text.
- The **snooze pattern** from marketing (`snoozed_until` + filter).

## P2b — Commitments store + extraction (meetings first)
1. **New `commitments` table** (alembic migration — see Lead note): `source_type` (e.g. `granola_meeting`),
   `source_id`, `text`, `owner_user_id`, `due` (nullable), `sensitivity`, `status` (`active|snoozed|done`),
   `snoozed_until`, `last_notified_at`, timestamps. Lifecycle (snooze/done) is why it's a dedicated table,
   NOT pure memory.
2. **Mirror to memory** — also write a `category='commitment'` memory observation (scoped appropriately) so
   agents can recall commitments in conversation. Best of both: lifecycle table + recallable memory.
3. **Ingest meeting action-items** as commitments (owner/due/source=granola id) + **owner resolution**
   (map the action-item owner → a `User`). Dedupe by `(source_type, source_id, text)` so re-ingesting a
   meeting doesn't duplicate. (Chat/email extraction is **out of scope** here — meetings first.)

## P2c — Proactive follow-up delivery (the differentiator)
4. **New scheduler job** (extend `proactivity/scheduler.py`, configurable cron/tz like the escalation): find
   commitments that are **due-soon or un-followed-up** and `status='active'` (skip `snoozed`/`done`, and
   skip if `snoozed_until` in the future).
5. **Route by sensitivity/domain:** personal/ops → **Artemis DM**; marketing → **Callie**. Deterministic
   message template (no surprising free-gen; named-agent lint).
6. **Dedupe + snooze:** stamp `last_notified_at` on send; don't re-ping within the window. A lightweight
   confirm/action path to mark **done** or **snooze** (stage to DB per the subscription-path pattern — NOT the
   reactive session-bound `confirmation_store`).

## Constraints
- **Subscription-path:** any LLM helper must be **deterministic or use the subscription adapter** — never
  `AnthropicAdapter` (no API key). Gated follow-up actions **stage to the DB** + apply in the main process.
- Don't break the existing proactivity jobs (morning brief / OKR check-in / stale-review escalation).
- Commit the migration + the lockfile if touched. No hardcoded secrets.

## Ship gate (Lead verifies LIVE — assert the EFFECT; test with owner = Jon so it DMs Jon, not a real person)
- **Extraction:** a meeting with action-items → ingest → rows in `commitments` (owner/due/source) **and**
  mirrored `category='commitment'` memory observations; re-ingest = no duplicates.
- **Follow-up:** an active, due-soon commitment (owner = Jon) → the job DMs **Jon via Artemis**; `last_notified_at`
  stamped → a second run does **not** re-ping (dedupe); a `snoozed`/`done`/future-`snoozed_until` commitment is
  **skipped**.
- **Routing:** a marketing-flagged commitment routes to **Callie**, personal/ops to **Artemis**.

## Lead note (post-merge)
This adds an alembic migration → after merge, Lead runs `uv run alembic upgrade head` on the prod DB before
the endpoint/scheduler smoke (`--reload` does not run migrations).
