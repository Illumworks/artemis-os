# Artemis OS: Master Functionality Map

**Written 2026-09-04. Repo state: commit `c43a15c` on `main`.**

## What this document is for

Things get built here, tested, and then reachable by nobody, because six weeks
later nobody remembers they exist. This is the antidote. It is the one place
that says what Artemis OS actually does today, so the answer does not have to be
re-derived by grepping.

Read it top to bottom once. After that, skim the section you need.

Two live examples of the problem, both found on 2026-09-04: a "stalled deals"
module was built and tested and wired to no tool for weeks, and `pipeline_hygiene`
sat unreachable in the same way. Both are fixed now (see section 5). Section 5 is
the section that matters most, because it is the list of things you already paid
for and cannot currently use.

**Contents**

1. [The named agents](#1-the-named-agents)
2. [Integrations](#2-integrations) (what connects to the outside world)
3. [Scheduled and autonomous work](#3-scheduled-and-autonomous-work)
4. [The data model in plain English](#4-the-data-model-in-plain-english)
5. [Built but not reachable](#5-built-but-not-reachable)
6. [How to regenerate this document](#6-how-to-regenerate-this-document)

---

## 1. The named agents

There are four. They are not four copies of the same thing: each has a different
job, a different place it lives, a different set of people allowed to talk to it,
and a deliberately different set of tools. The differences are security controls,
not styling.

### Quick comparison

| | Artemis | Callie | Kai | Ares |
|---|---|---|---|---|
| Role | Jon's personal assistant and the system operator | Marketing analyst and strategist | Enablement content librarian | Jon's private build and planning partner |
| Lives on | Slack DM + the in-app chat panel | Slack channels + the in-app chat panel | Slack (#enablement-library) | Slack (one private channel) |
| Who may talk to it | Jon only | Anyone in her allowed channels, plus anyone who @-mentions her or DMs her | Anyone in #enablement-library | Jon only |
| Memory it can read | Everything | Marketing-shared scopes only. No personal data, no Artemis's memory | Enablement scope only | Its own scope plus Artemis's context. No marketing, no enablement, no other humans |
| Tool count | **77** | **39** | **6** | **4** |
| Last active (as of writing) | scheduled brief 6h ago; last conversation 25d ago | conversation 39m ago; signal push 11m ago | conversation 8h ago | **77 days ago** |

The memory rules are enforced in `artemis/identity/scope_policy.py`, which is
written to fail closed: if it cannot work out who is asking, it returns nothing
rather than guessing. Tool lists are built in
`artemis/floating_artemis/tool_registry.py`.

An important structural detail: Kai, Ares and Callie each get an **explicit,
hand-listed** tool registry with an early return. A new tool added to the general
path does **not** silently reach them. It has to be added to their function by
name. That is deliberate. Before 2026-08-14 Callie fell through to the general
path and held nearly everything the app offers, protected only by which
credentials happened to be missing.

### Layers, in plain English

Every tool carries an authority layer:

- **Layer 1**: read-only. Runs immediately.
- **Layer 2**: a safe write or side effect. Runs immediately.
- **Layer 3**: a real side effect (posting, approving, changing a record).
  Pauses and asks a human first.
- **Layer 4**: destructive. Same as 3, treated more urgently. Nothing currently
  uses it.

One caveat worth knowing, because it shapes several design decisions here: a
layer-3 confirmation in a shared Slack channel is answered by **whoever replies
next**, not necessarily by someone authorised. So for the genuinely sensitive
tools the control is not the confirmation prompt, it is an identity check baked
into the tool itself ("identity-gated" below). Those checks read the Slack user
ID resolved from the inbound event, which tool input cannot fake.

---

### Artemis

**Who she is.** Not an assistant running inside the system, the system's owner
and operator. Infrastructure, routing, memory, agent coordination, and Jon's
personal assistant work (calendar, mail, Jira, the morning brief).

**Where she lives.** Slack DM, and the in-app floating chat panel when Jon is
logged in.

**Who may talk to her.** Jon alone. His Slack ID (`U09F3EPJXSQ`) is the entire
allowlist. This is a **user** allowlist, unlike the other agents, because she is
a personal assistant and the boundary is a privacy boundary. `SLACK_ALLOWED_USER_IDS`
is unset, so the list is just the authenticated owner.

**Memory scope.** All of it.

**Her 77 tools**, grouped by what they are for:

*Memory and preferences (5)*
| Tool | Layer | What it does |
|---|---|---|
| `query_memory` | 1 | Search the persistent memory store for relevant past observations |
| `write_memory` | 2 | Record a new observation into memory |
| `list_scopes` | 1 | List the memory scopes that exist |
| `set_pref` | 2 | Save an operator preference as a key/value pair |
| `surface_status` | 1 | Report which parts of the app have a working backend |

*Google Calendar (5)*
| Tool | Layer | What it does |
|---|---|---|
| `list_calendars` | 2 | List Jon's calendars |
| `list_events` | 2 | List events in a time window |
| `create_event` | 3 | Create an event (asks first) |
| `update_event` | 3 | Change an event (asks first) |
| `delete_event` | 3 | Delete an event (asks first) |

*Gmail (2)*
| Tool | Layer | What it does |
|---|---|---|
| `list_recent_gmail_messages` | 2 | List recent messages from the personal account |
| `get_gmail_thread` | 2 | Fetch one thread by ID |

*Slack (4)*
| Tool | Layer | What it does |
|---|---|---|
| `list_slack_channels` | 2 | List channels in the workspace |
| `read_slack_channel` | 2 | Read recent messages in a channel |
| `send_slack_message` | 3 | Post to a channel or thread (asks first) |
| `send_slack_dm` | 3 | DM a Slack user (asks first). Callie deliberately does **not** have this |

*Jira (7)*
| Tool | Layer | What it does |
|---|---|---|
| `search_jira` | 1 | Search issues by text |
| `get_jira_issue` | 1 | Full detail for one issue |
| `list_jira_assignable_users` | 1 | Who can be assigned in a project |
| `create_jira_issue` | 3 | Create an issue (asks first) |
| `add_jira_comment` | 3 | Comment on an issue (asks first) |
| `assign_jira_issue` | 3 | Assign or unassign (asks first) |
| `transition_jira_issue` | 3 | Move an issue to a new status (asks first) |

*Granola meeting notes (3)*
| Tool | Layer | What it does |
|---|---|---|
| `list_recent_meetings` | 2 | Recent meetings with titles, dates, participants |
| `get_meeting_summary` | 2 | Structured summary and action items for one meeting |
| `get_meeting_transcript` | 2 | The full transcript for one meeting |

*Morning brief control (2)*
| Tool | Layer | What it does |
|---|---|---|
| `set_brief_exclusion` | 2 | Mute a Jira ticket from the morning brief ("stop showing me MT-456") |
| `clear_brief_exclusion` | 2 | Unmute it again |

*OKR Studio (5)*
| Tool | Layer | What it does |
|---|---|---|
| `list_okr_objectives` | 1 | List objectives and their key results |
| `stage_okr_updates` | 1 | Stage proposed progress changes. **Writes zero rows**, it only prepares them for confirmation |
| `complete_okr_checkin` | 1 | Close out the current check-in session |
| `update_okr_kr` | 3 | Update one key result (requires explicit confirmation) |
| `update_okr_krs` | 3 | Batch-update several key results under one confirmation |

*The Builder: defining and running agents and workflows (11)*
| Tool | Layer | What it does |
|---|---|---|
| `list_agents` / `list_skills` / `list_workflows` / `list_chains` / `list_dags` | 1 | List what is defined in the builder |
| `run_agent` | 2 | Queue a run of a defined agent |
| `run_workflow` | 2 | Queue a run of a defined workflow |
| `spawn_subagent` | 3 | Spawn a one-shot helper for a bounded task; it returns a result and disappears |
| `propose_agent` / `propose_skill` / `propose_workflow` | 3 | Propose a new definition (asks first; does not create it directly) |

*System health and code (5)*
| Tool | Layer | What it does |
|---|---|---|
| `health_check` | 1 | Database connectivity and active run count |
| `recent_failures` | 1 | The most recently failed agent runs |
| `list_routes` | 1 | Every API route mounted in the server |
| `read_file` | 1 | Read a file inside the repo (no path traversal) |
| `propose_edit` | 2 | Propose a file edit as a reviewable proposal. Does not apply it |
| `propose_fix` | 3 | Propose a remediation for a detected problem (asks first) |

*Directory (1)*
| Tool | Layer | What it does |
|---|---|---|
| `resolve_person` | 1 | Turn a name ("Angela", "Julie K") into candidate company email addresses |

*Writing rules (2)*
| Tool | Layer | What it does |
|---|---|---|
| `list_writing_rules` | 1 | Read the house style rules |
| `propose_writing_rule` | 3 | Propose a new rule (asks first). Callie cannot reach this at all |

*Marketing (25)*: the same marketing tools Callie has. See her table below for
what each one does. Artemis holds `approve_signal`, `assemble_brief`,
`decide_approval`, `find_by_keyword`, `fire_scout`, `get_active_rulesets`,
`get_campaign_performance`, `get_district_contacts`, `get_message_compass`,
`get_signal`, `link_content_asset`, `list_candidates`, `list_content_assets`,
`list_scout_runs`, `list_signals`, `list_target_signals`, `post_analyst_message`,
`propose_ruleset_change`, `qualify_signal`, `react_to_slack_message`,
`reject_signal`, `search_claims_register`, `snooze_signal`, `submit_draft_for_review`.

**What Artemis does NOT have that Callie does** (8 tools, and this is worth
knowing because it is counter-intuitive): `check_salesforce_activity`,
`salesforce_pipeline`, `dispatch_research` (Argus), `get_screentime_report`,
`record_screentime_feedback`, `import_target_accounts`, `send_guarded_dm`, and
`read_web_page`. Callie's registry was hand-built later and got the newer tools;
Artemis's general path never had them added. **Artemis cannot currently read a
web page.** That is probably a gap rather than a decision.

---

### Callie (Calliope)

**Who she is.** The marketing strategist. She turns messy inputs into an angle, a
proof-backed claim, and a plan. She reports into the marketing team, not to
Artemis operationally, and she speaks only when she has a "so what".

**Where she lives.** Slack, principally #campaign-signals (`C0B9CHVC7KQ`) and
#market-signals (`C0BPT2T2KFY`). Also the in-app chat panel: **any logged-in user
who is not Jon gets Callie**, resolved server-side from the login email. A
non-owner cannot ask for Artemis instead.

**Who may talk to her.** This is a **channel** allowlist rather than a user one,
which stops the bot roaming into conversations nobody invited it into. Two things
bypass it, because both are unambiguous address rather than ambient chatter: a DM
(including a group DM), and an @-mention. Being mentioned counts as consent. That
rule was added on 2026-08-12 after Callie was invited to two channels, was
@-mentioned directly in one, and silently answered in neither.

**Memory scope.** Marketing-shared scopes only (workspace, campaign families,
districts, accounts, people, pipelines, meetings, global) plus her own agent
scope. Explicitly **not** anyone's personal memory and **not** Artemis's.

**Her 39 tools:**

*Signals and the marketing queue (14)*
| Tool | Layer | What it does |
|---|---|---|
| `list_signals` | 1 | List signals from the signal queue |
| `get_signal` | 1 | One signal by ID |
| `list_target_signals` | 1 | Signals for Josh's new-business target accounts, plus statewide signals in states where he has targets. This, not `list_signals`, is the right tool for "my accounts" |
| `find_by_keyword` | 1 | Search signals and campaigns by keyword or bill number |
| `get_active_rulesets` | 1 | The current signal-qualification rules |
| `propose_ruleset_change` | 3 | Propose changing those rules (asks first) |
| `qualify_signal` | 2 | Mark a signal qualified (idempotent score update) |
| `snooze_signal` | 2 | Snooze a signal until later |
| `approve_signal` | 3 | Approve a signal, which triggers downstream work (asks first) |
| `reject_signal` | 3 | Reject a signal (asks first) |
| `fire_scout` | 2 | Run a scout immediately rather than waiting for its schedule |
| `list_scout_runs` | 1 | Recent scout runs |
| `get_campaign_performance` | 1 | Raw campaign status, age, signal volume, pipeline state. Deliberately not aggregated KPIs |
| `list_candidates` | 1 | Campaign candidates |

*Content and messaging (6)*
| Tool | Layer | What it does |
|---|---|---|
| `get_message_compass` | 1 | The active source of truth for marketing messaging |
| `search_claims_register` | 1 | Search approved claims by text and tier, so she cites approved language |
| `list_writing_rules` | 1 | The house style rules (read only; she cannot propose changes) |
| `list_content_assets` | 1 | The content asset library |
| `link_content_asset` | 3 | Attach an asset to a campaign candidate (asks first) |
| `assemble_brief` | 3 | Build a campaign brief for a candidate (asks first) |

*Approvals and drafts (2)*
| Tool | Layer | What it does |
|---|---|---|
| `submit_draft_for_review` | 3 | Send a draft deliverable for review (asks first) |
| `decide_approval` | 3 | Record an approve/reject decision on an approval gate (asks first) |

*Salesforce, read-only (2)*
| Tool | Layer | What it does |
|---|---|---|
| `check_salesforce_activity` | 1 | District brief: customer status, open opportunities, decision-makers, and **which contacts a seller is already working**, so marketing does not step on sales' toes |
| `salesforce_pipeline` | 1 | Pipeline figures for a **fixed menu of seven questions** (win rate by size, open pipeline by stage, stalled deals, deals missing contacts, closing soon, big deals without contacts, loss-reason availability), plus a `none_of_these` escape hatch. There is deliberately no free-form query language, because handing an agent one is how it invents a filter, gets a number, and reports it as confidently as a real one |

*Research (1)*
| Tool | Layer | What it does |
|---|---|---|
| `dispatch_research` | 1 | Ask Argus to research a district in depth. Returns a **queued** acknowledgement, explicitly not a promise that research has started. A background claimer picks it up |

*Screen-time and policy watch (2)*
| Tool | Layer | What it does |
|---|---|---|
| `get_screentime_report` | 1 | On-demand overview of screen-time and AI-in-schools policy signals, with the Amira carve-out angle |
| `record_screentime_feedback` | 2 | Record an explicit teammate reaction to one signal, so future reports learn what this audience cares about |

*Slack and outbound (5)*
| Tool | Layer | What it does |
|---|---|---|
| `list_slack_channels` | 2 | List channels |
| `read_slack_channel` | 2 | Read recent messages |
| `send_slack_message` | 3 | Post to a channel or thread (asks first) |
| `post_analyst_message` | 3 | Post a synthesised analyst update to one of her configured channels under her own bot identity (asks first) |
| `react_to_slack_message` | 3 | Add an emoji reaction (asks first) |
| `send_guarded_dm` | 2, **identity-gated** | Her only way to start a DM with someone. Checks **both** who is asking and who would receive against fixed allowlists, using the Slack ID from the inbound event. Every attempt, sent or refused, is logged to `callie_dm_send_attempts` |

Requesters allowed to ask for a guarded DM: Jon, Angela Miata, Josh Mukai.
Recipients she may DM: those three plus Hannah Slater and Jaclyn Wright.
(`ARTEMIS_CALLIE_DM_REQUESTER_EMAILS` / `..._RECIPIENT_EMAILS`.)

She deliberately does **not** have the raw `send_slack_dm` tool. It has no
allowlist of its own, so leaving it alongside `send_guarded_dm` would make the
guard decorative.

*Admin, general (7)*
| Tool | Layer | What it does |
|---|---|---|
| `import_target_accounts` | 2, **identity-gated** | Replace Josh's new-business target account list from a spreadsheet posted in Slack |
| `get_district_contacts` | 1 | Known decision-makers for a district ("who runs Harford County") |
| `resolve_person` | 1 | Name to email, using who else is in the conversation to break ties |
| `read_web_page` | 1 | Fetch a public URL or PDF and return its text. Added 2026-08-31 after Josh sent her a michigan.gov link and she had 27 tools and no way to open it |
| `query_memory` | 1 | Search memory, scope-limited to what she is allowed to see |
| `write_memory` | 2 | Record an observation |

**Not given to Callie, on purpose:** the Builder tools, system tools, Google
Calendar, Gmail, Granola meeting transcripts (all personal data), OKR Studio
(owner-judgment surface), Jira, `propose_writing_rule`, and file reading.

---

### Kai (Chiron)

**Who he is.** The enablement content librarian. His whole job is answering
"where is the thing", so the field stops doing Drive archaeology. He retrieves,
verifies, explains and routes. He does not write or rewrite content.

**Where he lives.** Slack only, in **#enablement-library** (`C0BB17EJLKC`). He is
not reachable from the in-app chat panel at all.

**Who may talk to him.** Anyone in #enablement-library. But **side-effecting
actions are restricted to Jon and Missy Dahlberg** (`ARTEMIS_KAI_ACTION_AUTHORIZED_USER_IDS`),
and `update_asset_summary` is restricted to Jon, Sara and Missy. Everyone else
gets information only.

**Memory scope.** Enablement only. No personal memory, no marketing, no Artemis.

**His 6 tools:**

| Tool | Layer | What it does |
|---|---|---|
| `search_enablement_assets` | 1 | Search the catalog by meaning and by keyword. Returns title, summary, type, audience, tags, and links with visibility markers |
| `get_enablement_asset` | 1 | One asset by Drive file ID, name, title or URL. Returns the Drive link, summary, confidence label, audience and transcript link |
| `list_enablement_facets` | 1 | The vocabulary of the catalog: valid audiences, asset types and common tags, with counts. Meant to be used **before** searching, to learn what filters exist |
| `read_web_page` | 1 | Fetch a public URL or PDF. Added 2026-08-31 after Julie asked for a one-pager not on the resources sheet and gave him the link, and he could only search his own index |
| `flag_catalog_gap` | 2, **identity-gated** | Post a structured gap note into #enablement-library, tagging Sara and Missy, when a real request has no matching asset. It exists because Kai was claiming to escalate with no tool behind it: on 2026-08-10 he said "Escalation filed and noted" and nothing was filed |
| `update_asset_summary` | 2, **identity-gated** | Let a content owner correct a wrong summary in conversation and have it re-indexed immediately. Added when the bulk-review model was dropped, because nobody has time to sift 400+ generated summaries |

The security comment in the code is blunt about this registry: do not add tools
here. The standing exception is strictly read-only, zero-agency tools, and only
with the reason recorded at the point of the change.

---

### Ares

**Who he is.** Jon's private research, planning and build partner. The agent for
work that is complex, technical, uncertain or build-heavy. He shapes the build,
challenges assumptions, and returns either a research brief or a working
artifact. He is explicitly not the production operator of Artemis OS.

**Where he lives.** Slack, one private channel (`C0BBZCZA4EQ`). Not reachable
from the in-app chat panel.

**Who may talk to him.** Jon only, in practice: the channel is private and
`always_respond_in_channels` is set, so he answers everything said there.

**Memory scope.** His own scope, plus Artemis's context (the shared "one brain"),
and nothing else. Not marketing, not enablement, not other people's personal
memory. Deliberately not "all scopes": Jon is the owner, so Jon's personal
context already lives in Artemis's scopes, and granting those gives Ares what he
needs without opening the marketing and enablement surfaces. Other agents cannot
read `agent:ares`, which is enforced structurally by their allowances never
listing it.

**His 4 tools:**

| Tool | Layer | What it does |
|---|---|---|
| `query_memory` | 1 | Search memory, limited to his own scope and Artemis's |
| `list_scopes` | 1 | List memory scopes |
| `surface_status` | 1 | Which parts of the app have a working backend |
| `read_file` | 1 | Read a file in the repo |

All read-only. No writing, no proposing, no spawning.

**A caveat that belongs in section 5 as much as here.** Four more tools exist for
him (`read_project_file`, `list_project_dir`, `git_status`, `git_diff`), fully
written and tested, gated behind a `project_path` argument. **No live caller ever
passes that argument.** So in practice Ares has 4 tools, not 8. See section 5.

**Ares has not been used in 77 days.** He is wired up and answering, but nobody
is talking to him.

---

## 2. Integrations

### Where credentials actually live (read this first)

`artemis/config.py` claims to be the single source of truth for settings. For
*settings* it is. For **credentials it is not**, and assuming otherwise wastes an
hour. API keys and tokens live in four places:

1. **Encrypted in Postgres, `integration_configs` table**: the per-provider app
   credentials (OAuth client id and secret, API tokens). This is where Google,
   Jira and Salesforce actually live.
2. **Encrypted in Postgres, `integrations` table**: the per-agent OAuth
   access/refresh tokens. This is where the four Slack bot tokens live.
3. **Encrypted in Postgres, `connectors` table**: a second, separate store. Only
   Vista Social uses it.
4. **Env files**: `./.env` (project) and `~/.artemis/.env` (user-global).

The database value wins; the env var is the fallback. Two master keys unlock the
encrypted stores, `ARTEMIS_CREDENTIALS_KEY` and `ARTEMIS_CONNECTOR_KEY`, both in
`~/.artemis/.env`. Without them, nothing decrypts.

Practical consequence: **an env var being empty does not mean an integration is
down.** Salesforce, Jira and Google all have empty env vars and all work.

### The integrations, at a glance

| System | Status | Direction | Credential lives in |
|---|---|---|---|
| Slack | **LIVE** | read + write | DB (`integrations`), plus `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` / `SLACK_SIGNING_SECRET` |
| Google Docs + Drive | **LIVE** | read + write | DB (provider `gcal`) |
| Google Calendar | **LIVE** | read + write | DB (provider `gcal`) |
| Gmail (sending) | **LIVE** | write | DB (`google_credentials`) |
| Gmail (reading) | **BROKEN** | read | as above, but the lookup is wrong. See below |
| Salesforce | **LIVE** | read only, structurally | DB (provider `salesforce`) |
| Gong | **LIVE, indirect** | read only | none of its own. Rides on Salesforce |
| Starbridge (webhook) | **LIVE** | inbound | `STARBRIDGE_WEBHOOK_PUBLIC_KEY` |
| Starbridge (agent tool) | **STUBBED** | none | `STARBRIDGE_API_KEY` is set but the tool ignores it |
| Jira | **LIVE** | read + write | DB (provider `jira`) |
| Granola | **NEEDS REAUTH** | read | DB (provider `granola`) |
| HubSpot | **NOT INTEGRATED** | none | none |
| Vista Social | **CONNECTED, NOT WIRED** | none | DB (`connectors`) |
| Reddit | **DEAD** | none | `REDDIT_CLIENT_ID` etc, all unset |
| Cloudflare Access | **LIVE** | inbound auth | `ARTEMIS_CF_ACCESS_*` in the launchd plist |
| Enablement webhook | **LIVE** | inbound | `ARTEMIS_ENABLEMENT_WEBHOOK_SECRET` |

### The detail that matters

**Slack.** The main human interface. Four separate bot identities, one per agent,
each with its own token and signing secret in the database. Reads events,
mentions, and user/channel lookups; writes messages, DMs and interactive
approval cards. Channel IDs are configuration rather than secrets:
`ARTEMIS_MARKET_SIGNALS_CHANNEL_ID` (#market-signals, `C0BPT2T2KFY`),
`ARTEMIS_CALLIE_PROACTIVE_CHANNEL` (#campaign-signals, `C0B9CHVC7KQ`),
`ARTEMIS_ENABLEMENT_LIBRARY_CHANNEL_ID` (#enablement-library, `C0BB17EJLKC`),
`ARTEMIS_MARKETING_CAMPAIGNS_SLACK_CHANNEL` (#marketing-campaigns, `C0B8QE17DGQ`),
`ARTEMIS_MARKETING_CONTENT_REVIEW_CHANNEL_ID`,
`ARTEMIS_CRISIS_CONTENT_COPY_NOTIFY_CHANNEL`, and
`ARTEMIS_BRAND_SIGNALS_CHANNEL` (**unset**, which is why the Brand Signals brief
is dormant, see section 3).

**Google.** One OAuth app, several APIs. Docs and Drive are read **and write**:
the crisis-content pipeline writes into a document owned by an outside vendor and
posts Drive comments, so this is the integration with the largest external blast
radius. Calendar is read and write, with event creation behind a human approval
gate. Per-user tokens live in `google_credentials`, split by purpose (`personal`
is Jon; `marketing` is the shared marketing account plus two staff).

**Gmail: sending works, reading is broken.** The crisis-content backup email to
the vendor goes out fine. But the Floating Artemis Gmail *read* tools look up the
credential with a hardcoded `user_id=1`, which is the dev-shim user. No row in
`google_credentials` has `user_id=1` (the real rows are 8, 275 and 550), so the
lookup always resolves to nothing. Two other modules in the codebase carry
comments about fixing this exact mistake and one of them calls
`gmail/tools.py` "still-broken". Source: `artemis/integrations/gmail/tools.py:40`.

**Salesforce is read-only by construction, not by convention.** The client
exposes exactly two operations, `describe_sobject` and `query`, both GET, and a
test asserts the complete public method set so a write method cannot be added
without the test failing. The only POST it makes is the OAuth token exchange.
Its job is to stop marketing contacting someone sales is already working.

**Gong: confirmed, there is no Gong integration.** No client, no API key, no Gong
hostname anywhere in the codebase. Gong syncs its "Engage" flow data onto the
Salesforce Contact record, and Artemis reads those four synced custom fields
(`Gong__Actively_Being_in_a_Flow__c`, `Gong__Current_Flow_Name__c`,
`Gong__Current_Flow_User_Name__c`, `Gong__Added_to_Flow_Date__c`) inside its
ordinary Salesforce query. That answers "is someone already working this person"
with no Gong API at all. If Gong access were ever revoked at the Salesforce sync
level, this would go quiet with no error.

**Starbridge is two different things, and only one works.** The **webhook** is
live and is the single largest source of signals (1,037 in the last 30 days).
Starbridge POSTs a row the moment its columns finish processing; deliveries are
Ed25519-signed and verified against the raw request body. Separately, the
`starbridge_researcher` scout declares two agent tools, `starbridge.search` and
`starbridge.get_document`, and **both are stubs** whose own docstrings say they
need `STARBRIDGE_API_KEY`. The key is set. A fully working Starbridge client
exists at `artemis/scouts/starbridge/client.py`, verified against 68 bridges and
174,544 rows. It is simply not connected to the tool. This is why that scout has
run 563 times and produced zero signals. See section 5.

**Granola needs reauth.** Meeting transcripts and summaries. The code is complete
and wired; the stored credential has been in `needs_reauth` since it was
connected. There is a fallback path that reads the desktop app's local session
file on this Mac, which may or may not still be valid.

**Vista Social is connected but goes nowhere.** A connector row exists (created
2026-08-25, "Vista - Read Only") but no agent is linked to it, so nothing reads
it. The only code path that touches it is the "Test connection" button. The
useful part of Vista would be the owned-profile inbox; trend search there is
structurally useless because it never returns empty.

**Reddit is written but has never run once.** Two read-only GET operations,
deliberately no vote/comment/post method, and it never records post authors. The
module docstring says plainly that the Reddit app has not been created yet and
nothing in the module has ever been exercised against live Reddit. Every test
mocks the HTTP layer. Its two entry points have zero callers outside their own
tests.

**HubSpot is not integrated at all.** The only mention in the codebase is a
passing comment about URL formats.

### Scout data sources

| Source | Status | Credential |
|---|---|---|
| Google News RSS | **LIVE**, no key needed | none |
| NewsAPI.org | **STUBBED**, returns empty gracefully | `NEWS_API_KEY` (empty) |
| Federal Register | **LIVE**, public | none |
| LegiScan (state bills) | **LIVE** | `LEGISCAN_API_KEY` (set) |
| State DoE + governor RSS (~30 states) | **LIVE**, public | none |
| Bonfire / Euna (procurement) | **LIVE**, public RSS per district | none |
| SAM.gov | **LIVE** | `SAM_API_KEY` (set) |
| USASpending.gov | **LIVE**, public | none |
| Grants.gov | **LIVE**, public | `GRANTS_GOV_API_KEY` empty but not needed |
| BoardDocs / Granicus (board minutes) | **LIVE**, public scraping | none |
| LinkedIn | **STUBBED at every layer** | `LINKEDIN_SCRAPER_API_KEY` (empty) |
| OpenGov Procurement | **BLOCKED**, not publicly accessible | n/a |
| eMMA (Maryland) | **BLOCKED**, reCAPTCHA Enterprise | n/a |
| TX ESBD / TxSmartBuy | **BLOCKED**, robots.txt disallows | n/a |

### AI providers

| Provider | Status | Notes |
|---|---|---|
| Claude Code CLI | **LIVE, and it is the primary** | All 20 builder agents use it. No API key, it runs on the subscription login |
| Anthropic API | **DECLARED FALLBACK, WOULD FAIL** | All 20 agents name `anthropic` as their fallback, but `ANTHROPIC_API_KEY` is empty. **If Claude Code fails, the fallback fails too.** Worth fixing |
| Gemini API | **LIVE** | `GEMINI_API_KEY` set. Used as a scout-runner primary with Claude Code as fallback |
| OpenAI API | not configured | `OPENAI_API_KEY` empty; the adapter works |
| OpenRouter | key present, no known caller | `OPENROUTER_API_KEY` set in `~/.artemis/.env`, but no agent routes to it. Unverified |
| Codex CLI | **DEAD** | Adapter registered, but `codex` is not on this machine's PATH |
| LM Studio (local) | conditional | Works only when that Tailscale machine is up |
| Tavily | **DEAD** | Connector kind exists, no connector row, no search call anywhere |

---

## 3. Scheduled and autonomous work

**The single most important thing to understand here:** almost everything below
runs *inside the web app process*. There is no external cron (`crontab` is
empty). If the app is down at 8am, the morning brief does not run at 8am. That is
what the watchdog exists for.

### Always-on system services (launchd)

| Service | When | What it does |
|---|---|---|
| `me.artemisos.app` | continuously, restarts on crash | The web app itself (uvicorn on 127.0.0.1:8000). **Host for everything below** |
| `me.artemisos.tunnel` | continuously | Cloudflare tunnel making the app reachable at app.artemisos.me |
| `me.artemisos.watchdog` | every 120 seconds | Silently restarts the app if health checks fail. Slack-alerts Jon only if a restart fails to recover |
| `me.artemisos.logrotate` | daily 04:15 local | Rotates and compresses logs |

**There is currently no automated database backup.** Three plists exist in
`launchd/` for memory archiving, `pg_dump` backup, and a monthly recovery drill.
All three point at `/Users/artemis/Desktop/Artemis/artemis-os`, which is not
where the repo lives, and none are installed. This looks like bit-rot after a
directory move rather than a decision.

### The scouts

Nine scouts, all scheduled, all running. Each runs **every 24 hours**, staggered
7 minutes apart so they do not all fire at once, plus once more via the
`marketing.main` pipeline at 06:00 America/Chicago.

Each scout is an LLM agent that is handed a set of data-fetching tools and told
to write qualified signals into `signal_queue`.

Here is the honest yield table, from the last 14 days:

| Scout | Runs | Runs that produced a signal | Why |
|---|---|---|---|
| regional_news | 34 | 33 | working well |
| leadership_transition | 22 | 20 | working well |
| legislative | 28 | 5 | LegiScan is live; low base rate |
| state_doe | 20 | 4 | live; low base rate |
| procurement | 24 | 1 | most portals blocked |
| federal_funding | 27 | 1 | live; low base rate |
| **board_minutes** | 23 | **0** | treat as broken |
| **linkedin_observer** | 33 | **0** | its tools are stubs; no API key |
| **starbridge_researcher** | 43 | **0** | its tools are stubs, though a real client exists |

A broken source and a quiet world look identical from the outside, so a scout at
zero should be treated as broken until proven otherwise. Note that
`starbridge_researcher` producing nothing does **not** mean Starbridge is dark:
the webhook is the real Starbridge path and it delivered 1,037 signals in 30
days.

**A trap worth knowing.** The `agents` table has a `cadence_seconds` column, it
is populated, and the scheduler **ignores it**. Starbridge declares a 4-hour
cadence and runs daily like everything else. Changing that value in the UI would
do nothing.

**A second trap: there are three separate "scouts" systems in this repo.**

1. `artemis/marketing/` scouts, LLM agents run from the scheduler. **These are
   the ones that run.**
2. `artemis/marketing/scout_sources/`, an older adapter-based path where 8 of the
   9 adapters are explicit `NullAdapter` stubs. Only reachable from the manual
   "Run scout" button in the UI, which therefore mostly does nothing.
3. `artemis/scouts/`, a third implementation with its own worker and scheduler.
   Nothing launches it, and all 10 scouts in `config/scouts.yaml` are
   `enabled: false`. Its client code **is** used, though, called directly by
   Argus and by Screen-Time Watch.

### Jon's scheduled Slack DMs

All times America/New_York. All produce a Slack DM to Jon unless stated.

| Job | When | What it produces |
|---|---|---|
| **Morning brief** | weekdays 08:00 | The daily brief DM, plus a `morning_brief_deliveries` row. A unique constraint guarantees once per day |
| **OKR check-in** | Fridays 16:00 | A cited OKR proposal. Never writes an OKR itself |
| Commitment proposals digest | weekdays 09:00 | A digest, only if proposed commitments exist |
| Commitments follow-up | weekdays 09:30 | Follow-ups on commitments due within 48 hours |
| Commitment urgency nudge | every 2 hours, weekdays | An interrupt for commitments due within 12 hours |
| Stale review escalation | daily 17:00 | DMs each reviewer whose review has sat over 24 hours |
| Hub agent escalation | hourly | Escalates agent questions unresolved after about a day |
| Post-meeting scheduling | every 20 min, weekdays 8am to 6pm | Proposes meeting times from meeting action items. Proposes only, never books |
| Directory sync | Mondays 06:00 | Refreshes the `directory_people` name-to-email roster. Silent |
| Pre-meeting prep | n/a | **DISABLED.** The registration is commented out ("rarely needed", 2026-06-18). Code retained |

### Callie's scheduled work

| Job | When | What it produces |
|---|---|---|
| **Combined market-signals brief** | weekdays 13:00 UTC (09:00 ET) | **One** Slack post to #market-signals combining top campaign signals, crisis signals and screentime, tagging Josh and Angela |
| Screen-Time Watch daily sweep | daily 11:00 UTC | Rows in `screentime_signals`. No Slack post |
| Board peer-validation sweep | Sundays 12:00 UTC | More `screentime_signals` rows from BoardDocs. Silent |
| Proactive signal cards | on qualification, capped at 3/day | Individual signal cards posted to #campaign-signals |
| **Brand Signals daily brief** | weekdays 12:00 UTC | **DORMANT.** `ARTEMIS_BRAND_SIGNALS_CHANNEL` is unset, so it produces nothing. The startup log literally reads `channel '(unset - dormant)'`. Setting one env var turns it on |
| Standalone screentime digest | n/a | **DELIBERATELY NOT REGISTERED.** Jon's decision 2026-08-12: exactly one cron may post to #market-signals, so this contributes a section to the combined brief instead. Kept for manual use |

### Pipelines

Pipeline schedules live in the **database**, in `pipelines.trigger_config`, not in
code. Only one pipeline is actually cron-executed:

- **`marketing.main`**, daily at 06:00 America/Chicago. Runs the nine scouts plus
  the qualifier and content agents. Cut from every-4-hours to daily on
  2026-08-12 for cost.
- `screentime.watch` is marked display-only and is deliberately skipped; the real
  work is done by its own runner.
- `marketing.campaign_deliverables` is manual-trigger.

**A wedged run is silent.** A run stuck in `awaiting_approval` or `running`
blocks every future scheduled run of that pipeline with no error and no alert.
`marketing.main` sat wedged for two months in 2026 before anyone noticed. The
health report flags these with `!!`. As of writing there are none in flight.

### Background workers

| Job | Interval | What it does |
|---|---|---|
| **Argus research claimer** | every 15 seconds | Picks up `pending` rows from `argus_research_requests`, does the district research, and posts findings back to the requesting Slack channel. **This is what makes `dispatch_research` actually finish**; the tool itself only enqueues a row |
| **Crisis-content doc poller** | every 2 minutes | Reads the crisis-comms Google Doc, detects approval-status changes, posts Slack review cards |
| Crisis-content rule mining | every 60 minutes | Learns editing rules from accepted edits |
| Meeting auto-summarizer | every 2 minutes | Summarises meetings that just ended |
| OAuth token refresh | every 15 minutes | Refreshes Google and Slack tokens. DMs the owner once if a token dies |
| Memory maintenance | daily 03:00 UTC | Decays observation scores, collapses near-duplicates |
| Event-loop freeze diagnostic | continuous | Dumps stack traces if the event loop stalls. **Temporary**, to be removed with `artemis/loop_diag.py` once that bug is closed |

### Inbound, event-driven (no schedule)

- **Starbridge webhook** into `/api/starbridge`: the largest signal source.
- **Enablement indexer**: a Google Apps Script on the `amiracentral@` account
  POSTs the content catalog to `/api/enablement/ingest`. Authenticated with
  `ARTEMIS_ENABLEMENT_WEBHOOK_SECRET`; an empty secret disables the endpoint.
- **Slack events and interactivity**: mentions, DMs, and approval button clicks.

### The automations registry is empty

There is a general-purpose automation scheduler reading `automations` from the
database. The table has **zero rows**. The machinery works and is running; nobody
has defined an automation.

---

## 4. The data model in plain English

There are 132 tables. You do not need to know 132 tables. These are the ones that
carry the actual work. Row counts are live as of 2026-09-04.

### The marketing intelligence chain

This is the spine of the system. Things flow left to right.

**`districts`** (13,466 rows). Every US school district, from the federal NCES
dataset. Name, state, enrollment, size tier, whether we support them, whether
they are on a skip list, and a BoardDocs URL where one exists. This is the
reference table everything else joins against, and it is why a signal can say
"Harford County" and the system knows how big that is.

**`target_accounts`** (1,287 rows across 44 states). Josh's new-business target
list. Distinct from `districts`: this is the subset someone is actually trying to
sell to. Callie's `list_target_signals` reads this. Replaced wholesale via
`import_target_accounts` from a spreadsheet.

**`signal_queue`** (4,547 rows). **The most important table in the system.** One
row per thing-that-happened-in-the-world that might be worth acting on: a bill
introduced, an RFP posted, a superintendent hired, a board minute mentioning
literacy. Each row carries a headline, a summary, the source URL, the district
and state, a campaign family, an urgency tier, reason codes explaining why it was
flagged, and its status.

Where they came from, last 30 days: Starbridge webhook 1,037, news articles 983,
state DoE 295, manual 21, board minutes 3.

Status breakdown (all time): 3,390 qualified, 699 pending qualification, 325
rejected by hard filter, 58 approved, 29 archived, 29 suppressed as stale, 10
snoozed, 7 rejected at gate 1. Roughly 1,444 signals arrived in the last 7 days.

**`campaign_candidates`** (5 rows, all `archived_test`). A cluster of related
signals promoted into something worth building a campaign around. **This is
effectively empty.** Signals are flowing in at 200 a day and none are becoming
campaigns. That is either a gap or a deliberate pause, but it is not invisible.

**`campaign_briefs`** (1), **`campaign_deliverables`** (23),
**`content_assets`** (5), **`claims`** (91, the approved-claims register Callie
cites from), **`approvals`** (31). The downstream campaign machinery. Lightly
used.

**`scout_runs`** (3,166 rows). One row per scout execution: which scout, when it
started and finished, which signals it created, and any errors. This is how you
tell a working source from a broken one. 3,135 complete, 30 failed, 2 stuck in
`pending` (see the `fire_scout` note in section 5).

**`screentime_signals`** (1,215 rows). A deliberately separate feed from
`signal_queue`: national screen-time and AI-in-schools policy moves, with a
stance per state (`screentime_state_stance`, 47 rows). Different pipeline,
different purpose, do not confuse the two.

### The memory system

The rule here is **lossless**: nothing is deleted. An observation leaves active
retrieval only by being superseded, never by being removed. There is deliberately
no public delete API for drawers or observations.

**`memory_observations`** (2,171 rows). The atomic unit: one thing that was
learned. By category: trajectory 490, screentime reports 305, discovery 247,
Callie's signal pushes 213, Starbridge signals 204, district research 200,
commitments 188, trend snapshots 154, signal qualifications 73, conventions 71.

**`memory_scopes`** (67 rows). Who a memory belongs to, which is what the access
rules in section 1 operate on. By kind: meeting 33, agent 17, campaign family 5,
state 5, workspace 4, global 1, skill 1, pipeline 1.

**`memory_observation_scopes`** (2,687). The many-to-many join: one observation
can be filed under several scopes.

**`memory_drawers`** (629). Grouped collections of observations.
**`memory_evidence`** (1,049): the source material backing an observation, which
is what lets an agent cite rather than assert.
**`memory_embeddings`** (2,813): the vector index that makes "search by meaning"
work.
**`memory_entities`** (4), **`memory_relations`** (2): the knowledge-graph layer.
Barely populated. Built, essentially unused.

### Enablement (Kai's world)

**`enablement_assets`** (416 rows, last updated today). The searchable catalog of
sales and enablement content: title, summary, type, audience, tags, Drive links,
and a confidence label. Populated by a Google Apps Script that POSTs to
`/api/enablement/ingest`. This is Kai's entire knowledge base.

### Research

**`argus_research_requests`** (28 rows: 23 done, 5 failed). One row per deep
district-research job. Callie's `dispatch_research` writes a `pending` row here
and the background claimer picks it up within 15 seconds. **This table being
empty is what proved, in August, that Argus had never run once in five weeks
while Callie was reporting research as underway.** It is the check to run when
someone claims research happened.

### Observability

**`agent_traces`** (792 rows). One row per provider call: which agent, what it
cost, how long it took, which tools it used. Last 30 days: the nine scouts
dominate, then Callie (65 calls), Kai (41), the qualifier agents (43), and
Artemis (1).

Two warnings that have each cost an hour before. `agent_traces.tools_used` was
empty for every agent for over 30 days and is only trustworthy for rows after
2026-08-12. And **an agent is alive if ANY activity path is recent**, not just
this one. Artemis looks dead here (one call in 30 days) while delivering the
morning brief every weekday through a different table.

**`tool_invocations`** (45,721 rows). Every individual tool call. The highest-volume
table in the database.
**`cost_events`** (4,146): spend per call.
**`agent_runs`** (3,501) and **`agent_context`** (3,424): builder agent execution.

**`pipeline_runs`** (84 rows). One row per pipeline execution. `marketing.main`:
22 succeeded, 6 failed, 6 cancelled. `marketing.campaign_deliverables`: 2
succeeded, 22 failed, 4 cancelled, last touched 2026-06-07.

### Where agent activity is recorded (six places, and this trap is expensive)

Reading any one of these and concluding an agent is dead gives a confidently
wrong answer. This has happened.

| Table | Rows | Records only |
|---|---|---|
| `floating_artemis_messages` | 794 | conversational turns |
| `morning_brief_deliveries` | 92 | scheduled briefs and OKR check-ins |
| `memory_observations` (category `callie_signal_push`) | 213 | Callie's autonomous signal cards |
| `agent_traces` | 792 | any provider call |
| `slack_inbound_messages` | 845 | keyword-mention triage only, **not DMs** |
| `pipeline_runs` | 84 | pipeline executions |

Run `uv run python -m artemis.ops` instead. It reads all six.

### People and directory

**`directory_people`** (222 rows). Name to email resolution, refreshed from Slack
and calendar attendees every Monday. This backs `resolve_person`. Historically
the source of a painful bug: real approvers had `slack_user_id = NULL`, so every
authorisation lookup missed and every approval click was refused.

**`slack_users`** (16), **`slack_channels`** (2), **`users`** (6).

### Crisis content

**`crisis_content_cards`** (182), **`crisis_content_copy_versions`** (318),
**`crisis_content_notifications`** (66), **`crisis_content_decisions`** (18),
**`crisis_content_writeback_deliveries`** (46). The vendor-document approval
pipeline: copy arrives in a Google Doc, Callie posts a review card to Slack, an
approver clicks, and the decision is written back. Note that some
`crisis_content_notifications` rows still have NULL `channel_id` and
`message_ts`, because those columns were added by a later migration and
pre-existing rows never got them. Any code reading this table must handle that.

### Empty or near-empty tables worth noticing

`automations` (0), `automation_runs` (0), `agent_chains` (0), `agent_dags` (0),
`agent_skills` (0), `app_settings` (0), `personal_todos` (0),
`callie_dm_send_attempts` (0), `forge_runs` (0), `qualifier_rule_applications`
(0), `floating_artemis_voice_corpus` (0), `memory_conflicts` (0),
`project_workspace_memory` (0), `campaign_sends` (0), `content_asset_links` (0).

Each of these is machinery that runs but has never been given anything to do.

---

## 5. Built but not reachable

This is the list of things that exist, work, usually have tests, and that nothing
can currently call. Two structural results first, because they are good news and
they narrow the search:

- **Every API route in the repo is mounted.** All 69 routers are included in
  `artemis/main.py`. There are no orphaned endpoints.
- **Every agent tool-registration function is called.** All 22. The only
  scheduler registration that is deliberately skipped is the standalone
  screentime digest, and that is documented as a decision.

So the problem is not in the wiring layers. It is in individual modules, and in
tools that exist but do the wrong thing.

The two examples that prompted this document, **stalled deals** and
**`pipeline_hygiene`**, are both **now fixed**. They are reachable through
Callie's `salesforce_pipeline` tool as of commit `6724dab` today.

### Tier 1: working features that are silently doing nothing

These are the ones to look at first, because in each case someone can see the
feature, believe it is on, and be wrong.

---

**1. `fire_scout` reports success for work it does not do.**
`artemis/floating_artemis/tools/marketing.py:724`

Both Artemis and Callie have a tool called `fire_scout`, described as "Trigger a
scout run immediately". What it actually does is insert a `scout_runs` row with
status `pending` and return `Scout regional_news fired: run_id=scout_run_ab12cd34`.
**No scout is started. Nothing runs. Nothing ever will.** The row sits at
`pending` forever.

There are exactly 2 such orphan rows in the database, the most recent from
2026-07-06, so it has been used twice and quietly did nothing both times, while
telling the person who asked that it had worked.

This is the `dispatch_research` bug repeated, the one that cost five weeks of
Argus never running. The rule from that lesson applies unchanged: a tool must
never report success for work it did not do. **Fix: either wire it to the real
runner (spawn `python -m artemis.marketing.scout_cli <agent_id>`, the same thing
the scheduler does), or make it return a failure with a reason the model can
repeat.** Confidence: HIGH, verified against the live database.

---

**2. The Ghostwrite toggle in the agent builder does nothing.**
`artemis/builders/ghostwrite.py`

The agent builder UI has a dropdown labelled "Yes, output framed as Jon", and
when you select it the interface displays: "Ghostwrite is active, this agent's
output is framed as if Jon wrote it. Voice samples from the personality profile
are prepended to the system prompt at run-time."

None of that happens. The flag saves correctly to the database. The module that
implements it (`apply_ghostwrite_frame`, which prepends the directive and splices
in four deterministically-chosen voice samples) is complete and tested. But the
runtime prompt assembler, `_build_system_prompt` in
`artemis/builders/executor.py`, reads only `voice_notes` and `purpose` from the
persona. It never reads `ghostwrite` and never calls the module. `apply_ghostwrite_frame`
appears nowhere outside its own file and its test.

**Fix: one line in `_build_system_prompt`.** Also check the chain, DAG and
workflow executors, which build prompts the same way. Confidence: HIGH.

---

**3. The `starbridge_researcher` scout has a real client it is not allowed to
use.** `artemis/tools/starbridge.py` vs `artemis/scouts/starbridge/client.py`

That scout has run 563 times and produced zero signals, ever. The reason is that
its two declared tools, `starbridge.search` and `starbridge.get_document`, are
stubs whose own docstrings say they need `STARBRIDGE_API_KEY`. **The key is
set.** And a fully working Starbridge client exists elsewhere in the repo,
rewritten against the live API spec and verified against 68 bridges and 174,544
rows.

The webhook path means Starbridge data is not lost, so this is wasted compute
rather than a blind spot. But it is 563 LLM runs spent on an empty tool.

**Fix: back the two tool factories with the real client.** Confidence: HIGH.

The same shape applies to `linkedin_observer` (381 runs, zero signals): its tools
are stubs too, but there the blocker is real, there is no LinkedIn credential.

---

**4. Ares cannot see any code, because of an argument nobody passes.**
`artemis/floating_artemis/tool_registry.py:79`

Ares is Jon's build partner. Four tools were written and tested to let him read a
project: `read_project_file`, `list_project_dir`, `git_status`, `git_diff`. They
are registered only when a `project_path` argument is supplied to the registry
builder.

**No live caller passes it.** The two real call sites (the in-app chat path and
the Slack MCP subprocess path) both omit it. So Ares has 4 tools, all of which
are memory and status reads, and cannot look at a single line of code in a
project he is supposed to help build.

He has also not been used in 77 days, which may well be why.

**Fix: thread the dev-project's path through to the registry builder on the chat
path.** Confidence: HIGH, verified by grep across all callers.

---

**5. Callie may be missing 246 messages of context she was supposed to inherit.**
`artemis/floating_artemis/callie_history_handoff.py`

A one-shot migration that reads the retired Artemis Slack DM session and writes
its ~246 messages into Callie's memory scope, deduplicated by content hash so
re-running is safe. 302 lines, and the most thoroughly tested dead module in the
repo (699 lines of tests).

Its own docstring says it is "called at startup or via management command".
Neither exists. It is not in the app's startup sequence, there is no script for
it, and it has no command-line entry point. The flag it is supposed to flip,
`callie_handoff_pending`, appears nowhere else in the codebase, so nothing sets
or reads it either.

Worse: another module (`artemis/memory/near_duplicate.py`) contains a comment
describing observations "from `callie_history_handoff`" as a known data shape.
Downstream code was written assuming this had already run.

**Fix: add a script and run it once.** It is idempotent. First, check whether
Callie's memory already has this history from some other route. Confidence: HIGH.

---

**6. The cheap signal filter that would cut LLM cost has never run.**
`artemis/marketing/cross_reference.py` + `artemis/marketing/qualifier_rule_layer.py`

The largest single finding: 513 lines of working, heavily tested logic that
nothing calls.

It is a rule layer of 12 declarative rules in three tiers. **Hard-skip rules**
kill a worthless signal *before* any money is spent on an LLM call. **Suppress
rules** downgrade stale or paywalled items. **Boost rules** promote a signal to
hot. It loads the last 100 sibling signals for the same district plus Salesforce
account context to decide, and writes an audit row for every rule that fires.

The docstring names its own intended call sites precisely: hard skips before
phase 1, suppress-and-boost after phase 3. Neither call was ever added.

You can see the consequence in the database: `skipped_signals` has 0 rows and
`qualifier_rule_applications` has 0 rows. Nothing has ever written to either
table. Meanwhile 699 signals sit in `pending_qualification` and 325 were rejected
by a hard filter *elsewhere*, after the LLM had already looked at them.

**Fix: two call sites in `artemis/marketing/routes/signal_queue.py`.** There is a
`TODO(M3)` at the top of the module noting it writes status directly rather than
through the state machine, and a test exists that forbids direct status writes,
so that needs reconciling first. Confidence: HIGH. This is the one with the
clearest cost saving attached.

---

**7. Ares's durable project memory exists as a table with no door.**
`artemis/dev_projects/workspace_memory.py`

A per-project drawer holding a plan, a file map, progress, open threads, and an
append-only decisions log. Four repository functions, correctly written including
the JSONB in-place-mutation fix that has bitten this codebase before. 115 lines,
194 lines of tests, a database table, and a migration.

Nothing reads it. There is no route (all 30 dev-project endpoints checked), no
tool, and `artemis/dev_projects/tools.py` is a five-line docstring saying it
"exists as the future integration point" and registers nothing. One other module
references it in a comment, as a pattern to copy.

**This is worth flagging specifically because "durable project memory so Jon does
not have to re-brief" is the stated core of the Ares plan.** It is built. It just
has no door. Combined with finding 4, Ares currently cannot read a project's code
*or* remember anything about it between sessions.

**Fix: implement `register_dev_projects_tools` in that stub and call it from the
tool registry, so Ares can read and update the drawer mid-session.**
Confidence: HIGH.

---

**8. Screen-Time Watch is invisible on the pipelines page.**
`artemis/screentime/pipeline_seed.py`

184 lines whose entire job is to insert a display-only pipeline row so that
Screen-Time Watch shows up in the UI alongside the other pipelines. The insert is
idempotent. It is never called: no script, no startup call, no command-line entry
point. Its sibling, the marketing pipeline seeder, has both.

Net effect: a scheduled job that runs every day does not appear anywhere someone
would look for it.

**Fix: call it once from the screentime scheduler's startup, or add a seed
script.** Confidence: HIGH.

---

### Tier 2: duplicated or stranded code

**9. `artemis/memory/backup.py` was built, forgotten, then rebuilt bigger.**
A tested `pg_dump` and restore wrapper with zero references anywhere in the repo,
and no command-line entry point. Meanwhile `scripts/memory_backup.py` and
`scripts/memory_restore.py` are an independent, more capable second
implementation of the same thing, with safety the library version lacks
(an empty-database anomaly guard, never deleting the last backup, Postgres major
version matching). They do not import the library version at all.

This is the pattern in its purest form. **Recommendation: delete the library
version.** The scripts are better, and leaving both means someone eventually
imports the weaker one. Note separately that neither is currently *scheduled*
(see section 3, there is no automated backup running). Confidence: HIGH.

**10. The pipeline assistant has two copies of the same schema, and the half that
does the work is stranded.** `artemis/pipelines/assistant/proposals.py` defines a
`PipelineProposal` model and `apply_proposal`, the pure function that executes a
proposed pipeline change against the graph (including cascading edge removal).
Production imports `PipelineProposal` from a *different* module,
`assistant/schemas.py`, and `apply_proposal` is called by nothing: the apply step
was pushed to the frontend and the server-side implementation was left behind.
There are two test suites, one per copy. Confidence: HIGH.

**11. `artemis/automations/schemas.py` has zero references of any kind.**
The complete typed request and response models for the Automations API, plus two
converters. Not referenced in production, not in tests, not in docs, in a scan of
6,414 files. The routes hand-roll dicts instead. Lowest-risk item here: adopting
them buys typed API documentation for free. Confidence: HIGH.

**12. `artemis/marketing/schemas.py`: 322 lines of unused typed models.**
The full create/read/update models for the entire Marketing OS domain. All 16
marketing routers build response dictionaries by hand instead. This is unused
*typing* rather than unused *behaviour*, so nothing is broken. The risk is drift:
the models will silently diverge from what the endpoints really return, and
whoever trusts them later gets a wrong answer. Confidence: MEDIUM.

### Tier 3: parked on a precondition that has now been met

**13. `artemis/enablement/sync.py` was parked waiting for Kai, and Kai exists.**
417 lines that sync Kai's content catalog from a Google Sheet, cleverly using the
Drive export endpoint because the Sheets API scope is not provisioned. Its
docstring says "Do NOT register this on a scheduler yet. Lead wires the cron once
Kai's agent shell exists."

Kai's agent shell exists. The note is stale. Right now the catalog is only
refreshed when the external Apps Script pushes to the webhook.
**Fix: register it on the shared automation scheduler.** Confidence: MEDIUM.

**14. Reddit sentiment: 676 lines, never run once.**
A complete Reddit client for parent-sentiment monitoring, with rate limiting and
a deliberate privacy design (it never records post authors), and 731 lines of
tests, all mocked. The module header says plainly that the Reddit app has not
been created yet and nothing has been exercised against live Reddit. Genuinely
blocked on an external step rather than forgotten. **Fix: create the Reddit OAuth
app.** Confidence: MEDIUM.

### Tier 4: gaps rather than dead code

**15. Artemis cannot read a web page.** Callie and Kai both have `read_web_page`.
Artemis does not, along with seven other tools Callie has and she does not
(`check_salesforce_activity`, `salesforce_pipeline`, `dispatch_research`,
`get_screentime_report`, `record_screentime_feedback`, `import_target_accounts`,
`send_guarded_dm`). Callie's registry was hand-built later and got the newer
tools; the general path Artemis uses never had them added. This looks like drift,
not a decision.

**16. The Gmail read tools always return nothing.** They look up the credential
with a hardcoded `user_id=1`, the dev-shim user, which does not exist in
`google_credentials`. Sending works. Reading has never worked. Two other modules
carry comments about fixing this exact mistake.
`artemis/integrations/gmail/tools.py:40`.

**17. The Brand Signals daily brief is one environment variable away from
running.** Fully built, registered on the scheduler, and dormant because
`ARTEMIS_BRAND_SIGNALS_CHANNEL` is unset. The startup log says
`channel '(unset - dormant)'`.

**18. There is no automated database backup.** Three launchd files exist for
archiving, backup and a monthly recovery drill. All three point at a directory
the repo no longer lives in, and none are installed.

**19. The manual "Run scout" button mostly does nothing.** It calls a different,
older code path (`scout_runner` plus `scout_sources`) in which 8 of the 9 source
adapters are explicit stubs that return an empty list. Only `board_minutes` is
real. The scheduled scouts use a completely different path and do work.

### A decision to make, not a bug

**The `artemis/scouts/` class-based subsystem is half-dead and should not simply
be deleted.** Eleven scout classes plus their own runner, worker and scheduler,
superseded by the LLM-agent approach that production now uses. The top-level
classes are unreachable from the running app (they are documented operator
command-line tools). But their lower-level helpers are very much alive: the agent
tool layer imports the HTTP helper, the Bonfire procurement client, the board
minutes client and the state DoE sources from it, and Screen-Time Watch calls two
of the scout classes directly. Deleting it wholesale would break the tools layer.
This needs an explicit decision about which half survives.

---

## 6. How to regenerate this document

Nothing here is generated automatically. It was assembled by direct inspection on
2026-09-04, and it will start drifting immediately. Re-derive it, do not trust
it, when something surprises you.

**Start here, always.** One consolidated health report: service state, per-agent
activity across all six stores, the marketing funnel, in-flight pipeline runs,
source yield, and derived findings. Read-only and safe against production.

```bash
uv run python -m artemis.ops
```

**Section 1, the agents and their tools.** Build the registries and print them.
This is the only way to get a true tool list, because the registries are
constructed at runtime:

```python
# PYTHONPATH=. uv run python
from artemis.floating_artemis.tool_registry import (
    _build_callie_tool_registry, _build_kai_tool_registry,
    _build_ares_tool_registry, build_authorized_tool_registry,
)
from artemis.routes.status import _AVAILABLE_SURFACES

r = _build_callie_tool_registry("callie", speaker_id="U123")
for e in sorted(r.all_entries(), key=lambda x: x.tool.name):
    print(e.layer, e.tool.name, e.tool.description[:100])

# Artemis uses the general path:
build_authorized_tool_registry(set(_AVAILABLE_SURFACES), agent_id="artemis")
```

Memory permissions are in `artemis/identity/scope_policy.py`. Allowlists resolve
from settings: `callie_dm_requester_emails`, `callie_dm_recipient_emails`,
`kai_action_authorized_user_ids`, and for Artemis
`resolve_slack_config(session).allowed_user_ids`.

**Section 2, integrations.** Read `artemis/config.py` for settings, but remember
credentials mostly live in the database. To see what is connected without
printing any secret:

```bash
PYTHONPATH=. uv run python -c "
import asyncio; from sqlalchemy import text; from artemis.db import SessionLocal
async def m():
    async with SessionLocal() as s:
        for row in await s.execute(text('SELECT provider, agent_id, status FROM integrations ORDER BY id')):
            print(row)
asyncio.run(m())"
cut -d= -f1 .env | sort   # env var NAMES only, never values
```

**Section 3, scheduled work.** The startup log is the source of truth for what
actually registered, because several jobs are conditional:

```bash
grep -iE "schedule|scheduler|cron|dormant" ~/Library/Logs/artemisos/app.err.log | tail -40
launchctl list | grep -i artemis
crontab -l
```

Pipeline schedules are in the database (`pipelines.trigger_config`), not in code.

**Section 4, row counts.** Any `SELECT count(*)`. The consolidated version used
here iterates `information_schema.tables`.

**Section 5, the unreachable hunt.** The method that worked: for each module,
take its public names and grep for them across `artemis/` and `scripts/` while
excluding **both** test locations (`tests/` and `artemis/**/tests/`). Then check
three things before concluding anything is dead, because each produces false
positives: string-literal dispatch (grep the module name as a quoted string),
`python -m` entry points (look for `if __name__ == "__main__"`), and documented
operator command-line tools.

```bash
# routers defined vs routers mounted
grep -rn "= APIRouter(" artemis --include="*.py" | grep -v tests
grep -n "include_router" artemis/main.py

# tool registrations defined vs called
grep -rh "def register_[a-z_]*(" artemis --include="*.py" | sed 's/.*def \(register_[a-z_]*\)(.*/\1/' | sort -u
```

Both of those came back clean this time. The findings were all at module level.

**Keeping this current.** When a slice lands that adds a tool, a scheduled job,
an integration, or a table, update the relevant section in the same commit. The
sections are deliberately independent so that is a small edit rather than a
rewrite. When something in section 5 gets wired up, move it out and say so, the
way stalled deals and `pipeline_hygiene` were moved out of it today.
