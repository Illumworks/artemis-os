# Screen-Time Watch — Plan

> **Priority: HIGH / time-sensitive.** Screen-time restrictions are an active,
> material threat to Amira's business right now. This is a focused intelligence
> project to track that landscape nationally and arm the team.
>
> Companion briefs: `briefs/screentime-watch-1-pipeline.md`,
> `briefs/screentime-watch-2-callie-report.md`, `briefs/screentime-watch-3-page.md`.
> Coordination: logged in `../claudeck-artemis/COORDINATION.md` (migration `0102`
> claimed; file scope disjoint from the parallel Forge/Ares build).

## Why / the ask (grounded, not guessed)

From Jon + Angela's all-hands remarks (Granola, Jun 19) + Angela's Slack:
- Track **new AND proposed** state/district **screen-time** legislation & policy.
- **"Real moves," not headlines** — actual legislative/board action, with the
  *actual law text*, not press chatter ("don't just read the headline before
  dropping the tools").
- The detail that matters most: **exemptions / carve-outs for evidence-based,
  purpose-built tools** — i.e. *where Amira fits* (she cited Tennessee's exemption,
  LAUSD). The question isn't just "is there a restriction" but "does it restrict or
  carve out tools like Amira."
- **Rolling ~30 days, kept current.** Outcome: surface updates to Angela + staff so
  they can position with districts on the law in *their* state; also feeds the
  external narrative.

**NOT in scope:** cellphone-ban policy (a different lane — phones in schools, not
instructional screen-time). No cellphone trackers.

## Design principles (locked with Jon)

1. **Separate pipeline.** Built as its own pipeline (it appears on the pipelines
   page alongside, never inside, the marketing campaign pipeline). The campaign
   pipeline is never touched. Reuse the *scout building blocks*, not the campaign
   pipeline's logic.
2. **National.** All 50 states — **decoupled from the campaign's selected target
   states**. The scouts run nationwide here.
3. **Isolated + scrubbable data.** Dedicated `screentime_*` tables in their own
   namespace (NOT the marketing signal tables). Wiping later = truncate/drop those
   tables only. Build in a **purge action + optional retention window**.
4. **Tunable stance.** The favorable/unfavorable classification is **config-driven**
   so Angela can adjust it after seeing real signals — a settings change, not a code
   change.
5. **Cheap at national scale.** Keep a 50-state sweep affordable via the "real
   moves" filter (volume control) + **tool-less classification on Codex/local**
   (stance-tagging is text-only work, off Opus).
6. **Agent memory untouched.** Callie *reads* these tables as a data source; the
   `memory_observations` layer is not changed.

## Architecture

**Data (own namespace, isolated):**
- `screentime_signals` — one row per discovered move: `state`, `level`
  (state|district), `district_name?`, `title`, `summary`, `status`
  (proposed|passed|amended|guidance|news), `stance` (favorable|unfavorable|neutral),
  `amira_angle` (text — restricts? carves out?), `source_url`, `source_type`
  (legislative|state_doe|board_minutes|regional_news), `published_at`,
  `discovered_at`, `is_real_move` (bool), `content_hash` (dedup), `raw` (jsonb).
- `screentime_state_stance` — per-state rollup for the heat map: `state` (PK),
  `stance` (favorable|unfavorable|neutral|no_info), `rationale`, `signal_count`,
  `last_updated`. Recomputed from signals.
- **Stance config** — tunable rules (a settings blob / small config table) defining
  how signals map to stance. v1 default below; editable without a deploy.

**Pipeline (separate, national, cron):**
fan-out scouts (national, screen-time-tuned) → dedupe + **"real moves" filter** →
**stance-classify + Amira angle** (config-driven; tool-less provider) → store
signals → recompute per-state stance → **Callie posts to #policy-watch**.
Sources (existing scouts, run nationally, screen-time-tuned): `legislative` (bills),
`state_doe` (dept guidance), `board_minutes` (district board policy — the LAUSD
layer), `regional_news` (the lag-catcher). Optionally add structured screen-time
trackers (ECS, Common Sense Media) later — never cellphone trackers.

**Stance definition v1 (tunable):**
- 🟢 **favorable** = restriction *with* a carve-out for evidence-based/purpose-built
  tools, or otherwise pro-evidence-based policy.
- 🔴 **unfavorable** = *blanket* screen-time restriction, no carve-out (could limit us).
- ⚪ **neutral / no info** = nothing relevant yet, or unknown.
Expect to tune after Angela reviews real signals.

**Channel:** dedicated `#policy-watch` (config). Cadence: weekly digest + immediate
post on a big move.

**Page:** dedicated **"Screen-Time Watch"** page (internal-only). Hero = **50-state
heat map** colored by stance; below it a **searchable signal repository** (filter by
state, level, status, stance, date; click a state → its signals + sources). Includes
the **scrub/purge** control.

## Build sequence
1. **Brief 1 — Pipeline + isolated data + tunable stance** (the engine; owns migration `0102`).
2. **Brief 2 — Callie reports to #policy-watch** (digest + big-move alerts).
3. **Brief 3 — Screen-Time Watch page** (heat map + search + scrub).

## Coordination (parallel with Forge/Ares)
- Migration `0102` claimed in COORDINATION.md (Forge takes 0103+).
- Own test DB `artemis_test_screentime`.
- Isolated worktrees; merge to main deliberately; heads-up before any live restart.
- Shared-risk edits (additive): `config.py`, `main.py`, `navigation.js` (last one
  collides with Forge's rename — sequence it).

## Open item
- Stance definition will likely be tuned once Angela sees a first batch of real
  signals. Built config-driven precisely so that's a settings change.
