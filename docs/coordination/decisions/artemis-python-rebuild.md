# DECISION — Artemis Python Rebuild (Clean-Room with Node Reference)

**Decided:** 2026-05-16 by Jon (confirmed conversationally; this doc is the record, not the gate).
**Authors:** Lead Claude Code (Account 1), in conversation with Jon.
**Reverses:** the 2026-05-16 earlier-today direction lock that said "stay on Node + SQLite + sqlite-vec." That entry is preserved in `PROJECT_LOG.md` for history; this doc supersedes it.

---

## TL;DR

We are **rebuilding Artemis from scratch in Python + Postgres + pgvector**, in a new sibling directory, treating the current Node app at `claudeck-artemis/` as a **frozen reference implementation** — i.e. a working spec we read while we build. Nothing in the current Node app is in operational use; no marketing data is at risk; the only data we preserve is OKR Studio rows and Writing Studio rules.

This is not an in-place migration. The two codebases live side-by-side during the build. When the Python build reaches operational parity, the Python build *is* Artemis and the Node one becomes archive.

---

## Why we landed here

### What was actually true in the audit

- The current Node app's marketing surface has been built but **never operationally run**. No signals collected. No campaigns flowed. No agents executed against real sources. Yesterday's "end-to-end smoke test" injected synthetic findings through a harness — not live data.
- The current Node app **is the spec we sculpted into existence**, not the marketing machine in production. That changes its preservation value entirely.
- Six of nine planned scouts are scrape-and-PDF heavy. Python's ecosystem (Playwright, pypdfium2, pytesseract, Scrapy, httpx, APScheduler) is materially more polished for that work than Node's equivalents.
- Server-deploy + multi-user is the near-term destination (Jon's boss; expansion pressure expected at month 2–3 if MVP looks good). Postgres + pgvector is the obvious storage shape for that destination.
- The floating Artemis is conceived as "master of her own app" — one unified universe, not a polyglot one. Hybrid is therefore out.

### The fork we considered and rejected

| Option | Why rejected |
|---|---|
| All Node, keep building on the current app | The audit showed it's not a "running marketing machine" — it's an unrun prototype. Preservation value was overstated. Net-new code over the next 2–6 months is mostly scouts, which favor Python. |
| Hybrid (Node plumbing + Python scouts) | Kills the "Artemis is master of one universe" instinct. Two stacks in production indefinitely. Worst of both worlds. |
| In-place migration (rewrite Node files into Python in the same repo) | Higher risk to load-bearing code (memory keystone) because we'd be remembering design decisions from scratch. Drift between half-migrated states. |
| **Clean-room parallel rebuild (chosen)** | Current code stays running as a reference we can *read* — no design-recall risk on the keystone. Two codebases coexist only during the build window, not forever. End state is one stack. |

### Why "weeks" is plausible

The expensive part of building the current Node app wasn't writing the code — it was figuring out *what* to write. That sculpting work is done. The rebuild reads more like translation-plus-improvement than design-plus-build. With the current Node app sitting open as a reference, we can lift design decisions wholesale and only re-make them where the new stack genuinely changes the shape.

We are not promising a calendar number. We are saying: faster than the original month-long build, because the discovery is done.

---

## What we're keeping vs. reconceiving

### Kept (read from the Node app and translate behavior 1:1)

- The memory keystone *design*: drawers + observations + evidence + scope union + fusion reranker + lossless rule + consolidation + graph layer. Same shape, different storage substrate.
- The marketing OS *contracts*: signal schema, candidate schema, ruleset schema, campaign brief schema, content asset schema, writing-studio handoff contract.
- The qualifier *math*: three-phase deterministic scoring (hard filter → weighted match → territory multiplier). It's already pure; translates directly.
- The agent / workflow / chain / DAG model (per `docs/AGENT-ARCHITECTURE.md`). Same execution semantics.
- The frontend UI shape and CSS. Vanilla web components survive a backend change with minimal churn. The Tab SDK pattern is portable.
- The OKR Studio data and the Writing Studio rules. Migrated as data, not code.

### Reconceived (the rebuild is a chance to fix what isn't quite right)

- Storage substrate: SQLite → Postgres + pgvector. Multi-user / server-deploy ready from day one (`owner_user_id` is a first-class column, not a reserved one).
- Auth: single `ARTEMIS_TOKEN` → real identity model from the start (shared single-account for v1; multi-user activatable without schema migration).
- Scout execution: in-process Node harness → separate Python worker process with APScheduler. Decouples HTTP latency from scout latency.
- Embedding model: MiniLM-L6 → still MiniLM by default for offline / parity, but the embedding layer is built behind a clean interface so OpenAI text-embedding-3 / Voyage / etc. swap is a config change, not a refactor.
- Agent loop: rebuild against Anthropic Python SDK with prompt caching from day one (the current Node loop doesn't cache).
- Test harness: vitest → pytest + httpx async test client. Same coverage discipline (>90% for backend, 100% for keystone-class modules).
- Floating Artemis: rebuilt as a Python agent inside the new app.

### Discarded outright

- The Claudeck DNA. Working directory, branding, file-naming conventions inherited from the original fork. The new repo is Jon's app, not a fork.
- Plugin system (`plugin-loader.js`, `tab-sdk.js`, marketplace) — already deprecated in current CLAUDE.md; not carried over.
- Pre-existing tests that test Claudeck legacy paths (already partly noise — see the 18 we just fixed).

---

## What this commits us to

- **Stack:** Python 3.11+, FastAPI, Postgres 15+ with pgvector, SQLAlchemy 2.x, Pydantic 2.x, httpx, Playwright (Python), pypdfium2 + pytesseract, APScheduler, Anthropic Python SDK, pytest.
- **Repo layout:** new sibling directory at `/Users/artemis/Desktop/Artemis/<name-tbd>/`. Name is a Creative Director call — Lead will propose 2–3 in the phased plan; Jon picks.
- **Current Node repo:** frozen as reference. No new feature work lands there. Bugfix-only, and only if blocking the rebuild.
- **Data preserved:** OKR Studio rows + Writing Studio rules. Nothing else.

## What this leaves open

- **Repo name** — Creative Director call, surface in the phased plan.
- **Frontend framework** — default is keep the vanilla web components; revisit only if the rebuild reveals a real reason to upgrade (e.g. the agent builder UI proves too complex for vanilla).
- **Hosting model when we ship** — Fly.io / Render / a small VPS / Docker on a Mac mini. Not now, but flagged.
- **Scheduled scout execution at scale** — APScheduler is fine for v1; if volume forces it, swap to arq or Celery later.

## What reversing this would cost

If we get a month into the rebuild and decide to stop:

- The current Node app is untouched and still runs. We lose the rebuild work (3-4 weeks at expected pace), but nothing operational breaks.
- Less reversible after we migrate OKR + Writing Studio data into Postgres — that's the point of no easy return. Until then, the rebuild is fully discardable.

---

## Operational rules during the build

1. **No new feature work in the Node app.** It's a reference. Reading it is fine; editing it is not, unless a discovered bug blocks the rebuild (e.g. the reference is wrong about its own behavior).
2. **The rebuild lives in a new directory.** Not as a branch in the current repo. Clean root.
3. **The Lead owns architecture and merges to local `main` of the new repo.** The Worker owns slice execution.
4. **Per Jon's autonomy preference**, we do not check back on tactical choices. We check back on Creative Director moments and cutover.
5. **Local-only git.** No push, no remote, no PR ceremony.

---

## How this gets executed

See `decisions/rebuild-phased-plan.md` for the phased build plan.
