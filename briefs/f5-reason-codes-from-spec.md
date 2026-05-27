# F5 — reason_codes_emitted Derived From Josh's Spec (Option A)

**Paste-into:** Codex (new session)
**Target branch:** `lead/j6a-granola-integration` (Codex commits directly)
**Browser smoke owner:** n/a (no UI)
**Report back to me by:** Jon pastes the report into Lead chat
**LOC cap:** 120 (full-diff insertions including test changes). Hard stop at 160.

---

## Why this brief exists

After P1 removed the stale inline reason-code tables from scout blueprints, the `agents.reason_codes_emitted` column entered an inconsistent state:
- **Fresh seed** → empty column (blueprints no longer carry the tables).
- **Live dev DB** → STALE codes preserved by the seed's CASE-preservation logic across re-seeds (codes sourced from the *pre-P1* blueprint tables).

That divergence is a bug: in the live DB, `agent_executor` reads the stale column codes and injects them into the prompt alongside F2's correct Josh-spec codes — two competing sources.

**Decision (Jon, via Lead, 2026-05-26): Option A — the column becomes a derived cache of Josh's spec.** Josh's spec (`decisions/campaign-signal-spec-v1.md`, via the "Primary scouts" column) is the single authoritative source. The seed sources the column from the spec; there is no per-agent override (operators edit the spec, not the agent row). This makes the column truthful + queryable + consistent with F2 (`_build_system_prompt`) and with the `signal_queue.write` tool's allowlist enforcement.

---

## Scope

### Part A — Source `reason_codes_emitted` from Josh's spec in the seed

In `artemis/marketing/seeds/marketing_agents.py`, `_row()` currently does:

```python
"reason_codes_emitted": _reason_codes_emitted(
    _extract_section(markdown, "Reason codes emitted")
),
```

Change it so that **for scout agents** (`agent_id.startswith("marketing.scout.")`), the codes come from Josh's spec:

```python
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout

# at module level or computed once per seed run:
_SPEC = parse_spec()

# in _row():
slug = spec.agent_id.rsplit(".", 1)[-1]
if spec.agent_id.startswith("marketing.scout."):
    reason_codes = [rc.code for rc in reason_codes_for_scout(_SPEC, slug)]
else:
    reason_codes = []   # qualifier/content agents don't emit signals
...
"reason_codes_emitted": reason_codes,
```

(Note the variable shadowing: the function param is named `spec` — a `MarketingAgentSpec`. Don't collide with the Josh `JoshSpec`. Use a distinct name like `_josh_spec` / `_SPEC`.)

The `_reason_codes_emitted()` helper (the blueprint-table parser) is now unused — delete it OR leave it with a deprecation note; your call, but don't let it cause a lint failure.

### Part B — Make the seed overwrite reason_codes_emitted (remove override preservation)

The ON CONFLICT clause currently preserves existing non-empty values:

```sql
reason_codes_emitted = CASE
    WHEN agents.reason_codes_emitted = '[]'::jsonb
    THEN EXCLUDED.reason_codes_emitted
    ELSE agents.reason_codes_emitted
END,
```

Replace with a plain overwrite (Josh's spec is authoritative — no per-agent override):

```sql
reason_codes_emitted = EXCLUDED.reason_codes_emitted,
```

This clears the stale live-DB values on re-seed and keeps the column in sync with the spec.

### Part C — Re-seed to clear stale values

After the code change, run the re-seed against the live dev DB so the column reflects the spec:

```bash
uv run python -c "
import asyncio
from artemis.db import SessionLocal
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents
async def main():
    async with SessionLocal() as s:
        print(await seed_marketing_agents(s)); await s.commit()
asyncio.run(main())
"
```

### Part D — Update Test A

`artemis/marketing/tests/test_marketing_agents_seed.py::test_scout_reason_codes_emitted_seeded_and_override_preserved`:

1. Rename to `test_scout_reason_codes_emitted_sourced_from_josh_spec`.
2. First assertion: after fresh seed, `marketing.scout.starbridge_researcher` has `reason_codes_emitted` equal to the codes Josh's spec assigns it via the Primary scouts column. Compute the expected list with `reason_codes_for_scout(parse_spec(), "starbridge_researcher")` rather than hardcoding (so it stays correct if Josh edits the spec). Assert the seeded column equals `[rc.code for rc in that]`.
3. Replace the override-preservation half with an **overwrite** assertion: manually set the column to `["CUSTOM_CODE"]`, re-seed, assert it's been overwritten back to the Josh-spec codes (NOT preserved). This locks in "spec is authoritative, no per-agent override."

Also check: is there a separate test asserting the OLD override-preservation behavior elsewhere? If so, update it to the overwrite semantics too. Grep for `reason_codes_emitted` in the test file.

### Part E — Verify the other reason-code tests still pass

P2's `signal_queue.write` tool reads the allowlist from Josh's spec directly (not the column), so it's unaffected. F2's `_build_system_prompt` reads the spec directly too. Run the full marketing + tools + builders test subset to confirm no regression:

```bash
ARTEMIS_TEST_DB_URL="postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test" uv run pytest artemis/marketing/tests/ artemis/tools/tests/ artemis/builders/tests/ -q
```

---

## Files owned by this stream

- EDIT: `artemis/marketing/seeds/marketing_agents.py` (Parts A, B)
- EDIT: `artemis/marketing/tests/test_marketing_agents_seed.py` (Part D)

**Do not touch:** any tool file, executor.py, blueprint markdown, Josh's spec, agent_executor.py.

---

## Acceptance criteria (Worker must demonstrate each)

1. **DB reflects spec after re-seed:**
   ```bash
   psql -d artemis_os -t -A -F'|' -c "SELECT agent_id, reason_codes_emitted FROM agents WHERE agent_id IN ('marketing.scout.starbridge_researcher','marketing.scout.regional_news','marketing.qualifier.cross_reference') ORDER BY agent_id;"
   ```
   **Paste output.** starbridge + regional_news show their Josh-spec codes; cross_reference shows `[]`.
2. **No stale values:** confirm regional_news codes now match `reason_codes_for_scout(parse_spec(),'regional_news')` exactly (not the old 5-code blueprint set if it differs). **Paste a one-line comparison.**
3. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/marketing/tests/test_marketing_agents_seed.py -v` — Test A (renamed) passes. **Paste the test line.**
4. The broader subset (Part E) passes. **Paste the summary.**
5. `./scripts/check.sh` — confirm the previously-failing `test_scout_reason_codes_emitted_*` is now green; the only remaining failures should be the known-exempt j5b Jira flake and the pre-existing m5b FK-isolation test (both banked). **Paste the final summary + which failures remain.**
6. `git diff --stat` ≤ 120 (160 hard stop). **Paste it.**
7. `git log --oneline -1`. **Paste it.**

---

## Hard constraints

- LOC cap: 120 (160 hard stop).
- Do not touch tool files, executor.py, agent_executor.py, or blueprint markdown.
- Do not change the agents table schema.
- Local-only git. Codex commits directly on `lead/j6a-granola-integration`.

---

## Report-back format (Worker pastes verbatim, filled in)

```
F5 — reason_codes_emitted from spec report

1. Commit hash:
2. LOC diff stats:
3. DB reflects spec (acceptance #1):
4. Stale-value comparison (acceptance #2):
5. Test A renamed + passing (acceptance #3):
6. Broader subset pass (acceptance #4):
7. check.sh + remaining failures (acceptance #5):
8. Anything surprising:
```

---

**End of brief. Codex: operating principle — compute expected codes from the spec, don't hardcode. Verify the re-seed actually cleared the stale live-DB values (don't assume the overwrite worked — query it).**
