# SP — Signal Playbook (combined SP1 + SP2)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/sp-signal-playbook`
**Browser smoke owner:** Lead, post-merge — open Marketing → Signal Playbook, verify reason codes render with full metadata, edit one (change urgency or add a primary scout), save, verify the change persists + the next pipeline run uses it.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~500 (schema additions + extended routes + frontend UI + markdown export + tests).
**Priority:** HIGH — operator surface for Josh's signal spec. Per master plan priority order: Phase BH ✅ → **SP** → PIPE6. Combined SP1+SP2 in one stream per Lead's recommendation: edit-through-review-flow is proven by MC1 pattern; half-product read-only museum tour costs operator value.

---

## Why this exists

Per `docs/signal-playbook-design.md` (LOCKED 2026-05-26):

- **What:** Marketing-section UI where Josh / Anne Marie can view AND edit the criteria that fire campaigns (reason codes in v1; territory/rules/nuances later) without a deploy.
- **D — Source-of-truth architecture: Option B (table canonical, markdown = generated export).** The `signal_reason_codes` table IS canonical. Markdown becomes a one-way generated snapshot for git history + human reading.
- **D — Editing UX: structured, never raw text.** Card/list view + per-item structured form. Every field constrained. No raw markdown editing.
- **D — Retire = soft (lossless invariant).** Reason codes can be marked `is_active=false` but never hard-deleted (already enforced via DB trigger `signal_reason_codes_block_delete`).

**Substrate already in place** (verified 2026-05-30):

- `signal_reason_codes` table — has `code` (PK), `domain`, `description`, `what_scout_looks_for`, `default_urgency`, `is_active`, `created_at`, `updated_at`. DELETE trigger blocks hard deletes.
- Existing routes at `artemis/marketing/routes/signal_criteria.py`: GET/POST/PATCH `/api/marketing/signal-criteria/reason-codes`. v1 immutability constraint on `code` + `domain`. PATCH validates `default_urgency` against enum.

**Missing in substrate (this brief adds):**

- `primary_scouts` column (which scouts emit this code — array of scout slugs)
- `campaign_families` column (which campaign families this code maps to — array)
- Markdown export endpoint (one-way generated snapshot)
- Frontend UI surface
- Validation against the 9 known scout slugs + the campaign families enum

---

## Scope

### Part A — Schema additions (migration 0052)

`alembic/versions/0052_signal_reason_codes_primary_scouts.py`:

```python
def upgrade():
    op.add_column(
        "signal_reason_codes",
        sa.Column("primary_scouts", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "signal_reason_codes",
        sa.Column("campaign_families", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    
    # Backfill from josh_spec.py's parsed ReasonCodeSpec where possible
    # (left as a one-shot script the operator runs; OR Worker writes inline backfill SQL)

def downgrade():
    op.drop_column("signal_reason_codes", "campaign_families")
    op.drop_column("signal_reason_codes", "primary_scouts")
```

**Backfill:** read `artemis/marketing/josh_spec.py:parse_spec()` to get the current canonical `ReasonCodeSpec` list, then for each `code` in `signal_reason_codes`, update the `primary_scouts` array to match the parsed spec's `primary_scouts` tuple. Same for `campaign_families` if the parsed spec contains that mapping (per the spec model — verify against `josh_spec.py:ReasonCodeSpec`).

Backfill is one-shot in the migration's upgrade() function. Subsequent edits happen through the UI.

### Part B — Route extensions

In `artemis/marketing/routes/signal_criteria.py`:

**GET `/api/marketing/signal-criteria/reason-codes`** — extend response to include `primary_scouts` + `campaign_families`.

**POST `/api/marketing/signal-criteria/reason-codes`** — accept `primary_scouts` (list[str]) + `campaign_families` (list[str]) in body. Validate `primary_scouts` against the known 9 scout slugs (hardcoded list OR query `agents` table for `agent_id LIKE 'marketing.scout.%'`). Validate `campaign_families` against the campaign families enum (verify against existing constraint or list).

**PATCH `/api/marketing/signal-criteria/reason-codes/{code}`** — accept the same fields, same validation.

**NEW GET `/api/marketing/signal-criteria/reason-codes/markdown-export`** — return the current state of all active reason codes as a markdown snapshot matching the format in `decisions/campaign-signal-spec-v1.md` (or a simpler format). Operator can copy/save this to git when they want to commit the current state. **One-way generated. No file write from the API.**

### Part C — Frontend UI

`public/js/features/marketing/signal-playbook.js` (new file). Reachable from the Marketing left-rail entry "Signal Playbook" (need to add the nav item to `public/js/features/operations-shell.js` or equivalent — search for the Marketing nav rendering).

**Layout:**

```
┌─────────────────────────────────────────────────────────────┐
│  Signal Playbook                                            │
│  Live registry of campaign-signal criteria.                 │
│  Edits here are read by every scout + qualifier on next run.│
│                                                             │
│  [Filter by domain ▾]   [Filter by scout ▾]   [+ Add code] │
│  [Export as markdown]                                       │
├─────────────────────────────────────────────────────────────┤
│  Domain: literacy_growth                                    │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ POLICY_EDTECH_TIME_LIMIT                       [Edit] │ │
│  │ Trigger: District announces formal limits on...       │ │
│  │ Scout watches: school board minutes, state DoE...     │ │
│  │ Urgency: hot · Primary scouts: board_minutes, state_doe│ │
│  │ Campaign families: reading_growth, biliteracy         │ │
│  └───────────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ LEADER_TRANSITION_FORMAL                       [Edit] │ │
│  │ ...                                                   │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  Domain: brand_visibility                                   │
│  ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

**Edit form** (modal or inline panel):

- Code name (read-only after creation — v1 immutability lock per existing route constraint)
- Domain (read-only after creation — v1 immutability per existing route constraint)
- Plain-English trigger (textarea, max 2000 chars)
- What the scout looks for (textarea, max 2000 chars)
- Default urgency (dropdown: `hot` | `standard` | `low` | `enrichment` — match existing enum)
- Primary scouts (chip multi-select from the 9 known scouts)
- Campaign families (chip multi-select from known campaign families)
- Is active (toggle — soft retire)
- [Save] / [Cancel]

**Add code form:**
- Same as edit form
- Code name: required, validated format (SCREAMING_SNAKE, unique)
- Domain: required
- Other fields: optional but encouraged

**Validation:**
- All saves go through Pydantic-validated POST/PATCH body (server-side validation)
- Frontend pre-validates and shows inline errors before submit
- Server-side errors surface as toast notifications

**Markdown export button:**
- Single button: "Export as markdown"
- Calls GET `/markdown-export` → returns text/markdown
- Frontend triggers a download of `signal-playbook-YYYY-MM-DD.md`

### Part D — Lossless invariant respect

Per the locked design D:
- **NEVER hard-delete a reason code.** The DB trigger already blocks this; UI must not even expose a delete affordance.
- **Soft retire = set `is_active=false`.** Surface as a "Retire" button in the edit form that flips the flag.
- **Retired codes default-hidden** in the listing; "Show retired" toggle exposes them.
- **`updated_at` auto-bumps** on every PATCH (already enforced by DB trigger or model).

### Part E — Tests

`artemis/marketing/tests/test_sp_signal_playbook.py`:

1. **Migration 0052 applies cleanly.** Verify columns added with correct types + default empty arrays.
2. **Backfill populates from josh_spec.py.** After migration, query existing reason codes — verify `primary_scouts` matches the parsed spec.
3. **GET /reason-codes returns new fields.** Existing test extended; verify response includes `primary_scouts` + `campaign_families`.
4. **POST validates primary_scouts against known slugs.** Posting `primary_scouts=["bogus_scout"]` returns 400 with self-teaching error listing valid slugs.
5. **POST validates campaign_families.** Same shape.
6. **PATCH updates arrays.** Patching `primary_scouts=["new_scout_slug"]` updates the array. `code` + `domain` remain immutable (existing constraint).
7. **GET /markdown-export returns sane markdown.** Verify markdown structure includes all active codes with their fields.
8. **Soft retire flow.** PATCH `is_active=false`. Verify GET (default) hides the row; GET `?include_inactive=true` shows it.
9. **DB delete trigger still blocks.** Direct DELETE on `signal_reason_codes` raises (the trigger).
10. **Frontend smoke** (if JS test infra available, else Lead does eyes-on): load `/#/signal-playbook` → list renders → edit a code → save → verify the change shows in the list after refresh.

---

## Files owned

- NEW: `alembic/versions/0052_signal_reason_codes_primary_scouts.py`
- EDIT: `artemis/marketing/models.py` (extend `SignalReasonCode` model with array columns)
- EDIT: `artemis/marketing/routes/signal_criteria.py` (extend routes, add markdown-export endpoint)
- POSSIBLE EDIT: `artemis/marketing/schemas.py` (Pydantic request/response models for the routes)
- NEW: `public/js/features/marketing/signal-playbook.js` (frontend UI)
- NEW or EDIT: `public/css/features/marketing/signal-playbook.css` (or extend existing marketing CSS)
- EDIT: `public/js/features/operations-shell.js` (add Signal Playbook nav entry under Marketing)
- EDIT: `public/js/core/api.js` (add API wrapper functions for the new endpoints)
- NEW: `artemis/marketing/tests/test_sp_signal_playbook.py`

---

## Acceptance criteria

1. `uv run alembic upgrade head` shows `0052_signal_reason_codes_primary_scouts`. **Paste.**
2. **Backfill verified:** `SELECT code, primary_scouts FROM signal_reason_codes WHERE array_length(primary_scouts, 1) > 0 LIMIT 5;` shows real scout slugs (not empty). **Paste output.**
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_sp_signal_playbook.py -v` — all 9 backend tests pass. **Paste.**
4. `./scripts/check.sh` passes modulo known-exempt (j5b Jira + b3_consolidation). **Paste.**
5. **No regressions in existing signal_criteria route tests.** **Paste.**
6. **Manual UI smoke (Lead does this post-merge):**
   - Hard-reload `/#/signal-playbook` (or wherever the Marketing nav lands)
   - Verify the list renders with at least 5 reason codes from the existing DB
   - Click Edit on `POLICY_EDTECH_TIME_LIMIT` (or any code), change urgency or add a scout
   - Save, refresh, verify the change persists
   - Click "Export as markdown", verify a markdown file downloads with current state
   - **Paste a screenshot or DOM snippet showing the rendered playbook.**
7. `git diff --stat` + `git log --oneline -1` on `worker/sp-signal-playbook`. **Paste.**

---

## Hard constraints

- **Lossless invariant.** No hard-delete UI. Soft-retire only via `is_active=false`. DB trigger backstops this.
- **`code` + `domain` immutable after creation.** Existing route constraint stays.
- **Validation against known scout slugs.** Either hardcoded list (clean, fast) OR query `agents WHERE agent_id LIKE 'marketing.scout.%'` (dynamic but more code). Brief recommends hardcoded for v1 — re-evaluate when adding new scouts.
- **Validation against campaign families.** Verify the canonical list (probably in `artemis/marketing/seeds/` or hardcoded enum somewhere).
- **`updated_at` auto-bumps on PATCH.** Existing DB trigger or SQLAlchemy hook handles this — verify.
- **Markdown export is one-way.** No file write from API. Operator downloads + commits to git separately if they want.
- **Per locked design D:** structured CRUD only. NO raw markdown editing in the UI. Operator never types curly braces or pipes.
- **UI follows existing design language.** Same component patterns as Memory shell, Proposals Inbox, etc. No new visual primitives.
- **Local-only git.** Worker commits on `worker/sp-signal-playbook`; terminal-Lead merges after Lead approves.

---

## Future work this brief explicitly defers

Per signal-playbook-design.md, the Playbook becomes the full editable face of Josh's spec over time. v1 (this brief) is reason codes only. Future tabs/sections:

- **Territory config** — priority states, watchlist district criteria
- **Qualifier rules** — boost/suppress/skip patterns
- **Per-state nuances** — state-specific override rules

These ship as additional Playbook tabs in future briefs. They will follow the same Option B pattern (table canonical, markdown = export). Don't build them now.

---

## Report-back format

```
SP — Signal Playbook report
1. Commit / branch / worktree
2. LOC diff stats per file (backend + frontend split)
3. Migration apply confirmation + backfill verification
4. Tests added + pass count (9 backend tests)
5. Existing route regression check (no breaks on signal_criteria.py tests)
6. UI smoke result — PASTE screenshot OR DOM snippet
7. Markdown export sample — PASTE a few lines of the generated markdown
8. check.sh summary
9. Anything surprising — especially around the scout slugs / campaign families validation lists OR existing immutability constraints OR the backfill from josh_spec.py
```

---

**Worker: SP closes the operator-facing-spec-editing gap that's been queued since 2026-05-26. After this lands, Josh / Anne Marie can update signal criteria through a UI; every scout + qualifier picks up the changes on the next run; the markdown spec becomes a generated snapshot rather than a fragile source-of-truth. Per master plan priority order, SP fires next; PIPE6 (D6 lock — Workflows + Automations sunset) follows after.**
