# Signal Playbook — Design (LOCKED 2026-05-26)

**Status:** Design locked by Jon. Name + architecture decided. Build = post-Phase-BH stream (slotted before PIPE6 — see "Sequencing").
**Author:** Lead (Opus, 2026-05-26)
**Supersedes:** the "reason codes UI" discussion. The user-facing name is **Signal Playbook**.

---

## What it is

A **Marketing**-section UI surface where Josh / Anne Marie can view and edit the criteria that make a signal worth pursuing — without a deploy. It is the human-friendly face of Josh's campaign-signal spec (`decisions/campaign-signal-spec-v1.md`).

**v1 scope:** the **reason-code registry** — each code's plain-English trigger, what the scout looks for, default urgency, and which scouts emit it (the "Primary scouts" mapping). This is what Jon explicitly asked to be editable.

**Future sections (same surface, later):** territory config (priority states / watch keywords), qualifier rules (boost/suppress/skip), per-state nuances. The Playbook becomes the full editable face of Josh's spec over time. v1 ships the reason codes; the rest are additional Playbook tabs as they migrate to tables.

---

## Locked decisions

### D — Name + placement
- Name: **Signal Playbook**.
- Lives under **Marketing** (NOT Operations). It's domain content, not an orchestration primitive. (Contrast with Pipelines, which live under Operations per D6.1.)

### D — Source-of-truth architecture: Option B (table canonical, markdown = generated export)

Decided after weighing three options. **Option B chosen.** Reasoning (from the 2026-05-26 conversation):

- Jon's primary concern is **fragility** — no long editable text list that breaks easily.
- **Option A** (UI writes the markdown file) reintroduces that fragility — the UI would serialize structured data → markdown table syntax → file; one malformed generated row breaks the parser. Rejected.
- **Option C** (bidirectional file↔table sync) = two live sources that diverge. Bug factory. Rejected.
- **Option B** (DB table canonical; markdown becomes a one-way generated snapshot) — structured CRUD against typed columns, no markdown serialization in the edit path, so it **cannot be broken by bad syntax because there is no syntax**.

Option B also matches Master Plan build-philosophy #5 verbatim: *"operator-mutable config lives in a table; code reads from tables; would Anne Marie or Josh ever need to change this without a deploy? If yes → table."* And it consolidates onto the `signal_reason_codes` table **that already exists** (M1, used for FK validation on every signal write).

**"Keep current direction" is honored:** the *principle* (single source of truth, structured, read at runtime) is unchanged. Only the *store* moves file → table. The markdown spec survives as a generated one-way export for git history + human reading.

### D — Editing UX: structured, never raw text

- **Card/list view** of the codes — scannable: code name, plain-English trigger, urgency badge, scout chips.
- **Per-item structured form** (modal or inline), every field constrained:
  - Code name → validated format (SCREAMING_SNAKE, unique)
  - Plain-English trigger → textarea
  - What the scout looks for → textarea
  - Default urgency → dropdown (hot / standard / low / enrichment)
  - Primary scouts → chip multi-select from the known 9 scout slugs
  - Campaign families → multi-select
- **Add** = blank form. **Retire** = soft (superseded, NEVER hard-delete — per the lossless invariant). Needs a status/superseded column.
- **Validation on save** blocks invalid states (dup codes, bad urgency, unknown scouts). The user fills constrained fields; the system writes valid typed rows. No way to produce broken syntax.

---

## Architecture — what Option B requires

### The table becomes canonical
`signal_reason_codes` (exists from M1) holds the full Playbook data. Likely needs added columns (migration): `what_scout_looks_for`, `primary_scouts` (JSONB array of scout slugs), `campaign_families` (JSONB), `status` (active/superseded for soft-retire), `superseded_by`. Confirm current columns before the migration.

### Runtime read shifts file → table
- Today: `josh_spec.parse_spec()` reads the markdown file → `JoshSpec`. F2 (`_build_system_prompt`), the tools (`reason_codes.*`, `signal_queue.write` allowlist), and the seed all consume the parser.
- Under B: add `load_playbook(session) -> JoshSpec` that reads the **table** and returns the same `JoshSpec` dataclass shape — so **downstream code is unchanged**; only the source swaps. `parse_spec(file)` is demoted to the **import** path (file → table), used for the initial seed and any future bulk re-import.
- **Sync/async wrinkle:** `parse_spec` is sync; a DB read is async. Cleanest resolution: an **in-memory cached `JoshSpec` projection** loaded from the table, invalidated whenever the Playbook UI writes a change (and on startup). Keeps F2's hot-path cheap, refreshes on edit. (Finalize cache-vs-async in the SP1 brief.)

### Markdown becomes a generated export
On any Playbook change, regenerate `decisions/campaign-signal-spec-v1.md` **from the table** (one-way) so the readable, git-tracked artifact stays current. Never read at runtime after the migration. This is the safe sliver of "Option C" — export, not sync.

### UI + API
- New API: `GET/POST/PATCH /api/marketing/signal-playbook/codes` (+ soft-retire). Reads/writes the table, invalidates the cache, triggers the markdown export.
- New Marketing page: "Signal Playbook" — card list + structured form per the UX above. Builder-first philosophy applies long-term (a conversational editor), but v1 ships the structured form (which is already robust, unlike generic CRUD).

---

## Implied streams (when we build it)

- **SP1 — Backend canonical-source shift.** Migration (add Playbook columns to `signal_reason_codes` + status/superseded), `load_playbook(session)` + cache, migrate F2/tools/seed to the cached table projection, markdown one-way export generator, the CRUD API. Tests. (~1 Worker stream.)
- **SP2 — Signal Playbook UI.** The Marketing page: card list, structured form, validation, soft-retire. Browser-smoked by Lead. (~1 Worker stream.)

SP1 before SP2 (UI needs the API). Both after Phase BH closes.

---

## Sequencing — where this slots

**Recommended order (Lead's call, per Jon's "slot it where you feel"):**

1. **Finish Phase BH** — F6 (invocation task) lands + verified (real signals flow). This is the active priority.
2. **Signal Playbook (SP1 → SP2)** — slot it BEFORE PIPE6.
   - *Why before PIPE6:* it's in the marketing-demo critical path (Josh/Anne Marie editing criteria is a real, compelling workflow), we're already deep in the signal/marketing machinery (low context-switch cost), and it directly serves the domain Jon cares about. PIPE6 is infrastructure cleanup that isn't harmful to defer.
3. **PIPE6** — Workflows + Automations sunset → migrate to Pipelines (the D6 cleanup). Still queued; not urgent.

Jon can reorder; this is the Lead recommendation.

---

## Open question for Jon (non-blocking, decide when we start SP1)

- Does Signal Playbook v1 edit ONLY reason codes, or also territory config (priority states)? My lean: **reason codes only for v1** (what you asked for), with territory/rules/nuances as later Playbook tabs. Confirm when we pick up SP1.
