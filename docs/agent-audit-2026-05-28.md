# Agent Audit — 2026-05-28 (pre-SP1)

**Trigger:** Jon's pre-UI ask, 2026-05-28: "are scouts producing good results, is the system bulletproof, and did the original self-improving / suggest-skills goal carry over to this version?"

**Method:** Read the code paths + query the live DB to verify intent vs reality (the Phase BH discipline). The pattern of this session has been "wired but hollow" — so verify, don't infer.

---

## Headline finding — the self-improvement loop has been dead since day one

**0 trajectory summaries across 236 agent runs. 0 definition proposals. 0 skill proposals.** The entire loop — which the master plan calls a structural requirement (build-philosophy #2) — has never produced a single record. The infrastructure is complete: the table (`agent_run_trajectory_summaries`) exists, the summarizer code exists, the Builder routes are wired to read recent summaries, the Builder prompt explicitly instructs *"call `read_recent_runs()` to load trajectory summaries"* and *"For each missing capability, call propose() with kind='skill'"* — but **none of it fires** because of a single bug.

### A1 — Trajectory summarizer GC'd before it runs (CRITICAL)

`artemis/builder/trajectory_summarizer.py:51`:
```python
asyncio.create_task(_safe_summarize(run_id), name=f"trajectory_summarize_{run_id}")
```
The task's return value is discarded — **the exact same Python GC footgun CC7 just fixed in pipeline dispatch.** The event loop holds only a weak reference; the task gets garbage-collected before running. The "trajectory_summarizer: run_id=X not found" log lines we saw during smokes were misleading — they fire from the few tasks that *did* run, with a different (transaction-visibility) issue. The vast majority are silently GC'd before they even attempt the lookup. Net: **0 summaries / 236 runs**, the data Jon wanted has never been captured.

**The original goal — agents reflecting on what worked / stalled / was missing per run — has been wired but inert for the lifetime of the app.**

### A2 — Builder has read-empty for its entire history (downstream of A1)

`definition_proposals` table is empty (`0` total, `0` agent, `0` skill). Across 7 builder_sessions, the Builder has NEVER proposed an agent definition update or a co-proposed skill. The Builder's design is correct (per the prompt at `agent_builder.py:75-95`): on edit, read recent summaries → propose changes citing run IDs → co-propose skills for missing capabilities. But it reads from `agent_run_trajectory_summaries` which is empty (A1) → it has no signal to act on. A2 is a **downstream symptom of A1**; fix A1 and A2 starts working.

### A3 — Skill suggestion DOES exist (correction to my earlier read)

`agent_builder.py:82`: *"For each missing capability, call propose() with kind='skill' for a co-proposal."* The skill-suggestion path is implemented in the main Agent-Builder, not only in floating_artemis. Your original goal — agents/Builder proposing new skills as capability gaps are detected — **was preserved** in this version. It's just dormant for the same A1 reason.

---

## The fix — CC10 — same pattern as CC7, ~30 LOC

Apply the retained-task pattern (module-level set + done callback) to `summarize_async`. Same fix CC7 just shipped for pipeline dispatch, applied to the trajectory summarizer. Once fixed:
- Every agent run produces a summary.
- Builder reads recent summaries when opening an agent → "I noticed X across your last N runs."
- Builder proposes definition updates + skill co-proposals with run-id citations.
- The loop you wanted is live.

This is the highest-leverage small fix remaining in the system. Brief: `briefs/cc10-trajectory-summarizer-gc-fix.md`.

---

## Other audit items (next sweeps)

### Scout quality / signal volume
- **Dedup** — CC9 in flight (federal_funding 78/13 = 6× dupes).
- **Volume** — federal_funding's 78 signals from one smoke (after dedup, 13 distinct) feels right for one cycle; will re-evaluate after CC9.
- **Signal quality** — qualifier hard-filters 5/30 and suppress-stales 3/30 (~27% rejection). That ratio looks healthy, but no ground truth yet. Worth a sample review with Josh once we have 50+ qualified signals.

### Bulletproofing
- **Run-state crash-resume** (banked) — server restart mid-run leaves a run stuck `running`. Worth adding a sweep that marks long-running runs failed + offers re-dispatch.
- **Per-node retries** — banked.
- **Observability** — the cost-dashboard (`C-cost-dashboard`) should also surface scout-by-scout success/failure rates so silent skews (one scout always failing) become visible.

### Agent-Builder reach
- The earlier blueprint-audit found the Operating Blueprint surface is read-only. SP1's structured-form pattern (Signal Playbook) is the right template to extend to editable agent definition fields (urgency tiers, failure modes, implementation notes). Worth a follow-up after SP1.

---

## Recommended sequence

1. **CC10 (trajectory GC fix)** — small, immediate. Unlocks the entire self-improvement loop. Fire alongside CC8/CC9.
2. After CC8/CC9/CC10 land + a real run, **verify A1+A2+A3 are now live**: confirm summaries are being written, confirm the Builder produces a definition_proposal + a skill co-proposal on an edit session.
3. **THEN SP1.** With self-improvement actually flowing, the Playbook can show "this code was proposed by an agent based on runs X, Y, Z" — turning the platform's promise into something visible.
