# Build brief — Slack routing for signal clusters (QUEUED: build AFTER signals-funnel-redesign lands)

**Agent:** Codex or terminal (backend-heavy — Slack integration + the cluster lifecycle hooks; small FE for
deep-links). **Branch:** `worker/slack-signal-routing` off `main` **once the funnel redesign is merged**
(this depends on the unified clusters existing). **Own git worktree, cd inside it, own test DB
`artemis_test_slack`.** **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md` and the existing
Slack integration in the repo (grep `slack` under `artemis/` — the reference Node app had Slack approval
flows; reuse our posting + any interactivity plumbing, don't fork).

## Channel map (Jon-confirmed; Artemis bot already added to both new channels)
| Channel | ID | Posts | Buttons |
|---|---|---|---|
| **campaign signals** (action) | `C0B9CHVC7KQ` | Every new unified cluster | **Approve** + **View in Artemis** |
| **incoming signals** (sales view) | `C0B989DS5DZ` | **Hot** clusters only | none (view-only) |
| **Marketing Campaigns** (existing) | *(existing connected channel)* | After a campaign is initiated: asset approvals, performance reports | (existing/later) |

## A. Post clusters to the two signal channels
When a cluster surfaces (qualified + clustered — same clusters that drive the new Signals worklist):
- **campaign signals (`C0B9CHVC7KQ`)** — post the cluster as a Block Kit message: title (district/account),
  the "why ranked" summary, the underlying signals (source · snippet), and **two buttons: "Approve" and
  "View in Artemis."**
- **incoming signals (`C0B989DS5DZ`)** — post ONLY if the cluster is **hot** (define hotness from the
  existing prioritization — e.g. the "hot"/time-sensitive flag or velocity above the top-tier threshold used
  on the worklist; reuse the same signal, don't invent a new score). **View-only** — the cluster + its info,
  NO action buttons. This is the sales team's heads-up feed; do not fire-hose it with every cluster.
- **Dedup / update, don't duplicate:** post each cluster once; when its state changes (approved, dismissed,
  merged), UPDATE the existing Slack message (chat.update) rather than posting again. Track the channel+ts
  per cluster.

## B. The buttons (interactivity)
- **"Approve"** → fully **starts the campaign** — fire the SAME Gate-1 promote path the worklist
  "Start a campaign" uses (create the campaign candidate/workspace from the cluster's signals). Then UPDATE
  the Slack message to a done-state ("✓ Campaign started — <name>", with a link), and remove the buttons.
  Capture who clicked (Slack user → map to our user; reuse `users.lookupByEmail`, NOT list+filter — known
  pagination gotcha).
- **"View in Artemis"** → a deep-link to that cluster on the Signals worklist (e.g.
  `https://app.artemisos.me/#marketing-signals?cluster=<id>` — confirm the worklist supports focusing a
  cluster by id; add it if not).
- **NO reject/deny button in Slack.** Rejection happens in Artemis ON PURPOSE so the "why" dialogue captures
  the training reason (see signals-funnel-redesign A2). The Slack message can carry a hint ("Reject in
  Artemis →") but the action lives in-app.

## Dependency Jon must set (flag in the report; don't block on it for the posting half)
Interactive buttons require the Slack app's **Interactivity** to be ON with the request URL pointed at our
endpoint (e.g. `/api/slack/interactivity` or wherever the existing integration handles it). The POSTING half
(A) works without this; the BUTTON half (B) needs it. If interactivity isn't configured, build + verify
posting first and clearly document the exact Slack-app setting Jon must flip + the URL to paste.

## Acceptance (verify the EFFECT)
- A new cluster posts to **campaign signals** with Approve + View-in-Artemis; a HOT cluster ALSO posts to
  **incoming signals** view-only; a non-hot cluster does NOT hit incoming signals.
- Clicking **Approve** in Slack creates a real campaign (prove the candidate/workspace exists) and the Slack
  message updates to the done-state.
- **View in Artemis** opens the cluster in the app.
- No duplicate posts on re-runs (update-in-place). No reject button in Slack.
- Live Slack smoke against the real channels (IDs above) — assert the message posted + the button effect, not
  just HTTP 200. `./scripts/check.sh` for touched Python (note PRE-EXISTING separately).

## Constraints
Reuse the existing Slack integration + the funnel's promote/cluster code — don't fork. Lossless. Respect the
org dependency rule (no <7-day-old deps). Don't post secrets. Isolated worktree + own test DB. **Do NOT
merge** — report branch + SHA + worktree + the live Slack smoke proof + the exact interactivity setting Jon
must enable. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies
+ merges.
