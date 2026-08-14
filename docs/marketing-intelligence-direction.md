# Marketing intelligence — where we are and where we're going

**Owner:** Jon Fila. **Written:** 2026-08-14. **Status:** live direction, not a proposal.

This exists because two days of fixes and a live test with Josh produced a clear
direction, and none of it was written down. Read this before picking up work in
`artemis/argus/`, `artemis/market_signals/`, `artemis/marketing/`, or Callie's tools.

---

## The thesis, in Jon's words

> "I don't want to lose information that Argus and her uncover, because that leads to
> the power of the app that we have been building — to tailor marketing campaigns and
> track trends and info over time to build profiles on districts and states."

**The accumulating knowledge is the moat, not the drafting.** Anyone can write an
outreach email. What compounds is a district profile that gets richer every time we
touch it: who leads it, what they said they care about, when they buy, what they
already own, what happened last time. That is the asset. Every design decision below
serves it.

Corollary that decides arguments: **if a research pass produces knowledge that lands
only in a Slack message, it did not happen.** Slack is a delivery surface, never a
store of record.

## Where we are (verified 2026-08-14, not assumed)

**Working and proven:**

- **Argus researches districts end to end.** Enqueue-only dispatch, claimed by an
  in-app worker, survives the turn that asked for it. Findings persist to
  `memory_observations` (`category='district_research'`) via the drawer model.
- **Search resolves to district NAMES**, not drawer keys. `_fetch_news("11414")`
  returned nothing; `"FORT WORTH ISD"` returns 15 on-topic items.
- **Board minutes reach Argus** for 26 mapped districts, item bodies included, with
  an Argus-specific literacy filter validated on ~2,000 real agenda items across 11
  districts (zero false positives).
- **Tool use is observable** — `agent_traces.tools_used` is populated on both the
  conversational and pipeline paths. Before 2026-08-12 it was empty for every agent
  for 30+ days, which is why a five-week outage went unnoticed.
- **The daily brief** posts once a weekday to `#market-signals` (09:00 ET),
  combining campaign signals, crisis signals and screentime, ranked by buying intent.
- **Callie can DM** a fixed allowlist, with attribution in the message body.
- **Crisis-content approvals** run live with the vendor.

**Known thin, with reasons:**

| gap | why |
|---|---|
| `current_vendor`, `competitor_commitments` | Board agendas rarely name a literacy vendor in any given month. Real scarcity, not a bug. |
| USASpending | Every CFDA program we track pays the **state** agency; a district is structurally never the recipient. Dead end, stop counting it. |
| St. Louis, Elgin | Free-text drawer keys rather than numeric ids, so district lookup misses. Needs key canonicalization. |
| Salesforce | Not connected. Customer status and open opportunities are a manual check Josh does before every send. |

## The direction

**Josh drives campaigns from Slack; the app is where things live.** He said so by
behaviour, iterating 42 email drafts across 8 districts in chat. Slack is limiting and
he wants it anyway — so the answer is not to move him, it is to make Slack a good
control surface backed by real storage.

Four things follow, in this order. **The order is the argument** — each one is
useless without the one before it.

### 1. Written collateral persists and is linked (nothing exists yet)

Today: **zero** `campaign_deliverables` created in the last three days, while the
channel holds **42 drafted emails**. Every one of Josh's iterations is Slack text
only — not attached to anything, not editable by Angela, and it scrolls away.

So this is not a convenience feature. It is the difference between a day's work
existing and not existing. Requirements from Jon:

- Retroactively persisted into the app, **linked from the Slack message** so Josh
  never has to go looking.
- Editable by hand, by Josh or Angela, for fine-tuning.
- **Organised in a folder structure** — a flat list becomes unusable fast.

### 2. Contacts become a queryable, wipeable store

Argus already researches **people**: 14 findings name a superintendent. All of it is
prose inside observations, so nothing can ask "who runs Harford County", the email
drafter cannot personalise from it, and no single person could be removed on request.
`district_contacts` exists with the right shape (name, title, email, phone, source,
external_id, active) and holds **6 rows across 3 districts**.

**This carries the one hard architectural tension in the whole plan.** CLAUDE.md rule
3 says observations are never deleted, only superseded. Jon requires contacts be
"referenced and also wiped if need be" — correct, because these are real external
people and a removal request is a legitimate obligation.

Resolution: **PII lives in `district_contacts`, which is deletable. Observations
reference a contact rather than embedding the person's details.** The lossless rule
keeps applying to research; the personal data sits in a table that can be purged
without tearing holes in the knowledge graph. Anyone implementing this should not
"solve" it by making observations deletable.

### 3. Campaign creation is visible and firable from Slack

Jon: *"the campaign creation ability should be an option that I see for user
experience."* An affordance, not a hidden command.

**Today, asking for a 3-email sequence creates nothing.** No campaign, no
deliverable, no record. Once (1) exists there is something to attach; until then
"fire a campaign from Slack" has nothing to fire.

### 4. HubSpot last

Jon: *"after all this is finished and well oiled — I don't want to accidentally send
something off to someone."* Sending is the only irreversible step in the chain. It
goes last, on purpose.

## Presentation

Slack Block Kit gets meaningfully closer to the crisis-content approval cards than
plain text: collapsible sections, per-item buttons, an "open in app" link, and modals
that hold a full sequence without flooding a channel. What it will not do is rich
inline editing — that is what the app link is for. Target shape: **a compact card in
Slack, full text one click away.**

Measured baseline for why this matters: 60 messages in `#demand-gen-callie` total
**48,000 characters**, averaging 800 per message.

## Open questions

- **Salesforce**: OAuth Client Credentials, no redirect URL needed. Outbound egress is
  **108.18.96.219** on Verizon Business; whether that is contractually static is a
  question for the Verizon account owner, not answerable from the machine. Recommend
  not restricting by IP until confirmed — a reassignment presents as an auth failure.
- **What can Callie actually see?** Jon tested her for internal-information exposure
  deliberately. Not yet reviewed. This is the one open item with a security edge.
- **Josh's own list.** He is still testing to uncover functionality and will produce
  requirements afterwards. **Do not over-build ahead of it.**

## How we work (earned the hard way this week)

- **Delegate implementation to Sonnet workers in worktrees**; keep briefing, auditing,
  live verification and merging on Opus. One test database per worker — two pytest
  runs on one DB deadlock on TRUNCATE. Assign migration numbers explicitly in the
  prompt; two workers given the same number produced a duplicate revision that merged
  cleanly and only failed at runtime.
- **Ask workers to flag anything they believe is wrong rather than building to it.**
  That line caught a brief whose goal was unreachable, a fix that would have been a
  no-op, a data-loss bug three agents had tripped over, and a filter matching a
  Bible-reading resolution.
- **Verify the effect, never the report.** Six separate bugs this week were something
  claiming more than it had — a tool reporting "dispatched" having started nothing,
  `tools_used` empty while tools fired, 0.9 confidence on a coin flip, a finding
  attributed to a source that returned nothing. Including one of mine: I repeated
  "the classifier needs an API key" three times without running it. It never did.
