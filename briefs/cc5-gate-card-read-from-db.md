# CC5 — Gate-1 Card Reads Qualified Signals From DB (final chain close)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc5-gate-card-from-db`
**Browser smoke owner:** Lead (this session), post-merge — the visual Gate-1 proof.
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~200 (focused read-side change + tests).
**Depends on:** CC4 merged (qualifier transitions signals + writes briefs into `signal_queue.qualification_json.brief`). The data CC5 needs is already committed by CC4's run.

---

## Why this exists — the last gap

CC4 closed the data chain: the qualifier applies Josh's §4 rules (22 qualified, 5 rejected_hard_filter, 3 suppressed_stale), writes rich briefs into `signal_queue.qualification_json.brief`, and Gate 1 suspends holding them (`awaiting_approval`). But the Gate-1 approval CARD renders thin — `signal_count: 0`, `brief_preview: null`, `reason_codes: []`, `districts: []`.

**Root cause:** `_build_pipe4_context` builds the approval card from structured `qualified_signals`/`brief` keys in `node_states`. But claude-code agent nodes return only `output_summary` text (agent_executor.py ~222) — they do their real work via MCP tool calls that commit to the DB, and never emit those structured keys into node_states. So the 22 rich briefs exist in the DB but never reach the card.

**The principle this encodes (state it in the fix):** in the MCP era, agent *effects* live in the DB (via tool calls), not in node_states text. Gate/downstream context builders must READ FROM THE DB.

---

## Scope

### Part A — Rework `_build_pipe4_context` to read from the DB

Find `_build_pipe4_context` (the approval-card context builder — search `artemis/pipelines/`). Change it so that for the Gate-1 (signals inbox) approval, it reads the run's **qualified signals directly from `signal_queue`** (filtered by this run's `pipeline_run_id` and `signal_status='qualified'`) instead of from `node_states`.

For each qualified signal, pull from the committed row:
- `headline`, `reason_codes`, `district_id`/`state`, `urgency_tier`, `discovered_by`
- the brief from `qualification_json->'brief'` (the rich `## HEADLINE / ## WHY FLAGGED / ## EVIDENCE / ## FIT SCORES` body)

Populate the approval card payload:
- `signal_count` = number of qualified signals for the run
- per-signal entries (the card already renders a list — match its expected shape; check the approval-card JS + the existing payload schema)
- `brief_preview` = a sensible preview (e.g. the top signal's brief body, or a concatenation/first-N) — match what the card UI expects
- `reason_codes` = union across qualified signals
- `districts` = union of district_ids/states

Keep the existing `evidence_quote` / summary if the card uses it; this ADDS the structured per-signal briefs that were missing.

**Don't break the empty-signals path:** when a run genuinely has zero qualified signals, the context should still produce the clean "no signals" state (don't error on empty).

### Part B — Backfill the existing pending Gate-1 approval (so we can verify without a 13-min run)

CC4's run (`12cb2264`) left a real gate_1 approval (id≈7) in `awaiting_approval` with 22 qualified signals committed. Provide a way to rebuild that approval's payload with the new logic — either:
- a small idempotent function `rebuild_gate_context(approval_id)` that re-runs `_build_pipe4_context` for an existing approval and updates its payload, OR
- document the exact query/call to refresh it.

This lets Lead browser-verify the rich card against the already-committed data immediately, without spending 13 min on a fresh real run. (A fresh run is the ultimate proof, but the backfill makes the read-side fix verifiable fast.)

### Part C — Tests

`artemis/pipelines/tests/test_gate_card_from_db.py` (use `ARTEMIS_TEST_DB_URL`):
1. Seed a pipeline_run with N qualified signals in signal_queue (with qualification_json.brief). Call `_build_pipe4_context` → assert the returned context has `signal_count == N`, non-empty `brief_preview`, `reason_codes` populated from the signals, `districts` populated.
2. A run with 0 qualified signals → clean empty state, no error.
3. The per-signal entries match the approval-card's expected shape (so the UI renders them).

---

## Files owned
- EDIT: the file containing `_build_pipe4_context` (likely `artemis/pipelines/...` — find it)
- NEW: `artemis/pipelines/tests/test_gate_card_from_db.py`
- (If Part B's `rebuild_gate_context` is added, it lives alongside `_build_pipe4_context`.)

**Do not touch:** the MCP server, the adapter, the tools, the qualifier/scout agents, run_turn, blueprints, the seed. This is a read-side context-builder change only.

---

## Acceptance criteria (demonstrate each)
1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/pipelines/tests/test_gate_card_from_db.py -v` — all pass. **Paste.**
2. **Rebuild the existing CC4 gate-1 approval** (Part B) and show its payload now has `signal_count` ≈ 22, non-empty `brief_preview` (real brief text), populated `reason_codes` + `districts`. **Paste the before→after payload.** (This is the proof, against already-committed data — no new 13-min run required.)
3. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste.**
4. `git diff --stat` + `git log --oneline -1` on `worker/cc5-gate-card-from-db`. **Paste.**

---

## Hard constraints
- Read-side only. Do NOT modify the agent/MCP/tool path (it's proven — CC1–CC4).
- Match the approval-card UI's expected payload shape (read the card JS + existing schema; don't invent a shape the UI can't render).
- Don't break the genuine empty-signals path.
- Reuse existing models/tables; no new migrations.
- Local-only git. Worker commits on `worker/cc5-gate-card-from-db`; terminal-Lead merges after Lead approves.

---

## Report-back format
```
CC5 — Gate Card From DB report
1. Commit / branch / worktree
2. LOC diff stats
3. Which file/function held _build_pipe4_context; what you changed (read node_states → read signal_queue)
4. Test pass summary (acceptance #1)
5. Rebuilt gate-1 approval payload before→after (acceptance #2) — signal_count, brief_preview, reason_codes, districts
6. check.sh summary
7. Anything surprising — especially the approval-card payload shape the UI expects
```

---

**Claude Code Worker: the data is already in the DB (CC4 committed 22 qualified signals + briefs). This is purely making the Gate-1 card READ them. Operating principle: match the approval-card UI's real payload shape — read the card JS, don't guess. The proof (acceptance #2) is the existing approval rendering rich, verified against committed data.**
