# Brief — Gate-1 operator selection + "suggested strongest cluster" + visual clustering

**Type:** design + build (UI + backend). **For terminal opus to delegate** (likely splits into 2-3 Worker
briefs). Jon's decisions (2026-06-05) below are locked; build to them.

## Background (the data model — already true)
- A campaign can hold MANY signals via `campaign_candidate_signals` (one `is_primary` + corroborating) —
  multiple signals → one campaign = richer context. This works today.
- `cluster_or_create_candidate` (repository.py) auto-clusters a promoted signal into an existing open
  candidate when it shares **resolved_district_id + campaign_family** within a time window; else creates a
  new one. So related signals merge into one campaign; unrelated ones become separate campaigns.
- Gate-1 (`gate_1_signals_inbox`) approval now promotes signals via the shared `promote_signal_to_candidate`
  / `promote_qualified_signals_for_run` (both manual + pipeline paths unified). **But** it currently
  promotes ALL qualified signals → on a multi-district run that's many candidates, and
  `content_brief_assembler` requires "exactly one uninitiated candidate per run" → fails.

## Locked decisions (Jon)
1. **Operator picks at the gate** (for now, while training the agent/rejection reasons). The Gate-1 Signals
   Inbox lets the human SELECT which signal cluster(s) to turn into a campaign — NOT auto-promote everything.
2. **Hybrid "suggested strongest":** the system computes and **visually flags the strongest cluster/signal**
   (border + subtle glow) as the recommended pick — the operator still chooses, but the system guides.
   (Direction: "many — one per cluster" is the eventual model; operator-picks is the interim.)
3. **Related signals are visually grouped** in the inbox by their cluster (same district + family), shown
   together (primary + corroborating), not as disconnected individual rows.

## The pieces to build (terminal opus: split as you see fit)
1. **Gate-1 selection (backend):** promote only the operator-SELECTED signals/cluster (via the existing
   shared promotion fn), not all qualified. The gate-decision payload carries the selected signal ids /
   cluster. Related selected signals still auto-cluster (corroborating). Keep both gate paths unified.
2. **"Suggested" scoring:** compute the strongest cluster for the run (e.g., highest combined fitScore /
   stacked-signal strength) and mark it `suggested: true` in the gate context the UI reads. Deterministic,
   explainable (so the UI can show *why* it's suggested).
3. **UI — Signals Inbox / Gate-1 card:** group qualified signals by cluster (district + family); render each
   cluster as a unit (primary + corroborating signals together); give the **suggested** cluster a visual
   indicator (border + glow). Let the operator select a cluster (or signals) and approve → campaign. (This
   is the "show related signals grouped" enhancement — today the inbox shows them flat.)
4. **`content_brief_assembler`:** replace the rigid "exactly one uninitiated candidate per run" with handling
   the operator's selected candidate(s) — process the chosen campaign (one at a time as advanced), don't
   fail when a run has multiple candidates. Coordinate the run-candidate scoping (`list_run_candidates`).

## Verify (live + tests)
- A real `marketing.main` run reaching Gate-1 with signals across multiple districts: the inbox shows them
  grouped by cluster, the strongest cluster is visibly suggested (border/glow), the operator selects one →
  exactly that campaign is created (with its corroborating signals) → `content_brief_assembler` succeeds →
  the run proceeds. Unselected signals remain qualified in the inbox.
- Both gate paths (manual /approve + pipeline gate) still share one promotion fn. Lossless; no DELETE on
  signals/observations. Per-worker test DBs. Browser-smoke the inbox grouping + glow.

## Interim usability cleanup (Jon 2026-06-05 — Jon + Opus Lead own Campaign UI; NOT terminal opus)

Goal: a USABLE Campaign UI now. The full visual overhaul happens later (after the whole-app debug pass —
lower priority), so these are interim fixes, not the redesign:
- **Text alignment / padding / margins are off** across the Campaigns surface — clean them up to "usable,"
  not pixel-perfect.
- **The campaign initiation/approval overlay is too dark — text is barely readable.** Interim fix: reuse
  the **Jira lightbox's overlay/lightbox color scheme** (find the Jira lightbox CSS and apply its
  backdrop/surface/text colors to the campaign initiation modal) until the redesign. (This is the same
  "black overlay" issue noted earlier.)
- These are interim — do not over-invest; the real redesign is a separate later effort.

### Detailed Campaign-UI fixes (Jon screenshots 2026-06-05, against real campaign #15)
**Initiation overlay (the dark modal):**
- Overlay is too dark, text gets lost; there's a **linear-gradient mask** over it making it worse.
- Fix: reuse the **Jira modal styling** — `.jira-modal-backdrop` / `.jira-modal` (+ head/title/close) in
  `public/css/features/jira-board.css:680+`. Apply that backdrop/surface/text scheme to the campaign
  initiation modal (`.mkt-modal-backdrop` / `.mkt-initiation-modal`).
- **Gradient mask scope (Jon 2026-06-05):** KEEP the gradient mask on light-bg *containers* (he likes it
  there) — remove it ONLY on the initiation pop-up/overlay. Scope the removal to the modal, not globally.
**Campaign detail view (light page):**
- Visual hierarchy problems — tighten heading/label/value hierarchy.
- Some **buttons are the same color as the background** (invisible) — give them visible borders/fills.
- **Padding issues** on some text blocks.
- Some **white text on a light bg** (invisible) — fix contrast (audit color tokens on these surfaces).
**New: surface attached signals**
- Add a way to see **which signals are attached to this campaign** (the cluster — primary + corroborating).
  Recommend a dedicated **"Signals" tab** alongside Brief/Audience/Assets/Sequence/Compliance/Performance/
  Approval Log (Lead's call unless Jon prefers a section). The data exists (campaign_candidate_signals).

### Future (roadmap, NOT now): campaign lineage + asset reuse/clone
Jon's "related campaigns attach to each other; reuse assets / use a prior campaign as a starting point
(makes a copy)." **Good news — the foundation already exists:** `campaign_candidates.predecessor_id` +
`get_candidate_predecessor_context` already links a campaign to a predecessor AND fetches the predecessor's
linked assets (`repository.py:676+`). So lineage is partly modeled. The NEW parts (future): surface the
lineage/related campaigns in the UI + a "start from / clone (copy assets)" action. Defer to the redesign.

## Constraints
Lossless; local-only git; org dep rule (nothing <7 days old); browser-smoke after merge; one clear
recommendation + worst-case framing to Jon for any UI judgment calls (the glow/border styling is
Creative-Director territory — show Jon before finalizing visuals). Trailer on commits:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
