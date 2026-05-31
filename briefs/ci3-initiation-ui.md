# CI3 — Campaign Initiation UI form (Stream 2)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. UI form wired to existing endpoints + the CI2 proposal; mirrors existing approval/review UI patterns. Some inference about the marketing-os.js structure.
**Target branch:** `worker/ci3-initiation-ui`
**Fires:** AFTER CI2 merges (needs the persisted `CampaignInitiationProposal` + `initiate_campaign` release path).
**Browser smoke owner:** Worker (full flow: approve signal → initiation form → confirm → content fires), Lead re-verifies.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400.
**Priority:** HIGH — closes Stream 2; the operator-facing payoff.

---

## Why this exists

Per `docs/campaign-initiation-and-district-design.md` CI-1/CI-3: after Gate 1 approval, the operator sees a **Campaign Initiation form** pre-filled with brief_assembler's proposal (CI2), edits/confirms name + objective + owner + deliverable mix + target scope, and confirms — which calls `initiate_campaign` and releases the paused pipeline step so content fires. This is where the LLM's proposal meets human judgment.

---

## Scope

### Part A — Endpoints (if not already exposed by CI2)

- `GET /api/marketing/campaigns/{candidate_id}/initiation-proposal` → the persisted proposal (name, objective, recommended mix, target scope, rationale) + the active deliverable-type registry (for the checkboxes) + district context (resolved district tier/state, for smart targeting defaults).
- `POST /api/marketing/campaigns/{candidate_id}/initiate` → body = operator-confirmed { name, objective, owner_user_id, deliverable_type_slugs, target_scope }. Calls `initiate_campaign` (CI1) → releases the CI2 pause. Returns the initiated candidate.

Pydantic-validated (reuse CI1/CI2 schemas). Self-teaching errors surface to the form.

### Part B — Initiation form UI

In the marketing UI (find where Gate 1 approval lands — `public/js/features/marketing-os.js`). After a signal is approved at Gate 1, the operator gets an **Initiation form** (modal or panel) showing:
- **Name** — text input, pre-filled with proposal.name (editable).
- **Objective** — textarea, pre-filled (editable).
- **Owner** — user select, default current user.
- **Deliverable mix** — checkboxes from the **active** registry (today only `outreach_email` is checkable; show inactive ones greyed/"coming soon" so the expansion path is visible). Pre-check proposal.recommended_deliverable_types.
- **Target scope** — mode selector:
  - "All districts" / "Specific states" (multi-select from US states, default to the resolved district's state when known) / "By district tier" (D1–D3 checkboxes, D4 shown but explained as currently-unsupported).
  - `named_districts` mode: hidden/disabled for now (deferred per CI-4).
  - Pre-fill from proposal.target_scope.
- **Confirm / Cancel.** Confirm → POST initiate → success → pipeline resumes → toast "Campaign '<name>' initiated; <N> deliverable(s) queued."

Mirror the existing approval/review UI idiom (the Gate 1 approve flow + Proposals Inbox confirm pattern). Use the existing toast/feedback components.

### Part C — Empty/edge states

- If proposal fetch fails or is missing → graceful error, don't render a blank form with fake data.
- District unresolved → targeting defaults to "All districts" with a note, not a fabricated state.
- After initiation, the candidate appears in the Campaigns view with its **real name** (sets up CMP1 to remove the mock).

### Part D — Tests

- Backend: `artemis/marketing/tests/test_ci3_initiation_endpoints.py` — GET returns proposal+registry+district-context; POST initiates + releases pause; POST with inactive deliverable slug → self-teaching 4xx; POST on already-initiated → idempotency error.
- Frontend: if the repo has JS tests, add a render test for the form; otherwise the browser smoke is the gate.

---

## Files owned

- EDIT/NEW: `artemis/marketing/routes/` (initiation-proposal GET + initiate POST — may partly exist from CI2)
- EDIT: `public/js/features/marketing-os.js` (initiation form + wiring)
- EDIT: `public/js/core/api.js` (+2 wrappers)
- POSSIBLE: new component file for the form if marketing-os.js is too large
- NEW: `artemis/marketing/tests/test_ci3_initiation_endpoints.py`

---

## Acceptance criteria

1. `pytest .../test_ci3_initiation_endpoints.py -v` — all pass. **Paste.**
2. **Full browser smoke:** approve a Gate-1 signal → initiation form appears pre-filled → edit name → confirm → toast → candidate shows real name in Campaigns → pipeline resumed (content node fired for the chosen mix). **Paste console + step description.**
3. Deliverable checkboxes: only `outreach_email` checkable; others greyed "coming soon". **Describe/screenshot.**
4. District-unresolved → targeting defaults to "All districts" with note (no fake state). **Confirm.**
5. `./scripts/check.sh` + `git diff --stat` + `git log --oneline -1`. **Paste.**

---

## Hard constraints

- **No fabricated defaults.** Unresolved district → honest "All districts" default, never a made-up state/tier.
- **Confirm is the commit.** The proposal is editable; nothing initiates until the operator confirms.
- **Respect the active registry** — inactive deliverable types are visible-but-disabled, not selectable.
- **Mirror existing approval UI idiom** — don't invent a new interaction pattern.
- **Fires after CI2.** **Local-only git.**

---

## Report-back format

```
CI3 — initiation UI report
1. Commit / branch
2. LOC per file
3. Endpoint test pass count
4. FULL browser smoke (approve → form → confirm → resume → real name in Campaigns)
5. Deliverable checkbox + targeting default behavior
6. check.sh summary
7. Surprises — esp. marketing-os.js structure, where Gate 1 approval currently lands
```
