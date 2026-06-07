# Memory Phase 3 — Curate (Pin / Confirm / Retire / Supersede) + Conflicts drawer

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-phase-3-curate-conflicts`
**Browser smoke owner:** Lead, post-merge — open Memory page, pin a memory, mark one confirmed, retire one with a reason, supersede one with new content, resolve a conflict; verify each persists and the lossless invariants hold.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~440 + 1 alembic migration (4 new endpoints + 1 column + frontend action panel + conflicts drawer + tests).
**Priority:** HIGH — first phase with write paths. Phase 3 is the only phase carrying medium risk; Lead browser-smokes with a seeded DB.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md`
**Depends on:** Phase 2 must be merged first (the action buttons live in the detail panel that Phase 2 rebuilt). Phase 1 is recommended but not strictly required.

---

## Why this exists

Per the audit, there is zero affordance to act on a memory in the current page. The CLAUDE.md lossless contract rules out direct delete/edit, so Phase 3 introduces four lossless-safe actions:

- **Pin** — cosmetic float-to-top. No effect on retrieval. Soft signal.
- **Confirm** — sets `user_confirmed = true`. Asserts authority.
- **Retire** — sets `valid_until = now()` with a free-text reason logged as evidence. Lossless; row stays for audit.
- **Supersede with new content** — composer for a replacement claim. Writes new observation, links old → new via `supersedes`/`superseded_by`.

Plus the Conflicts drawer: surfaces unresolved rows from the existing `/api/memory/conflicts` endpoint, lets Jon resolve via the four backend-supported resolution types.

Locked decisions (2026-06-06):
- **Both Pin AND Confirm ship** — they answer different questions (#2).
- **Retire requires a free-text reason** — stored as the evidence record on retirement (#3).

---

## Scope

### Part A — Schema migration

New alembic migration. ONE column add:

```sql
ALTER TABLE memory_observations ADD COLUMN pinned_at TIMESTAMPTZ NULL;
CREATE INDEX idx_memory_observations_pinned ON memory_observations(pinned_at) WHERE pinned_at IS NOT NULL;
```

Use a sequential revision number; check `uv run alembic current` for the next slot.

No other schema changes. `user_confirmed` already exists. `valid_until` already exists. `supersedes` / `superseded_by` already exist.

### Part B — Backend endpoints (4 new)

All under `/api/memory/observations/{id}/...`. All require token. All wrap existing store/repository functions where possible.

**1. `POST /api/memory/observations/{id}/pin`** — toggle pin.

Body: `{}` (no params needed; toggles based on current state).

Behavior: `pinned_at = now() if pinned_at is null else null`. Returns the updated observation row.

**2. `POST /api/memory/observations/{id}/confirm`** — assert authority.

Body: `{}`.

Behavior: `user_confirmed = true`, `confidence_origin = 'operator'`. Returns updated row. Idempotent (re-confirming is a no-op).

**3. `POST /api/memory/observations/{id}/retire`** — soft delete with reason.

Body: `{"reason": "<free-text, required, 1-500 chars>"}`.

Behavior, transactional:
- `UPDATE memory_observations SET valid_until = now() WHERE id = :id AND valid_until IS NULL` — idempotent on already-retired.
- `INSERT INTO memory_evidence (observation_id, source_kind, source_id, source_quote, weight) VALUES (:id, 'operator_retirement', :ts_iso, :reason, 1.0)` — the reason itself becomes evidence.
- 422 if reason empty or > 500 chars.
- 404 if observation not found.

Returns updated row.

**4. `POST /api/memory/observations/{id}/supersede`** — write replacement.

Body: `{"content": "<new claim, 1-5000 chars>", "reason": "<optional free-text>"}`.

Behavior, transactional, in this order:
- Validate `content` non-empty, ≤5000 chars (422 otherwise).
- Fetch old observation; 404 if not found; 409 if `valid_until IS NOT NULL` (cannot supersede a retired observation).
- Call existing `write_observation(content=..., scope_kind=old.scope_kind, scope_id=old.scope_id, category=old.category, score=old.score, supersedes=old.id, confidence_origin='operator')` from `artemis/memory/store.py`. Capture new id.
- Call existing `supersede_observation(old_id=old.id, new_id=new.id)` from store.
- Link evidence: `link_evidence(observation_id=new.id, source_kind='operator_supersedes', source_id=str(old.id), source_quote=reason or 'superseded by operator', weight=1.0)`.

Returns `{"old_observation": {...}, "new_observation": {...}}`.

### Part C — Frontend: Action panel

Edit `public/js/features/memory-shell.js`, `renderM6DetailPanel`. Add an actions block at the bottom of the detail panel (below evidence, below entities placeholder for Phase 5):

Four buttons in a row:
- **📌 Pin** (or "Unpin" if pinned) — primary if unpinned, secondary if pinned
- **✓ Mark confirmed** — primary if not confirmed, hidden if already confirmed (replaced by a confirmed badge)
- **⊘ Retire** — secondary; opens a modal asking for reason before fire
- **↻ Supersede with…** — secondary; opens composer

**Retire modal:**
- Single text area, required, max 500 chars
- "Retire" + "Cancel" buttons
- On submit: POST endpoint, then re-fetch detail and list, close modal, show toast "Retired (reason logged)"

**Supersede composer:**
- Header: "Replace this memory with..."
- Shows the current observation content as a non-editable quote (for reference)
- Large textarea for new content (required, max 5000 chars)
- Small "reason" input (optional)
- "Supersede" + "Cancel" buttons
- On submit: POST endpoint, then re-fetch list + detail, select the new observation in the list, close composer, show toast "Memory superseded (lineage preserved)"

### Part D — Conflicts drawer

A bottom-anchored drawer in the Memory page that surfaces unresolved conflicts.

**Closed state:** a thin bar at the bottom of the page reading:
> "3 conflicts to resolve · open ⌃" (or "No conflicts" with no dot if zero)

A red dot appears next to the count if any conflicts are unresolved.

**Open state:** drawer slides up; lists each conflict as a row:
- Side-by-side: observation A (left) vs observation B (right)
- Each side shows: scope, age, score, content preview, "open detail →" link
- Four resolution buttons below the side-by-side: **A wins · B wins · Both valid (different scope) · Need human (mark for later)**
- A small "reason" input (optional) above the buttons

Resolution buttons call `POST /api/memory/conflicts/{id}/resolve` (already exists in `artemis/routes/memory.py:67`). After resolution: row slides out, drawer count decrements, toast "Conflict resolved (A wins / B wins / etc.)".

If `Need human` is picked: posts with resolution=`manual_review_needed`; row stays in drawer with a "deferred" tag and grays out the resolution buttons.

### Part E — Tests

`artemis/routes/tests/test_memory_curate_endpoints.py` (new file):

1. **Pin toggles `pinned_at`.** Post pin twice; verify first sets, second clears.
2. **Confirm sets `user_confirmed = true` and is idempotent.** Post twice; second is no-op.
3. **Retire sets `valid_until` and links operator_retirement evidence.** Post with reason; verify column + evidence row.
4. **Retire on already-retired returns 200 (idempotent, no double evidence).** Post twice; verify only one evidence row.
5. **Retire with empty reason returns 422.** Verify error code + message.
6. **Supersede writes new observation and links old → new.** Post; verify new row exists with `supersedes=old.id` and old row has `superseded_by=new.id`.
7. **Supersede on retired observation returns 409.** Pre-retire, then post supersede; verify 409.
8. **Supersede with empty content returns 422.**

`artemis/routes/tests/test_memory_shell_routes.py` (extend):

9. **`list_observations?status=all` includes both pinned and active.** Verify ordering: pinned first when `sort=recent`.

`artemis/memory/tests/test_lossless_invariants.py` (new file — paranoid checks):

10. **No public DELETE endpoint exists on `/api/memory/observations`.** Reflect the FastAPI router; assert no `DELETE` route is registered. Lossless guard.
11. **No content mutation endpoint exists.** Same reflective check for PUT/PATCH on the content field.

---

## Files owned

- NEW: `alembic/versions/00XX_add_pinned_at_to_memory_observations.py`
- EDIT: `artemis/memory/models.py` (add `pinned_at` to `MemoryObservation`)
- EDIT: `artemis/memory/schemas.py` (add `pinned_at` to `Observation`)
- EDIT: `artemis/memory/repository.py` (extend `list_observations` to surface `pinned_at`; add pin/confirm/retire/supersede helpers if cleaner than inline)
- EDIT: `artemis/routes/memory.py` (4 new POST endpoints)
- EDIT: `public/js/features/memory-shell.js` (action panel + retire modal + supersede composer + conflicts drawer)
- EDIT: `public/css/panels/memory.css` (action button row, modal styles, drawer styles)
- NEW: `artemis/routes/tests/test_memory_curate_endpoints.py`
- NEW: `artemis/memory/tests/test_lossless_invariants.py`
- EDIT: `artemis/routes/tests/test_memory_shell_routes.py` (test #9)

---

## Acceptance criteria

1. **Schema migration applied.** `uv run alembic upgrade head` succeeds; `\d memory_observations` shows `pinned_at`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/routes/tests/test_memory_curate_endpoints.py artemis/memory/tests/test_lossless_invariants.py -v` — all tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge with a seeded DB):**
   - Pin an observation; verify it floats to top of its scope; click again to unpin; verify it returns to date-sorted position.
   - Confirm an observation; verify a "confirmed by operator" badge appears in the detail panel; reload page; badge persists.
   - Retire an observation with a reason like "no longer accurate, replaced by Q2 strategy"; verify:
     - The retired row disappears from default list view (status=active filter).
     - Status=All filter shows it greyed out with "retired" badge.
     - Detail panel shows "Retired by operator on {date}: '{reason}'" as part of provenance.
     - The reason appears as a new evidence row of source_kind `operator_retirement`.
   - Supersede an observation:
     - Click Supersede; composer opens; type a new claim; submit.
     - Verify the lineage timeline (from Phase 2) now shows both old and new with the link.
     - Verify the list shows the new observation, and the old one has `superseded_by` set.
   - Seed a conflict row in `memory_conflicts`; verify the bottom bar shows "1 conflict to resolve" with the red dot; open drawer; pick "A wins"; verify B's `valid_until` is set and `supersedes` points to A.
   - **Paste DOM snippets for each action's post-state.**
5. `git diff --stat` + `git log --oneline -1` on `worker/memory-phase-3-curate-conflicts`. **Paste.**

---

## Hard constraints

- **Lossless rule is non-negotiable.** No DELETE endpoint. No content mutation endpoint. `test_lossless_invariants.py` enforces this reflectively.
- **All writes pay the evidence rule.** Retire logs evidence; supersede links evidence; both have audit trails. Pin and Confirm are flag toggles and don't strictly need evidence rows — but log them via app logger at INFO level: `"pin_toggled observation_id=N actor=operator new_state=true|false"`.
- **Schema migration is single-column, additive, indexed.** Don't bundle other schema changes. Don't drop or alter existing columns.
- **Retire reason is required.** Empty reason = 422. Lead enforces this in smoke by attempting empty submission via DevTools.
- **Supersede is atomic.** New observation + supersession link + evidence must all succeed in one transaction, else nothing persists. Use `async with session.begin()` in the endpoint.
- **No mid-flight UI race.** When the user fires an action, disable the button until the response lands; re-fetch list + detail in parallel; re-enable buttons.
- **Conflicts drawer is collapsed by default.** Open state persists in `localStorage` like the floating Artemis memory-inspector (existing pattern in `memory-inspector.js`).
- **No "Optimize" or "Archive" admin** — explicitly out of scope per the redesign brief.
- **Local-only git.** Worker commits on `worker/memory-phase-3-curate-conflicts`; terminal-Lead merges after Lead approves AND completes the smoke checklist.
