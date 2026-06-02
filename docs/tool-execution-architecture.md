# Tool Execution Architecture (F4 Design)

**Status:** Draft for Jon's review. Gates Phase 2 streams P2 + P3.
**Author:** Lead (Opus, 2026-05-26)
**Audience:** Phase 2 Workers (P2: registry + executor wiring; P3: concrete tool implementations) and any future Lead session inheriting this work.

---

## TL;DR

**The infrastructure already exists.** `artemis/agent/loop.py` has a full tool-use loop. `artemis/agent/tools.py` has a `ToolRegistry`. `artemis/agent/types.py` has `Tool` / `ToolImpl` / `ToolUseBlock` / `ToolResultBlock`. The Anthropic provider already passes `tools=tool_specs` in `CompletionRequest`. The loop already iterates: LLM call → tool_use blocks → execute → tool_result → next LLM call, with max_iterations + hooks + error handling.

**What's missing is the bridge from `agent.tools` (list of strings on the DB row) to actual `(Tool, ToolImpl)` pairs.** Today `run_agent()` at `artemis/builders/executor.py:223` passes `tools=None`. We need to (a) maintain a global registry of tool factories keyed by name, (b) at `run_agent` time, look up the factories for the agent's declared tools, (c) instantiate them with a context that carries the DB session / agent_id / run_id, and (d) pass the resulting per-call `ToolRegistry` into `run_turn`.

**Two streams to implement:**
- **P2 (Claude Code, ~500 LOC):** the bridge — global factory registry, per-call assembly, integration with `run_agent()`. Surgical change to `executor.py`. Tests.
- **P3 (Claude Code, ~800 LOC):** the concrete tool implementations — `signal_queue.write`, `news_api.search`, `state_doe.fetch`, etc. Each is small (~50-100 LOC) but there are many.

P2 and P3 are independent and can run in parallel. P3 builds the implementations; P2 wires them into the runtime. Both depend on F4 being signed off (this doc).

---

## What the LLM sees

When `run_agent()` calls `run_turn()` with `tools=registry`, the Anthropic provider sends an Anthropic-format tool list to the model. Each tool looks like:

```json
{
  "name": "signal_queue.write",
  "description": "Write a signal to the qualification queue. Signal must include sourceType, headline, campaignFamily, urgencyTier, reasonCodes, evidence.",
  "input_schema": {
    "type": "object",
    "required": ["sourceType", "headline", "campaignFamily", "urgencyTier", "reasonCodes", "evidence"],
    "properties": {
      "sourceType": {"type": "string", "enum": ["manual", "starbridge", "news_article", "board_minutes", "state_doe", "linkedin_post"]},
      "headline": {"type": "string"},
      "campaignFamily": {"type": "string"},
      "urgencyTier": {"type": "string", "enum": ["hot", "standard", "low"]},
      "reasonCodes": {"type": "array", "items": {"type": "string"}},
      "evidence": {"type": "string"},
      "districtId": {"type": "string"},
      "sourceUrl": {"type": "string"}
    }
  }
}
```

The model decides to emit a `tool_use` block with input matching the schema. The loop catches it, dispatches to the `ToolImpl`, gets a string back, and feeds it as `tool_result` into the next turn.

---

## The bridge — what P2 builds

### 1. Tool context

```python
# artemis/tools/context.py

from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession

@dataclass(frozen=True)
class ToolContext:
    """Per-agent-run context passed to tool factories.

    Tools that need DB access, the current agent's identity, or the pipeline
    run they belong to receive this. Lifetime: one agent run.
    """
    session: AsyncSession
    agent_id: str               # e.g. "marketing.scout.regional_news"
    agent_db_id: int            # primary key, for connector lookups
    agent_run_id: str           # UUID — for provenance / trace linkage
    pipeline_run_id: str | None # set when called from a pipeline executor; None for standalone Builder runs
```

### 2. Tool factory protocol

```python
# artemis/tools/registry.py

from collections.abc import Callable

from artemis.agent.types import Tool, ToolImpl
from artemis.tools.context import ToolContext

#: A factory takes a ToolContext and returns the (Tool definition, async impl) pair.
#: Factories are cheap to call; called once per agent run.
ToolFactory = Callable[[ToolContext], tuple[Tool, ToolImpl]]

_TOOL_FACTORIES: dict[str, ToolFactory] = {}

def register_tool(name: str, factory: ToolFactory) -> None:
    """Register a tool factory globally. Called at module import time."""
    if name in _TOOL_FACTORIES:
        raise ValueError(f"tool {name!r} already registered")
    _TOOL_FACTORIES[name] = factory

def get_factory(name: str) -> ToolFactory | None:
    return _TOOL_FACTORIES.get(name)

def known_tool_names() -> tuple[str, ...]:
    return tuple(sorted(_TOOL_FACTORIES))
```

### 3. Per-call registry assembly

```python
# in run_agent(), before calling run_turn():

from artemis.agent.tools import ToolRegistry
from artemis.tools.context import ToolContext
from artemis.tools.registry import get_factory

# Build the per-call registry from agent.tools
tool_context = ToolContext(
    session=session,
    agent_id=agent_id,
    agent_db_id=agent.id,
    agent_run_id=run_id,
    pipeline_run_id=shared_context.get("pipeline_run_id"),
)

tool_registry = ToolRegistry()
unknown_tools: list[str] = []
for tool_name in (agent.tools or []):
    # agent.tools is a list of strings like "signal_queue.write" — already supported.
    # Future: support dict shape for namespace + connector_kind once that ships.
    name = tool_name if isinstance(tool_name, str) else tool_name.get("name", "")
    factory = get_factory(name)
    if factory is None:
        unknown_tools.append(name)
        continue
    tool_def, impl = factory(tool_context)
    tool_registry.register(tool_def, impl)

if unknown_tools:
    logger.warning(
        "Agent %r declares unknown tools (will be silently dropped): %s. Known: %s",
        agent_id, unknown_tools, known_tool_names(),
    )

# Pass to run_turn instead of None
result = await run_turn(
    adapter=adapter,
    messages=[_user_msg(effective_message)],
    system=system_prompt,
    model=agent.model,
    max_iterations=agent.max_iterations,
    tools=tool_registry if len(tool_registry) > 0 else None,
    hooks=hooks,
)
```

That's the entire P2 wire-up. Replace the existing `tools=None` line at `executor.py:223` and the warning block above it (line 184) with this assembly. ~50 LOC delta in `executor.py`, plus the new module files.

### 4. Module structure for P3

```
artemis/tools/
├── __init__.py             # imports all submodules so register_tool() side-effects fire
├── context.py              # ToolContext
├── registry.py             # ToolFactory + register_tool / get_factory
├── signal_queue.py         # signal_queue.write
├── memory_layer.py         # memory_layer.get / upsert / compute_similarity (stubs OK for v1 if memory_layer.upsert_last_seen still TODO)
├── territory_config.py     # territory_config.get_priority_states
├── contact_db.py           # contact_db_stub.has_contact (returns True for v1)
├── news.py                 # news_api.search (Google News RSS based — no key required, NEWS_API_KEY optional enrichment)
├── state_doe.py            # state_doe.fetch (RSS — reuse artemis/scouts/state_doe/sources.py)
├── board_minutes.py        # board_minutes.fetch (BoardDocs — reuse artemis/scouts/board_minutes/client.py)
├── pdf_extractor.py        # pdf_extractor.extract (reuse artemis/scouts/_pdf.py)
├── legiscan.py             # legiscan.search / get_bill (stub if no key)
├── starbridge.py           # starbridge.search / get_document (stub if no key)
├── linkedin.py             # linkedin_scraper.fetch_posts / check_profile_delta (existing logic in artemis/scouts/linkedin/)
├── grants_gov.py           # grants_gov.search
├── federal_register.py     # federal_register.search
├── procurement.py          # procurement_portal.fetch
└── tests/
    └── test_<each>.py      # one test file per tool, ~30-50 LOC each
```

Each tool file follows the same skeleton:

```python
# artemis/tools/signal_queue.py
import json
from artemis.agent.types import Tool
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool
from artemis.marketing.models import SignalQueue
from artemis.marketing.scout_intake import normalize_intake_payload

_DEF = Tool(
    name="signal_queue.write",
    description=(
        "Write a signal to the qualification queue. The signal will be normalized "
        "and validated against the reason-code registry before insertion. Returns "
        "the new signal ID, or an error message if validation fails."
    ),
    input_schema={
        "type": "object",
        "required": ["sourceType", "headline", "campaignFamily", "urgencyTier", "reasonCodes", "evidence"],
        "properties": {
            "sourceType": {"type": "string", "enum": ["manual", "starbridge", "news_article", "board_minutes", "state_doe", "linkedin_post"]},
            "headline": {"type": "string"},
            "campaignFamily": {"type": "string"},
            "urgencyTier": {"type": "string", "enum": ["hot", "standard", "low"]},
            "reasonCodes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "evidence": {"type": "string"},
            "districtId": {"type": "string"},
            "sourceUrl": {"type": "string"},
            "whyFlagged": {"type": "string"},
        },
    },
)

def _factory(ctx: ToolContext):
    async def _impl(arguments: dict) -> str:
        # Anti-spoof: scout_type is inferred from the agent_id, not LLM-controlled.
        slug = ctx.agent_id.rsplit(".", 1)[-1]
        try:
            normalized = normalize_intake_payload(arguments, scout_type=slug)
        except ValueError as exc:
            return f"VALIDATION_ERROR: {exc}"
        row = SignalQueue(
            source_type=normalized.source_type,
            headline=normalized.headline,
            campaign_family=normalized.campaign_family,
            urgency_tier=normalized.urgency_tier,
            reason_codes=normalized.reason_codes,
            district_id=normalized.district,
            state=normalized.state_code,
            discovered_by=normalized.discovered_by,
            signal_status="pending_qualification",
            source_url=normalized.source_url,
            summary=normalized.verbatim_snippet or normalized.headline,
            pipeline_run_id=ctx.pipeline_run_id,
            provenance={
                "agent_run_id": ctx.agent_run_id,
                "agent_id": ctx.agent_id,
                "why_flagged": normalized.why_flagged,
            },
        )
        ctx.session.add(row)
        await ctx.session.flush()
        return json.dumps({"signal_id": row.id, "status": "written"})
    return (_DEF, _impl)

register_tool("signal_queue.write", _factory)
```

`__init__.py` imports every submodule so registration side-effects fire on package import. `artemis/builders/executor.py` adds one new line at top: `import artemis.tools  # noqa: F401 — registers tool factories`.

---

## Initial tool catalog — v1

These are the tools P3 ships. Each gets a real implementation OR a clearly-flagged stub returning a known error string (so the LLM gets actionable feedback instead of silent failure).

### Writes (DB side effects)

| Tool | Description | Status |
|---|---|---|
| `signal_queue.write` | Write a signal row, normalized via scout_intake. | Real (uses existing scout_intake module). |
| `unresolved_signals.write` | Write a malformed-signal row for later triage. | Real (existing table). |
| `memory_layer.upsert_last_seen` | Record (district, reason_code, signal_id) for dedupe. | Stub (table doesn't exist yet; tool returns "ok-stub", logs warning). Replaced when Memory-M2 lands. |

### Reads (DB + config)

| Tool | Description | Status |
|---|---|---|
| `territory_config.get_priority_states` | Returns the priority_states tuple from Josh's spec. | Real (reads `josh_spec.parse_spec()`). |
| `territory_config.get_watch_keywords` | Returns watch_keywords for a campaign family. | Real. |
| `reason_codes.get_allowlist` | Returns codes this scout is allowed to emit (from Josh's spec primary_scouts mapping). | Real. |
| `reason_codes.lookup` | Returns full ReasonCodeSpec for a code (description, urgency, nuance). | Real. |
| `contact_db_stub.has_contact` | True/False whether we have a contact at this district. | Stub (returns True always for v1; flag for replacement when SF integration ships). |

### Fetches (network — no API key required)

| Tool | Description | Status |
|---|---|---|
| `news_api.search` | Google News RSS query for district + keywords. | Real (new — uses `xml.etree.ElementTree` per state_doe pattern). |
| `state_doe.fetch` | State DoE RSS feed for a state. | Real (reuse `artemis/scouts/state_doe/sources.py`). |
| `pdf_extractor.extract` | Download + extract text from a PDF URL. | Real (reuse `artemis/scouts/_pdf.py`). |

### Fetches (network — API key required, stub if missing)

| Tool | Description | Status |
|---|---|---|
| `board_minutes.fetch` | BoardDocs scrape for a district. | Real (reuse `artemis/scouts/board_minutes/client.py`). Returns empty list if district not configured. |
| `legiscan.search` / `legiscan.get_bill` | Legiscan API. | Stub (returns "LEGISCAN_API_KEY not set" if absent). Marked TODO for real impl. |
| `starbridge.search` / `starbridge.get_document` | Starbridge API. | Stub. |
| `linkedin_scraper.fetch_posts` | LinkedIn public post fetch (Mode B). | Stub (existing scraper has playwright dep; deferred). |
| `grants_gov.search` | Grants.gov API. | Stub. |
| `federal_register.search` | Federal Register API. | Stub. |
| `procurement_portal.fetch` | Statewide procurement portal scrape. | Stub. |

### Stubs return shape

For every stub:
- The tool is registered.
- Calling it returns a string like: `"STUB: <tool_name> not yet implemented. To enable, set <ENV_VAR_NAME> in the Connectors panel."` if a config is missing, or `"STUB: <tool_name> returns no data in v1."` if it's intentional.
- The string is non-empty so the LLM gets a tool_result and can continue.
- Logged at WARNING level so we can see stub-call rates in production.

---

## Error handling

The existing loop at `artemis/agent/loop.py` already catches `Exception` in `_execute_tool` and surfaces it as `ToolResultBlock(is_error=True, content=<exception message>)`. No changes needed there.

**Tool implementations should:**
- Return a string (success or "human-readable error" — the LLM can read the latter and retry).
- Raise an exception only for unexpected bugs. Validation failures and API errors should be returned as strings, not raised.
- Never write to DB without `await ctx.session.flush()`. Transaction boundary is owned by the caller of `run_agent`.

---

## Cost accounting

Each `run_turn` call already aggregates token usage via `Usage.add()`. The total reaches `run_agent`'s `agent_run` row via `result.usage.input_tokens` and `result.usage.output_tokens`.

**Per-tool cost** (paid APIs like NewsAPI, Legiscan): tools that hit a paid API write the call to `agent_runs.metadata.tool_costs` array. P2 wires a helper:

```python
def record_tool_cost(ctx: ToolContext, tool_name: str, cost_usd: float):
    """Record a paid tool call cost. Aggregated into agent_runs.metadata at end of run."""
    ...
```

Stubs are free. Free APIs (Google News RSS, state DoE RSS) are free.

### Cost cap policy — v1 (decided 2026-05-26)

The existing `DEFAULT_COST_CAP_USD = 1.00` in `scout_runner.py` stays in place as the paranoia armor (catches a misconfigured-prompt-that-loops scenario), but with two changes for v1 testing:

1. **Raise the default to $50** and make it env-configurable:
   ```python
   DEFAULT_COST_CAP_USD = float(os.getenv("ARTEMIS_SCOUT_COST_CAP_USD", "50.0"))
   ```
   At haiku rates, $50 buys ~200M input tokens — effectively unlimited for any realistic run. The cap will not fire in legitimate operation. If we ever observe runaway, dial it down via env var without a code change.

2. **No new per-call cost cap.** The existing per-run cap is the only enforcement layer for v1.

3. **Cost observability** is required. Every scout run logs a one-line cost summary at INFO level:
   ```python
   logger.info(
       "Scout %s completed: items=%d signals_emitted=%d signals_rejected=%d cost_usd=$%.4f tokens=in:%d/out:%d duration_ms=%d",
       slug, items_processed, signals_emitted, signals_rejected, cost_usd,
       input_tokens, output_tokens, duration_ms,
   )
   ```
   The `agent_runs.cost_input_tokens` / `cost_output_tokens` columns already capture this; the log line just makes it grep-able from server logs without a DB query.

4. **Cost visibility surface** (separate follow-up stream, post Phase 1): The Pipeline Run History UI + Agents tab Run History show cost per agent_run today. Jon flagged he wants a clearer view — a per-run cost breakdown + a rolling-N-days cost-per-scout aggregate so he can dial the cap intelligently when v1 matures. This becomes stream **C-cost-dashboard** (see STREAMS doc).

**Why this answer:** Jon's hesitation on a tight cap was the right instinct — "let it work before you kneecap it." The elegant move is keeping the safety net (so we never burn budget on a runaway scenario), making it loose enough to never fire in normal operation, env-configurable so it adapts without code, and adding observability so we have empirical data when we decide to tune.

---

## Permissions / safety

**Reason-code allowlist:** the `signal_queue.write` tool enforces that `arguments["reasonCodes"]` is a subset of `reason_codes.get_allowlist()`. If the LLM tries to emit a code outside the scout's allowed list, the tool returns `"VALIDATION_ERROR: reason code X not in this scout's allowlist [...]"`. The LLM can retry.

**Anti-spoof:** `discoveredBy` and `scout_type` are unconditionally overridden to the running agent's slug, regardless of what the LLM put in the payload. Already done by `scout_intake.normalize_intake_payload`.

**Write permission:** tools that write to DB check `ctx.agent_id` against an allowlist:
- `signal_queue.write`: only `marketing.scout.*` agents.
- `campaign_brief.write`: only `marketing.content.brief_assembler`.
- `unresolved_signals.write`: any marketing agent.

If a non-allowed agent calls the write tool, return `"PERMISSION_DENIED: agent <id> cannot call <tool>"`. Easier to fix at the tool layer than in 16 separate agent prompts.

**Network egress:** v1 makes no attempt to sandbox tool HTTP calls. Tools that hit external APIs are expected to use `artemis/scouts/_http.py` which has timeout + UA defaults. Future: a network-policy layer.

---

## MCP graduation path (not v1, but design for it)

The `ToolFactory` protocol is in-process. If we later add MCP servers (per the `mcp` dep in pyproject.toml), the factory just returns a `(Tool, ToolImpl)` pair where `ToolImpl` proxies to the MCP client. The `_TOOL_FACTORIES` dict gains entries from both in-process modules AND a startup MCP-discovery pass. No agent-side changes.

For v1: all tools are in-process. The `_TOOL_FACTORIES` dict is populated at import time only. MCP integration is a Phase 3+ concern.

---

## Testing strategy

**P2 (bridge tests):**
- Unit: register a fake tool, agent.tools = ["fake.tool"], run_agent invokes it. Assert tool was called with expected args.
- Unit: agent.tools includes an unknown tool name. Run completes; warning logged; LLM saw a valid (smaller) tool list.
- Unit: agent.tools = []. tools=None passed to run_turn. Backward-compat smoke.
- Integration: real agent (e.g. `marketing.scout.regional_news`), real registry with stub tools, pipeline executor runs the agent node, agent_runs row reflects successful tool calls.

**P3 (per-tool tests):**
- Each tool gets a test file with ~30-50 LOC.
- DB-writing tools: test that the row lands, anti-spoof works, validation rejects bad input.
- Network-fetching tools: use `httpx.MockTransport` with fixture XML/JSON.
- Stub tools: assert the stub string is returned, warning is logged.

**Acceptance smoke (post-P2+P3 merge):** click Run on Marketing Pipeline. Assert at least one scout's agent_run shows `tool_calls > 0`. Assert at least one signal in `signal_queue` with non-null `provenance.agent_run_id`. Lead does this smoke.

---

## File ownership summary

**P2 owns:**
- NEW: `artemis/tools/__init__.py`
- NEW: `artemis/tools/context.py`
- NEW: `artemis/tools/registry.py`
- EDIT: `artemis/builders/executor.py` (replace `tools=None` + warning block with assembly, ~50 LOC delta)
- NEW: `artemis/builders/tests/test_tool_bridge.py`

**P3 owns:**
- NEW: each `artemis/tools/<tool_name>.py` (one file per tool group)
- NEW: each `artemis/tools/tests/test_<tool_name>.py`
- May READ but not edit: `artemis/scouts/*/client.py`, `artemis/scouts/_pdf.py`, `artemis/scouts/_http.py`, `artemis/marketing/scout_intake.py`, `artemis/marketing/josh_spec.py`

**P2 and P3 do NOT touch:**
- Anything outside `artemis/tools/` and the one `executor.py` line in P2.
- Agent blueprint markdown (P1/P4 streams).
- The reason-codes seed (F1 stream).
- The runtime injection in `run_agent` (F2 stream).

---

## Design questions — RESOLVED 2026-05-26

All five signed off by Jon. Decisions locked:

1. **Stub strategy:** ✅ Stub-with-placeholder string + WARNING log. LLM gets the capability advertised, can decide whether to work around the stub or give up.

2. **Reason-code allowlist enforcement:** ✅ Both layers. System prompt declares the allowlist (F2 stream). Tool enforces it on every write (P2 stream). Belt-and-suspenders.

3. **`memory_layer.upsert_last_seen` stub:** ✅ Ship the stub. Returns "ok-stub" silently. Qualifier degrades gracefully. Replaced when Memory-M2 lands.

4. **Cost cap:** ✅ See "Cost cap policy — v1" section above. Existing per-run cap stays, default raised to $50, env-configurable, no new per-call cap, observability logging added, cost-dashboard UI stream queued as follow-up.

5. **Streaming/parallel tool calls:** ✅ Synchronous for v1. Streaming + parallel is a v2 concern.

P2 and P3 are GO once Phase 1 (F1/F2/F3) lands.

---

## Why this design vs alternatives

**Why not MCP-only?** Overkill for in-repo tools that share the same DB session. MCP forces tool boundaries we don't need yet. We keep MCP as graduation path.

**Why not regex-on-JSON output (no tool_use protocol)?** Couples scout output to a single response. Scouts that need to look up "what reason codes am I allowed to emit?" before writing can't do that in one shot. Tool-use is iterative; JSON-output isn't.

**Why a global factory registry vs per-module registration in `run_agent`?** Factories register via import-time side effects (one `register_tool(...)` line at the bottom of each file). Means `agent.tools = ["news_api.search"]` "just works" without `run_agent` knowing the universe of tools. Decoupled.

**Why not let agents declare custom tools per agent?** That's the Builder's job long-term. For v1 the universe of tools is fixed in code; agents pick from that universe. Adding a new tool requires a new file in `artemis/tools/` and shipping the deploy. Acceptable for v1.

---

**End of design brief. Awaiting Jon's sign-off / comments on the 5 questions.**
