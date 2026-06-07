# Agent working principles — the drive that fixed the deadlock

Captured 2026-06-06 (Jon): the behavior that root-caused the content-node hang is the behavior we want to
repeat. It cost real tokens, but it fixed a core blocker the right way after band-aids failed. Bake these
into briefs and into how agents (terminal, Codex, Opus Lead) work.

## 1. Chase the TRUE root cause — don't ship band-aids
When a problem resists, keep digging until you find the real cause, not a symptom patch.
- The content-node "hang" looked like prompt-size starvation ($0 cost, empty output, full timeout). Four
  iterations of band-aids (timeout bumps 120→300→600s, a serialize-semaphore, an MCP bypass) didn't fix it.
- The real cause was a **Postgres FK deadlock** — found by capturing `pg_stat_activity` and seeing the lock
  contention. The fix was 20 lines (commit before the subprocess to release the row lock).
- Instrument, query the DB, reproduce, read the actual state — don't theorize and patch symptoms.
- A band-aid that makes the symptom *look* better but doesn't fix the cause is worse than no fix: it hides
  the problem and wastes the next person's time. Drop redundant band-aids once the root cause is fixed.

## 2. Verify the EFFECT, not "tests pass"
Drive the real thing and assert the observable outcome — and on a FRESH case to rule out state-coincidence.
- "27 tests pass" wasn't enough; the proof was a real campaign producing a real `draftBody` at Gate-2, run
  on a fresh candidate (#14, not the over-used #15).
- Independently verify (query the DB / drive the flow yourself) — don't just trust a worker's report.

## 3. Match the depth to the stakes
- **Core blockers / MVP-critical paths** (the content engine, lossless invariants, auth): earn the deep
  drive + full verification. The token cost is justified — fixed-right beats five cheap wrong patches.
- **Low-stakes / cosmetic:** a quick fix is fine; don't gold-plate. Scope the effort to what's at risk.

## 4. Capture the lesson so it isn't re-learned
Terminal saved `feedback-mcp-fk-deadlock-vs-prompt-size.md` so the next hang with that signature checks for
FK lock contention first. Do this for any non-obvious root cause: a short memory/feedback note.

**Briefs should ask for #1 + #2 explicitly** on anything non-trivial: "root-cause it, don't band-aid; verify
the effect live on a fresh case; report how you proved it."
