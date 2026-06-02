# Content Team — Use Assets

Three components turn an approved Signal Brief into Writing Studio draft submissions. Two are deterministic; one is an LLM agent.

## Roster

| ID | Component | Type | Role |
|---|---|---|---|
| 5.1 | Campaign Brief Assembler | Deterministic | Build the immutable campaign brief |
| 5.2 | Asset Selector Agent | Agent (LLM) | Pick ONE asset bundle for the whole campaign |
| 5.3 | Writing Studio Adapter | Deterministic integration | POST drafts to Writing Studio API |

## Trigger

Triggered when a Campaign Workspace is created (Gate 1 approval). 5.1 → 5.2 → 5.3 run synchronously in sequence per workspace.

## Where compliance went

The original Artemis OS design had a Compliance team between content and Writing Studio. **Compliance is out of v1 scope.** Brand voice enforcement happens inside Writing Studio (which owns trained brand voice memory and format-specific rules).

What Artemis OS still does on the input side:
- **5.1 runs input validation checks** (deterministic) before sending the brief downstream — `evidence_present`, `reason_codes_in_registry`, `campaign_type_approved`. These are hygiene, not compliance.
- If any validation check fails, the workspace status becomes `content_preparation_failed` and the brief never reaches Writing Studio. Alert raised.

## End-to-end flow

```
Gate 1 approval
    │
    ▼
campaign_workspaces (status: pending_content)
    │
    ▼
5.1 Campaign Brief Assembler
    ├── Build immutable campaign_brief object from signal + district + reason codes
    ├── Run validation checks (deterministic)
    └── On pass: write to campaign_workspaces.campaign_brief
                  status → in_content_preparation
    │
    ▼
5.2 Asset Selector Agent
    ├── Score available assets against campaign_brief
    ├── Pick ONE primary + up to 3 supporting
    └── Write to campaign_workspaces.asset_bundle
    │
    ▼
5.3 Writing Studio Adapter
    ├── For each deliverable_type the campaign needs (email, social, etc.):
    │     POST /drafts to Writing Studio with brief + assets
    └── Record draft_id back into campaign_workspaces.writing_studio_drafts
        status → sent_to_writing_studio
```

## What lives here, what doesn't

**Lives here:**
- Brief assembly logic
- Asset selection scoring
- Writing Studio API integration

**Does NOT live here:**
- Drafting itself (Writing Studio)
- Brand voice enforcement (Writing Studio)
- Format-specific rules (Writing Studio Rules primitive)
- Approval / review (Writing Studio Approval Drawer = Gate 2; reference only in this spec)
- Send / distribution (Hubspot / Olivia / Julia downstream of Writing Studio)
