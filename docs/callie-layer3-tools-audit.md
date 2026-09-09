# Callie's ten layer-3 tools — what each one actually does

**Date: 2026-09-09.** Read-only code audit. Nothing here was executed against the
live database or Slack.

**Why this exists.** `_build_callie_tool_registry`
(`artemis/floating_artemis/tool_registry.py`) registers 40 tools for Callie, but
`build_floating_artemis_tool_set`
(`artemis/tools/mcp_server.py:322`, gate at `artemis/tools/mcp_server.py:354` —
`if entry.layer > 2: continue`) drops every layer-3/4 tool before the claude-code
subprocess ever lists them. Ten tools are therefore registered and unreachable in
production. Jon is deciding whether Callie should be able to act rather than only
report, and the registered one-line descriptions are not enough to decide on.

**How to regenerate.** `grep -rn "registry.register(.*layer=3" artemis/` finds the
registrations; each handler is the `_<tool_name>` function in the same module.
Re-check `artemis/tools/mcp_server.py:354` to confirm the gate is still in place.

---

## The table

| Tool | 1. What it writes | 2. What it triggers downstream | 3. Reversible? | 4. Confirmation flow | 5. Works today? |
|---|---|---|---|---|---|
| **approve_signal** | `signal_queue.signal_status = 'approved'` + `updated_at`, via `repo.update_signal` — `artemis/floating_artemis/tools/marketing.py:541`, write at `artemis/marketing/repository.py:142`. Then a fire-and-forget `memory_observations` write (+ `memory_evidence` links) via `write_fa_marketing_approval_observation` — `artemis/builder/memory_carryover.py:595-640`. | **Far less than the description implies.** It sets a column. It does **not** call `promote_signal_to_candidate`, so unlike the real route (`artemis/marketing/routes/signal_queue.py:490`) it creates **no `campaign_candidates` row**, writes **no `campaign_state_transitions` audit row**, records **no engagement observation**, and starts **no pipeline**. Second-order only: `repository.py:101` and `:419` treat `approved` signals as promotable, so a later human promote/cluster call will accept the signal. Nothing fires by itself. | Partly. The column can be set back by any `update_signal` caller (`snooze_signal`, layer 2, already does exactly this). But the state machine cannot undo it: `SignalState.APPROVED` maps to an empty transition set (`artemis/marketing/state_machine.py:129`), so the supported path is closed. The memory observation is never deleted (lossless-memory rule). | Layer 3. In-process (`_build_intercepting_tool_registry`, `chat.py:1494`) the turn suspends and the operator confirms. On Slack, `route_inbound` (`artemis/routes/integrations_slack_events.py:1078-1100`) classifies the **next inbound message** as YES/NO. The operator sees **the model's prose description, not the payload** — see "What the confirmation actually shows" below. | Yes — the write path is real and complete. |
| **reject_signal** | `signal_queue.rejected_reason` (only when a non-empty reason is given) + `signal_queue.signal_status = 'rejected_at_gate_1'` via `state_machine.transition`, plus one append-only `campaign_state_transitions` row — `artemis/floating_artemis/tools/marketing.py:583`. With a reason, also a `memory_observations` row via `record_signal_engagement` (`artemis/marketing/callie_push.py:371`). | Down-weights future Callie signal pushes: `record_signal_engagement` writes a `callie_signal_engagement` observation that `get_engagement_weights` reads when ranking proactive pushes. No pipeline, no message, no send. | **No, not through supported code.** `transition` only allows `qualified → REJECTED_AT_GATE_1`, and `REJECTED_AT_GATE_1` is terminal (`state_machine.py:130`). Raw SQL could reverse the column; the audit row and the observation cannot be removed. | Same layer-3 flow as above. | Yes. Note it is *stricter* than `approve_signal` — it goes through the state machine, so it raises `IllegalTransition` on a signal that is not `qualified`. |
| **assemble_brief** | One `campaign_briefs` row (`candidate_id`, `content`, `generated_at`, `generated_by=NULL`) — `artemis/floating_artemis/tools/marketing.py:664`, insert at `artemis/marketing/repository.py:814`. | No immediate trigger. But `campaign_briefs` is read latest-first by `pipelines/node_executors/agent_executor.py:175` (agent context) and `pipelines/node_executors/human_gate_executor.py:870` (gate-card rendering), so the new row silently **becomes the brief those surfaces show** on the next pipeline run touching that candidate. | Not deletable (append-only table, lossless rule). Recoverable only by writing a newer, correct brief — the newest row wins by `generated_at`. | Same layer-3 flow. | **Works, but it does not do what its name says.** It never calls `brief_assembler.assemble_brief` (`artemis/marketing/brief_assembler.py:152`). It persists whatever JSON the model puts in the `content` argument, unvalidated, defaulting to `{}`. A call with no `content` writes an empty brief that then shadows the real one. |
| **submit_draft_for_review** | Via `ws_invoke.submit_draft_for_review` (`artemis/marketing/writing_studio/invoke.py:611`): one `approvals` row `kind='writing_gate_2'`, `status='pending'` (`invoke.py:687`); `campaign_deliverables.status → draft_ready` plus a `campaign_state_transitions` row when not already there (`invoke.py:691`). Commits internally. | Two things. (a) An in-process `draft.approved` event on the Writing Studio bus (`invoke.py:699`) — in-memory, single-process, subscriber failures swallowed. (b) **A scheduled Slack DM**: `send_stale_review_escalations` (`artemis/marketing/writing_studio/review_escalation.py:24`, wired into the proactivity scheduler at `artemis/proactivity/scheduler.py:155`) joins pending `writing_gate_2` approvals to deliverables and DMs the resolved reviewer. It is conditional — it skips any deliverable with no `ready_for_review_at` / `review_requested_at` in `deliverable_metadata`, and this tool does not set that metadata (only `POST /drafts/{id}/ready-for-review`, `routes/writing_studio.py:1765`, does). So on a deliverable that has never been through that route, no DM. On one that has, a DM to a human can follow hours later. | The approval row can be decided (not deleted). `draft_ready` is reachable again from `rejected` (`state_machine.py:181`), so the deliverable state is recoverable. A DM that has gone out is not. | Same layer-3 flow. | Yes. Idempotent: an existing pending `writing_gate_2` approval is returned rather than duplicated (`invoke.py:637-663`). The external Writing Studio call is a no-op stub unless `ARTEMIS_WRITING_STUDIO_URL` **and** `_TOKEN` are set (`writing_studio/external.py:208`). |
| **decide_approval** | `approvals.status`, `.decided_by`, `.decided_at` — `artemis/floating_artemis/tools/marketing.py:699`, write at `artemis/marketing/repository.py:1060-1062`. `decided_by` defaults to the string `"artemis"` and is taken from **tool input**, so the recorded decider is model-supplied, not an identity. | **Nothing — and that is the danger.** It calls `repo.decide_approval` directly, bypassing `apply_approval_decision` (`artemis/marketing/routes/approvals.py:211`). So none of the real gate side effects run: no PIPE4 gate resume, no Gate-1 signal promotion (`approvals.py:492`), no deliverable transitions (`approvals.py:447`), no `enqueue_send_for_deliverable` (`approvals.py:549`). | **Effectively no.** `apply_approval_decision` refuses any row that is not `pending` (`approvals.py:224`), so once this tool flips a row the legitimate UI/Slack path can no longer decide it — the gate it belonged to is stranded, decided on paper and unactioned in fact. Reversal needs raw SQL. | Same layer-3 flow. | It writes successfully, so it "works". But it is the one tool here whose correct-looking success is a false report of a decision having been applied. |
| **propose_ruleset_change** | One `approvals` row `kind='ruleset_change'`, `status='pending'`, `subject_id=<ruleset_id>`, `decision_payload={type, ruleset_id, changes}` — `artemis/floating_artemis/tools/marketing.py:838`, insert at `artemis/marketing/repository.py:1044`. Validates the ruleset exists first. | **Nothing.** `grep` finds no reader of `kind='ruleset_change'` anywhere in `artemis/`. Ruleset activation is a separate path (`repository.activate_ruleset_version`, reached only from `routes/signal_criteria.py:168`) that never consults this row. The proposal shows up in `GET /api/approvals` and nowhere else. | The row is inert, so nothing needs undoing; it can be decided but deciding it also does nothing. | Same layer-3 flow. | It writes the row. It does **not** propose a change to anything that will ever read it — the write is a dead end. Note also that CLAUDE.md names Writing Studio rules an owner-judgment surface and `_build_callie_tool_registry` deliberately withholds `propose_writing_rule` for that reason; this is the qualification-ruleset analogue and it is still registered. |
| **link_content_asset** | One `content_asset_links` row (`candidate_id`, `asset_id`, `link_role`) — `artemis/floating_artemis/tools/marketing.py:967`, insert at `artemis/marketing/repository.py:992`. Unique on `(candidate_id, asset_id)`; a duplicate raises and is returned as a failure string. | Nothing immediate. The link is read by campaign/asset surfaces (`list_campaign_asset_links`); no pipeline or message. | No delete path exists in the public API (lossless rule). A wrong link stays. | Same layer-3 flow. | Yes. Lowest-consequence tool in the set. |
| **post_analyst_message** | **No DB write.** | **Posts a Slack message as Callie's own bot** — `chat.postMessage` at `artemis/floating_artemis/tools/marketing.py:375`, HTTP call at `artemis/integrations/slack/client.py:49`. Constrained: it resolves Callie's *own* integration row by `agent_id == "callie"` (`marketing.py:156`) and refuses any channel outside her allowlist (`metadata.allowed_channel_ids`, else the defaults `#campaign-signals` `C0B9CHVC7KQ` + `settings.marketing_campaigns_slack_channel`) — `marketing.py:75-100`. Text passes through `lint_agent_text` first. | **No.** Slack messages are not deleted by any code here. A correction is a second message. | Same layer-3 flow. | Yes, provided Callie's Slack integration row exists and is active; otherwise it returns "Callie's Slack integration is not configured for analyst posting" and does nothing. |
| **send_slack_message** | **No DB write.** | **Posts a Slack message to any channel the token can reach** — `artemis/integrations/slack/tools.py:53`. No channel allowlist, no linting, and it accepts arbitrary `blocks`. Identity is *not* pinned to Callie: it takes `integrations[0]` from `repo.list_active(provider="slack")`, ordered `connected_at DESC` (`artemis/integrations/repository.py:112-117`) — i.e. the **most recently connected** Slack bot in the workspace, which may be Artemis's. | **No.** | Same layer-3 flow. | Yes. This is the broadest tool in the set: `post_analyst_message` is the same capability with a channel allowlist, a lint pass and a pinned identity, and this one has none of the three. |
| **react_to_slack_message** | **No DB write.** | Adds an emoji reaction via `reactions.add` — `artemis/integrations/slack/tools.py:125`. Same "first active integration" identity problem as `send_slack_message`. Visible to everyone in the channel. | Not by this tool — there is no `remove_reaction` handler anywhere in the registry. A human can remove it in Slack. | Same layer-3 flow. | Yes. Lowest-consequence of the three Slack tools, but still an externally visible act. |

---

## Notes that do not fit a cell

### What the confirmation flow actually shows a human

Two implementations exist, and neither shows the operator the literal payload on
the Slack surface Callie lives on.

- **Web / in-process path** (`_build_intercepting_tool_registry`,
  `artemis/floating_artemis/chat.py:1494`): the turn raises
  `_PendingConfirmationError`, the run suspends, and a
  `floating_artemis.tool_pending` WS event carrying the **full `tool_input`** is
  broadcast (`chat.py:1060-1069`). A web operator can see the exact arguments.
  Confirmation arrives at `POST /api/floating-artemis/sessions/{id}/tool-confirm`.
- **Slack path**: `route_inbound` reads `confirmation_store.list_for_session`,
  takes the **most recent** pending, and runs an LLM YES/NO/NEITHER classifier
  over the next inbound message
  (`artemis/routes/integrations_slack_events.py:1078-1100`). What the human sees
  is the assistant's own prose, extracted from the suspended turn
  (`chat.py:1084-1087`) — a description the model wrote, not the payload.
  `_STAGED_TOOL_MESSAGE` (`chat.py:1560`) literally instructs the model to
  "tell them plainly what will change".

Three properties of the Slack path are worth naming explicitly, because they
decide how much protection layer 3 actually is:

1. **The confirmer is whoever replies next.** Nothing compares the replying
   `slack_user_id` to the requester, or to any allowlist. In a shared channel
   such as `#demand-gen-callie`, any member's "yes" executes. This is exactly
   the reasoning already recorded in `_build_callie_tool_registry` for
   withholding `send_slack_dm` and `propose_writing_rule`.
2. **Confirmation state is in-process memory.** `ConfirmationStore`
   (`artemis/floating_artemis/authority.py:120`) is a plain dict on a module
   singleton. An app restart silently discards every pending confirmation.
3. **`resume_after_confirm` rebuilds the registry without `speaker_id`**
   (`chat.py:1338-1341`). Identity-gated tools fail closed on the resume path.
   None of these ten read `speaker_id`, so it does not weaken them — but it
   means the resume path has no notion of who authorised the call either.

### The gate that is actually in force today

Callie runs on the claude-code adapter, whose tools are served from a subprocess
(`_serve_floating_artemis`, `artemis/tools/mcp_server.py:1058`). The parent passes
a name allowlist, the subprocess rebuilds the registry itself, and
`build_floating_artemis_tool_set` filters `entry.layer > 2` before listing
(`mcp_server.py:354`). So these ten are not merely blocked at call time — the
model is never told they exist. Lifting the gate is a code change in
`mcp_server.py`, plus a decision about which confirmation flow applies, since the
subprocess cannot yield into the in-process one (that is the stated reason the
gate exists).

### `approve_signal` is the tool whose description is most wrong

Registered as "side-effect: status change + downstream triggers"
(`marketing.py:1102`). There are no downstream triggers. The real Gate-1
approval — candidate creation, state-machine transition, engagement recording —
lives in `promote_signal_to_candidate` (`artemis/marketing/repository.py:530-560`),
which this handler does not call. It is also the only one of the four
signal/approval writers here that skips the state machine entirely, so it can move
a signal to `approved` from any state, including terminal ones like
`rejected_hard_filter`, with no audit row. Under the repo's own rule — *a tool
must never report success for work it did not do* — this handler reports a
promotion it did not perform.

### Nothing in this set sends an email

`enqueue_send_for_deliverable` (`artemis/marketing/sends.py:205`) is reached only
from `routes/approvals.py:549`, never from these tools. And even that path is
inert as outbound: `campaign_sends.transport` is hardcoded `"stub"` and
`mark_send_sent` writes `"NO REAL EMAIL — transport pending ESP"`
(`sends.py:355-361`). No worker consumes `status='queued'` rows. External email is
not currently reachable from anywhere in this system.

### Fire-and-forget writes

`approve_signal` and `reject_signal` both spawn background work
(`asyncio.create_task` for the memory observation; an inline `await` for the
engagement observation). Failures are logged and swallowed by design, so the tool
returns success whether or not the observation landed. Verify the effect in
`memory_observations`, not the tool's return string.

---

## Which of these would actually leave the building

**Externally visible — a human outside the system sees it, and it cannot be
retracted by any code here:**

| Tool | What escapes |
|---|---|
| `post_analyst_message` | A Slack message in `#campaign-signals` (or the configured marketing-campaigns channel), posted as Callie. Bounded by an allowlist and linted. |
| `send_slack_message` | A Slack message in **any** channel the bot token can post to, unlinted, arbitrary Block Kit, posted as whichever Slack bot was connected most recently. Unbounded. |
| `react_to_slack_message` | An emoji reaction on a specific message, visible to the channel. |
| `submit_draft_for_review` | *Indirectly and on a delay.* Creates the pending `writing_gate_2` row that the scheduled stale-review sweep turns into a Slack DM to a named reviewer — but only for a deliverable whose metadata already carries `ready_for_review_at` / `review_requested_at`. |

**Internal only — a row changes state and nothing leaves:**

| Tool | Internal effect |
|---|---|
| `approve_signal` | `signal_queue.signal_status` + a memory observation. |
| `reject_signal` | `signal_queue.signal_status` / `rejected_reason`, one audit row, and a learning observation that shifts future push ranking. |
| `assemble_brief` | A `campaign_briefs` row that becomes what pipeline agents and gate cards read. |
| `decide_approval` | An `approvals` row flipped out of `pending` — with none of the effects deciding it is supposed to have, and no supported way back. |
| `propose_ruleset_change` | An `approvals` row nothing reads. |
| `link_content_asset` | A `content_asset_links` row. |

The line worth drawing is not layer 3 versus layer 2. Three tools put words in
front of colleagues; one can wake a colleague with a DM days later; two
(`decide_approval`, `assemble_brief`) are quiet writes that corrupt what a human
will later be shown or blocked from doing, which is arguably worse than a message
that can be corrected in the next line.

---

## What could not be determined from the code

- **Whether Callie's Slack integration row and its `allowed_channel_ids`
  metadata are actually populated in production.** `post_analyst_message` fails
  closed without them; `_default_callie_channel_ids` supplies a fallback. This
  needs a live query against `integrations`, which this audit did not run.
- **Whether any production deliverable carries the metadata that would let
  `submit_draft_for_review` result in a DM.** That depends on whether
  `POST /drafts/{id}/ready-for-review` has been used on it. Row data, not code.
- **Whether the proactivity scheduler's stale-review job is enabled in the
  running process.** It is registered at `artemis/proactivity/scheduler.py:155`;
  whether that scheduler is live, and at what `review_escalation_age_hours`,
  is runtime configuration.
- **What a stranded PIPE4 gate does over time** after `decide_approval` flips its
  approval row outside `apply_approval_decision`. The refusal at
  `approvals.py:224` is certain; the resulting pipeline-run behaviour was not
  traced end to end here.
