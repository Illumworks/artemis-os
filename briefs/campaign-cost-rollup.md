# Brief — Campaign cost rollup + cost-per-district (v1)

**Type:** feature (cost-infra attribution + campaign-detail UI). **Coordination:** the attribution + schema
touch the COST infrastructure terminal opus owns — coordinate that half with terminal; the campaign-detail
display is Lead+Jon (Campaign UI). **Sequence:** start AFTER cost-phase-2 merges to main (avoid more
shared-tree churn). Own worktree, cwd inside, branch off `main`. Own test DB. Do NOT merge — report.

## Goal (Jon, 2026-06-06)
Show, ON each campaign, **what it cost to run** and **cost per district contacted**, so Jon can see ROI.
v1 scope = the work a campaign triggers: **scouts, brief assembly, content drafting**. True "cost per lead"
(per response/conversion) is explicitly v2 — we don't track responses yet (agreed with Jon).

## What exists today (grounding)
- `artemis/costs/events.py::record_cost_event(...)` writes a `cost_events` row. Params: provider, model,
  provider_path, feature_tag, tokens…, `source_kind`, `source_id`, `agent_id`, `session_id`,
  `workflow_run_id`, duration, error. **No campaign field.** Rates snapshotted onto the row (immutable).
- Already called from: `marketing/scout_runner.py` (`feature_tag="marketing_scout"`), `marketing/
  brief_assembler.py` (has `candidate.id` in scope), plus consolidator/graph_extractor/builders.executor/
  floating_artemis/meetings/trajectory.
- Content drafting for a campaign runs through agent runs (`builders/executor.py`) — cost recorded there
  with `agent_id`/`source_*`; the campaign linkage must be threaded into that call path.
- `cost_events` is append-only / lossless (Phase 1). Campaign audience size is computable from the
  targeting builder (`resolve_district_ids_for_candidate` / the preview count); actual recipients live in
  the send `recipients_snapshot`.

## Design

### Part A — Attribution (cost infra; coordinate with terminal — DO NOT fork the cost module)
1. **Add a nullable `campaign_candidate_id` column to `cost_events`** (additive Alembic migration; lossless;
   no backfill required — historical rows stay NULL). Add the optional param to `record_cost_event(...,
   campaign_candidate_id: int | None = None)`.
2. **Tag the directly-campaign-tied work** (clean, 1:1 with a campaign):
   - `brief_assembler` — pass `candidate.id`.
   - Content drafting — thread the candidate id into the content-agent run so its cost row carries it.
   - Per-candidate sends — tag with the candidate id.
   These are the core of "cost to run this campaign."
3. **Scouts (the one nuance — shared cost, needs allocation, NOT direct tagging):** a scout run produces
   many signals across districts; only some become campaigns, so its cost can't be tagged to one candidate
   at run time. Allocate AFTER the fact: `scout_run_cost ÷ signals_produced_by_that_run`, then attribute the
   per-signal share of THIS campaign's seeding signals (the candidate's primary + corroborating signals via
   `campaign_candidate_signals`, each tracing to the scout run/agent that produced it). Show as a separate
   **"discovery"** line so the number stays honest (no double-counting a shared run across campaigns).
   **Jon-confirmed (2026-06-06): a per-SIGNAL share, NEVER a flat amount per campaign** — a 1-signal
   campaign must not be charged the same as a 5-signal one; charging by signal makes the shares sum to the
   true total. So: `campaign discovery cost = (per-signal scout cost) × (this campaign's seeding-signal
   count)`.
   **Simpler fallback if per-run tracing is fiddly:** use ONE average cost-per-signal
   (`total scouting spend ÷ total signals over the window`) × the campaign's seeding-signal count — less
   precise, much simpler, still honest, still per-signal (not flat).
   **If even that is hard for v1, ship Part A.2 (brief+content+sends) first and add the discovery line as a
   fast follow** — log clearly that scouting isn't yet included rather than silently omitting it.

### Part B — Rollup endpoint (cost infra)
`GET /api/marketing/campaigns/{candidate_id}/cost` →
```
{ "totalUsd": <num>,
  "byStage": { "scouting": <num|null>, "brief": <num>, "content": <num>, "sends": <num> },
  "districtsContacted": <int>,         // actual recipients if sent, else resolved target-audience count
  "districtsBasis": "recipients" | "target_audience",
  "costPerDistrict": <num|null> }      // totalUsd / districtsContacted, null if 0
```
Direct rows: `SELECT … WHERE campaign_candidate_id = :id`. Scouting (if included): the allocation query.
Reuse pricing already snapshotted on rows (no recompute). Read-only.

### Part C — Display (Campaign UI — Lead+Jon own)
On the campaign detail, a small **"Cost"** surface (a tab alongside Brief/Audience/…/Signals, OR a compact
card in the header — Lead's call, lean toward a tab for parity). Shows:
- **Cost to run: $X.XX** (with the stage breakdown on expand: brief / content / sends / discovery).
- **Cost per district: $Y.YY** — "(across N districts contacted)" / "(across N targeted)" per `districtsBasis`.
- Honest empty state before any spend ("No cost recorded yet — assemble a brief or draft content to begin").
Reuse the cost-page CSS/tokens terminal built (`cost.css`) for visual consistency.

## Verify (live — assert the effect)
- Unit: attribution writes `campaign_candidate_id`; rollup sums only that candidate's rows; cost-per-district
  math; districtsBasis switches recipients↔target. Backward compat: historical NULL rows excluded cleanly.
- Live: on a real campaign, assemble a brief / draft content → cost_events carry the candidate id → the
  campaign's Cost surface shows a non-zero total + a sane per-district number; a second campaign's cost is
  isolated (no bleed). If scouting included: allocation doesn't double-count across two campaigns from the
  same scout run.
- ruff + mypy + tests clean. Browser-smoke the Cost surface.

## Constraints
Lossless (additive nullable column only; cost_events stays append-only; no DELETE). Coordinate the schema +
attribution with terminal — extend the cost module, don't fork it. Backward compatible (NULL = unattributed
historical). Org dep rule (nothing <7 days). Local-only git; commit/merge via an isolated worktree (we hit
a shared-tree collision — don't repeat it). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
