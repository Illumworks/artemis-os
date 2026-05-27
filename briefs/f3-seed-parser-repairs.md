# F3 — Seed Parser Repairs (urgency_tiers / failure_modes / implementation_notes)

**Paste-into:** Codex (new session)
**Target branch:** `lead/j6a-granola-integration` (Codex commits directly — no isolated worktree)
**Browser smoke owner:** n/a (no UI surface)
**Report back to me by:** Jon pastes the Worker's report into Lead chat
**LOC cap:** 200 (full-diff insertions including tests). Hard stop at 250.

---

## Why this brief exists

The seed loader `artemis/marketing/seeds/marketing_agents.py` parses 12+ structured fields from each agent blueprint markdown via section-extracting regex. Audit (2026-05-26) shows that for all 16 marketing agents in the live DB:

- `urgency_tiers` — 0/16 populated
- `failure_modes` — 0/16 populated
- `implementation_notes` — 0/16 populated
- `lifecycle_status` — 0/16 populated
- `reason_codes_emitted` — 1/16 populated (only `marketing.scout.legislative`; rest are NULL even though the blueprint markdown has the table)

Root cause: the existing regex helpers `_urgency_tiers()` and `_failure_modes()` in `marketing_agents.py` require `**bolded-name**` markdown prefixes on each bullet (pattern: `\*\*(?P<tier>[^*]+)\*\*\s*[—:-]`). The actual blueprints use plain bullets (`- Formal RFPs`, `- PDF extraction garbage from scanned image → emit ...`). So 100% of these sections fail to parse silently. Plus the seed appears to have not re-run since the loader was extended (explains the reason_codes_emitted gap — but F1 doesn't fix that because we're now reading codes from Josh's spec at runtime, not from blueprint markdown).

Note: this work is forward-compatible with P1/P4's blueprint rewrite. After F3 + P1/P4 ship together, blueprints can use either plain-bullet or `**name** — body` format and the regex parses both. P1/P4's rewritten blueprints will use the cleaner format the regex prefers.

---

## Scope

### Part A — Fix `_urgency_tiers()` regex

Current code at `marketing_agents.py` ~line 136-142:

```python
def _urgency_tiers(section: str) -> dict[str, str] | None:
    tiers: dict[str, str] = {}
    for bullet in _bullets(section):
        match = re.match(r"\*\*(?P<tier>[^*]+)\*\*\s*[—:-]\s*(?P<body>.+)", bullet, re.DOTALL)
        if match:
            tiers[match.group("tier").strip().lower()] = " ".join(match.group("body").split())
    return tiers or None
```

Make it accept **three** bullet formats:

1. **Bolded-name colon/dash form** (existing): `**hot** — Description here`
2. **Plain inline tier-name form**: `Hot: Description here` (treat first colon/dash as separator)
3. **Plain tier-reserve form** (this is what the blueprints use): when the section's lead-in text says something like "Reserve hot for:" or "Speculation = standard.", treat the BULLETS as examples of the tier mentioned in the lead-in text. e.g. for regional_news's blueprint:
   ```
   ## Urgency tiers (this scout)

   Conservative with hot priority. Speculation = standard. Reserve hot for:
   - Formal RFPs
   - Board votes (passed)
   - Official transitions (announced)
   - Gubernatorial directives
   ```
   The lead-in `Reserve hot for:` says the bullets are HOT criteria. Output: `{"hot": "Formal RFPs; Board votes (passed); Official transitions (announced); Gubernatorial directives", "standard": "speculation"}` (synthesized from the lead-in's "Speculation = standard.").

For Part A, **implement forms 1 and 2 robustly, and form 3 as best-effort**:
- If the section begins with prose ending in `Reserve <tier> for:` (case-insensitive), all subsequent bullets are joined into that tier.
- If the section's lead-in mentions another tier inline (e.g. `Speculation = standard.`), add that mapping too.
- If neither pattern matches, the regex falls back to extracting nothing from that section (graceful — function returns None, field stays NULL, no crash).

Add a small helper:

```python
def _urgency_tiers_v2(section: str) -> dict[str, str] | None:
    """Permissive parser: accepts bold-bullet, plain-bullet with colon, and reserve-for-X prose+bullets."""
    ...
```

Replace `_urgency_tiers` calls with `_urgency_tiers_v2`. Keep `_urgency_tiers` as a deprecated alias for backward-compat in tests.

### Part B — Fix `_failure_modes()` regex

Current code at ~line 145-161 tries two patterns:
1. `\*\*(?P<name>[^*]+)\*\*\s*(?:[—:-]|→)\s*(?P<body>.+)` — requires `**bold**` name prefix
2. Fallback: `(?P<name>.+?)\s*(?:[—:-]|→)\s*(?P<body>.+)` — any name then separator

The fallback (#2) SHOULD work for plain bullets like `PDF extraction garbage from scanned image → emit source_quality_low flag.` Let's verify with a direct call before claiming it's broken.

**Action:** Add a unit test that calls `_failure_modes` with the actual `## Failure modes` section text from `docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md`. If the test fails, identify the gap. If it passes, the failure to populate `failure_modes` in DB is purely a "seed wasn't re-run" issue, not a regex bug. Either way, the test serves as a regression guard.

### Part C — `_extract_section` header flexibility

The implementation_notes section in `regional_news` blueprint is `## Implementation notes for Codex`. Other blueprints may use `## Implementation notes` (no `for Codex` suffix). The existing `_extract_section` is called with literal string `"Implementation notes for Codex"` — too rigid.

**Action:** Modify `_row()` (~line 255) to try both headings:

```python
"implementation_notes": (
    _extract_section(markdown, "Implementation notes for Codex")
    or _extract_section(markdown, "Implementation notes")
    or None
),
```

That's a 2-line delta. Done.

### Part D — Lifecycle status

The `_status()` parser at ~line 122 looks for `**Status:**` header. Inspect all 16 blueprint markdowns to see if this header exists. If not, that's why `lifecycle_status` is NULL for all 16 — the blueprints don't declare a status, and the field stays None (which is fine; the field is nullable). **Document this in the report** as a finding but do not invent status values. P1/P4 may add `**Status:** active` to blueprints if Jon wants, but that's their scope, not yours.

### Part E — Re-seed and verify

After fixes, re-run the seed:

```bash
uv run python -c "
import asyncio
from artemis.db import get_session_factory
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents
async def main():
    SessionLocal = get_session_factory()
    async with SessionLocal() as s:
        result = await seed_marketing_agents(s)
        await s.commit()
        print(result)
asyncio.run(main())
"
```

Then query the DB to confirm fields populated:

```sql
SELECT agent_id,
       urgency_tiers IS NOT NULL AS has_urgency,
       failure_modes IS NOT NULL AS has_failure,
       implementation_notes IS NOT NULL AS has_notes
FROM agents
WHERE agent_id LIKE 'marketing.%'
ORDER BY agent_id;
```

Acceptance bar: at least **12 of 16** agents have non-null urgency_tiers AND failure_modes after re-seed. (Some blueprints may have weak/empty sections — that's OK; the goal is the regex works, not that every blueprint has rich content. P1/P4 fills in the thin ones.)

### Part F — Tests

Extend `artemis/marketing/tests/test_marketing_agents_seed.py` (create if absent):

1. **`_urgency_tiers_v2` happy path — bold form.** Input: `- **hot** — RFPs and board votes\n- **standard** — speculation`. Assert returns `{"hot": "RFPs and board votes", "standard": "speculation"}`.
2. **`_urgency_tiers_v2` happy path — reserve-for form.** Input: the verbatim `## Urgency tiers` section from regional_news blueprint. Assert returns a dict with `"hot"` key whose value contains all 4 bullets joined with `; `, and `"standard"` key from "Speculation = standard." inline.
3. **`_urgency_tiers_v2` graceful empty.** Input: empty string. Assert returns None.
4. **`_failure_modes` regression test.** Input: the verbatim `## Failure modes` section from regional_news blueprint. Assert returns a non-empty list with at least 4 entries, each having `name` and `description`.
5. **Implementation notes header fallback.** Mock a blueprint markdown with only `## Implementation notes` (no `for Codex`). Assert `_row()` extracts it.
6. **Re-seed idempotency.** Call `seed_marketing_agents(session)` twice. Assert the second call's `updated` count equals 16 (all rows updated, not inserted).

---

## Files owned by this stream

- EDIT: `artemis/marketing/seeds/marketing_agents.py` (Parts A, B if needed, C)
- EDIT or NEW: `artemis/marketing/tests/test_marketing_agents_seed.py`

**Do not touch any other files.** Especially do not:
- Touch any `docs/marketing-ops-v1/agents/*.md` (P1/P4 streams)
- Touch `artemis/marketing/josh_spec.py` (F1)
- Touch `artemis/builders/executor.py` (F2)
- Modify the agent table schema
- Add or run any migrations

---

## Acceptance criteria (Worker must demonstrate each)

1. `uv run pytest artemis/marketing/tests/test_marketing_agents_seed.py -v` shows all 6 tests passing. **Paste the test summary.**
2. After re-seed, the DB query returns ≥12 agents with non-null urgency_tiers AND ≥12 with non-null failure_modes. **Paste the full query result.**
3. `_urgency_tiers_v2` smoke against the regional_news blueprint section:
   ```bash
   uv run python -c "
   from pathlib import Path
   from artemis.marketing.seeds.marketing_agents import _urgency_tiers_v2, _extract_section
   md = Path('docs/marketing-ops-v1/agents/scout/1.2-regional-news-scout.md').read_text()
   section = _extract_section(md, 'Urgency tiers')
   print(_urgency_tiers_v2(section))
   "
   ```
   **Paste the output.** Should show a dict with `hot` key.
4. `./scripts/check.sh` passes (modulo the known pre-existing j5b Jira flake). **Paste the final summary line.**
5. `git diff --stat` showing full-diff insertions ≤ 200 (250 hard stop). **Paste it.**
6. `git log --oneline -1` showing the new commit on `lead/j6a-granola-integration`. **Paste it.**
7. Finding on `lifecycle_status` per Part D — how many blueprints have `**Status:**` header. **Paste a one-line summary.**

---

## Hard constraints

- LOC cap: 200 (250 hard stop).
- Do not polish unrelated code.
- Do not modify blueprint markdown files.
- Do not change the agent table schema.
- Local-only git. No `git push`.
- Codex commits directly on `lead/j6a-granola-integration`.

---

## Report-back format (Worker pastes this verbatim, filled in)

```
F3 — Seed Parser Repairs report

1. Commit hash:                <git log -1 --format=%H>
2. LOC diff stats:             <git diff --stat>
3. Files changed:              <numbered list>
4. Test pass:                  <pytest summary line>
5. DB field population:        <full output of the SQL query in acceptance #2>
6. urgency_tiers_v2 smoke:     <stdout from acceptance #3>
7. check.sh:                   <final summary line>
8. lifecycle_status finding:   <one-line summary per Part D>
9. Anything surprising:        <free text>
10. _failure_modes regression: <was the existing fallback regex sufficient? yes/no + why>
```

---

**End of brief. Codex: do not start until you've read this top to bottom. Operating principle: never assume — if the existing `_failure_modes` regex works without changes, document that and move on; don't "improve" it unnecessarily.**
