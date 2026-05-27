# F1 — Josh-Spec Parser (Single Source of Truth)

**Paste-into:** Codex (new session)
**Target branch:** `lead/j6a-granola-integration` (Codex commits directly — no isolated worktree)
**Browser smoke owner:** n/a (no UI surface in this stream)
**Report back to me by:** Jon pastes the Worker's report into Lead chat
**LOC cap:** 300 (full-diff insertions including tests). Hard stop at 350.

---

## Why this brief exists

`decisions/campaign-signal-spec-v1.md` is Josh's canonical spec for reason codes, territory config, qualifier rules, and per-state nuance. Today it's duplicated and partial-copied across **four** places:

1. `decisions/campaign-signal-spec-v1.md` — the canonical doc
2. `docs/marketing-ops-v1/Campaign Signal Spec v1.md` — **byte-identical duplicate** (drift risk)
3. `artemis/marketing/seeds/reason_codes.py` — 17 codes re-encoded as a Python list
4. Each agent's blueprint markdown — partial inline tables of relevant codes

This brief consolidates: Josh's spec becomes the single source. A parser reads it. The reason-codes seed reads the parser. Future scouts read the parser at runtime (next stream). One file to edit. Edits reflow everywhere.

---

## Scope

### Part A — Add a "Primary scouts" column to Josh's spec

Edit `decisions/campaign-signal-spec-v1.md`. The §2 reason code registry table currently has 4 columns:

```
| Code | Plain-English trigger | What the scout looks for | Default urgency |
```

Add a 5th column at the end:

```
| Code | Plain-English trigger | What the scout looks for | Default urgency | Primary scouts |
```

Populate the column with the mappings below. Format: comma-separated scout slugs (no spaces around commas), matching the slugs in `artemis/marketing/seeds/marketing_agents.py` (the part after `marketing.scout.`).

**The mappings (use exactly these — they're inferred from Josh's "What the scout looks for" column, not from the stale blueprints):**

| Code | Primary scouts |
|---|---|
| POLICY_LIT_MANDATE | legislative,starbridge_researcher,state_doe |
| POLICY_EDTECH_TIME_LIMIT | legislative,regional_news,board_minutes |
| FUNDING_LITERACY_GRANT | federal_funding,starbridge_researcher,state_doe |
| FUNDING_DEADLINE_NEAR | federal_funding,starbridge_researcher |
| FUNDING_HB2_ELIA | board_minutes,legislative,starbridge_researcher |
| VENDOR_APPROVED_LIST | state_doe,procurement |
| VENDOR_DISSATISFACTION | regional_news,board_minutes,linkedin_observer |
| DISTRICT_STRATEGIC_LITERACY | board_minutes,regional_news |
| DISTRICT_PROFICIENCY_GAP | board_minutes,regional_news |
| DISTRICT_DLL_EXPANSION | board_minutes,regional_news |
| DISTRICT_MTSS_STRAIN | board_minutes,regional_news |
| PROCUREMENT_ELA_ADOPTION | board_minutes,procurement |
| PROCUREMENT_LITERACY_RFP | procurement,state_doe,starbridge_researcher |
| TX_HB1416_WAIVER | board_minutes,state_doe,regional_news |
| TX_HB3_DYSLEXIA_COMPLIANCE | board_minutes,state_doe |
| LEADER_TRANSITION_FORMAL | leadership_transition,regional_news,board_minutes |
| LEADER_TRANSITION_INTERIM | leadership_transition,regional_news,linkedin_observer |

**Editing rule:** Preserve all other content in the spec verbatim. Only Part A's task is the new column and its 17 cells. Do not rewrap lines, fix typos, or change other tables. The §3 campaign-type-mapping table at line 60-65 is a different table and stays unchanged.

### Part B — Build the parser

New file: `artemis/marketing/josh_spec.py`.

Public API (Codex implements exactly this signature):

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ReasonCodeSpec:
    code: str                     # e.g. "POLICY_LIT_MANDATE"
    domain: str                   # parsed from the code prefix: POLICY, FUNDING, VENDOR, DISTRICT, PROCUREMENT, TX, LEADER
    description: str              # "Plain-English trigger" column
    what_scout_looks_for: str     # "What the scout looks for" column
    default_urgency: str          # "Default urgency" column (free text — qualifier interprets at runtime)
    primary_scouts: tuple[str, ...]   # tuple of scout slugs from Part A's new column

@dataclass(frozen=True)
class TerritoryConfigSpec:
    priority_states: tuple[str, ...]              # ("FL", "IN", "MD", "MO", "IL", "TX")
    watchlist_districts_criteria: str             # the prose description from §1.watchlist_districts

@dataclass(frozen=True)
class CampaignTypeMapping:
    campaign_type: str            # e.g. "OBC"
    reason_codes: tuple[str, ...] # codes that emit this type
    watch_keywords: tuple[str, ...]

@dataclass(frozen=True)
class QualifierRule:
    layer: str                    # "skip" | "suppress" | "boost"
    name: str                     # short label like "HMH partner districts"
    description: str              # full text

@dataclass(frozen=True)
class StateNuance:
    state: str                    # "Florida" | "Texas" | "Indiana, Maryland, Missouri, Michigan, Illinois" | "All states"
    text: str                     # the full bullet-list text under that subheading in §5

@dataclass(frozen=True)
class JoshSpec:
    reason_codes: tuple[ReasonCodeSpec, ...]
    territory_config: TerritoryConfigSpec
    campaign_type_mappings: tuple[CampaignTypeMapping, ...]
    qualifier_rules: tuple[QualifierRule, ...]
    state_nuances: tuple[StateNuance, ...]
    raw_source_path: Path
    raw_source_hash: str          # sha256 of the file contents — for cache invalidation in runtime consumers

def parse_spec(path: Path | None = None) -> JoshSpec:
    """Parse Josh's spec from the canonical doc. Default path: decisions/campaign-signal-spec-v1.md."""
    ...

def reason_codes_for_scout(spec: JoshSpec, scout_slug: str) -> tuple[ReasonCodeSpec, ...]:
    """Return all reason codes whose primary_scouts contains scout_slug."""
    ...
```

**Implementation notes:**

- Use stdlib only (no markdown parser deps). Use regex for section extraction (`re.findall` on `## N\\. <heading>` and `| ... | ... |` table rows).
- The Markdown uses backslash-escaped numbers like `1\.`, `2\.` for section headers — handle this. Same for backslashed pipes inside table cells (rare but possible).
- The spec uses `:----` row separators for table headers — skip those.
- `default_urgency` is free text; do not parse it into structured tiers (that's the Qualifier's job at runtime).
- `state_nuances` text content includes the bullet-listed lines under each `## State` subheading — preserve formatting (don't strip bullets; the LLM consumer will format).
- For `qualifier_rules`, parse §4.1, §4.2, §4.3 subsections. §4.1 has a table → each row becomes a rule with `layer="skip"`. §4.2 and §4.3 are bullet lists → each `**Name**` bolded item becomes a rule.
- The `domain` field on `ReasonCodeSpec` is derived from the prefix before the first underscore (e.g. `POLICY_LIT_MANDATE` → `POLICY`, `TX_HB1416_WAIVER` → `TX`).

### Part C — Rewrite the reason-codes seed to consume the parser

`artemis/marketing/seeds/reason_codes.py` currently holds a hand-encoded `JOSH_SPEC_V1: list[dict[str, str]]` of 17 codes.

Rewrite it to:
1. Import and call `parse_spec()` from `artemis.marketing.josh_spec`.
2. Iterate the returned `ReasonCodeSpec` tuples.
3. Insert/upsert into the `signal_reason_codes` table using the existing INSERT statement shape (preserve the existing schema — do NOT change the table or migration).
4. Keep the `run_seed()` function signature and idempotency contract unchanged.
5. The inline `JOSH_SPEC_V1` list goes away.

**Schema compatibility check:** the existing `signal_reason_codes` table likely has columns matching `ReasonCodeSpec` minus `primary_scouts` (which is new). If the table doesn't have a `primary_scouts` column, **do not add a migration in this brief** — store the primary-scouts list as a JSONB column update later, or in this stream just ignore it on the seed (runtime consumers read the parser directly, not the DB). Pick whichever path keeps this brief under 300 LOC. Document the choice in your report-back.

### Part D — Delete the duplicate

```bash
git rm "docs/marketing-ops-v1/Campaign Signal Spec v1.md"
```

That's it — no replacement, no symlink. The canonical lives at `decisions/campaign-signal-spec-v1.md`.

### Part E — Tests

New file: `artemis/marketing/tests/test_josh_spec.py`.

Test cases:
1. `parse_spec()` with the default path returns a `JoshSpec` with `len(reason_codes) == 17`.
2. Every `ReasonCodeSpec` has all 6 fields populated (non-empty strings, non-empty tuple of primary_scouts).
3. `reason_codes_for_scout(spec, "regional_news")` returns at least 5 codes (per the mapping above: POLICY_EDTECH_TIME_LIMIT, VENDOR_DISSATISFACTION, DISTRICT_STRATEGIC_LITERACY, DISTRICT_PROFICIENCY_GAP, DISTRICT_DLL_EXPANSION, DISTRICT_MTSS_STRAIN, TX_HB1416_WAIVER, LEADER_TRANSITION_FORMAL, LEADER_TRANSITION_INTERIM).
4. `parse_spec()` returns a `TerritoryConfigSpec` with `priority_states == ("FL", "IN", "MD", "MO", "IL", "TX")`.
5. `parse_spec()` returns at least 3 entries each in `qualifier_rules` for layer="skip", "suppress", "boost".
6. `parse_spec()` returns `state_nuances` with at least entries for Florida, Texas, and "All states — vendor dissatisfaction".
7. `raw_source_hash` is a 64-char hex string (sha256).
8. Re-running the reason-codes seed (`artemis.marketing.seeds.reason_codes.run_seed()`) is idempotent — call it twice, row count stays 17.

Mock the file read with a real fixture if needed (or use the actual spec file; the file is checked in and stable).

---

## Files owned by this stream

- NEW: `artemis/marketing/josh_spec.py`
- NEW: `artemis/marketing/tests/test_josh_spec.py`
- EDIT: `decisions/campaign-signal-spec-v1.md` (add Primary scouts column — Part A)
- EDIT: `artemis/marketing/seeds/reason_codes.py` (refactor to read parser — Part C)
- DELETE: `docs/marketing-ops-v1/Campaign Signal Spec v1.md` (Part D)

**Do not touch any other files.** If you discover you need to edit a file outside this list, STOP and report back to Lead. No silent scope creep.

---

## Acceptance criteria (Worker must demonstrate each)

1. `uv run python -c "from artemis.marketing.josh_spec import parse_spec; s = parse_spec(); print(len(s.reason_codes), s.territory_config.priority_states)"` outputs `17 ('FL', 'IN', 'MD', 'MO', 'IL', 'TX')`. **Paste the actual output.**
2. `uv run python -c "from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout; s = parse_spec(); print([r.code for r in reason_codes_for_scout(s, 'regional_news')])"` shows at least 5 codes. **Paste the actual output.**
3. `uv run pytest artemis/marketing/tests/test_josh_spec.py -v` shows all 8 tests passing. **Paste the test summary line.**
4. `uv run alembic upgrade head` runs cleanly (no new migrations expected from this brief). **Paste the output.**
5. `uv run python -c "import asyncio; from artemis.marketing.seeds.reason_codes import run_seed; print(asyncio.run(run_seed()))"` returns a result dict and is idempotent on second call. **Paste both call outputs.**
6. `psql artemis_os -t -c "SELECT count(*) FROM signal_reason_codes;"` returns `17`. **Paste.**
7. `ls "docs/marketing-ops-v1/Campaign Signal Spec v1.md"` returns "No such file or directory". **Paste.**
8. `./scripts/check.sh` passes. **Paste the final summary line.**
9. `git diff --stat` showing full-diff insertions. **Paste it.** Total must be ≤ 300 lines (~350 hard stop).

---

## Hard constraints

- LOC cap: 300 (350 hard stop). At cap, commit what's done, ping back, do not push through.
- Do not polish error messages, docstrings beyond what tests verify, or log formatting.
- Do not change the `signal_reason_codes` table schema or add migrations.
- Do not edit any agent blueprint markdown files (P1/P4 streams handle those).
- Do not touch `artemis/marketing/seeds/marketing_agents.py` (F3 stream handles that).
- Do not edit `artemis/builders/executor.py` or `run_agent` (F2 stream handles that).
- Local-only git. No `git push`.

---

## Report-back format (paste this verbatim into your reply, filled in)

```
F1 — Josh-Spec Parser report

1. Commit hash:            <git log -1 --format=%H>
2. LOC diff stats:         <output of git diff --stat against the fork point>
3. Files changed:          <numbered list>
4. parse_spec() smoke:     <stdout from acceptance #1>
5. reason_codes_for_scout smoke: <stdout from acceptance #2>
6. Test pass:              <pytest summary line>
7. Alembic:                <stdout>
8. Seed idempotency:       <two run_seed() outputs>
9. DB row count:           <stdout>
10. Duplicate deleted:     <ls output>
11. check.sh:              <final summary line>
12. Anything surprising:   <free text>
13. signal_reason_codes schema decision: <"added primary_scouts column via migration X" OR "skipped DB storage of primary_scouts, runtime reads parser only">
```

---

**End of brief. Codex: do not start until you've read this top to bottom.**
