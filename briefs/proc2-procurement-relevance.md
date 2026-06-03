# PROC2 — Procurement relevance: education NAICS + scout-side gate (SAM.gov)

**Paste-into:** Codex.
**Recommended Codex model / effort:** `gpt-5.4` · `medium`. Iterative tuning against the live
SAM.gov API + a scout prompt nudge.
**Target branch:** `worker/proc2-procurement-relevance`
**No migration.** Touches `artemis/tools/procurement.py` (+ maybe the procurement scout prompt) +
tests.
**Fires:** now (no overlap with FIX115 or the intelligence work).
**LOC cap:** ~150.
**Priority:** MEDIUM — without this the procurement scout produces ~0 useful signals.

## Why
PROC1 connected `procurement_portal.fetch` to SAM.gov, but `keyword=literacy` returns irrelevant
results (DoD parts solicitations) — SAM keyword match is loose. The scout LLM filters the junk so no
garbage reaches the queue, but it also means **~0 useful education RFPs surface.**

## The fix (iterate against the live API — `SAM_API_KEY` is set)
- **NAICS filter:** SAM.gov supports a NAICS code param (verify exact name — `ncode`/`naics`). Add
  education NAICS to the default query: `611110` (elementary & secondary schools), `611710`
  (educational support services), and evaluate `611310`, `611691`, `624310`, etc. Test which codes
  actually surface K-12 literacy/curriculum/assessment opportunities.
- **Title vs keyword:** evaluate `title=` search (tighter) vs `keyword=` (loose). Use whichever
  returns domain-relevant results; consider combining NAICS + a focused keyword.
- **Scout-side gate (defense in depth):** confirm/tighten the procurement scout prompt so it only
  emits signals for genuinely education-relevant opportunities (it already filters, but make the
  relevance bar explicit).
- Keep the PROC1 safety behavior: stub-until-key, `[]` on error, never raise, `ScoutHttpClient`
  rate-limit.

## Acceptance
1. **Live smoke:** with `SAM_API_KEY`, run the tuned tool → **paste real, education-relevant
   opportunities** (literacy/curriculum/assessment RFPs), not DoD parts. State the exact params
   (NAICS codes + keyword/title) that worked.
2. If education RFPs are genuinely sparse right now, show the query is correctly scoped (e.g. returns
   education-category results even if few) and note the sparsity — don't fake relevance.
3. Tests (mocked) still pass + a test for the NAICS param being sent. `./scripts/check.sh` (j5b exempt). **Paste.**
4. **COMMIT on the branch, local git only.** Message ends `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Constraints
- No new deps (org rule). Tool returns data; scout writes signals. Local-only git.
