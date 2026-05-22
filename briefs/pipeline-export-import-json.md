# Pipeline Export / Import JSON — n8n-Style Portability

**Owner:** Codex (paste-ready)
**Branch:** `codex/pipeline-export-import-json`
**LOC budget:** ~250 (honest overrun OK to ~320)
**Depends on:** PIPE1 merged. M5 agents seeded.

## Why

Pipelines are valuable artifacts. Users will want to:
- **Back up** a pipeline before risky edits
- **Share** a pipeline with a teammate (or version-control it externally)
- **Migrate** a pipeline between Artemis instances (dev → prod)
- **Bootstrap** from a known-good template

n8n's export/import pattern is the right reference. Per Jon: "should there be a way to export a JSON of the workflow (and it includes all the agent information in it as well) and then an import option."

## Scope

### Export

`GET /api/pipelines/{id}/export` → returns a JSON bundle:

```json
{
  "format_version": "1",
  "exported_at": "2026-05-22T...",
  "exported_from": "https://app.artemisos.me",
  "pipeline": {
    "name": "...",
    "description": "...",
    "nodes": [...],
    "edges": [...],
    "trigger_config": {...},
    "status": "active",
    "metadata": {...}
  },
  "agents_required": [
    {
      "agent_id": "marketing.scout.starbridge_researcher",
      "name": "Starbridge Researcher",
      "system_prompt": "...",
      "tools": [...],
      "persona": {...},
      "model": "claude-haiku-4-5",
      "provider": "claude-code",
      "fallback_provider": "anthropic",
      "fallback_model": "claude-haiku-4-5",
      "memory_policy": "persistent",
      "permission_mode": "auto",
      "reason_codes_emitted": [...]  // if column landed
    },
    // ... one per unique agent_id referenced in pipeline.nodes
  ],
  "connectors_required": [
    {
      "kind": "starbridge",
      "label": "Required for scouts that call starbridge.* tools",
      "fields_needed": ["api_key", "api_url"]
    }
    // ... one per kind referenced
  ]
}
```

**Critically — no credentials in the export.** The `connectors_required` section is a manifest, not values. Importer must configure connectors locally before the pipeline can run.

### Import

`POST /api/pipelines/import` with body = the export JSON.

Flow:
1. Validate `format_version` and JSON shape
2. For each agent in `agents_required`:
   - If `agent_id` exists in DB: warn but don't overwrite (operator decides via separate flow if they want to update)
   - If not: create the agent row with all fields from the export
3. Create the pipeline row with nodes/edges/trigger_config/metadata
4. Check `connectors_required`:
   - For each kind, query existing connectors of that kind
   - If none exist: import succeeds but pipeline status defaults to `paused` with a warning in `metadata.import_warnings = ["No starbridge connector configured; create one before running"]`
   - If at least one exists: pipeline is `active`
5. Return:
   ```json
   {
     "pipeline_id": "...",
     "agents_created": ["marketing.scout.new_one"],
     "agents_skipped": ["marketing.scout.starbridge_researcher"],  // already existed
     "import_warnings": [...]
   }
   ```

### UI

**Pipelines list page:**
- New kebab menu items per pipeline card: "Export JSON"
- Page-level button: "Import JSON"

**Export flow:**
- Click "Export JSON" → browser downloads a file `<pipeline-name>-<YYYY-MM-DD>.json`
- Filename sanitized (lowercase, hyphens, no spaces)

**Import flow:**
- Click "Import JSON" → file picker opens
- User selects .json file
- Frontend validates structure client-side (parse JSON, check format_version)
- POST to `/api/pipelines/import`
- Show result toast: "Imported as 'Marketing Pipeline' with 2 new agents. 1 warning: configure Starbridge connector to run."
- Redirect to the imported pipeline's canvas

### Tests

- Export round-trips: create a pipeline → export → parse JSON → contains expected agents + connectors_required
- Import creates pipeline + missing agents
- Import skips agents that already exist with same agent_id
- Import with missing connectors → pipeline created as paused with warning
- Round-trip: export pipeline A → import as pipeline B → both have same nodes/edges (different IDs)
- Invalid JSON → 422 with clear error
- Older format_version → 422 with "Format upgrade required" message

### Out of scope

- Connector credentials in export. Security; never travel in JSON.
- Multi-pipeline bundle (export entire workspace). Single-pipeline only.
- Diff view ("import would change agent X — accept changes?"). Defer.
- Browser file-system access to overwrite the original .json on save. Standard download only.
- Encryption of exported JSON. Plaintext for v1.
- Format_version migrations. Hardcode "1" for v1; future versions handle upgrades.
- Imported pipeline's `pipeline_runs` history. Fresh state on import.

## Files

| File | LOC |
|---|---|
| `artemis/pipelines/routes.py` | ~80 delta (export + import routes) |
| `artemis/pipelines/repository.py` | ~50 delta (export bundle builder, import processor) |
| `artemis/pipelines/schemas.py` | ~40 delta (bundle schema) |
| `public/js/features/pipelines.js` | ~70 delta (export download + import flow) |
| `public/css/features/pipelines.css` | ~10 delta (kebab menu item) |
| Tests | ~80 |

**Total: ~330 LOC.** Cap 400. Backend is the bulk; UI is straightforward download + file picker.

## Invariants

- Credentials NEVER appear in exported JSON (security invariant — add a test that asserts no field named "api_key", "secret", "token" appears anywhere in any exported pipeline)
- Import never silently overwrites existing agents (must skip + warn)
- format_version validates strict (only "1" accepted in v1; future versions are explicit upgrades)
- conftest hard-fail on non-test DB
- node --check on modified JS
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, sample exported JSON (paste, redacting any non-trivial content), screenshots of export download + import file picker + import result toast, test pass count, branch.

---

**Lead notes (not for Codex):**
- This is foundational portability. Once exports work, users can version-control their pipelines in their own repos, share templates, migrate between environments — all useful workflows.
- The connectors_required manifest is the key UX call. Without it, an imported pipeline silently fails when first run because credentials are missing. With it, the operator sees what they need to configure before triggering.
