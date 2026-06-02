# M1 — Reason-code registry table + FK validation + Josh seed loader

**Owner:** Lead designs (this brief is the design); Worker implements. ~180 LOC cap. Half-day Worker time.
**Depends on:** none — clean alembic chain after `0030_agent_persona_jsonb`.
**Blocks:** **M2** (qualifier boost/suppress rules need a reason-code registry to reference) and **M5** (scout fixtures cannot ship without canonical codes to emit).

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

Node slice 13 (Signal Criteria / Scout Ruleset Lite) shipped a first-class `signal_reason_codes` registry table. The Python rebuild silently dropped it — only `signal_queue.reason_codes` (free-form JSONB) survived the migration. The damage:

1. **Josh's 17 canonical reason codes** in `decisions/campaign-signal-spec-v1.md` §2 (POLICY_LIT_MANDATE … LEADER_TRANSITION_INTERIM) have nowhere to land. They're authoritative product spec — they belong in a queryable table, not a markdown file.
2. **Invariant I-10** (every signal's `reason_codes[].code` must FK into the registry) is unenforceable. Today `POST /api/signal-queue/intake` accepts any string in `reason_codes[].code`. A scout can hallucinate a code; nothing rejects it.
3. **The Qualifier (M4) can't route on reason code without knowing what codes exist.** Boost rules like "TX_HB1416_WAIVER → always hot" (§4.3) need to assert the code is real before applying the boost.
4. **Scout agents (M5) need a deterministic emitter list.** Their system prompts will reference codes by name — the registry is the single source of truth so prompts and the runtime agree.

This is the gating brief for the Marketing slab. M2 and M5 cannot ship until it lands.

`docs/marketing-slab-grounding.md` §2.1 captures the same regression. This brief closes it.

## Vision — what the user experience looks like

There is no UI surface in M1. The contract is API + DB. A future Ruleset Manager Agent (Layer 2, M5) will read/write through the routes below. For v1 the routes are admin-only — Josh edits codes via curl or via the Ruleset Manager once it ships.

A scout posting a signal with an unknown code now fails fast:

```bash
curl -X POST http://localhost:9009/api/signal-queue/intake \
  -H "Content-Type: application/json" \
  -d '{"signal": {"reason_codes": [{"code": "FAKE_CODE", "evidence_quote": "..."}], ...}}'
# → 400 {"error": "unknown reason codes", "codes": ["FAKE_CODE"]}
```

A scout posting with a valid code succeeds as before.

## Architecture

### Schema additions

```sql
-- New migration: 0031_signal_reason_codes_registry.py
CREATE TABLE signal_reason_codes (
    code                    TEXT PRIMARY KEY,                  -- SCREAMING_SNAKE_CASE, immutable
    domain                  TEXT NOT NULL,                     -- POLICY | FUNDING | VENDOR | DISTRICT | PROCUREMENT | TX | LEADER
    description             TEXT,                              -- Josh's "plain-English trigger"
    what_scout_looks_for    TEXT,                              -- Josh's "what the scout looks for"
    default_urgency         TEXT,                              -- Josh's default tier hint (free-form text — supports "hot at PASSED_CHAMBER")
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Append-only enforcement: no hard deletes.
CREATE OR REPLACE FUNCTION signal_reason_codes_no_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'signal_reason_codes is append-only — soft-delete via is_active = false';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER signal_reason_codes_block_delete
    BEFORE DELETE ON signal_reason_codes
    FOR EACH ROW EXECUTE FUNCTION signal_reason_codes_no_delete();
```

Down-migration drops the trigger, function, and table in that order.

Soft-delete is via `is_active = false`. There is **no route to hard-delete**. The trigger is belt-and-suspenders against direct SQL.

### ORM model

Add `SignalReasonCode` to `artemis/marketing/models.py` (next to `Ruleset`, `TerritoryConfig`):

```python
class SignalReasonCode(Base):
    __tablename__ = "signal_reason_codes"
    code: Mapped[str] = mapped_column(Text, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    what_scout_looks_for: Mapped[str | None] = mapped_column(Text)
    default_urgency: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
```

### Backend routes

Three additions in `artemis/marketing/routes/signal_criteria.py` (existing module — same `/api/signal-criteria` prefix, same `require_token` dep):

```
GET    /api/signal-criteria/reason-codes                  # list, default filters is_active=true; ?include_inactive=true to see all
POST   /api/signal-criteria/reason-codes                  # body: {code, domain, description?, what_scout_looks_for?, default_urgency?}
PATCH  /api/signal-criteria/reason-codes/{code}           # body: subset of {description, what_scout_looks_for, default_urgency, is_active}
```

`code` is immutable. `PATCH` rejects any attempt to mutate it (400). `domain` is also immutable in v1 (the 7-domain taxonomy is part of Josh's spec — domain drift would invalidate downstream routing).

Sort order: GET returns `ORDER BY domain ASC, code ASC`.

`POST` is upsert-by-code with `ON CONFLICT (code) DO NOTHING` returning 409 on duplicate, OR pure insert returning 201 — Worker picks. Idempotency is a property of the **seed loader**, not the route.

### FK validation in intake

`POST /api/signal-queue/intake` already calls `normalize_intake_payload` in `artemis/marketing/scout_intake.py`. Add a registry check **after** normalization:

```python
# pseudocode — Worker writes the real thing
codes_in_payload = [rc["code"] for rc in normalized.reason_codes if rc.get("code")]
if codes_in_payload:
    active_codes = set(await fetch_active_reason_codes(session))
    unknown = [c for c in codes_in_payload if c not in active_codes]
    if unknown:
        raise HTTPException(400, {"error": "unknown reason codes", "codes": unknown})
```

The check runs **on intake only**. Existing rows in `signal_queue` are NOT migrated or backfilled — log a one-line warning at startup if any existing rows reference codes that aren't in the registry. Don't crash.

The same check does NOT run on `/qualify` or any read path — we trust what's already persisted, even if a code was later deactivated.

### Seed loader

A new module `artemis/marketing/seeds/reason_codes.py` exposes:

```python
JOSH_SPEC_V1: list[dict] = [
    {"code": "POLICY_LIT_MANDATE",      "domain": "POLICY",      "description": "...", "what_scout_looks_for": "...", "default_urgency": "..."},
    # ... 17 entries total, paste verbatim from decisions/campaign-signal-spec-v1.md §2
]

async def seed_reason_codes(session: AsyncSession) -> dict:
    """Idempotent upsert. Returns {"inserted": N, "updated": M, "skipped": K}."""
```

Idempotency: `INSERT … ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description, ...` — re-running is a no-op when nothing changed. Worker decides whether to update or skip on conflict; preference is **skip** (treat seed as initial-load only, edits go through PATCH).

Wire the loader into the existing seed-runner CLI if one exists (grep for `seed_marketing` / `seed_territory`); else expose as a callable from `artemis/marketing/seeds/__init__.py` and document the one-liner in the brief's smoke section.

### The 17 codes (paste verbatim into the seed module)

From `decisions/campaign-signal-spec-v1.md` §2 — Worker copies the description and what_scout_looks_for columns verbatim:

| Code | Domain | Default urgency (verbatim from spec) |
|---|---|---|
| POLICY_LIT_MANDATE | POLICY | hot at PASSED_CHAMBER or ENACTED; standard at INTRODUCED |
| POLICY_EDTECH_TIME_LIMIT | POLICY | standard; hot if bill is statewide and includes K–3 |
| FUNDING_LITERACY_GRANT | FUNDING | hot if deadline ≤ 30 days; standard if 30–90; enrichment otherwise |
| FUNDING_DEADLINE_NEAR | FUNDING | hot ≤ 30 days, standard 30–90 |
| FUNDING_HB2_ELIA | FUNDING | enrichment (context only — not a discrete event) |
| VENDOR_APPROVED_LIST | VENDOR | hot |
| VENDOR_DISSATISFACTION | VENDOR | standard; hot if board votes non-renewal or RFP follows |
| DISTRICT_STRATEGIC_LITERACY | DISTRICT | standard |
| DISTRICT_PROFICIENCY_GAP | DISTRICT | standard; hot if paired with vendor dissatisfaction or RFP |
| DISTRICT_DLL_EXPANSION | DISTRICT | standard |
| DISTRICT_MTSS_STRAIN | DISTRICT | standard |
| PROCUREMENT_ELA_ADOPTION | PROCUREMENT | standard; hot when RFP posts |
| PROCUREMENT_LITERACY_RFP | PROCUREMENT | hot if days_to_close ≤ 14; standard 15–45; reject > 45 unless strategic |
| TX_HB1416_WAIVER | TX | hot |
| TX_HB3_DYSLEXIA_COMPLIANCE | TX | hot |
| LEADER_TRANSITION_FORMAL | LEADER | hot for 90 days post-hire |
| LEADER_TRANSITION_INTERIM | LEADER | standard |

## Hard constraints

- **Total scope cap: 180 LOC** across migration + model + routes + seed + tests. Self-report via full-diff insertion counts (`git diff --staged --stat` + `git diff --staged | grep -c '^+[^+]'`).
- **Single migration.** Single commit. Message: `feat(m1): signal_reason_codes registry + FK validation + Josh seed loader`
- `git diff --staged` before commit. `pwd && git branch --show-current` before commit.
- Worker runs in isolated worktree off `lead/j6a-granola-integration` HEAD. Background execution. Branch auto-creates. Worker does NOT self-merge to lead.

## Acceptance criteria

- [ ] Alembic migration `0031_signal_reason_codes_registry.py` up/down round-trips clean (verify with `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`)
- [ ] 17 codes seeded from Josh's spec §2 (verbatim list above) — Worker pastes from `decisions/campaign-signal-spec-v1.md` §2 with no rewording
- [ ] `GET /api/signal-criteria/reason-codes` returns all 17, sorted by `domain` ASC then `code` ASC
- [ ] `POST /api/signal-criteria/reason-codes` with a new code → appears in subsequent GET
- [ ] `PATCH /api/signal-criteria/reason-codes/POLICY_LIT_MANDATE` with `{"is_active": false}` → code disappears from default GET, FK validation rejects it on intake
- [ ] `PATCH` attempt to mutate `code` field → 400
- [ ] `PATCH` attempt to mutate `domain` field → 400 (v1 immutability)
- [ ] FK validation: `POST /api/signal-queue/intake` body containing `reason_codes: [{"code": "FAKE_CODE", ...}]` → 400 with body `{"error": "unknown reason codes", "codes": ["FAKE_CODE"]}`
- [ ] FK validation: same intake with valid code (e.g. `POLICY_LIT_MANDATE`) → 201 (or whatever intake's existing happy-path status is)
- [ ] FK validation does NOT run on `/qualify` or read paths — existing rows with deactivated codes still readable
- [ ] Direct `DELETE FROM signal_reason_codes WHERE code = '...'` raises the trigger error (test via raw SQL)
- [ ] Idempotency: re-running `seed_reason_codes()` is a no-op — no duplicate rows, no errors, return dict reports `inserted: 0` on second run
- [ ] Tests: minimum 5 cases — (a) GET returns 17 sorted, (b) POST new code roundtrips, (c) PATCH `is_active=false` hides code + rejects intake, (d) FK validation rejects unknown code with proper 400 body, (e) seed loader idempotent on second call

## Quality acceptance gates

- [ ] `git diff --staged` before the commit (twice-bitten rule).
- [ ] `pwd && git branch --show-current` before commit (CWD-trap defensive reflex).
- [ ] Alembic up/down clean.
- [ ] `ruff check` + `mypy` clean.
- [ ] LOC self-report via full-diff insertion count, NOT estimation. Cap is 180 — Worker stops and flags Lead if approaching.
- [ ] Manual smoke: paste the two curl invocations (rejected FAKE_CODE → 400, valid code → 201) into the report verbatim with response bodies.

## Out of scope (separate briefs)

- **Ruleset Manager Agent UI** — the chat surface for editing reason codes lives in M5/M6 (Layer 2 agent definitions). M1 ships the API only.
- **Proposed-code workflow** — schema doc `docs/marketing-ops-v1/schemas/reason-codes.md` describes a `proposed_reason_codes` table for human review of scout-invented codes. Defer to M3.
- **Existing-row migration / backfill** — log warning at startup, do not rewrite history.
- **Append-only `qualifier_decisions` table** — that's M4's concern, not M1's.
- **`district_roster` table for `geography.district_id` FK** — separate Layer 1 brief.
- **Boost/suppress rule layer** — M2.

## Where to start

1. Read this brief twice
2. Read `briefs/CONVENTIONS.md` ("CWD trap" + path conventions — non-optional)
3. Read `decisions/campaign-signal-spec-v1.md` §2 (source of truth for the 17 codes)
4. Read `artemis/marketing/routes/signal_criteria.py` for the existing route style + auth pattern
5. Read `artemis/marketing/routes/signal_queue.py` `intake()` for where FK validation slots in
6. Read `artemis/marketing/scout_intake.py` `normalize_intake_payload` — the FK check happens after this returns
7. Read `alembic/versions/0030_agent_persona_jsonb.py` for the latest migration's revision id (your `down_revision`)
8. Implement order: migration → model → seed loader (with the 17 codes) → routes → intake FK check → tests
9. Run the acceptance checklist top to bottom before reporting
10. Surface any deviations clearly

## Paste-ready Worker prompt (for terminal-Lead to spawn)

```
Implement briefs/m1-reason-code-registry.md.

Scope cap: 180 LOC TOTAL across migration + model + routes + seed + tests.
Self-report LOC via full-diff insertion count (git diff --staged | grep -c
'^+[^+]'), not estimation. If you approach 180, stop and flag Lead.

Isolated worktree off lead/j6a-granola-integration HEAD. Background
execution. Branch auto-creates. Do NOT self-merge to lead.

CRITICAL framing:
- This is the gating brief for M2 + M5. Both depend on signal_reason_codes
  existing with the 17 codes seeded.
- The 17 codes come from decisions/campaign-signal-spec-v1.md §2. Paste
  description + what_scout_looks_for VERBATIM — no rewording, no
  summarization. The default_urgency column is free-form text and may
  contain the verbatim phrases from the spec (e.g. "hot at PASSED_CHAMBER
  or ENACTED; standard at INTRODUCED").
- Append-only: no DELETE route, ON DELETE trigger raises. Soft-delete via
  is_active = false only.
- CWD-trap reflex: pwd && git branch --show-current before commit.
- git diff --staged before commit, every time.
- Single commit, message exactly:
  feat(m1): signal_reason_codes registry + FK validation + Josh seed loader

The brief has a paste-ready acceptance checklist. Run each bullet against
the running app (uvicorn artemis.app:app) before reporting done. No
analytical-instead-of-empirical shortcuts.

Report when complete:
- Branch name + final SHA
- Full-diff LOC count (inserted lines, by file)
- Acceptance checklist verbatim with each bullet green or explained
- Manual smoke: curl FK rejection (FAKE_CODE → 400) and curl FK happy
  path (valid code → 201), response bodies pasted verbatim
- alembic upgrade head + alembic downgrade -1 + alembic upgrade head
  output pasted
```
