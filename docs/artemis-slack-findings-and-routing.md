# Artemis-in-Slack — findings, routing, and approval governance (2026-06-06)

Captured from a long Slack PM session where Artemis went "beyond scope" triaging signals + finding bugs.
Net read: **good initiative + good safety judgment** (she refused to auto-fire side-effecting campaign
approvals, flagged framing risks, left positioning calls to Jon, proposed-not-applied fixes). Two friction
sources made it messy: a glitchy relay and a read/propose-only surface. Document now; fix after Codex
finishes WS Phase 2.

## A. Bugs / gaps found (queue behind Codex Phase 2 — small Codex briefs)
1. **Relay echo-loop (HIGHEST pain, infra).** Messages echoing, sessions crossing ("message from a
   different session"), Artemis can't tell Jon's input from echoes of her own ("Still an echo. Holding.").
   Burned major cycles. Investigate the Slack↔Artemis relay. **Fix this first.**
2. **`_snooze_signal` bug — CONFIRMED in code** (`artemis/floating_artemis/tools/marketing.py`). Passes the
   raw `until` string to the timestamp column AND writes `signal_status` directly via `update_signal`
   instead of the state machine `transition()` (no audit row; any-state snooze; fails on a real date).
   Fix: parse `until`→datetime (UTC, default now+14d) and route through `transition(.., SignalState.SNOOZED)`.
3. **Reject tool tier-gated, not missing.** `_reject_signal` EXISTS but is a tier-3 tool; Artemis's Slack
   surface had only tier-2 (`qualify_signal`, `snooze_signal`). She fell back to a score-0 "reject" hack.
   Fix = give the appropriate surface the reject tool (decide the tier policy), not build a new tool.
4. **Qualifier hard-filter has no signal-TYPE check (likely real — verify).** The 0.5 `min_fit_score` gate
   filters on score + territory, not type — so interim appointments + "search begins" signals pass and pile
   up as `qualified` noise. And `rejected_hard_filter` is a defined terminal state that **nothing writes**
   (fossil on signal 225). Fix: add a signal-type check to the hard filter (`qualifier.py` ~218) → interim/
   search-begins → `rejected_hard_filter` before scoring.
5. **Scoring-as-rejection is brittle.** "Reject" via score-0 relies on a downstream threshold gate; if a
   stage doesn't enforce it, score-0 signals leak forward. Prefer a real rejected status (ties to #3/#4).

## B. Slack channel routing (Jon 2026-06-06 — design, flesh out)
PMs being a firehose of everything is the problem. Route by purpose:
- **Ops/engineering channel** (e.g. `#artemis-ops`): system errors, bug findings, **fix proposals**, pipeline
  + health alerts, and the future **health + propose-upgrade** reports. (Most of the PM stream belonged here.)
- **Marketing channel** (e.g. `#artemis-marketing`): signal triage, Gate-1/Gate-2 approval prompts — the
  marketing workflow (what those PMs actually were; marketing-ops, not personal).
- **DMs reserved for the PERSONAL workspace:** Focus, Calendar, Meetings, OKR, daily brief, "what needs you
  today." Needs the personal section **fleshed out** to be genuinely useful (curated, not annoying) — its own
  design effort.
Goal: each surface is purpose-scoped, so DMs become a useful personal stream instead of an everything-feed.

## C. Approval governance (Jon's advisor question: approve fixes from Slack without consulting Lead?)
**Approving from Slack is fine as a GREENLIGHT; what's risky is an unreviewed code fix hitting main.**
- Her diagnoses are often right but not always (she was confidently wrong that `reject_signal` didn't
  exist). And the content-node fix took FOUR wrong band-aids before the real cause — auto-applying a
  Slack-approved "fix" can ship a plausible-but-wrong band-aid that HIDES the real bug.
- Safe pattern: **"approve" in Slack = "yes, pursue this"** (prioritization — fine to do solo). The actual
  code change still flows through **review → verify → merge** (Lead / Codex / terminal with the verify gate),
  never straight-to-disk/main.
- **Match depth to stakes:** trivial isolated reversible fix = a glance; load-bearing/shared code (executor-
  class) = real review. Ask: shared/load-bearing? blast radius? diagnosis verified? yes → review.
- **Operational actions** (qualify/reject/snooze signals, run a campaign) ≠ code fixes — those are hers to
  propose + Jon to approve, lower-stakes, and the system already gates the side-effecting ones.

## D. The behavior to formalize: "health + propose-upgrade schedule"
Her find-a-problem → propose-a-fix drive is exactly what the planned **scheduled health audit + upgrade
proposals** should do (cron/scheduled: audit the system, surface bugs/gaps/staleness, propose fixes as
briefs/PRs to the ops channel for human greenlight → review → merge). Connects to the model-freshness design
+ this episode. Roadmap it.
