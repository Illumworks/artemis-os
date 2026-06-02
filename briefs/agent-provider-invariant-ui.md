# Agent Provider Invariant — Required UI Fields

**Owner:** Codex (paste-ready, mechanical UI work)
**Branch:** `codex/agent-provider-invariant-ui`
**LOC budget:** ~200 (full-diff insertions; cap at 250)
**STOP CONDITION:** if you reach 200 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** O1/O2/O3 merged (Agent model has `provider` + `fallback_provider` + `fallback_model` fields).
**Grounded in:** D6's locked agent invariant in `docs/ARTEMIS-OS-MASTER-PLAN.md` — every Agent must have `preferred_provider` AND `fallback_provider`.

## Why this brief exists

D6 locks: every Agent MUST have a preferred provider AND a fallback provider. The DB fields exist (`agents.provider`, `agents.fallback_provider`, `agents.fallback_model`). The Builder and Agent Card UIs partially surface them but don't enforce them. Today an agent can be created with `provider` set to default and `fallback_provider` null — that violates the invariant. This brief makes both required in the UI and validates them on save.

## Scope

### In scope

1. **Provider/model picker** as a reusable component (if not already present — survey first):
   - Pulls available providers + their models from `/api/floating-artemis/models` (existing endpoint extended in Codex's earlier effort/speed work) OR `/api/providers` (whatever endpoint lists adapters — survey).
   - Renders two two-step pickers: Preferred (`provider` dropdown → `model` dropdown filtered to that provider's models) and Fallback (same shape).
   - "No fallback" is NOT a valid option. Validation: both fields required to save.

2. **Builder surface** (`public/js/features/agent-builder.js` or the Builder modal — Codex finds the right insertion point):
   - When the user reaches the provider/model step of the conversational Builder (or the form-equivalent), the picker is mandatory.
   - If the user tries to skip / leave blank, Builder responds (via SSE turn or inline validation) with "Both preferred and fallback provider are required — these protect your agent from upstream outages."
   - On save, the proposal's persisted `provider` + `fallback_provider` + `fallback_model` fields must all be populated.

3. **Agent Card surface** (`public/js/features/agents.js` or operations-shell.js — wherever the Agent Card detail panel lives):
   - Provider section in the right detail panel shows: Preferred `[provider] · [model]`, Fallback `[provider] · [model]`.
   - Edit button on the section opens an inline editor with the two-step picker.
   - Save validates both fields populated before PATCH.
   - If a legacy agent has `fallback_provider = null` (e.g., the existing Smoke Test Agent or WS Integration Agent), the panel renders the fallback as `⚠ Not set — required` in a warning color (existing token, not new). Edit button is highlighted to prompt the user.

4. **Backend validation guard** in `artemis/builders/schemas.py` or the agent CRUD route:
   - On PATCH `/api/agents/{id}`, if the request body sets `fallback_provider` to null OR if the agent currently has null fallback and the PATCH doesn't populate it, return 422 with a clear error: `"agent must have a fallback_provider per D6 invariant"`.
   - On POST (new agent), require both `provider` and `fallback_provider` in the request body.
   - **Exception:** seed loaders (M5 marketing agents seed) bypass this via a `_skip_invariant_check` internal flag set in the loader path. Users can't set this flag. (If M5 already populates fallback for all 16 agents, no exception needed — verify against the M5 seed code.)

5. **One-time data audit:** add a CLI script `scripts/audit_agent_providers.py` (~25 LOC) that lists any agent with null fallback_provider. Run-once helper for the operator; not in app startup.

6. **Tests:**
   - PATCH that sets fallback_provider to null → 422.
   - PATCH that updates other fields but agent has null fallback → 422 (force-populate-on-touch).
   - POST without fallback_provider → 422.
   - Seed loader bypass works (M5 re-seed succeeds).
   - Frontend smoke: load Agent Card, click Edit on provider section, picker opens with current values, save persists.

### Out of scope

- Adding new providers to the registry. Use whatever's already registered.
- Cost-aware fallback routing logic. That's a runtime adapter concern, separate brief.
- "Multiple fallbacks" (a chain of providers). Single fallback only for now.
- Per-tool provider override. Agent-level only.
- Migration of legacy agents — handled via the audit script + UI prompt, not auto-fix.

## Invariants

1. **No agent can be saved via UI without both providers populated.** Backend 422 enforces; UI prevents the bad save in the first place.
2. **Picker uses the existing provider registry, not a hardcoded list.** If a new provider is added to the registry tomorrow, it appears in the picker automatically.
3. **Visual treatment of "not set" fallback uses an existing warning token,** not a new one.
4. **The "edit provider" inline editor lives on the Agent Card,** not a separate modal. Click to expand, save inline.

## Files expected

| File | LOC |
|---|---|
| `public/js/features/agent-builder.js` | ~40 delta (picker integration in flow) |
| `public/js/features/agents.js` (or wherever Agent Card lives) | ~50 delta |
| `public/css/features/agents.css` | ~30 delta (provider section styling) |
| `public/js/components/provider-picker.js` (new — if no existing reusable) | ~70 |
| `artemis/builders/schemas.py` | ~10 delta (Pydantic validator) |
| `artemis/routes/builders/agents.py` | ~10 delta (route-level guard) |
| `scripts/audit_agent_providers.py` | ~25 |
| `artemis/builders/tests/test_agent_provider_invariant.py` | ~40 |

**Total: ~275 LOC.** Slightly over the 200 budget, fine if under 250. If you scope `provider-picker.js` smaller (reuse existing dropdown component if one exists), this drops to ~200. Survey before assuming a new component is needed.

## Test plan

1. POST agent without fallback_provider → 422 with clear message.
2. PATCH agent setting fallback_provider to null → 422.
3. PATCH agent (existing null fallback) updating description without populating fallback → 422 (force-populate-on-touch).
4. Builder flow: conversational turn asking for provider; user submits without fallback → Builder responds with rejection; user provides fallback → proposal persists.
5. Agent Card: load existing agent with both populated → both shown. Load legacy agent with null fallback → warning displayed.
6. Inline edit save → PATCH succeeds, panel re-renders with new values.
7. M5 re-seed → succeeds (seed loader bypass works).
8. `audit_agent_providers.py` script lists 0 violations after M5 has populated fallbacks.

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` must pass within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new JS console errors

## What "done" looks like

1. Both provider fields required at the DB layer (Pydantic + route guard).
2. Builder rejects incomplete proposals.
3. Agent Card shows both providers, warns on missing fallback, supports inline edit.
4. Tests pass.
5. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat` output.
2. The validator code (paste — Lead spot-checks).
3. Screenshot or description of the Agent Card provider section in two states (both populated; missing fallback).
4. Audit script output on current lead (should list any legacy agents needing fixup).
5. Test pass count.
6. Branch.

---

**Lead notes (not for Codex):**
- This brief operationalizes part of D6's locked invariants — the agent fallback requirement.
- Surveying the existing UI first matters: there may already be a provider/model picker component from Codex's effort/speed work that this can extend instead of duplicating.
- If the audit script surfaces legacy agents without fallback, that's a known issue from before this brief (Smoke Test Agent + WS Integration Agent likely). Document them in the report; don't auto-fix — let the operator click into each in the UI and set fallback explicitly. The UI's warning state is doing its job there.
