# Kai Agent Registration Map

**Purpose:** Precise file:line references so Lead can build Kai's agent shell
without needing to explore the codebase.  Nothing is built here — this is a
map only.  All paths are repo-root relative.

---

## 1. Persona loading — `load_agent_profile`

**File:** `artemis/floating_artemis/personality.py`

### How it works

`load_agent_profile(agent_id: str) -> AgentProfile` (line 163) is the single
entry point.  It is `@cache`-decorated so the profile is loaded once per
process per agent_id.

Resolution order (lines 101–122):

1. Normalize `agent_id` → lowercase strip.
2. Look up `_AGENT_DEFAULTS` dict (line 101).  Known entries today:
   - `"artemis"` → `artemis-personality-profile.md` + hardcoded `ARTEMIS_PERSONA_CORE`
   - `"callie"` → `callie-personality-profile.md` + hardcoded `CALLIE_PERSONA_CORE`
3. If not found in `_AGENT_DEFAULTS`, fall back to `f"{agent_id}-personality-profile.md"`
   (line 120) — meaning **Kai works already** with just the file on disk.
4. Profile text is read from `_REPO_ROOT / filename` (line 88 + 122).  Missing
   file → empty string + warning (never crashes, line 128–132).
5. Returns `AgentProfile(agent_id, display_name, persona_core, profile_text, voice_corpus)`.

### What Kai needs

**The `kai-personality-profile.md` file already exists** in the repo root
(`/kai-personality-profile.md`).  `load_agent_profile("kai")` will find it
automatically via the fallback path — **no code change needed** for persona
loading.

To add a `KAI_PERSONA_CORE` inline string (like Artemis/Callie have) and a
display name, add a `"kai"` entry to `_AGENT_DEFAULTS` at line 101:

```python
"kai": {
    "display_name": "Kai",
    "persona_core": KAI_PERSONA_CORE,          # define above
    "profile_filename": "kai-personality-profile.md",
},
```

Without this entry `display_name` defaults to `"Kai"` (title-cased agent_id,
line 170) and `persona_core` defaults to `""` — both acceptable for MVP.

---

## 2. Agent registration — the three wiring points

### 2a. `integrations` DB row — Slack app credentials

**File:** `artemis/integrations/models.py` (line 35–58)

Table: `integrations`.  Unique on `(provider, workspace_id, agent_id)`.

Callie's row (example from the DB schema + triage code):

| column | value for Callie | value for Kai |
|---|---|---|
| `provider` | `"slack"` | `"slack"` |
| `workspace_id` | Slack team/workspace ID | same workspace |
| `agent_id` | `"callie"` | `"kai"` |
| `encrypted_credentials` | Callie's bot token (encrypted) | Kai's bot token |
| `bot_user_id` | Callie's Slack bot user ID | Kai's Slack bot user ID |
| `scopes` | bot token scopes | same scopes |

**How triage looks it up:** `artemis/integrations/slack/triage.py` —
`resolve_agent_id_for_event()` maps the incoming Slack `channel_id` or
`app_id` to an `agent_id`.  The exact lookup path depends on how Callie was
wired; Lead should verify via:

```bash
grep -rn "agent_id.*callie\|callie.*agent_id" artemis/integrations/ --include="*.py"
grep -rn "channel.*callie\|callie.*channel" artemis/ --include="*.py" | grep -v __pycache__ | head -20
```

**Action for Lead:**
1. Duplicate Callie's Slack manifest → create Kai's Slack app → install in workspace.
2. Insert a row into `integrations`: `provider="slack"`, `agent_id="kai"`,
   `workspace_id=<team_id>`, `bot_user_id=<kai_bot_user_id>`,
   `encrypted_credentials=<encrypted Kai bot token>`.
3. Invite Kai's bot to channel `C0BB17EJLKC`.
4. Wire `C0BB17EJLKC` → `agent_id="kai"` in the triage routing (see triage.py).

### 2b. Floating-agent loop parameterisation — `handle_turn`

**File:** `artemis/floating_artemis/chat.py`

`handle_turn(...)` takes `agent_id: str` and passes it to:
- `load_agent_profile(agent_id)` (line 73 context, builds system prompt)
- `build_authorized_tool_registry(available_surfaces, agent_id=agent_id)` →
  `register_core_tools(registry, agent_id=agent_id)` so `query_memory` is
  scoped to the calling agent's allowance (M3).
- `resolve_surface_scope(all_surfaces, session_id, metadata)` which reads
  `_session_agent_id(session_id, metadata)` to find the agent_id.

**Session id format** (line 53–64 in `session_scope.py`): `slack-{agent_id}-...`
— so a Kai session would be `slack-kai-{workspace}-{channel}-{ts}`.  The
triage layer constructs this; Lead should verify `triage.py` builds the right
session_id for Kai's channel.

**No code change is needed** for `handle_turn` itself — it is already fully
parameterised by `agent_id`.

### 2c. `session_scope.py` — surface allowlist for Kai

**File:** `artemis/floating_artemis/session_scope.py` (line 25–29)

```python
_AGENT_SURFACE_ALLOWLIST: dict[str, frozenset[str]] = {
    "callie": _MARKETING_SURFACES,
    # ADD:
    # "kai": _KAI_SURFACES,
}
```

Kai should get a **new frozenset** containing only enablement-related surfaces
(none of the marketing surfaces).  For MVP where Kai has no surface-gated
tools, an empty frozenset causes `all_surfaces ∩ {}` = `{}` — Kai gets zero
surfaces.  That is correct (all Kai's tools register unconditionally, not
surface-gated).  OR omit the entry entirely so Kai falls through to
`return set(all_surfaces)` (line 97) and sees all surfaces but tool_registry
gates what he can actually call.

**Recommendation:** Add `"kai": frozenset()` to be explicit that Kai has no
surface entitlements, then register Kai's retrieval tools unconditionally in
tool_registry.py (like `register_slack_tools` and `register_gcal_tools` today).

---

## 3. Scope policy — `scope_policy.py`

**File:** `artemis/identity/scope_policy.py`

### Where to add Kai

Four touchpoints:

**3a.** `_AGENT_MARKETING_SCOPE_IDS` (line 58) — do NOT add Kai here (this is
marketing-only).

**3b.** Add a new `allowance_for_agent_kai()` function after line 146:

```python
# Scope kinds Kai may read — enablement scope only.
_ENABLEMENT_SCOPE_KINDS: frozenset[str] = frozenset({"enablement"})

def allowance_for_agent_kai() -> ScopeAllowance:
    """Kai — enablement-scoped read-only. NO personal:*, NO agent:artemis, NO marketing."""
    return ScopeAllowance(
        allow_all=False,
        allowed_scope_kinds=_ENABLEMENT_SCOPE_KINDS,
        allowed_agent_ids=frozenset({"kai"}),
        personal_user_id=None,
    )
```

Note: `source_scope="enablement"` in `enablement_assets` is NOT a
`memory_observations` scope — Kai's retrieval tools will query `enablement_assets`
directly (not via the memory observation API), so the scope_policy above guards
**memory read access** only.  The enablement_assets table has its own RBAC
enforced at the tool level.

**3c.** `allowed_scopes_for_agent()` (line 177) — add the Kai branch:

```python
if normalized == "kai":
    return allowance_for_agent_kai()
```

Add it after the `"callie"` branch (line 192) and before the `_logger.warning`
deny block.

**3d.** Kai does NOT need an entry in `resolve_agent_id_from_email()` (line 196)
— that function is for human email → agent mapping (owner=artemis, other=callie).
Kai is channel-routed, not email-routed.

---

## 4. Tool registry — scoping Kai's tools

**File:** `artemis/floating_artemis/tool_registry.py`

`build_authorized_tool_registry(available_surfaces, agent_id)` (line 24) is
where all tools are registered.  Current surface-gated blocks:

```python
if "okr" in available_surfaces: register_okr_tools(...)
if "writing-rules" in available_surfaces: ...
if "marketing-os" in available_surfaces or ...: register_marketing_tools(...)
if "jira-board" in available_surfaces: ...
if "meetings" in available_surfaces: ...
```

Unconditional (always registered):
- `register_core_tools` — includes `query_memory` (M3-scoped) + general tools
- `register_builders_tools`
- `register_system_tools`
- `register_slack_tools`
- `register_gcal_tools`
- `register_gmail_tools`

### What Lead needs to add for Kai

1. **Create** `artemis/enablement/tools.py` with a `register_enablement_tools(registry)`
   function that registers:
   - `search_enablement_assets` — semantic + keyword search over `enablement_assets`
     (top-N by embedding cosine similarity + optional text filter)
   - `get_enablement_asset` — single asset lookup by drive_file_id or title
   - Both are **read-only** (no INSERT/UPDATE/DELETE, no side effects)

2. **Add** to `build_authorized_tool_registry` (unconditional OR gated on a
   new `"enablement"` surface):

   ```python
   # Recommended: gate on a new "enablement" surface so Kai gets it and others don't
   if "enablement" in available_surfaces or agent_id == "kai":
       from artemis.enablement.tools import register_enablement_tools
       register_enablement_tools(registry)
   ```

3. **Callie's analyst toolset reference:** `artemis/floating_artemis/tools/marketing.py`
   — `register_marketing_tools(registry)`.  Pattern is identical: one file per
   domain, functions added to the registry via `registry.register(tool_def)`.

4. **Authority layer:** `artemis/floating_artemis/authority.py` — the
   `AuthorizedToolRegistry` wraps the base `ToolRegistry` and adds layer
   enforcement.  Kai's tools should be **Layer 1 (read-only)** — no
   confirmation required.  Declare `layer=1` on each tool definition.

---

## 5. Summary — Lead's build checklist for Kai's agent shell

In order:

1. **Scope policy** (`scope_policy.py`): add `allowance_for_agent_kai()` +
   wire it in `allowed_scopes_for_agent()`.
2. **Surface allowlist** (`session_scope.py`): add `"kai": frozenset()`.
3. **Persona** (`personality.py`): optionally add `"kai"` to `_AGENT_DEFAULTS`
   for inline `persona_core`; otherwise the fallback file path works as-is.
4. **Slack app**: duplicate Callie's manifest → new Kai app → install → get
   bot token + bot_user_id.
5. **DB row**: insert `integrations` row (`provider="slack"`, `agent_id="kai"`,
   `workspace_id=<team>`, encrypted bot token, `bot_user_id`).
6. **Triage routing**: wire `C0BB17EJLKC` → `agent_id="kai"` in
   `artemis/integrations/slack/triage.py`.
7. **enablement/tools.py**: create with `register_enablement_tools(registry)`.
8. **tool_registry.py**: add `register_enablement_tools` call for Kai.
9. **Migration** (`0095_enablement_assets.py`): run `uv run alembic upgrade head`.
10. **Cron**: wire `sync_enablement_index` to a cron task (Lead's call on cadence;
    `artemis/enablement/sync.py:sync_enablement_index` is the callable).
