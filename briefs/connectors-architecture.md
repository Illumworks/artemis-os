# Connectors Architecture — Per-Source Credentials via Integrations

**Owner:** Sonnet Worker (architectural design + integration with existing surfaces)
**Branch:** `worker/connectors-architecture`
**LOC budget:** ~900 (honest overrun OK to ~1200 — backend + integrations panel UI + agent linking)
**Depends on:** OP1 (automations registry — not strictly required but informs scheduler patterns), existing Integrations panel (Slack OAuth, Google Calendar, Granola).

## Why

Agents need credentials to access external data sources (Starbridge API key, OpenAI API key, Anthropic API key, Tavily, etc.). Today these live in `~/.artemis/.env` — hand-managed, no UI, no per-environment, no audit. When PIPE4 ships and the Run button actually invokes agents, those agents need credentials at runtime.

**Solution:** extend the existing Integrations panel to host **per-source-type Connectors**. Each Connector is one configured credential set (e.g., "Starbridge — production"). Agents reference connectors via their `tools` JSONB. Runtime resolver looks up the connector, pulls credentials, injects into the API call.

This is closer to MCP's pattern + n8n's credential management. Shared connectors, agents reference them by ID.

## Scope

### Data model

1. **`connectors` table:**
   - `id` UUID PK
   - `kind` TEXT — `slack`, `google_calendar`, `granola`, `starbridge`, `openai`, `anthropic`, `tavily`, etc.
   - `name` TEXT — human-readable ("Starbridge — production")
   - `credentials` JSONB — encrypted-at-rest field values (API keys, OAuth tokens, etc.). The schema varies by `kind`.
   - `status` TEXT — `active` / `disabled` / `needs_reauth`
   - `owner_user_id` BIGINT NULL
   - `metadata` JSONB — free-form (e.g., OAuth expiry, last_validated_at)
   - `created_at`, `updated_at`

2. **`agent_connectors` join table** (many-to-many):
   - `agent_id` BIGINT FK → agents
   - `connector_id` UUID FK → connectors
   - `tool_namespace` TEXT — which tool prefix this connector serves (e.g., `starbridge`, `openai`)
   - PK: `(agent_id, connector_id, tool_namespace)`

3. **Credential encryption:**
   - Use Python's `cryptography.fernet` with a key from `~/.artemis/.env` (e.g., `ARTEMIS_CONNECTOR_KEY`)
   - On startup, fail loudly if key is missing
   - Encrypted bytes stored in JSONB as base64 strings
   - Decrypted at runtime when the connector is fetched for an agent invocation

### Routes

- `GET /api/connectors` — list with `status` and last_validated_at
- `POST /api/connectors/` — create. Body: `{kind, name, credentials}`. Encrypts credentials before write.
- `GET /api/connectors/{id}` — detail (DECRYPTED credentials returned only to authenticated owner; for other contexts, returns masked).
- `PATCH /api/connectors/{id}` — update credentials, name, status.
- `DELETE /api/connectors/{id}` — soft delete (status → disabled). Hard delete (`/permanent`) requires confirmation.
- `POST /api/connectors/{id}/test` — validate credentials by making a test API call. Returns success/failure with error message.
- `POST /api/agents/{agent_id}/connectors` — link an agent to a connector (creates agent_connectors row).
- `DELETE /api/agents/{agent_id}/connectors/{connector_id}` — unlink.

### UI

1. **Integrations panel extension:**
   - Existing OAuth-based integrations (Slack, Google, Granola) stay as-is — they're already connectors in shape
   - Add new section: "API Connectors" listing all non-OAuth connectors with their kind icons
   - "Add connector" button → modal: pick kind from dropdown → kind-specific form (e.g., Starbridge form has "API key" + "API URL" fields; OpenAI form has "API key" + "Organization ID")
   - Test button on each connector card → fires `/test` → shows success or error inline
   - Edit / Delete actions

2. **Agent Card additions:**
   - New section: "Linked Connectors"
   - Lists connectors this agent uses (based on `agent_connectors` join)
   - When an agent's `tools` array contains a string like `starbridge.search`, the UI infers the `starbridge` namespace and shows a "Required: Starbridge connector" badge if no connector of that kind is linked
   - "Link connector" button → multi-select from available connectors of matching kind
   - Visual cue: agents with unlinked required connectors show a warning badge (similar to the missing-fallback warning Codex already shipped)

### Runtime resolver

Module: `artemis/connectors/resolver.py`

Function: `get_credentials_for_tool(agent_id: int, tool_namespace: str) -> dict[str, str]`

Behavior:
- Look up `agent_connectors` row for this agent + namespace
- Fetch the linked connector
- Decrypt credentials
- Return as dict (e.g., `{"api_key": "sk-...", "url": "https://..."}`)
- If no connector linked: raise `ConnectorNotConfigured` with clear message naming what's missing

Used by:
- `artemis/marketing/scout_runner.py` (when invoking source adapters)
- PIPE4 executor (when invoking agent_invocation nodes)
- Anywhere else an external API call is made

### Connector kinds for v1

Seed these as supported kinds (hardcoded list in `artemis/connectors/kinds.py`):
- `starbridge` — fields: `api_key`, `api_url`
- `openai` — fields: `api_key`, `organization` (optional)
- `anthropic` — fields: `api_key`
- `gemini` — fields: `api_key`
- `tavily` — fields: `api_key`
- (OAuth-based connectors — slack, google_calendar, granola — already exist; just need to be modeled in the connectors table for consistency)

### Tests

- CRUD round-trip including encryption
- Test endpoint validates Starbridge / OpenAI / Anthropic stubs (mock the actual API call; assert auth header is correct)
- Agent linking + unlinking
- Runtime resolver: returns correct credentials for linked connector
- ConnectorNotConfigured raised when no connector linked
- Soft delete preserves row; permanent delete requires status=disabled

### Out of scope

- Credential rotation automation (future)
- Per-environment connectors (dev/prod). v1 is single-environment.
- Multi-user sharing of connectors. owner_user_id only.
- Audit log of who set which credential when. Defer.
- Browser-side credential entry validation (e.g., "OpenAI keys start with sk-"). Defer.

## Files expected

| File | LOC |
|---|---|
| `alembic/versions/<rev>_connectors.py` | ~80 |
| `artemis/connectors/__init__.py` | ~5 |
| `artemis/connectors/models.py` | ~80 |
| `artemis/connectors/schemas.py` | ~100 |
| `artemis/connectors/repository.py` | ~120 |
| `artemis/connectors/routes.py` | ~160 |
| `artemis/connectors/resolver.py` | ~80 |
| `artemis/connectors/kinds.py` (kind registry) | ~60 |
| `artemis/connectors/encryption.py` | ~50 |
| `artemis/main.py` | ~3 delta |
| `artemis/marketing/scout_runner.py` (resolver integration) | ~20 delta |
| `public/js/features/integrations.js` (or similar) | ~150 delta (API Connectors section) |
| `public/js/features/operations-shell.js` (Agent Card connector section) | ~100 delta |
| `public/css/features/integrations.css` | ~50 delta |
| Tests | ~150 |

**Total: ~1200 LOC.** Cap 1400. Connectors is a real-sized feature; honest budget.

## Invariants

- Credentials are encrypted at rest, never logged
- `ARTEMIS_CONNECTOR_KEY` failure on startup is fatal (don't run without encryption capability)
- Existing OAuth integrations (Slack, Google, Granola) work unchanged
- conftest hard-fail on non-test DB
- node --check on modified JS
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, route paths + methods, encryption module's design (algorithm + key handling), screenshots of new Connectors section + Agent Card with linked connector, test pass count, branch.
