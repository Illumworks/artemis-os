# Writing Studio + Self-Training Audit & Roadmap — 2026-06-03

**Why this doc exists:** Jon flagged that the Writing Studio (and the "self-training"
core of the app) feels like a gap / lost knowledge across the Node→Python rebuild. We
audited it. This captures *reality vs. spec* and the agreed build order so it isn't
re-lost. The frozen Node reference at `../claudeck-artemis/` has the working originals —
most of this is a **port**, not a from-scratch design.

This is plain-English-first; file:line pointers are for whoever does the work.

---

## The headline

The Writing Studio shell is real (open it, edit a draft, save versions, deep-link to a
draft). But the **training brain** — converse with the AI, rules that actually shape
drafts, a seed corpus, and the propose/learn-new-rules loop — **did not survive the
rebuild.** Several visible buttons are wired to nothing. The signal rejection→learning
loop is likewise **write-only**. The self-training core Jon cares most about is, today,
largely unbuilt.

Good news: the data models, CRUD APIs, editing, and deep-linking exist, and the Node app
has all the missing pieces working — so this is mostly porting proven code.

---

## Writing Studio — reality vs. spec

| Capability | Status | Notes |
|---|---|---|
| Draft storage + version history on save | **BUILT** | Drafts are `campaign_deliverables` rows; content/versions in JSONB metadata. `PUT /api/writing-studio/drafts/{id}` appends a version (`writing_studio.py:279`). |
| Deep-link to a specific draft (`/#writing-studio?draft=<id>`) | **BUILT** | Fully wired end-to-end (`navigation.js:51-63`, `writing-studio.js:122-138`). The Slack "Edit in WS" button just needs the real deliverable id. |
| Campaign-folder grouping | **BUG / STUB** | Drafts dump into "All drafts." Cause: `create_draft_from_candidate` sets `campaign_id = str(candidate_id)` (numeric) but the filter dropdown uses `campaign_family` (`invoke.py:230`), so they never match; and nothing creates/assigns a per-campaign folder. Folder table exists (`writing_rules/models.py:59`). Small fix. |
| Converse with the AI about a draft | **MISSING (backend)** | Frontend chat calls `POST /api/writing-studio/drafts/{id}/compose` (`api.js:704`) — **that endpoint doesn't exist in Python.** Node reference: `writing-studio-invoke.js:524` + `buildWritingMemoryPrompt`. Biggest gap. Also needs `writing_draft_thread_messages` table (not migrated). |
| Heavily-seeded ruleset guiding drafting | **PARTIAL** | Tables + CRUD exist (`writing_rules/`), but (a) no seed importer in Python (`/seed/import` 404), so they're empty, and (b) draft generation never reads them — `create_draft_from_candidate` and `writing_studio.enqueue` never query rules/examples. |
| Suggest improvements / propose & set new rules | **STUB / MISSING** | UI exists but code says "not wired in this rebuild yet" (`writing-studio.js:1455,1621`). `writing_training_candidates` table not migrated. Node reference auto-extracts "Proposed learning:" lines from each AI turn (`writing-studio-invoke.js:416`). Floating-Artemis `propose_writing_rule` returns text, persists nothing. |

## Signal/content rejection → agent learning loop

**Status: WRITE-ONLY.** Gate decisions are written as memory observations
(`write_pipeline_gate_decision_observation` / `write_signal_gate1_approval_observation`,
`builder/memory_carryover.py`), but:
- **No runtime agent reads them.** Qualifier/content agents assemble context with no
  memory retrieval (`agent_executor.py:120-196`, `builders/executor.py` `_build_system_prompt`).
  The only reader is the human Agent-Builder design tool.
- **The "why" is dropped.** Reject reasons reach `signal_queue.rejected_reason` /
  `Approval.decision_payload["reason"]` but are never passed into the observation content.
- **Scope is too broad** (`workspace:marketing` / `pipeline:<id>`, not agent-level), so an
  agent couldn't fetch *its own* history even if it tried.
- Resume routes/Slack callback don't accept a reason field at all.

A partially-scoped brief already exists: `briefs/cc29-rejection-memory-carryover.md`.

---

## Agreed build order (locked with Jon 2026-06-03)

**Principle Jon set:** self-training is core — every rejection should make the agent
(signals *and* writing, all agents) a little better over time, so humans correct them
less. Build toward that, in this order:

### Phase 1 — Make the cockpit usable (the screenshot problems). Parallelizable now.
- **Signal Slack card (Gate-1):** lead with the real signal/group name + reasoning +
  descriptive text (not "Marketing Pipeline — Gate 1 Signals Inbox"); carry enough to
  decide; **remove Reject** (reject goes to the app, for training); keep Approve + View.
- **Content Slack card (Gate-2):** say what the draft is about; **show the draft itself**
  in Slack (preview if long) so Approve is informed; **remove Reject**; "Edit in Writing
  Studio" deep-links to the exact draft (already supported).
- **Campaign folders:** fix `campaign_id` to be `campaign_family`; auto get-or-create a
  per-campaign folder at draft creation and set `metadata.folder_id`.

### Phase 2 — Rebuild the brain: "converse with the AI."
- Port `POST /api/writing-studio/drafts/{id}/compose` from the Node reference; migrate
  `writing_draft_thread_messages`; wire the ruleset (rules + examples + voice profile)
  into the system prompt so rules actually shape drafts; port the seed corpus
  (`/seed/import`). Nothing can be *taught* through conversation until this exists.

### Phase 3 — Close both learning loops (the self-training core).
- Writing: extract "proposed learnings" from compose turns → store in
  `writing_training_candidates` → wire the propose/approve-reject review UI.
- Signals/content: capture optional (never-required) reject reason → write it into the
  observation, scoped to the agent → make qualifier/content agents READ their own past
  rejections at runtime so the next run improves. Build on `cc29` brief.

**Worst-case if mis-ordered:** jumping to Phase 3 first = nothing generates the lessons
(the compose conversation is where the signal comes from); doing only Phase 1 = a pretty
cockpit that never gets smarter. Usable → brain → learning.

### Banked for later (agreed, not now)

- **Google Docs preview for long-form approvals.** The Slack content card shows the full
  draft inline today, which works for everything we currently send (outreach emails,
  social — all short). Slack's only limit is genuinely long content (full articles,
  landing pages). When we add long-form deliverable types, the right answer is: export
  the draft to a Google Doc (the Writing Studio already has Google Docs import/export
  wired) and post the Doc **link** in the Slack card — Slack unfurls a rich preview and
  the reviewer opens the full formatted doc. Jon's idea; do it when long-form content
  lands, not before. (Until then: full draft inline, chunked across Slack blocks.)

---

## Status as of 2026-06-03

- **MERGED to main:** Slack Gate-2 approver-DM lookup fix + Slack-approve persistence
  (`5ca5127`); marketing content-agent prompt grounding (`0aead86`, Codex, verified live —
  real deliverable + draft now reach Gate-2).
- **Verified unblock:** content agents now call their MCP tools; Gate-2 has real draft
  content (`deliverable_ids` populated, `draft_summary` real).
- **Next:** Phase 1 worker briefs (signal card / content card / folders).
