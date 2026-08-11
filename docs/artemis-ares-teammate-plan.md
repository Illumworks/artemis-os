# Plan — Artemis & Ares as teammates, not assistants

**Written:** 2026-08-11 by Opus Lead, from a capability audit of both agents against the live
system (tool registries, real activity history, and live failure logs).

**Jon's framing, which drives everything below:** Artemis and Ares are meant to help him
*create, manage and maintain projects and applications* — teammates, brothers in arms — and they
need to do that **without him babysitting**, correcting limitations and gaps by hand.

**Owner decisions (Jon, 2026-08-11):**
- Ares writes **freely in a sandbox**; promotion to anything real is **Jon-approved**.
- First pain to kill: **no re-briefing** (durable project memory).
- Division: **Artemis oversees, Ares builds.**
- Reporting: **receipts, not claims** — every completed action carries its evidence.

---

## 1. Where they actually stand

### Ares — a persona with read access, never used

His complete working history is **four messages on 2026-06-19, all "this is a test message."**
Two provider calls, ever. He answered "What are we building?" twice and was never given anything.

That is not neglect — he **structurally cannot do the job**. `_build_ares_tool_registry()`
(`artemis/floating_artemis/tool_registry.py:45`) gives him, at most, eight read-only tools:
`query_memory`, `list_scopes`, `surface_status`, `read_file`, plus `read_project_file`,
`list_project_dir`, `git_status`, `git_diff` when a project path is set. No write, no create,
no delegate, no spawn. He can describe Jon's projects. He cannot touch them.

Against the four capabilities in `docs/ares-plan.md`:

| Capability | Status |
|---|---|
| Durable project memory (no re-briefing) | ❌ **not built** — the keystone, and Jon's stated #1 pain |
| Multi-provider sub-agent fleet | ✅ exists (provider cascade, feature tiers) |
| Auto-delegate → validate → report-up | ❌ **not built** — gated on a delegation primitive that does not exist |
| Named-agent wrapper (persona/Slack) | ✅ exists |

The two unbuilt capabilities are exactly the two that separate a teammate from an observer.

`spawn_subagent` is not a substitute. Its own docstring says *"SPAWN (do, return, disappear) —
NOT propose (save, persist, reuse)"* (`tools/core.py:714`). It is one-shot and fire-and-forget:
no named delegation, no multi-step work, no result gathering, no validation.

### Artemis — real, but her "make things happen" layer is hollow

She works where it is verifiable: morning brief delivered every weekday without a miss,
conversation healthy (live-verified 2026-08-10, 9-second reply), and the propose→confirm agency
gate is genuinely live for OKR, Jira and Calendar writes.

Three things are broken, all the same way:

1. **`propose_edit` is a stub.** `tools/core.py:463` builds a dict, `json.dumps` it, and returns
   it as a string. No file write, no persisted record, no queue, no follow-up. When Artemis says
   "I've proposed that change," nothing exists afterward.
2. **Hub escalation fails every hour.** `artemis/hub/escalation.py:169` `_post_in_channel()`
   hardcodes `agent_id="artemis"` for the Slack token, then posts into whatever channel the
   pending ask came from — including Kai's private `C0BB17EJLKC`, which Artemis is not a member
   of. Result: `SlackAPIError: channel_not_found`, **13 times in the current log**, on an hourly
   cron. It silently falls back to DMing Jon, which is why the symptom was visible but the cause
   was not.
3. **No agent-to-agent path.** Kai had no way to reach Artemis, which is why it fabricated one
   and told Jon it had escalated (see `briefs/kai-upgrades.md` F1).

## 2. The root cause — one defect, three agents

Across Kai, Artemis and Ares the same thing is true:

> **They cannot reliably act, and they cannot tell when they failed.**

Kai announced escalations it had no tool for. Artemis announces proposals that evaporate and
posts that fail hourly. Ares cannot act at all. In every case **nothing noticed** — the failure
was silent and Jon discovered it by observing the absence of a result.

That is the babysitting, precisely. It is not an intelligence problem. The missing layer is
between *"decided to do something"* and *"it actually happened, and I verified it."*

**This is why Phase 0 below is the verify-and-report layer, not a feature.** Every capability
added on top of a hollow action layer inherits the hollowness.

---

## 3. The plan

### Phase 0 — Truthful action (build first; it is small and everything depends on it)

Applies to **all** named agents. Kai's Stream 1/2c in `briefs/kai-upgrades.md` is the same work —
build it once, agent-agnostic.

- **No tool may report success it did not verify.** Every side-effecting tool returns a real
  result (id, URL, commit sha, message ts) or an explicit failure. Agents report the receipt.
- **Delete or implement stubs.** `propose_edit` either persists a real proposal or is removed
  from the registry. A tool that exists but does nothing is worse than no tool — it teaches the
  agent it can do something it cannot.
- **Fix hub escalation** (`escalation.py:169`): use the token of the agent that owns the ask
  (`ask.agent_id`), which is by definition in that channel, instead of hardcoding Artemis.
- **Announce failure.** On provider/tool failure the agent says so in-channel rather than going
  quiet. No invented causes.
- **Detect silence.** Extend `artemis/ops/health.py` to flag inbound-with-no-replies — an agent
  receiving Slack events but producing no turns. Nothing currently detects the July outage
  signature.

### Phase 1 — Durable project memory (Jon's #1 pain: no re-briefing)

Ares resumes any project cold, knowing the plan, the decisions, what is done, what is open, and
the file map. Mechanism per `docs/ares-plan.md` §4: write project state into the memory keystone
and auto-load the project drawer at session start.

- A **project workspace drawer** per project: plan · decisions (with rationale) · progress ·
  open threads · key file map. `project_workspace_memory` already exists — check before building.
- **Written continuously, not at the end.** State captured as work happens, so a crashed or
  abandoned session loses nothing.
- **Session→memory bridge**: capture what Jon and Claude Code are doing in the terminal into the
  keystone, so Artemis can answer "what am I working on?" This delivers the bridge's value
  *before* full Ares exists, and is the cheapest useful slice.
- Verify by the only test that matters: start a cold session, say "pick up where we left off,"
  and get a correct answer with zero briefing.

### Phase 2 — Ares builds in a sandbox, Jon promotes

**Owner decision: write freely in a sandbox; promotion is Jon-approved.**

- **The sandbox is a git branch/worktree, not a copy.** This is the load-bearing design choice:
  promotion becomes a *merge*, not a re-application, so nothing drifts and review is an ordinary
  diff. Branch convention `ares/<project>-<slug>`.
- Ares gets real write tools **scoped to his worktree**: create/edit files, run tests, commit.
  Never `git push`, never a merge to `main`.
- **Blast radius:** free rein inside his own project repos. **artemis-os and anything
  customer-facing always require Jon's approval** — a bad run must not reach production quietly.
- **Promotion flow:** Ares reports done *with receipts* (branch, diff stat, tests run and their
  output, what he verified and what he could not) → Jon reviews → approve merges, reject deletes
  the branch and nothing leaked.
- Reuse the existing agency gate (`agency_gate.py`) rather than inventing a second approval path.

### Phase 3 — Delegation that gathers results

The capability that kills the copy-paste-between-terminals pain. Needs Phase 1 to be useful
(a delegate with no durable context re-briefs, which is the problem restated).

- A **named, multi-step delegate primitive**: dispatch a chunk to a sub-agent (any provider),
  track it, collect the result, **validate it**, and report up. `spawn_subagent` is the wrong
  shape — extend or replace, do not overload it.
- **Validation is mandatory, not optional.** The lesson from this session's own delegation: of
  three sub-agents, one reported a production bug that was actually a stale test mock, and one
  burned its entire budget without committing anything. Ares must verify a sub-agent's claim
  before reporting it up — exactly as a Lead does.
- Route by cost/capability via the existing `feature_catalog` tiers.

### Phase 4 — Artemis oversees

**Owner decision: Artemis oversees, Ares builds.**

- Artemis can hand build work to Ares and report status up to Jon — closing the build-world /
  assistant-world split that `docs/ares-plan.md` was written to fix.
- Requires the working agent-to-agent path from Phase 0. Do not build this before that works;
  the current failure mode is precisely a broken hand-off.

---

## 4. Sequencing rationale

Phase 0 first because it is small, it fixes live bugs, and it is the foundation for trusting
anything either agent says. Phase 1 next because Jon named it as the pain to kill first and
because Phase 3 is not useful without it. Phase 2 gives Ares hands. Phase 3 gives him a team.
Phase 4 connects him to Artemis.

**Do not start Phase 3 or 4 before Phase 0 is real.** Delegation and hand-off on top of an
unverified action layer produce confident reports about work that did not happen — which is the
exact failure this whole plan exists to end.

## 5. Settled (Jon, 2026-08-11)

1. **Sandbox location:** local on the Mac mini. A projects directory Ares owns, each project its
   own git repo, each task its own branch/worktree.
2. **`propose_edit`:** **delete it** in Phase 0. It is a stub, and Phase 2's branch-and-diff flow
   is the real answer — a proposal queue would be a worse reimplementation of git.
3. **Unattended long jobs: yes.** This is the point — Ares runs research, builds and test suites
   without Jon watching. Implications to build for: bounded cost per run, a hard wall-clock cap,
   progress written to project memory as it goes (so a killed job is resumable, not lost), and a
   report-with-receipts on completion or failure.
4. **First test project:** see §6.

---

## 6. First project — State Policy Tracker (screen-time + AI-in-schools)

**A standalone web tracker: one page per US state showing current screen-time and AI-in-schools
policy activity — bills with real status, recent news, stance, and last-updated.**

### Why this one

- **It is a decision already made, never executed.** The team explicitly chose *not* to scrape
  Whiteboard Advisors' Tableau tracker (their product, downloads disabled) and to broaden Amira's
  own scouts instead. This is the deliverable that decision was pointing at.
- **The data already exists and is live.** `screentime_signals`: 94 signals across 26 states,
  collecting daily since 2026-06-19, from both `legislative` (17) and `national_news` (77).
  Ares starts with real material, not a blank page. The coverage gaps are themselves useful
  output — they show where collection needs work.
- **Real value.** A publishable artifact for Amira, and the thing a competitor has that Amira
  does not.
- **Zero production risk.** Own repo in Ares's sandbox. Reads the DB, writes nothing to it.
- **It genuinely exercises every capability being built:**
  - *Durable memory* — multi-session by nature (design → build → data-quality iteration), so
    "pick up where we left off" gets tested for real rather than theoretically.
  - *Unattended long jobs* — generating 51 state pages, backfilling classifications.
  - *Receipts* — every claim on the page traces to a source URL; every build reports what was
    verified.
  - *Sandbox → approval → promote* — Jon reviews a running page, not a diff he has to imagine.
- **Judgeable in ten seconds.** Open it and look. No code reading required to know if it worked.

### Rough shape (Ares should plan this WITH Jon, not receive it as a spec)

A static-generated site is likely right: cheap, no infra, publishable later. One index with a
50-state map or table, one page per state, a "recently changed" feed, and honest empty states for
the 25 states with no signal yet. Stance rendering needs care — per the Screen-Time notes, a
chatbot ban is *not* automatically unfavorable to Amira, so the tracker should show the finding
and its reasoning rather than a bare good/bad label.

### What success proves

1. Ares held the project across days without being re-briefed.
2. He ran a long job unattended and reported back with receipts.
3. Jon reviewed and promoted work he did not supervise.
4. Amira gets something publishable.

**Deliberately not chosen as the first project:** the session→memory bridge (circular — Ares
building his own memory), an ops dashboard (touches artemis-os), and closing Kai's content gaps
(research, not building, and it overlaps work already in flight).

## 6. Related documents

- `docs/ares-plan.md` — the original Ares design; §2 capability table and §4 no-re-briefing
  mechanism remain correct and are cited above.
- `briefs/kai-upgrades.md` — Kai's Stream 1 and 2c are the same Phase 0 work, agent-agnostic.
- `docs/artemis-pa-build-plan.md`, `docs/artemis-hub-plan.md` — Artemis's original design.
- `docs/HANDOFF-2026-08-10-compose-auth-slack.md` — the auth outage behind the silent-failure
  finding.
