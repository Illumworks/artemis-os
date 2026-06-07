# Memory Phase 2 — Provenance & lineage in the detail panel

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-phase-2-provenance-lineage`
**Browser smoke owner:** Lead, post-merge — open Memory page, click into any observation, verify the detail panel renders provenance copy + lineage timeline + resolved evidence.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~340 (backend resolver + extended detail endpoint + frontend renderers + tests).
**Priority:** HIGH — first phase of the Memory page redesign. Sets the tone for everything that follows.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md`
**Depends on:** none (independent of all other phases).

---

## Why this exists

Per the audit, the M6 detail panel today shows opaque source IDs:

> "Backed by (3): drawer #4731 · …preview… / agent_run #182 · …preview… / signal_queue #44 · …preview…"

This is data, not a story. Jon's instinct that the page "doesn't surface anything" lives largely in this panel — even when you click a row, the detail view answers *what* it is but not *where it came from* or *what it replaced*.

Phase 2 turns the detail panel into a narrative: who picked it up, when, from which source, what it superseded, what it might supersede later. The backend already has the data — supersession chain endpoint exists (`GET /api/memory/observations/{id}/history`); evidence rows already resolve `drawer` and `observation` source kinds in `get_observation_detail`. This brief extends that resolution to the remaining source kinds and renders a lineage timeline.

Locked decision: Phase 2 ships **before** Phase 1 because provenance is the more emotionally compelling first impression (Jon, 2026-06-06).

---

## Scope

### Part A — Backend source resolution

Create `artemis/memory/source_resolution.py` (new file, ~80 LOC). Single public function:

```python
async def resolve_evidence_source(
    session: AsyncSession,
    source_kind: str,
    source_id: str,
) -> dict[str, Any]:
    """Return a structured preview of an evidence source.

    Returns: {"label": str, "preview": str | None, "deep_link": str | None, "meta": dict}
    """
```

Resolution rules (one per known `source_kind`):

| source_kind | Label format | Preview | Deep link |
|---|---|---|---|
| `drawer` | "drawer · {drawer.source_kind}" (e.g. "drawer · floating_artemis_turn") | drawer.content[:200] | `/memory?drawer={id}` |
| `observation` | "observation #{id}" | observation.content[:200] | `/memory?obs={id}` |
| `agent_run` | "agent · {agent.name} · {run.purpose}" | run.summary or run.what_worked, first 200 chars | `/agents?run={id}` |
| `signal_queue` | "scout signal · {signal.headline}" | signal.summary or first 200 of headline+excerpt | `/marketing?signal={id}` |
| `floating_artemis_turn` | "your conversation with Artemis · {ts}" | turn.user_message[:200] | `/floating-artemis?turn={id}` |
| `legacy_memory` | "imported legacy memory" | row.content[:200] if reachable, else None | None |
| anything else | `"{source_kind} #{source_id}"` (fallback) | None | None |

Implementation notes:
- `source_id` is `TEXT` (per existing convention in `MemoryEvidence`). Numeric IDs need `int()` cast for BigInt PK lookup; non-numeric source_ids fall through to the fallback label without raising.
- For `agent_run`, join `agent_runs` + `agents` tables (assume they exist; if not, fall back to label only and skip the join — Lead verifies in smoke).
- For `signal_queue`, read from the canonical signal table — confirm name in `artemis/marketing/` before writing the query.
- Tolerate missing source rows: if the FK no longer resolves, return label only with `preview=None`.
- All queries via `AsyncSession`; no new connections.

### Part B — Extend `get_observation_detail`

Edit `artemis/memory/repository.py:243` (`get_observation_detail`). Today it manually resolves `drawer` and `observation` source kinds inline. Replace that inline resolution with calls to `resolve_evidence_source`.

After this, the returned evidence list has the shape:
```python
{
  "id": int,
  "source_kind": str,
  "source_id": str,
  "weight": float,
  "label": str,              # NEW — human-readable, e.g. "scout signal · Houston ISD adopts ELA"
  "preview": str | None,     # existing — now resolved for all kinds
  "deep_link": str | None,   # NEW — frontend uses this to make the row clickable
  "created_at": str,
}
```

### Part C — Frontend: provenance + lineage in detail panel

Edit `public/js/features/memory-shell.js`, function `renderM6DetailPanel` (line 422). Replace the current detail body with three new sections in this vertical order:

**1. Provenance block** (above the existing quote):

For an observation, derive the *primary* provenance from the highest-weight evidence row. Use the `label` field returned by the new resolver. Copy template:

> She picked this up from **{label}** on **{date}**.

If no evidence rows: "Origin unknown — likely a direct write."

If `user_confirmed = true`: append "Confirmed by operator on **{confirmation_ts}**." (Note: confirmation ts isn't stored yet; for now, omit the date if it's not available.)

**2. Lineage timeline** (between quote and evidence list):

Call `GET /api/memory/observations/{id}/history` (existing endpoint, walks supersession chain backward). Renders as a vertical timeline:

- Top: this observation (highlighted, "(current)" tag)
- Below: each ancestor with relative timestamp + 60-char preview
- If `superseded_by` is set, prepend a "Replaced by → #{newer_id}" row that's clickable (calls `selectObservation(newer_id)`)
- If the chain has length 1: render as "No prior versions" instead of an empty timeline

Skip the timeline entirely if there's no ancestry and no successor (lone observation).

**3. Evidence list — resolved** (replaces the existing list):

For each evidence row:
- Show `label` instead of `source_kind #id`
- Show `preview` (existing behavior)
- If `deep_link` is non-null, render the row as a clickable link (anchor styled like the current row but with hover affordance); clicking opens the deep link in a new view *only when the same shell route is implied* — for cross-shell deep links (e.g. `/agents`, `/marketing`), fall back to a small "→ open" affordance that calls `setState("view", <view>)` with relevant local-storage seeding.

Add a small "Authority" row in the meta block:
- `confidence: {round(confidence * 100)}% (from {confidence_origin or "system"})`
- `user_confirmed: yes/no`

### Part D — Tests

`artemis/memory/tests/test_source_resolution.py` (new file):

1. **drawer source resolves to drawer label + preview.** Fixture: 1 observation, 1 drawer-kind evidence link. Assert `label` starts with `"drawer · "` and preview matches drawer content.
2. **observation source resolves with cycle guard.** Fixture: obs A → cited by obs B (evidence row). Assert resolution returns observation preview, no infinite loop.
3. **agent_run source resolves to agent name.** Fixture: 1 agent_run row with linked agent. Assert label contains agent name.
4. **signal_queue source resolves to headline.** Fixture: 1 signal_queue row. Assert label contains headline.
5. **Unknown source_kind falls back gracefully.** Fixture: evidence row with `source_kind="something_else"`. Assert label = `"something_else #<id>"`, preview = None.
6. **Missing source row tolerates gracefully.** Fixture: evidence pointing to non-existent drawer. Assert label resolves to fallback, no raise.

`artemis/routes/tests/test_memory_shell_routes.py` (extend existing):

7. **`get_observation_detail` returns evidence with `label` + `deep_link` fields.** Fixture: 1 obs with 3 mixed-kind evidence rows. Assert all three have `label` populated.

No frontend test infra exists per the M6 brief precedent — Lead does eyes-on smoke (acceptance criterion #4 below).

---

## Files owned

- NEW: `artemis/memory/source_resolution.py`
- EDIT: `artemis/memory/repository.py` (`get_observation_detail` rewrites to use resolver)
- EDIT: `public/js/features/memory-shell.js` (extend `renderM6DetailPanel`, add `renderProvenance`, `renderLineage`, `renderResolvedEvidence`, `renderAuthority`)
- EDIT: `public/css/panels/memory.css` (timeline component, provenance row, authority row — reuse tokens)
- NEW: `artemis/memory/tests/test_source_resolution.py`
- EDIT: `artemis/routes/tests/test_memory_shell_routes.py` (test #7)

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` is unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/memory/tests/test_source_resolution.py artemis/routes/tests/test_memory_shell_routes.py -v` — all tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Open Memory page, click into 3 different observations spanning at least 2 different `source_kind`s of evidence.
   - For each: verify provenance copy reads in plain English (no "drawer #4731" anywhere user-visible).
   - For at least one observation that has been superseded or supersedes another: verify the lineage timeline renders with the "Replaced by →" link clickable.
   - Verify the Authority row shows confidence + user_confirmed badges.
   - **Paste a DOM snippet of the detail panel for the most interesting observation.**
5. `git diff --stat` + `git log --oneline -1` on `worker/memory-phase-2-provenance-lineage`. **Paste.**

---

## Hard constraints

- **Read-only.** Phase 2 adds no write paths. No new endpoints; only extends `get_observation_detail`'s return shape. Tests must confirm no DB mutation in resolution helpers.
- **Backwards-compatible response.** The existing `get_observation_detail` callers must continue to work. Add `label`, `deep_link` fields *in addition* to existing fields; do not remove `source_kind`, `source_id`, `weight`, `created_at`, `source_preview`.
- **Tolerate missing source rows.** Never raise when an evidence FK points to a deleted/missing source row; return fallback label.
- **No new visual languages.** Timeline component uses existing CSS tokens (`--surface-card`, `--surface-outline`, `--accent`). Match the existing detail panel's spacing.
- **Empty-state safe.** Detail panel for an observation with zero evidence rows must still render the quote and authority row, with provenance line reading "Origin unknown — likely a direct write."
- **Local-only git.** Worker commits on `worker/memory-phase-2-provenance-lineage`; terminal-Lead merges after Lead approves.
- **LOC discipline.** Provenance prose, lineage timeline, evidence resolution — that's the entire scope. Resist temptation to also add Pin/Confirm buttons (those are Phase 3) or category badges (those are Phase 1). Stop at storytelling.
