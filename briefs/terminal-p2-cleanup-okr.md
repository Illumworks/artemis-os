# Terminal Orchestration Brief — Morning-Brief Cleanup + OKR Check-in (Sonnet sub-agents)

**Owner:** terminal (orchestrator). **Executors:** 2 Sonnet sub-agents. **Lead:** Artemis (Opus) reviews +
merges + live-verifies + restarts. Build the two ready P2 slices. Test DB is at head (artemis_test @ 0080) —
write real DB-backed tests, not mock-only.

## The two slices (each = one sub-agent, one brief)
1. **W-cleanup** — `briefs/p2a-morning-brief-cleanup.md` — drop the "Confidence" line, strip the dangling
   `; source`/`; level` suffixes, show Jira ticket **titles** not bare keys.
   Touches: `proactivity/scheduler.py` (`_format_brief_for_slack`), `brief/sources.py` (`_safe_jira`),
   `brief/generator.py`.
2. **W-okr** — `briefs/p2-okr-checkin.md` — Friday 4pm OKR check-in (propose→approve→word-dump→update).
   **CRITICAL FIRST STEP in that brief:** bump `update_okr_kr` from layer-2 to **layer-3 (propose→confirm)** —
   no OKR write without Jon's confirm. Touches: `floating_artemis/tools/okr.py`, `proactivity/scheduler.py`
   (new Friday job + reservation kind), a new OKR-proposal generator module, `okr/repository.py` reads.

## Isolation + the shared-file note
- Spawn each sub-agent in its **own git worktree** (isolation) so they run **in parallel** without touching
  the same working tree. (If worktree isolation isn't available in your setup, run them **sequentially**:
  W-cleanup first, then W-okr branched from the cleanup result — that avoids the overlap entirely.)
- **Both edit `proactivity/scheduler.py`** but in DIFFERENT functions (cleanup → `_format_brief_for_slack`;
  OKR → a new `_fire_okr_checkin` + its cron registration). Worktree isolation removes the live-edit conflict;
  the small overlap is resolved at MERGE (Lead handles it, merging W-cleanup → W-okr in that order).

## Each sub-agent must
- Implement ONLY its brief's scope; read the brief fully first. W-okr: do the layer-3 gate FIRST.
- Real DB-backed tests where natural (test DB is ready). Run `ruff check` + `mypy` (strict) on touched files +
  the targeted suite; report results.
- Commit to its own branch (`worker/p2a-brief-cleanup`, `worker/p2-okr-checkin`). **Do NOT merge to main.**
- Report: diff (files + key hunks), test/ruff/mypy results, judgment calls. W-okr: explicitly confirm
  `update_okr_kr` now requires confirmation and that the Friday job writes NO OKR on its own.

## Constraints (both)
- Don't regress the just-landed P2a or the `gather_sources` per-session fix. **Approval-first:** OKR writes
  gated; nothing fabricated; every OKR proposal cites its basis. Lossless; no new deps; ruff + mypy strict.
- Pre-existing repo-wide format debt in ~9 unrelated files is a known baseline — don't fix those here.

## Merge (Lead — sequential)
Lead merges **W-cleanup → W-okr**, resolves the `proactivity/scheduler.py` overlap, runs the combined
proactivity/okr/brief suite, then one `launchctl kickstart -k gui/$(id -u)/me.artemisos.app` + live-verify with
Jon: re-fire a brief (clean format, Jira titles, no Confidence) and run the OKR check-in round-trip (an OKR KR
changes ONLY after Jon's explicit confirm).
