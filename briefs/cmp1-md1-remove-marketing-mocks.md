# CMP1 + MD1 — Remove the Campaigns/dashboard mock data (render real, add empty states)

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4-mini` · reasoning effort `medium`. Frontend surgery on a large live file (`marketing-os.js`) — mechanical removals, but it touches the app's main marketing surface, so it needs a browser-load smoke as a hard gate. Not `low`.
**Target branch:** `worker/cmp1-md1-remove-mocks`
**Fires:** now. Bundled because CMP1 + MD1 both edit `public/js/features/marketing-os.js` (can't parallelize). No migration.
**Authoritative finding:** `docs/marketing-flow-audit-2026-05-30.md` (CMP1, MD1, XC2).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (mostly deletions + a couple of empty states).
**Priority:** HIGH — the last visible hollowness on the marketing surface. Now safe because real initiated campaigns have real names + shape (Stream 2 shipped: 2 real campaign_candidates exist, 1 initiated).

---

## Why this exists

`public/js/features/marketing-os.js` still renders **hardcoded demo data** as a fallback the
operator sees as real:
- **CMP1:** a `CAMPAIGNS` array (~line 101) of fake campaigns ("Michigan Field Guide" etc.).
  `_campaignMap` is seeded from it (~407), and multiple sites fall back to it
  (`campaigns = CAMPAIGNS` ~661; `mergedCampaigns.length > 0 ? mergedCampaigns : CAMPAIGNS`
  ~2413, ~2451). So when the real candidate list is short, the operator sees demo campaigns
  presented as real.
- **MD1:** dashboard summary counts fall back to `APPROVALS_MOCK.filter(...).length` (~2409),
  `SIGNALS_MOCK.length` (~2411). Zero real rows → the operator sees fake numbers that look real.
- **XC2:** `loadMarketingSignals` renders `SIGNALS_MOCK` as a load skeleton (~2475) — defensible
  but should be a real loading/empty state, not fake signals.

(Line numbers are from the current file but WILL have shifted — locate by symbol.)

Real data now exists: 2 `campaign_candidates` (named; 1 initiated via the Stream-2 flow).

---

## Scope

### CMP1 — Campaigns tab renders only real candidates
- **Delete** the `CAMPAIGNS` hardcoded array and the `SIGNALS_MOCK` / `APPROVALS_MOCK` arrays
  (and their export at the bottom, ~3879).
- Render the Campaigns view from the **real campaign-candidates list endpoint** (find it — it's
  in `artemis/marketing/routes/campaign_ops.py` / an existing `api.js` wrapper; if no list
  endpoint exists yet, add a thin `GET /api/marketing/campaigns` returning candidates with
  name/objective/state/initiated_at/campaign_family + signal-cluster count). Drop the
  merge-with-mock logic (`_campaignMap` seeded from CAMPAIGNS; the `: CAMPAIGNS` fallbacks).
- **Empty state:** when there are no campaigns, show a clear "No campaigns yet — approve signals
  at Gate 1 to start one" panel, NOT demo rows.
- A campaign card shows the real fields (name, family, state/tier via the primary signal,
  initiated vs proposed status, # signals in the cluster). Reuse the existing card styling.

### MD1 — dashboard counts are real
- Replace the `APPROVALS_MOCK.length` / `SIGNALS_MOCK.length` count fallbacks with the real
  counts already fetched (`signalResult.total`, the real pending-approvals count). When a count
  is genuinely 0, show 0 / an empty-state label — never a mock number.

### XC2 — signals load state
- `loadMarketingSignals`: replace the `SIGNALS_MOCK`-as-skeleton with a real loading skeleton
  (spinner/placeholder) or empty state. No fabricated signal rows.

### Cleanup
- Remove now-dead helpers tied to the mocks (`_convertMockSignal`, demo-data labels, the
  "Static CAMPAIGNS format" branch ~1884) — but verify each has no remaining live caller first.

---

## Files owned
- EDIT: `public/js/features/marketing-os.js` (remove mocks + fallbacks, render real, empty states)
- EDIT: `public/js/core/api.js` (+a campaigns-list wrapper if needed)
- POSSIBLE EDIT: `artemis/marketing/routes/campaign_ops.py` (+`GET /api/marketing/campaigns` list if none exists) + a test for it
- POSSIBLE: a small test for the list endpoint

---

## Acceptance criteria
1. `grep -nE "CAMPAIGNS|SIGNALS_MOCK|APPROVALS_MOCK" public/js/features/marketing-os.js` returns
   ONLY incidental comments, no live mock arrays/fallbacks. **Paste.**
2. `node --check public/js/features/marketing-os.js` + `public/js/core/api.js` pass. **Paste.**
3. **Browser-load smoke (hard gate):** app loads, no console errors; the **Campaigns tab shows
   the real candidate(s)** (the 1 initiated campaign by its real name) — NOT "Michigan Field
   Guide"; dashboard counts reflect real data; with no campaigns the empty state shows. **Paste
   console + a description of what Campaigns renders.**
4. If a list endpoint was added: `pytest` for it passes. **Paste.**
5. `./scripts/check.sh` (JS + j5b Jira flake known-exempt) + `git diff --stat` + `git log --oneline -1`. **Paste.**
6. **COMMIT on `worker/cmp1-md1-remove-mocks`. Local git only, no push.**

---

## Hard constraints
- **No mock data anywhere in the render path** — real data or honest empty state. (XC2 coding
  rule: mock allowed only as a transient skeleton, never as a fallback for empty API results.)
- **Verify-before-delete** every helper removal (no remaining live caller) — this is the app's
  main marketing surface; a broken import breaks the page. The browser-load smoke is mandatory.
- **Don't touch the CI3 initiation form** or the signal-tree/Gate-1 rendering — only the
  Campaigns LIST + dashboard counts + signals load-state.
- **Local-only git.**

---

## Report-back format
```
CMP1+MD1 — remove marketing mocks report
1. Commit / branch
2. LOC per file (deletions)
3. grep proof: no live CAMPAIGNS/SIGNALS_MOCK/APPROVALS_MOCK
4. Browser-load smoke (console + what Campaigns + dashboard render with real data)
5. List endpoint (reused existing vs added new) + test
6. check.sh summary
7. Surprises — esp. the real campaign-candidates list endpoint shape + any dead helper that was still referenced
```
