# J10 — Trailing-slash compatibility sweep

**Owner:** Worker or Codex — purely mechanical, no architectural decisions.
**Scope:** ~50 LOC. ~2 hours.
**Depends on:** Nothing. This is a leaf.
**Unblocks:** 8 list endpoints across Operations and Marketing slabs (Agents, Skills, Workflows, Chains, DAGs, Approvals, Signals, Content Assets), each of which is currently 404'ing on first load because the copied-from-Node frontend calls them without a trailing slash.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

The Codex audits (Operations + Marketing, both at `audits/`) independently surfaced the same root cause: FastAPI routers register their list endpoints as `@router.get("/")` under a prefix, so the canonical URL is `/api/agents/` (trailing slash). But the frontend was copied from the Node app, where Express handles `/foo` and `/foo/` identically — so the frontend calls `/api/agents` (no slash) and gets a 404.

Curl-verifiable from the running app:

```text
GET /api/agents              → 404
GET /api/agents/             → 200  ✅
GET /api/skills              → 404
GET /api/skills/             → 200  ✅
GET /api/workflows           → 404
GET /api/workflows/          → 200  ✅
GET /api/agent-chains        → 404
GET /api/agent-chains/       → 200  ✅
GET /api/agent-dags          → 404
GET /api/agent-dags/         → 200  ✅
GET /api/approvals?…         → 404
GET /api/approvals/?…        → 200  ✅
GET /api/signal-queue?…      → 404
GET /api/signal-queue/?…     → 200  ✅
GET /api/content-assets?…   → 404
GET /api/content-assets/?…  → 200  ✅
```

Across both slabs this is the single highest-leverage fix — more parity restored per LOC than any feature port would deliver. **It blocks the Operations and Marketing slab walkthroughs.** Ship it before J11.

## Two implementation approaches

### Option A — ASGI middleware (recommended; try first)

Add a small middleware to `artemis/main.py` that, on a 404 response, transparently retries the request with a trailing slash appended (only for `/api/` paths, only when the original path doesn't already end in `/`).

```python
# Sketch — adapt to existing style in main.py
@app.middleware("http")
async def _no_trailing_slash_compat(request: Request, call_next):
    response = await call_next(request)
    if (
        response.status_code == 404
        and request.url.path.startswith("/api/")
        and not request.url.path.endswith("/")
    ):
        # Retry the request with trailing slash by re-dispatching through the app.
        # If FastAPI's redirect_slashes=True already does this, this branch is unreachable
        # — investigate why it's not currently active.
        ...
    return response
```

**Investigate first:** FastAPI's `redirect_slashes=True` is the default, which *should* be sending a 307 redirect to the trailing-slash URL. Find out why it isn't:

- Check `artemis/main.py` for `FastAPI(redirect_slashes=False)` or any equivalent override
- Run `curl -v http://localhost:8000/api/agents` and look for the `307` response — if FastAPI is sending a 307 and the frontend is just not following it, the fix is different (frontend `fetch()` needs `redirect: "follow"`, which is the default — so this should already work)
- The current 404 strongly suggests `redirect_slashes` is disabled somewhere; finding and re-enabling it may be the actual one-line fix

**If `redirect_slashes` is the answer:** enable it, verify, done. The middleware sketch above is the fallback.

### Option B — Per-router alias (fallback)

If middleware doesn't work cleanly (interferes with `StaticFiles` mount, breaks WebSocket upgrades, etc.), fall back to registering each affected list endpoint at both `""` and `"/"`:

```python
@router.get("")           # ← new alias
@router.get("/")
async def list_agents(...): ...
```

Apply to: `agents`, `skills`, `workflows`, `agent_chains`, `agent_dags` (in `artemis/routes/builders/`), and `approvals`, `signal_queue`, `content_assets` (in `artemis/marketing/routes/`).

Boilerplate but explicit; easier to grep.

## Acceptance — what done looks like

- [ ] All 8 endpoints above return **the same status code** for both URLs (slash and no-slash). 200 for an authenticated request with no required params; 422 if missing params; never 404 because of slash semantics.
- [ ] Verify via a curl loop pasted **verbatim** in the report:

```bash
for path in /api/agents /api/skills /api/workflows /api/agent-chains /api/agent-dags \
            "/api/approvals?status=pending" "/api/signal-queue?status=in_inbox" \
            "/api/content-assets?status=draft"; do
  echo "=== $path ==="
  curl -s -o /dev/null -w "no-slash:  %{http_code}\n" "http://localhost:8000$path"
  # add trailing slash before any ? query
  slash_path="${path%%\?*}/$(echo $path | grep -o '?.*' || echo '')"
  curl -s -o /dev/null -w "trailing:  %{http_code}\n" "http://localhost:8000$slash_path"
done
```

- [ ] No regressions on any currently-working route. Run `curl -sS http://localhost:8000/health` and at least one trailing-slash endpoint that already worked (e.g. `/api/dev-projects/projects`) and confirm 200.
- [ ] If middleware approach: no impact on `StaticFiles` mount (the SPA at `/` still loads correctly — open the page in a browser).
- [ ] If middleware approach: no impact on WebSocket routes (`/api/ws/*` — confirm a Floating Artemis chat session still connects).
- [ ] Open the Operations page (`/agents`, `/skills`, `/workflows`) in the browser. Each should at minimum stop spinning forever and render either content or a recognizable empty state. (They may still be missing subresources — J11 covers that — but the no-slash 404 should no longer be the blocker.)

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim** in your report (the curl loop above + screenshots/textual confirmation of the browser test)
- [ ] If you chose middleware: explain why in 2 sentences. If you chose per-router alias: same.
- [ ] If you found `redirect_slashes` was disabled and just re-enabled it: explain the **why** behind whoever disabled it (`git blame` the relevant line) and confirm re-enabling doesn't break anything they were avoiding.
- [ ] `ruff check` + `mypy` clean
- [ ] Tests: add a single `test_trailing_slash_compat.py` with one parameterized test that asserts each of the 8 endpoints returns the same status code for both forms. Should fit in ~30 lines.

## Out of scope

- Subresource routes that are entirely missing (e.g. `/api/agents/:id/instruction`, `/api/agents/runs/active`) — those are J11.
- Contract drift (e.g. `/api/campaign-deliverables?campaignId=X` vs `/api/campaign-deliverables/X`) — different problem, separate brief.
- Anything in Memory HTTP — separate brief, Lead-led.
- Adding the same compatibility for routes that already require trailing slashes by design (e.g. directory-like resource paths). If unsure, leave alone.

## Where to start

1. Read this brief twice
2. `curl -v http://localhost:8000/api/agents` — look at the response headers. Is there a `307`? Where does it redirect to?
3. `grep -rn "redirect_slashes" artemis/ | head` — is it explicitly disabled anywhere?
4. If `redirect_slashes` is the answer, fix is one line. If not, go middleware (option A) or per-router (option B).
5. Run the curl smoke loop locally before reporting done.

## After you ship

Note in your report: how long did the investigation take vs the actual fix? This will inform future "is this a one-liner or a real port?" estimates.
