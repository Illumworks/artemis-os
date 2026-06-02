# CMP-SEND-2 — Contacts substrate + human-gated send queue (TERMINAL CLAUDE LEAD chunk)

**Paste-into:** terminal **Claude Lead** (decompose into sub-worker tasks with sonnet sub-workers).
**This is a LEAD brief, not a single-worker brief.** Own the design, split it into sub-tasks, run
them in **separate git worktrees** (concurrency hazard — see Coordination), integrate, verify
end-to-end, and report back. Use Opus judgment for the architecture; hand mechanical pieces to
sonnet sub-workers.
**Target integration branch:** `lead/cmp-send-2-contacts-outbox` (sub-workers branch off it).
**Migration head:** `0059`. You'll add new ones (0060+). Coordinate numbering across sub-workers
(one sub-worker owns migrations, or assign disjoint numbers and verify the alembic chain — the
CLAUDE.md migration-renumber lesson is real).
**Builds on:** CI4 (merged `679addc`) + CMP-SEND-1 (merged `4244c7b`). The campaign engine now runs
signal → … → draft → Gate-2 review → **approve** → pipeline resumes → run **succeeds**. The
deliverable lands at `DeliverableState.approved` (terminal today) — and there is **no one to send
to and no send mechanism**. This chunk builds that foundation.

---

## The gap this closes
After Gate-2 approve, a campaign deliverable is `approved` and… stops. Two missing pieces:
1. **No recipients.** There is **no contacts substrate** — `contact_db_stub.has_contact` returns
   `true` for everyone, `ContactData` is an empty dataclass, no `district_contacts` table. An
   outreach email has no address.
2. **No send mechanism.** `DeliverableState.approved` is terminal; there is no `sent` state, no
   outbox, no send action, no UI.

This chunk builds the **contacts substrate + a human-gated send queue (outbox) + the review/send
UI**, and records the send event (the capture seam for future outcome tracking, #106).

## LOCKED design decisions (Lead — Jon delegated; these are the safe defaults)
1. **Human-gated send. NO autonomous send.** The system NEVER auto-emails districts. An operator
   explicitly clicks "Send" on a reviewed item. (Brand + real-world risk; matches the org safety
   posture on sending messages on someone's behalf.)
2. **Real email transport is DEFERRED — STUB IT.** Do NOT integrate SendGrid/SES/Gmail/SMTP in this
   chunk. The "Send" action resolves recipients, records a send event, transitions state to `sent`,
   and writes a **stub transport log** (no actual email leaves the system). The ESP/Gmail decision
   is Jon's and comes later; design a clean `transport` seam so it drops in without rework.
3. **Contacts v1 = manual/seeded**, with a **Salesforce sync seam** (the stub says "until
   Salesforce integration ships"). Add a `source` column (`manual`|`salesforce`) + an
   `external_id` column so a future Salesforce sync upserts cleanly. Do NOT build Salesforce now.
4. **Lossless / append-only** — sends and contacts are never hard-deleted (supersede/deactivate).

If any of these feels wrong as you design, STOP and flag Jon before building — they're the
load-bearing judgment calls.

## Suggested decomposition (you may re-cut; ~3 sub-tasks)

### SEND2-A — District contacts substrate
- Migration: `district_contacts` (id, district_id FK→districts ON DELETE CASCADE/SET NULL, name,
  title/role, email, phone?, source [`manual`|`salesforce`], external_id nullable, active bool,
  created_at; unique on (district_id, email) and/or (source, external_id)).
- Model + repository (create/list-by-district/deactivate; NO hard delete).
- `contact_db_stub.has_contact` → upgrade to read the real table (return true iff an active contact
  exists for the district); keep the tool name/signature stable. Seed a handful of real-ish manual
  contacts for the districts behind existing campaigns (5/7/8) so the rest of the chunk is testable.
- Tests: substrate CRUD + has_contact reads real data.

### SEND2-B — Outbox + send state machine + recipient resolution
- Extend `DeliverableState`: add `queued_for_send` and `sent` (approved → queued_for_send → sent;
  approved is no longer terminal). Update `state_machine.py` transitions + tests.
- Migration: `campaign_sends` (id, candidate_id FK, deliverable_id FK, recipients JSONB [resolved
  contacts snapshot], status [`queued`|`sent`|`failed`|`skipped`], transport [`stub` for now],
  transport_log JSONB, queued_at, sent_at nullable, sent_by nullable). Append-only.
- **On Gate-2 approve** (hook into the existing approve path in `artemis/marketing/routes/
  approvals.py` `_decide_content_draft_approval`): resolve recipients from `district_contacts` for
  the campaign's target district(s); create a `campaign_sends` row `status=queued` +
  deliverable → `queued_for_send`. If NO contacts resolve, create the row `status=skipped` with a
  reason (don't fail the approval). **Do not change the existing approve→resume behavior** — this
  is additive.
- `POST /api/marketing/sends/{id}/send` (human-gated): mark `sent`, deliverable → `sent`, write the
  stub transport_log, set sent_at/sent_by. Idempotent (already-sent → 4xx). **No real email.**
- Tests: approve → queued send created with resolved recipients; no-contacts → skipped; send →
  sent + state transitions; idempotency.

### SEND2-C — Outbox / "Ready to Send" review UI (`public/js/features/marketing-os.js`)
- A surface listing `campaign_sends` with `status=queued`: campaign name, district, resolved
  recipients, the approved draft preview, deliverable type. A **"Send" button** per item →
  `POST /sends/{id}/send` → on success remove from queue + toast ("Recorded — transport pending ESP
  setup"). Empty state. `skipped` items show "no contacts on file for this district" with a link to
  add one (or just surface the reason for v1).
- `api.js` wrappers (list queued sends + send). Mirror the Gate-2 drawer idiom from CMP-SEND-1.
- **Make the stub-transport honest in the UI** — the operator must understand no email actually
  went out yet (label it clearly). Do NOT imply a real send.

## Coordination (READ — concurrency hazards)
- **Work in separate worktrees.** The main repo tree is shared with Codex + the floating Artemis.
  Each sub-worker gets its own `git worktree` off `lead/cmp-send-2-contacts-outbox`. Do NOT all edit
  the main tree. (Two-cooks-one-tree has bitten us; isolation:worktree for Agent sub-workers.)
- **`marketing-os.js` is yours (SEND2-C).** Codex's parallel PROC1 job does NOT touch it. But if the
  floating Artemis or another stream edits it, coordinate — it's the app's main marketing surface.
- **Migrations:** head is 0059. Assign 0060/0061 to disjoint sub-workers and verify the chain
  (`alembic heads` shows one head; `alembic upgrade head` clean) before integrating. Run
  `git diff --staged` on any commit mixing a rename/migration with content edits.
- **Don't auto-send anything.** No transport. No emails. Ever, in this chunk.

## Acceptance criteria (Lead verifies end-to-end before reporting)
1. All sub-task test suites pass against the test DB (`ARTEMIS_TEST_DB_URL=…artemis_test`,
   `alembic upgrade head` first). Paste counts.
2. `alembic heads` → single head; `uv run alembic upgrade head` clean on the live DB.
3. **Real end-to-end on the live app:** seed contacts for candidate 7's district → re-run (or use a
   fresh) campaign through Gate-2 approve → a `campaign_sends` row appears `status=queued` with
   resolved recipients + deliverable `queued_for_send` → the Outbox UI shows it → click Send →
   `sent` + transport_log recorded + deliverable `sent`. **Paste DB state before/after + the UI
   description.** Also show the no-contacts → `skipped` path.
4. `./scripts/check.sh` (j5b Jira flake exempt). Paste summary.
5. Integrate sub-worker branches into `lead/cmp-send-2-contacts-outbox`; **commit locally, no push**;
   commit messages end `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Propose the merge
   to `main` back to Jon (or the coordinating Opus Lead) — do NOT merge to main yourself without a
   green end-to-end.

## Hard constraints
- **Human-gated, transport-stubbed, no real email.** This is the non-negotiable safety boundary.
- **Additive to the approve path** — don't regress CMP-SEND-1's approve→resume→succeed flow.
- **Lossless / append-only**; **no new deps < 7 days old** (org rule); **local-only git**.
- **Use the DeliverableState machine** for transitions; don't bypass it.

## Report-back format
```
CMP-SEND-2 — contacts + outbox report
1. Sub-task breakdown + branches + migrations (0060/0061…) + final alembic head
2. Contacts substrate: table shape + how has_contact now reads it + seeded contacts
3. Outbox: campaign_sends shape + the approve→queued hook + the human-gated send endpoint
4. State machine: new states + transitions
5. UI: what the Outbox surface renders + the stub-transport labeling
6. Real e2e: approve→queued→send→sent (DB before/after) + no-contacts→skipped
7. check.sh summary
8. Surprises + anything that hit a locked decision (esp. recipient resolution from target_scope)
9. Open question for Jon: the ESP/transport decision (what actually sends, when we un-stub)
```
