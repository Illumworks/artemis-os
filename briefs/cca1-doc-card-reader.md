# CCA1 — Crisis-comms doc card reader

**Paste-into:** Codex / Worker (`isolation: "worktree"`)
**Recommended Codex model / effort:** `gpt-5.4-mini` / medium

Design doc: `docs/crisis-content-approval-pipeline.md`. Read it first — it records
live-verified findings that this brief depends on and that you must not re-litigate.

## What you are building

A read-only module that fetches a Google Doc through the **HTML export endpoint** and
parses "review cards" out of it. No Slack, no database writes to the doc, no
notifications — those are later slices. This slice ends at: given the doc, return a
list of `ReviewCard` records plus a stable identity for each.

Scope: `artemis/crisis_content/` (new package) + tests. Target ≤450 LOC of
implementation.

## Why the export endpoint and not the Docs API

The statuses in this doc are Google Docs **dropdown chips**. The Docs API returns
them as `{"startIndex": 196, "endIndex": 197}` — a range with **no content, no type,
no value**. This is verified, not assumed. Chip values are only recoverable from the
export rendering. Do not attempt to read them via `documents.get`; you will waste the
run.

## The fetch

```
GET https://docs.google.com/document/d/{document_id}/export?format=html
Authorization: Bearer <access_token>
```

- Use the existing personal-credential + refresh helpers. Mirror the token handling
  in `artemis/routes/google_docs.py` (`_valid_access_token`) rather than reinventing
  it; the credential you want is `purpose="personal"`.
- `follow_redirects=True` is required.
- A 200 whose body is a Google **sign-in page** is a failure, not a document. Detect
  it (no `<table` present, or a sign-in marker in the first few KB) and raise.
- This endpoint is undocumented and honored by observation. Isolate it behind one
  function so a future swap to "Jen types a plain-text status" touches one place.

## Card signature

A review card is a `<table>` whose text content contains **both** `Platform:` and
`Copy review`. Verified against the live doc: exactly 4 of 7 tables match, and the
non-matching 3 are strategy/calendar tables on other tabs.

**Do not resolve tab ids and do not filter by tab name.** The export concatenates all
tabs into one document, and more tabs (organized by month) will be added over time.
The signature is the only tab-agnostic filter, and it is sufficient.

## Real fixture

This is genuine output from the live doc (long `style`/`class` attributes stripped
for readability — your fixture should keep at least one card with them intact, since
the real payload has them). Save fixtures under `tests/fixtures/crisis_content/`.

```html
<table><tr><td colspan="2" rowspan="1"><p><span>August XX, 2026 - </span><span>Welcome Back blog</span></p></td></tr><tr><td colspan="1" rowspan="1"><p><span>Platform: </span><span>LinkedIn</span></p><p><span>Asset for review -</span><span>&nbsp;LINK</span></p><p><span>Draft</span></p><p><span>Copy review</span></p><p><span>Ready</span></p></td><td colspan="1" rowspan="1"><p><span>New from </span><span>Reading Between the Lines</span><span>, by Jaclyn Brown Wright: </span><span>&quot;Welcome Back: ...&quot;</span></p><p><span>Read it: </span><span><a href="https://www.google.com/url?q=https://amiralearning.com/blog/welcome-back-celebrating-the-magic-of-a-new-school-year-and-how-we-protect-it&amp;sa=D&amp;source=editors&amp;ust=1786485136252472&amp;usg=AOvVaw020QK_7h9hcOFNDuThnD6t">https://amiralearning.com/blog/welcome-back...</a></span></p></td></tr></table>
```

Five things in that fixture that will bite you if you skip them:

1. **The header cell is `colspan="2"`** — row 0 has one cell, row 1 has two.
2. **Text is split across many `<span>`s.** `Platform: ` and `LinkedIn` are separate
   spans. Concatenate all spans within a `<p>`; never rely on span boundaries.
3. **`<p>` boundaries are the line structure** and are load-bearing — they are how
   the chips on their own lines are distinguished. Do not flatten the cell to a
   single string before parsing; you will get
   `Platform: LinkedInAsset for review - LINKDraftCopy reviewReady`.
4. **Every `href` is wrapped in Google's redirector:**
   `https://www.google.com/url?q=<REAL_URL>&sa=D&source=editors&ust=...&usg=...`
   Unwrap the `q` parameter to recover the real URL. Note the `ust`/`usg` values
   change between fetches, so never hash raw HTML for change detection — hash
   normalized text only.
5. **`LINK` is a placeholder with no `<a>` here.** The asset URL is legitimately
   absent until Jen attaches one. Absent ≠ error.

Also unescape entities (`&nbsp;`, `&quot;`, `&#39;`) and normalize whitespace.

## Status parsing

Within the status cell (`row1.cell0`), operating on the `<p>`-derived lines:

| Line pattern | Meaning |
|---|---|
| `^Platform:\s*(.+)$` | platform — the chip is **inline** on this line |
| line after `Asset for review...` | asset status — chip on its **own** line |
| line after `Copy review` | copy status — chip on its **own** line |

**Mandatory guard:** if the line following a label is itself a known label
(`Platform:`, `Asset for review`, `Copy review`) or looks like a card header, the chip
is **unset** — return `None`. Never absorb the next label as a status value. An unset
chip is normal; Jen fills these in over time.

### Vocabulary (observed live 2026-08-11 — do not hardcode as closed)

| Chip | Options |
|---|---|
| `Asset for review` | `Draft`, `Ready`, `Approved` |
| `Copy review` | `Draft`, `Ready`, `Approved`, `Published` |
| `Platform` | `TBD`, `Facebook`, `Instagram`, `FB/IG`, `LinkedIn`, `X`, `All`, `Facebook/IG, LinkedIn…`, `Facebook, IG, X`, `FB, LI, & X`, `Facebook/LinkedIn`, … |

Note the two status chips have **different** option sets — `Asset for review` has no
`Published`. The platform list is **user-editable** in Google Docs ("Add / Edit
Options") and contains multi-platform combos that are single opaque values
(`FB, LI, & X`). So:

- Carry every value through as a plain string. Never validate against a closed list
  and never drop an unrecognized value — Jen can add options at any time.
- A `classify_status` helper may label `Draft`/`Ready` as actionable and
  `Approved`/`Published` as **terminal**, for slice B's benefit. Slice B fires only on
  `→ Ready`; terminal states must never trigger a notification. Keep this as
  classification only — the parser still reports the raw string.

## Card identity

```
primary = (normalized_header, platform, ordinal_within_duplicates)
secondary = sha256(normalized_copy_body)
```

Both are required, for reasons that are live realities in this doc, not hypotheticals:

- Cards 0–2 share the **identical** header (`August XX, 2026 - Welcome Back blog`)
  and differ only by platform. Identity must include platform.
- Two cards could share header *and* platform, hence the ordinal.
- Jen writes `August XX` placeholders. When she fills in a real date, the header
  changes and the primary key changes with it — an already-approved card would look
  brand new. The copy-body hash lets a later slice recognize it as the same post.

**Never key off table index.** Inserting a card at the top shifts every index and
would re-ping the entire backlog.

## Deliverables

- `artemis/crisis_content/__init__.py`
- `artemis/crisis_content/export_client.py` — the fetch, isolated, with sign-in-page
  detection and the redirector unwrapper.
- `artemis/crisis_content/parser.py` — pure functions: HTML → `list[ReviewCard]`.
  No network, no DB. This is the module that must be trivially testable.
- `artemis/crisis_content/models.py` — a Pydantic `ReviewCard`:
  `header`, `date_text`, `title`, `platform`, `asset_status`, `copy_status`,
  `asset_url`, `copy_body`, `identity_key`, `copy_hash`.
- Tests in `tests/unit_no_db/test_crisis_content_parser.py`.

Prefer stdlib `html.parser` or an existing repo dependency. **Do not add a new
dependency** for HTML parsing — and per `CLAUDE.md` rule 4, nothing released in the
last 7 days regardless.

## Tests (all required)

Happy path:
- [ ] The 4-card fixture parses to exactly 4 cards, none from the strategy tables.
- [ ] `Platform: LinkedIn` → `platform == "LinkedIn"`; `copy_status == "Ready"`;
      `asset_status == "Draft"`.
- [ ] A card with a real asset hyperlink yields the **unwrapped** URL, not the
      `google.com/url?q=` wrapper.
- [ ] Copy body preserves paragraph breaks and unescapes entities.

Failure and edge modes:
- [ ] A 7-table fixture yields only the 4 signature matches.
- [ ] An unset chip (label immediately followed by another label) → `None`, and the
      following label is **not** consumed as the value.
- [ ] `LINK` with no `<a>` → `asset_url is None`, no exception.
- [ ] Three cards sharing a header differ by `identity_key`.
- [ ] Identity is **stable** when the same doc is parsed twice with different
      `ust`/`usg` values in the hrefs.
- [ ] Identity is **stable** under card reordering (move card 3 above card 0).
- [ ] An unknown status value (e.g. `Needs legal`) is carried through, not dropped.
- [ ] `classify_status` marks `Approved` and `Published` terminal, `Draft`/`Ready`
      actionable, and an unknown value as unknown (not terminal, not actionable).
- [ ] A multi-platform combo (`FB, LI, & X`) survives as one value and is not split
      on the comma or the ampersand.
- [ ] A sign-in-page body raises a typed error rather than parsing to zero cards.
- [ ] **Zero cards parsed raises/flags loudly** — this is the most likely real
      failure (Jen renames a label) and it must be distinguishable from "no work to
      do". Do not return an empty list silently.

## Out of scope — do not build

Slack, Callie, polling loop, transition detection, doc write-back, Drive comments,
email, image handling. Those are slices B and C. Do not widen OAuth scopes (already
done on `main`) and **do not write anything to the target document** — it belongs to
an external vendor.

## Quality acceptance

- [ ] `./scripts/check.sh` passes (ruff + mypy strict + tests).
- [ ] Every test above exists and passes; paste verbatim output in your report.
- [ ] `parser.py` has zero network/DB imports.
- [ ] `git diff --staged` re-read twice before commit (see `CLAUDE.md` commit
      discipline).
- [ ] No new dependencies added.
- [ ] Report states explicitly that nothing was written to the target Google Doc.
