# Memory Keystone

Two-tier, evidence-linked memory store. The foundation for all retrieval in Artemis OS.

## Architecture

```
OBSERVATIONS  (curated — retrieved at prompt time)
     ▲  many-to-many via memory_evidence
DRAWERS  (verbatim — immutable evidence floor)
```

**Drawers** are written once and never changed. They hold raw content with full provenance.

**Observations** are curated summaries. Each links to ≥1 drawer or older observation via `memory_evidence`. Observations may be superseded (replaced by a newer version) but are never deleted.

**Scopes** catalog the memory namespace. Six kinds: `project`, `workspace`, `brand`, `agent`, `skill`, `global`.

## Lossless rule (load-bearing)

Drawers and observations are **never deleted**. Observations leave active retrieval only via `superseded_by`. There is no `delete_drawer` or `delete_observation` in the public API. This is a deliberate constraint — deleting would destroy the evidence chain and break provenance for any downstream observation that referenced the deleted row.

## Public API (`artemis.memory.store`)

All functions accept `session: AsyncSession`. Wrap calls in `async with session.begin():`.

| Function | Description |
|---|---|
| `write_drawer(session, scope, content, source, ...)` | Write a drawer. Embeds on write (best-effort). Idempotent on content hash. |
| `write_observation(session, scope, content, ...)` | Write an observation. Embeds on write (best-effort). Idempotent on content hash. |
| `upsert_embedding(session, target_table, target_id, model_version, vector)` | Insert or update embedding for a drawer or observation. |
| `link_evidence(session, obs_id, source_kind, source_id, ...)` | Link drawer or observation as evidence. Idempotent. |
| `supersede_observation(session, old_id, new_id)` | Mark `old_id` superseded. No-op if already superseded. |
| `get_drawer(session, id)` | Fetch drawer by id; returns `None` if missing. |
| `get_observation(session, id)` | Fetch observation by id; returns `None` if missing. |
| `list_evidence_for_observation(session, obs_id)` | All evidence for an observation, ordered by weight DESC. |

## Retrieval API (`artemis.memory.retrieval`)

```python
from artemis.memory.retrieval import search_observations, RetrievalConfig

results = await search_observations(
    session,
    scope_set=[Scope(scope_kind="workspace", scope_id="default")],
    query="brand voice guidelines",
    limit=10,
    as_of=datetime.now(timezone.utc),   # validity window anchor
    modes=["fts", "semantic", "recency", "score"],  # all by default
)
# returns list[ScoredObservation] sorted by final_score DESC
```

Weights are loaded from `config/memory-retrieval.yaml` (fts: 0.30, semantic: 0.40, recency: 0.15, score: 0.15). Edit the YAML to tune.

## Embedding service (`artemis.memory.embeddings`)

Default provider: `MiniLMProvider` — `all-MiniLM-L6-v2`, 384 dims, lazy-loaded.

```python
from artemis.memory.embeddings import get_default_provider

provider = get_default_provider()
vector = await provider.embed("some text")   # list[float], len 384
```

Set `ARTEMIS_EMBEDDING_PROVIDER=minilm` (default) in `.env`. Only `minilm` is available in V1.

## Backfill CLI

Rows written without an embedding (e.g. when the model was unavailable) are backfilled:

```bash
uv run python -m artemis.memory.backfill
```

Idempotent. Processes drawers and observations in batches of 50.

### Scope kinds

| `scope_kind` | `scope_id` meaning | Example |
|---|---|---|
| `project` | absolute cwd | `/Users/jon/Desktop/artemis-os` |
| `workspace` | host workspace name | `default` |
| `brand` | brand slug | `artemis-marketing` |
| `agent` | agent_id | `bug-hunter` |
| `skill` | skill slug | `resume-session` |
| `global` | always `"global"` | `global` |

### Content hash

`sha256(f"{scope_kind}:{scope_id}:{content}")` — scope-aware, so identical text in different scopes produces different hashes. Deduplication is per-scope.

## Usage example

```python
from sqlalchemy.ext.asyncio import AsyncSession
from artemis.memory import Scope, Source, write_drawer, write_observation, link_evidence

scope = Scope(scope_kind="workspace", scope_id="default")
source = Source(source_kind="document", source_id="brief-2026-05")

async with session.begin():
    drawer = await write_drawer(session, scope, "Jon prefers direct tone.", source)
    obs = await write_observation(session, scope, "Brand voice: direct, confident.")
    await link_evidence(session, obs.id, "drawer", drawer.id, source_quote="Jon prefers direct tone.")
```

## Out of scope (this slice)

- Consolidation, scoring, decay — Slice B3
- Graph entities and relations — Slice B4
- HTTP routes — Phase C

## Running tests

```bash
# Requires Postgres running (docker compose up -d)
uv run pytest artemis/memory/tests/ -v
```
