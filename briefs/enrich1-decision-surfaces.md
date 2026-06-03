# ENRICH1 — Make the approve/initiate decisions informed (surface dropped data + drill-down)

**Paste-into:** Codex OR terminal Claude Lead.
**Recommended model / effort:** `gpt-5.4` · `medium`. Backend serializers + frontend render; touches
the live marketing surface, so a browser smoke is a hard gate.
**Target branch:** `worker/enrich1-decision-surfaces`
**FIRES AFTER `worker/fix115-workspace-status` + `worker/proc2-procurement-relevance` land** — they
currently share the main tree; ENRICH1 must start from a clean main and ideally run in its **own
worktree**. No overlap with their files, but don't add to the commingled tree.
**No migration.** All data surfaced here is ALREADY in the DB / API responses — this is rendering +
two thin read additions, not new substrate.
**Authoritative finding:** `docs/brief-sufficiency-audit-2026-06-02.md`.
**LOC cap:** ~350.
**Priority:** HIGH — includes a safety gap (do-not-contact flag invisible) + makes the
commit-to-a-campaign decision actually informed.

## The principle
**Summary by default, full evidence on demand.** Today the operator decides off a condensed
summary while richer, already-computed justification is dropped before display. Keep the scannable
summary, but (a) surface the high-value fields we currently discard, (b) let the viewer **expand**
into the complete picture, and (c) never hide a deal-breaker.

## 🔴 Part A — Safety: surface `districts.on_skip_list` (do-not-contact) everywhere
`on_skip_list` is surfaced nowhere; an operator can approve/initiate for a do-not-contact district.
- Add `on_skip_list` to the district context (`annotate_district_tier` / `_build_district_context`).
- Render a prominent **"⚠ Do-not-contact (skip list)"** badge in the Gate-1 district block AND the
  initiation modal header.
- At initiation, if the primary district is skip-listed, require an explicit operator acknowledgment
  (a confirm step / checkbox) before initiate — **warn, don't hard-block** (operator may have reason),
  but make it impossible to do silently.

## Part B — Gate-1 enrichment (`signal_queue.py` serialize + `public/js/components/signal-tree.js`)
Surface (all already in the payload / DB):
- **`provenance.why_flagged`** as its own "Why flagged" line (the scout's reasoning).
- **Scout identity** (`discovered_by`) + a trace link to the `agent_run_id`.
- **Related-signals count** — run the existing `find_signal_by_dedupe_key` logic for display
  ("N related signals seen") so the operator sees recurrence.
- **Expand control** → full signal: complete summary/evidence text, full scout reasoning, source
  title + URL, qualifier audit detail. (Summary stays; full is one click away.)

## Part C — Initiation enrichment (`initiation.py` context + `marketing-os.js` initiation modal)
This is the thin-for-the-decision surface — deepen it:
- **Render `proposal.rationale`** — currently fetched (`initiation.py:126`) then discarded. Show the
  LLM's reasoning for the campaign prominently (below objective).
- **Per-signal evidence in the cluster rows** — add each signal's **reason codes + `why_flagged`**
  (reasonCodes already serialized; add `why_flagged` to `_serialize_signal_row`), with an **expand**
  per signal to its full evidence/source.
- **Promotion score** — read `candidate.metrics_json` (adjustedScore / recommendedFamilies, written
  at approval) into the context + show it ("qualified at score X").
- **District depth parity** — enrollment + supported + skip-list in the modal header (match Gate-1).
- **Target-scope cardinality** — when a state/tier scope is chosen, show "→ N districts"
  (one `SELECT count(*) FROM districts WHERE state = ANY(...) [AND supported]`). De-risks scope size.
- **Inline lineage** — render the predecessor's brief summary + asset/draft summaries
  (`get_candidate_lineage_context` already loads `latest_brief.content` + assets) instead of just a
  boolean "available."

## Explicitly OUT of scope (later — needs substrate / #106)
Trend/history counts ("3rd signal in IL this quarter"), memory readback before the decision,
predecessor *outcomes*, contact reachability. These need the trend substrate + #106 — ENRICH1 is
strictly "surface what we already compute + drill-down."

## Acceptance
1. **Skip-list (browser smoke):** a signal/candidate for a skip-listed district shows the warning at
   Gate-1 + initiation; initiating one requires the acknowledgment. **Paste.** (Seed/flag a test
   district's `on_skip_list=true` to demonstrate.)
2. **Initiation depth (browser smoke):** the modal shows rationale, per-signal reason codes +
   expandable evidence, score, district depth, target-scope count, inline lineage. **Paste console +
   a description of the richer modal.**
3. **Gate-1 (browser smoke):** why-flagged, scout identity, related-signals count, expand-to-full.
4. Tests for the new serializer fields + the target-scope count; `node --check`; `./scripts/check.sh`
   (j5b exempt). **Paste.**
5. **COMMIT on `worker/enrich1-decision-surfaces`, local git only.** Message ends
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Constraints
- **No new data/substrate/sources** — only render what's already in the DB/API. No trend/history/#106.
- **Summary stays scannable** — full evidence is opt-in expand, don't wall-of-text the default view.
- **Skip-list warns, never silently allows.** Lossless. No new deps. Local-only git. Own worktree.

## Report-back format
```
ENRICH1 — decision surfaces report
1. Commit / branch
2. Skip-list: where the badge/warning renders + the initiate acknowledgment behavior
3. Gate-1 fields added + the expand-to-full control
4. Initiation fields added (rationale, per-signal evidence, score, scope count, lineage) + expand
5. Tests + browser smoke (Gate-1 + initiation, with screenshots/descriptions)
6. check.sh
7. Surprises
```
