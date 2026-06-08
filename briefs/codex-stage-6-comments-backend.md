# Codex brief — Composer Stage 6 BACKEND: draft comments (identity-aware, lossless)

**Agent:** Codex. **Branch:** `worker/comments-backend` off `main`. **Own git worktree, cwd inside. Own test
DB** (`artemis_test_comments`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`. **Runs in
PARALLEL with terminal's composer-FE work — touch only NEW backend files + a new router; do NOT touch
`public/js/features/composer-v5.js`/`.css` or `writing_studio.py`.** Mirrors the merged Claims Register /
Templates slices — build it the same way.

## The point
The composer's floating margin comments (Stage 6 UI, terminal builds next) need a backend: store comments
anchored to a span of a draft, authored by the logged-in user (Track A identity is live), with replies,
@mentions, and resolve. **Data + API only.** The FE is separate.

## Existing surface to build on (don't fork)
- Identity (merged): `artemis/identity/` — `get_current_user` dependency returns the verified current user
  (id, email, name). Use it for `author_user_id`. Users table = `users`.
- Draft = `CampaignDeliverable` (`artemis/marketing/models.py`). Comments attach to a draft by `draft_id`.
- Pattern to mirror: `artemis/marketing/routes/claims.py` + `templates.py` (model/repo/schemas/router/
  migration), and how routers register in `artemis/main.py`.

## 1. Model + migration (additive, lossless — new revision off current head 0074)
`Comment` (table `comments`): `id`; `draft_id` FK→campaign_deliverables; `author_user_id` FK→users;
`parent_id` FK→comments.id nullable (replies/threads); `body` text NOT NULL; `anchor_start` int nullable,
`anchor_end` int nullable, `anchored_text` text nullable (the quoted span — lets the FE re-anchor if offsets
drift); `status` text NOT NULL default `'open'` (allowed `open`·`resolved`); `mentions` jsonb NOT NULL
default `'[]'` (list of mentioned user emails); `created_at`, `updated_at`, `resolved_at` (nullable),
`resolved_by_user_id` FK→users nullable. CHECK on status. Index on (draft_id, status). Migration chains off
**0074**. downgrade drops the table. **No deletes** (resolve = status; never DELETE a comment).

## 2. Repo + 3. API (new router `artemis/marketing/routes/comments.py`, register in main.py)
- `list_comments(session, draft_id)` → top-level comments + their replies (or flat with parent_id; FE
  threads them), each with author {id,email,name}. `create_comment`, `resolve_comment`, `reopen_comment`,
  `update_comment` (edit own body, optional).
- Endpoints (under `/api/writing-studio/drafts/{draft_id}/comments` or `/api/writing-studio/comments`):
  - `GET …/drafts/{draft_id}/comments` → the draft's comments (with authors + replies).
  - `POST …/drafts/{draft_id}/comments` body `{body, anchorStart?, anchorEnd?, anchoredText?, parentId?,
    mentions?}` → creates; **author = `get_current_user`** (NOT from the body). Returns the created comment.
  - `POST …/comments/{id}/resolve` → status resolved + resolved_by/at = current user/now.
  - `POST …/comments/{id}/reopen` → status open.
  - (optional) `PATCH …/comments/{id}` → edit body (author only).
- Mentions: store the `mentions` list (emails). Validating/notifying is OUT (see below) — just persist them.

## Acceptance (verify the EFFECT — mock/dev current-user; CF disabled in tests = dev shim)
- Migration up/down round-trips off 0074.
- `POST` a comment on a draft → author is the current user, anchor + body + mentions stored; `GET` returns it
  with author info. Paste it.
- `POST` a reply (parentId) → nested/linked under the parent.
- `POST …/resolve` → status `resolved` + resolved_by/at set; comment STILL EXISTS (lossless); `reopen` →
  `open`.
- Unit/integration tests (repo lifecycle + endpoints + author-is-current-user + lossless resolve).
  `./scripts/check.sh` clean (note PRE-EXISTING failures separately).

## OUT OF SCOPE
The comments UI (terminal, Stage 6 FE — floating margin, @mention picker, ping); actual notification/ping
DELIVERY (store mentions now; in-app/push delivery is a thin follow-on); presence/soft-lock; touching the
composer FE or writing_studio.py.

## Constraints
Lossless (no deletes; resolve=status). Author always = verified current user (never trust a client-supplied
author). Additive migration off head **0074**. New router + main.py registration only. Mirror claims/
templates. Org dep rule. Isolated worktree + own test DB. **Do NOT merge** — report branch + SHA + worktree +
the create→reply→resolve→reopen proof + author-is-current-user proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
