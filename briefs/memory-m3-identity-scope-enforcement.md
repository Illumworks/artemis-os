# Worker Brief — Memory M3: identity-aware scope enforcement (access control)

**Owner:** worker (Sonnet) in an isolated worktree. **Lead:** Opus — reviews + **merges (SECURITY-sensitive;
a bug leaks one user's/agent's private memory to another — do NOT self-merge).**
**Isolation:** own worktree (`worker/memory-m3-scope-enforcement`), own test DB (name contains `artemis_test`).
Commit on the branch before reporting; report branch + the live-smoke evidence (a marketing identity DENIED
personal scope, owner ALLOWED). **Status:** READY (design grounded; see decisions below).

## Why / context (read first)
- **D8 (locked 2026-06-14, `docs/ARTEMIS-OS-MASTER-PLAN.md`):** personal + marketing run as ONE app with
  role-based access enforced at the **data/retrieval layer** (not two apps). **F1 (parked):** build scope
  boundaries clean enough that the personal scope can later be split into its own app without a rewrite.
- **D10 (`docs/memory-shell-vision-2026-05-29.md`):** "Scope IS the privacy boundary." The `personal` scope
  kind already exists (`artemis/memory/schemas.py` ScopeKind). D10 **deferred the explicit enforcement until
  multi-user lands.** It has landed: marketing teammates log in via Google/Cloudflare (CF Access) today.
- **LIVE EXPOSURE to close:** the memory HTTP API and personal surfaces AUTHENTICATE but do NOT AUTHORIZE by
  scope. `artemis/routes/memory.py` (`/observations`, `/observations/{id}`, `/scopes`) returns ALL scopes to
  any token/identity; personal tabs are visible to marketing users. This must end.

## The access matrix (POLICY — Lead-confirmed; implement exactly this)
Identity is resolved via `artemis/identity/dependencies.py` (`RequestIdentity.email` from CF Access →
`get_or_create_user` → `User.id`). Owner = `amiracentral@amiralearning.com`.

| Caller | May READ scopes | May NOT read |
|---|---|---|
| **Owner (Jon)** | ALL scopes (personal:*, agent:artemis, agent:callie, workspace:*, district/account/person/campaign_family, global) | — |
| **Marketing human (any other authed user)** | marketing-shared: `workspace:marketing`, `agent:callie`, `campaign_family:*`, `district/account/person`, `global`; AND their OWN `personal:<their_user_id>` | `personal:<other>` (incl. Jon's), `agent:artemis`, any non-marketing workspace |
| **Agent: Callie** (`agent_id="callie"`) | `agent:callie`, `workspace:marketing`, `campaign_family/district/account/person`, `global` | `agent:artemis`, any `personal:*` |
| **Agent: Artemis** (`agent_id="artemis"`) | ALL (she is Jon's PA / overseer) | — |
| **Workers** | only the explicit scope they're handed; ephemeral | everything else |

Per-user personal scope = `personal:<user_id>`. A human reads ONLY their own personal scope, never another's.
(Marketing teammates SHARE the marketing scopes — no teammate-vs-teammate isolation in v1; that's a future
add, the labelling already supports it.)

## What to build
1. **Identity → allowed-scopes resolver.** A single function `allowed_scopes_for(identity_or_agent) ->
   ScopeFilter` (allow-list of (scope_kind, scope_id) patterns, with wildcards per the matrix). One source of
   truth, reused by both the HTTP layer and the agent retrieval layer. Owner check by email; agents by agent_id.
2. **Enforce at the HTTP memory API** (`artemis/routes/memory.py`): derive the caller's allowed scopes from the
   resolved identity (add `Depends(resolve_request_identity)` where missing), and CONSTRAIN every query to that
   allow-list — both when the caller supplies a `scope_kind/scope_id` filter (reject/ignore if outside their
   allowance) and when they supply none (default to their full allowance, NOT all scopes). `/scopes` lists only
   scopes the caller may see. `/observations/{id}` 404s (not 403 — don't reveal existence) if the obs is outside
   the caller's scopes.
3. **Enforce at the agent retrieval path** (`artemis/floating_artemis/memory.py`, `search_observations`
   callers): an agent's memory reads are constrained to its allowed scopes from the same resolver. Verify Callie
   cannot retrieve `agent:artemis` or any `personal:*` even if a scope_set is passed. `_scope_for_agent` sets the
   write/target scope; M3 adds the READ allow-list enforcement.
4. **Gate personal SURFACES to owner** (the personal tabs/routes — OKRs, calendar, morning brief, personal DM
   context). Non-owner authed users get 404/hidden. Reuse `session_scope.personal_surfaces` / the per-agent
   surface allowlist machinery; extend to HTTP surfaces.
5. **Per-user personal scope:** when a human writes personal memory, scope it `personal:<their_user_id>`; reads
   of `personal:*` are constrained to the caller's own id.

## Cardinal rule — FAIL CLOSED (opposite of M1)
This is access control: on ANY uncertainty — identity unresolved, unknown agent_id, scope not in the matrix,
resolver error — **return NOTHING / deny**, never fall back to "all scopes." Default-deny. A bug that hides
data is acceptable; a bug that leaks data is not. (Contrast M1's fail-safe = don't supersede.)

## F1 (split-friendly) — design constraint
Keep `personal:*` cleanly separable: do not entangle personal-scoped data into marketing-shared tables/queries;
the allow-list resolver is the single chokepoint. A future split = export `personal:*` (+ that user's rows) and
stand up a personal-only deploy. Note anywhere personal data is hard-coupled to shared data.

## Tests + LIVE smoke (required — assert the EFFECT)
- Unit: `allowed_scopes_for` for owner / marketing-human / callie / artemis / worker / unknown(→deny).
- HTTP integration: marketing identity hitting `/api/memory/observations` (no filter) gets ONLY marketing-shared
  + own personal; gets ZERO personal:<owner> / agent:artemis rows; cannot widen via query params. Owner gets all.
  `/scopes` reflects the allow-list. `/observations/{id}` of a personal obs → 404 for marketing, 200 for owner.
- Agent: Callie's `search`/recall returns nothing from agent:artemis or personal:*; Artemis gets all.
- **Live smoke:** seed a `personal:<owner>` observation; simulate a marketing CF-Access identity end-to-end
  through the real route; assert the response contains NONE of the personal rows; then the owner identity sees
  them. Assert the response BODY, not just status.
- Regression: existing marketing users keep WS + signals + marketing memory access (don't over-restrict).

## Ship gate (Lead verifies)
- Live smoke proves a marketing identity is DENIED personal/agent:artemis scopes and the owner is ALLOWED, via
  the real HTTP path. Callie denied personal/agent:artemis via the agent path.
- Fail-closed verified (unknown identity/agent → deny, not all).
- No regression to legitimate marketing access. F1 separability noted. Lossless/data unchanged (read-path only;
  any new writes only set `personal:<user_id>` correctly).
