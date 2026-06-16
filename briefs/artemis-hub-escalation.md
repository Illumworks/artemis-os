# Brief: Artemis Hub — Escalation / Sole-Interrupt Layer (Phase 1)

## Why
Make Artemis the team's connective tissue + the **sole** agent that can break Jon's notification silence.
Phase 1 = the escalation layer on the EXISTING agents (Kai, Callie): when an agent asks Jon something and he
hasn't responded in ~1 day, **Artemis** steps in — posts a terminal connective comment + notifies Jon directly.

## FIRST: read the authoritative design + reconcile with existing infra
- `docs/artemis-hub-plan.md` — **authoritative**; the rules below come from it.
- `docs/p2-proactivity-build-plan.md` + the **commitments** store/scheduler + the **morning brief** generator +
  `artemis/routes/integrations_slack_events.py` (how Jon's replies arrive). REUSE the existing scheduler /
  brief / commitments infra; do not reinvent.

## The rules (from artemis-hub-plan.md — HARD constraints)
- **Loop-proof:** no agent ever replies to another agent. ONLY Artemis comments *about* another agent's work,
  only on a trigger, and her comment is **terminal** (no agent responds back). Chains end at a human.
- **Sole interrupt authority:** ONLY Artemis may bypass Jon's notification silence. Other agents queue.
- **Wait window ≈ 1 day** before Artemis escalates.
- **Routing:** Artemis → DM Jon directly; everyone else → batched into the morning brief. (Ares = N/A in
  Phase 1, not built.)
- **Interrupt bar** (gates bypass-silence vs wait-for-brief): external deadline / a real person waiting on Jon /
  production breaking / a commitment Jon made about to slip.

## Build (Phase 1)
1. **Pending-ask record:** when an agent posts a message that needs Jon (an `@Jon` question / explicit ask),
   record a pending ask (`agent_id`, `channel_id`, `message_ts`, `summary`, `created_at`, `resolved_at`).
   Simplest reliable detector: agent outbound message that @mentions Jon and/or poses a question to him.
   Reconcile with the commitments store — reuse it if it fits. Mark resolved when Jon replies in that
   thread/channel/DM.
2. **Escalation job (scheduler):** periodic check (~hourly) finds pending asks unresolved after ~1 day. For
   each: Artemis (a) posts a TERMINAL connective comment in-channel ("@Agent, I'll take this — escalating to
   Jon."), and (b) notifies Jon via her DM. The original agent does NOT respond to Artemis.
3. **Sole interrupt path:** centralize a notify-Jon path that ONLY Artemis uses; tag escalations meeting the
   urgency bar as `urgent` (the bypass-silence hook — the actual DND-bypass mechanism can be a follow-up; for
   now route to Artemis's DM + mark urgent). Other agents must NOT use this path.
4. **Routing:** Artemis escalations → her DM with Jon; non-urgent / other-agent FYIs → fold into the existing
   morning brief.

## Constraints
- Loop-proof + sole-interrupt are HARD rules — no agent-to-agent loops, no other agent bypassing silence.
- Non-blocking; no new dependencies; match style.
- If a migration is needed: **revision 0097, down_revision 0096** (after trace-capture's 0096). If 0096 isn't
  merged yet, confirm the latest revision with the Lead before numbering. Do NOT run `alembic upgrade` in the
  worktree (no `.env`).
- Worktree has NO `.env` → UNIT tests only; Lead live-verifies.

## Tests (unit)
Pending-ask recorded on an `@Jon` agent message; resolved on Jon's reply; escalation fires only after the
window; only Artemis uses the interrupt path; agent never replies to Artemis's terminal comment.

## Deliverable
Branch `worker/hub-escalation`; commit; report files changed + decisions + test results + anything needing
live verification. Do NOT merge.
