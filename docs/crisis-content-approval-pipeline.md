# Crisis-comms content approval pipeline

**Status:** designed, not built. Findings below are verified live against the real doc.
**Date:** 2026-08-11
**Owner:** Jon (Lead)

## The problem

A crisis-management vendor (Jen, `jen@digigeeks.com`, DigiGeeks) drafts social posts
into a Google Doc she owns. Jon has edit access but **does not own the file**. The
team is too busy to poll the doc. When Jen flips a status dropdown on a post, the
right approver should get a Slack ping with the copy inline and an Approve button,
and Jen should be notified of the decision without anyone writing her an email by
hand.

Target doc: `1IcXikVORzIfzKxsU57zoKTf2jr5rqmIkNHmHP0EAPUw`
("Draft Amira Social Content Plan"). Review cards currently live on the
"Content To Review" tab (`t.cv99t981gtu6`), but **more tabs will be added, organized
by month** — the design must not depend on tab names or ids.

## Verified findings

These were established by live probes against the real doc, not from documentation.

### 1. The Docs API cannot read dropdown chips. At all.

Jen's statuses are Google Docs **dropdown chips** (`Instagram`, `Draft`, `Ready`).
The Docs API returns them as an element with a range and *no content whatsoever*:

```json
{ "startIndex": 196, "endIndex": 197 }
```

No type, no value, no text. The chip occupies one character and is completely
opaque. The paragraph structure of a status cell comes back as:

```
'Platform:'                 ← the [Instagram] chip is simply absent
'Asset for review - LINK'
''                          ← this empty paragraph IS the [Draft] chip
'Copy review'
''                          ← this empty paragraph IS the [Draft] chip
```

Consequences: no polling interval, no scope, and no request shape fixes this.
Anything that reads chip values through `documents.get` is dead on arrival.
Detecting *that* a chip exists is possible; reading its value is not.

### 2. The export endpoint renders chip values as plain text. This is the way.

```
GET https://docs.google.com/document/d/{DOC_ID}/export?format=html
Authorization: Bearer <token>
```

Returns 200 with our **existing** credential. Chip values render as text:

```
August XX, 2026 - Welcome Back blog
	Platform: LinkedIn
	Asset for review - LINK
	Draft            ← asset status chip
	Copy review
	Ready            ← copy status chip
	New from Reading Between the Lines, by Jaclyn Brown Wright: "Welcome Back: ...
```

`format=html` is preferred over `format=txt`: it preserves `<table>`/`<tr>`/`<td>`
structure **and** `href`s, so we get the chip values without giving up structure.

**This endpoint is not part of the documented Drive/Docs API.** It is the URL behind
File → Download and it honors an OAuth bearer token today. Treat it as a supported-
by-observation dependency: if it changes shape or starts refusing tokens, the
fallback is asking Jen to type a status word as plain text instead of using a chip.
Everything downstream of the parser is unaffected by that swap.

### 3. All tabs arrive in one fetch, so monthly tabs are free.

A single export contains every tab concatenated in order. Verified tab order in the
target doc: `Strategy Plan`, `Content Plan Draft`, `Repeatable Framework`,
`Content To Review`.

We therefore **never resolve a tab id**. A review card is identified by its shape:

> A card is a `<table>` whose text contains **both** `Platform:` and `Copy review`.

Verified against all 7 tables in the doc: exactly the 4 review cards match; the
strategy/calendar/framework tables on other tabs do not. New monthly tabs simply
contribute more matching tables.

### 4a. Doc sharing state (read 2026-08-11) — two things that matter

```
writer   anyone   (link-shareable)          ← anyone with the URL can EDIT
owner    user     jen@justrightstrategy.com
writer   user     jon.fila@amiralearning.com
writer   user     jen@digigeeks.com
```

- **Jen has two addresses on the doc.** The owner is `jen@justrightstrategy.com`;
  `jen@digigeeks.com` (the address Jon supplied) is a separate writer entry. The
  `@mention` should target **both** — cheap, and it removes any guess about which
  inbox she watches.
- **The doc is link-editable by anyone**, and Angela / Hannah / Jaclyn are **not**
  explicit collaborators — they reach it through that open link (Hannah has already
  commented via it). So tightening the sharing, which is worth doing for unapproved
  crisis-comms copy, **will cut off the three copy approvers** unless they are added
  explicitly first. Sequence matters; this is Jon's and Jen's call, not ours.

### 4. The Drive API cannot see this file, which blocks comments.

`GET /drive/v3/files/{id}` and `/comments` both return **404 File not found** under
our `drive.file` scope — that scope only covers files our app created. A Docs
`@mention` comment requires `comments.create`, which requires full
`https://www.googleapis.com/auth/drive`.

Jon accepted this trade. Full `drive` was added to `GOOGLE_PERSONAL_SCOPES` in
`artemis/google_integration.py` and **granted 2026-08-11 21:04 UTC** (verified: 9
scopes on the credential, Drive file metadata 200, `canComment: true`,
`canEdit: true`, comments readable). This also resolves the unaddressed 403 note in
`artemis/enablement/sync.py:386`, which hit the identical wall from the other
direction — that code path can now use the personal credential against
externally-owned files.

### 5. Chip values cannot be written back.

The Docs API can insert text but **cannot set a chip value** — the same opacity that
blocks reading blocks writing. When an approver clicks Approve, Jen's "Copy review"
chip will still read `Ready` until a human changes it. Our write-back is a text line
in the card, plus a real notification (comment `@mention` + email).

### 6. Latent bug found in passing

`import_google_document` (`artemis/google_docs/client.py:241`) omits
`includeTabsContent=true`, so **any tabbed doc imported into Writing Studio silently
returns only the first tab**. Verified: without the param the response carries tab 1
("Strategy Plan"); with it, there is no top-level `body` at all and content moves to
`tabs[].documentTab.body`. Tracked separately — not in scope here.

## Design

### Read path

1. Every ~2 minutes, fetch `export?format=html` with Jon's **personal** credential
   (`cred.purpose="personal"`, `jon.fila@amiralearning.com` — the account with
   access; `amiracentral@` is unverified against this doc).
2. Extract all `<table>` blocks; keep those matching the card signature.
3. Per card table:
   - `row0.cell0` → header line, e.g. `August XX, 2026 - Welcome Back blog`
   - `row1.cell0` → status block, split on `<p>` boundaries into lines
   - `row1.cell1` → the post copy, plus any `href`s
4. From the status block lines:
   - `^Platform:\s*(.+)$` → platform (the chip is **inline** on this line)
   - the line after `Asset for review...` → asset status (chip on its **own** line)
   - the line after `Copy review` → copy status (chip on its **own** line)

**Guard:** if the line following a label is itself a known label or a card header,
the chip is unset — record `None`, do not absorb the next label as a value. Jen's
`LINK` text is currently a placeholder with no `href`; the asset URL is absent until
she attaches one, and that must not be treated as an error.

### Card identity (dedup)

Cards 0–2 in the live doc share the identical header
(`August XX, 2026 - Welcome Back blog`) and differ **only by platform**, so:

- **Primary key:** `(normalized_header, platform, ordinal_within_duplicates)`
- **Secondary guard:** hash of the copy body

The secondary guard exists because Jen uses `August XX` placeholders. When she fills
in a real date the header changes, the primary key changes, and an already-approved
card would look brand new. If a new key appears whose copy-body hash matches a card
already actioned, treat it as the same post and stay quiet.

Never key off table index — inserting a card at the top would shift every index and
re-ping the whole backlog.

### Routing

Jon does **not** approve copy. Two independent routes:

| Transition | Notify | Resolution |
|---|---|---|
| `Asset for review` → `Ready` | Jon | Jon approves. Skip entirely if the card has no asset. |
| `Copy review` → `Ready` | Angela, Hannah, Jaclyn | **Any one** of them approves; Callie tells the other two it is handled. |

Copy approvers: `angela.miata@amiralearning.com`, `hannah.slater@amiralearning.com`,
`jaclyn.wright@amiralearning.com`. Slack channel: `C0BM9TL63TL` (Callie already a
member).

Fire on the **transition**, once per `(card_identity, route, status_value)`. Never
re-fire for a status we have already notified on.

### Status vocabulary (observed 2026-08-11)

The two status dropdowns have **different** option sets, and the platform dropdown is
long, includes multi-platform combos, and is **user-editable** ("Add / Edit Options"):

| Chip | Options |
|---|---|
| `Asset for review` | `Draft`, `Ready`, `Approved` |
| `Copy review` | `Draft`, `Ready`, `Approved`, `Published` |
| `Platform` | `TBD`, `Facebook`, `Instagram`, `FB/IG`, `LinkedIn`, `X`, `All`, `Facebook/IG, LinkedIn…`, `Facebook, IG, X`, `FB, LI, & X`, `Facebook/LinkedIn`, … |

Three consequences:

- **Fire only on `→ Ready`.** `Approved` and `Published` are terminal. Never ping on
  them, and never re-ping when a card moves `Ready → Approved → Published`.
- **`Approved` is a real chip option, so a human *can* set it** — we cannot (finding
  5). This makes the mismatch visible: our text line says approved while the chip may
  still read `Ready`. Word the write-back so it reads as a record of the decision and
  makes clear the chip is Jen's to flip. Do not let the two sources of truth diverge
  silently.
- **Never validate platform against a closed list.** Jen can add options at will, and
  combos like `FB, LI, & X` are single opaque values. Carry the string through.

### Notify path (on approve)

1. Write a text line into the card in the doc:
   `✅ Approved — <approver>, <date time>`
2. Create a Drive comment on the doc `@mention`ing `jen@digigeeks.com` so she gets a
   real Google notification (requires the new `drive` scope).
3. Email Jen via `gmail.send` as a belt-and-braces backup. Already in scope.

Order matters: the doc write is the riskiest step, so it ships last (see slices).

### Failure modes — all must be loud

This repo has been bitten repeatedly by silent failures (see the six-store liveness
trap in `CLAUDE.md`). Every one of these alerts Jon rather than going quiet:

- **Zero cards parsed** — labels renamed, export shape changed, or the endpoint
  broke. This is the single most likely failure and it is indistinguishable from
  "no work to do" unless we alert explicitly.
- Export returns non-200, or an HTML body that is actually a sign-in page.
- Token refresh fails → reuse the existing owner-alert path from the GCal token fix.
- A status value outside the known vocabulary → log and skip, never guess.

## Slices

- **A — reader + parser.** Fetch, card-signature extraction, status parsing,
  identity, snapshot persistence. Pure and unit-testable against fixtures. No Slack,
  no writes. Brief: `briefs/cca1-doc-card-reader.md`.
- **B — watcher + routing.** Poll loop, transition detection, dedup, Callie's Slack
  card with copy inline and Approve / Request-changes, both routes, any-one quorum.
- **C — write-back + notify.** Doc text line, Drive `@mention` comment, Gmail
  fallback, and the authorization widening below.
- **D — harvest approved copy into Writing Studio.** See below. Cheap only if built
  alongside C; expensive as a later backfill.

Ship A→B and run it on real posts before starting C. Notify-only is genuinely useful
on its own; writing into a doc we do not own is the one step that can damage someone
else's work.

## Slice D — harvest approved copy into Writing Studio

**Why it belongs here and not in a later project:** at the moment of approval the
pipeline already holds the final copy, the platform, the topic, the approver, and the
asset link. Capturing it costs one insert. Reconstructing the same corpus after the
vendor engagement ends means re-reading and re-classifying every post, which is a
genuine cost in calls and tokens for information we already had in hand.

### The existing substrate — do not build a parallel store

State read 2026-08-11:

| Table | Rows | What it actually holds |
|---|---|---|
| `writing_examples` | 7 | **All** `example_type` `reference`/`template` — glossary, proof pack, claims register, message compass. **`channel` is NULL on every row.** |
| `writing_training_candidates` | 41 | 38 `rule`/`proposed` (Angela's review queue), 3 decided |
| `writing_sources` | 9 | Reference docs only (`master_prompt`, `glossary`, `claims_register`, …) |
| `writing_profiles` | 1 | `Amira Marketing Voice` — "Shared writing profile for Angela and the marketing team" |
| `writing_rules` | 3 | Standing guidance |
| `floating_artemis_voice_corpus` | 0 | Unused |

The gap is precise: **Writing Studio has reference material and rules but zero
examples of finished, approved content.** `writing_examples` already carries
`example_type`, `asset_type`, `channel`, and `body` — it is shaped for exactly this
and the `channel` column has simply never been populated. Target that table; do not
introduce a new one.

### Capture mechanism: a second button, not a second pass

Add `Approve + save as example` alongside `Approve` on Callie's Slack card. The
approver is already reading the copy at that instant, so the "is this worth
imitating?" judgment costs one click and **zero additional model calls**.

This distinction is load-bearing: *approved to publish* ≠ *exemplary writing*. Plenty
of posts are merely fine. Auto-harvesting every approval would fill the corpus with
mediocre examples and quietly degrade every future draft that retrieves from it.

### Subtlety that will bite if ignored

Capture the copy **as it reads at approval time**, by re-reading the card — not the
text captured when the notification fired. Jen can and will edit copy between "Ready"
and someone clicking approve, and the corpus must hold what was actually approved.

Follow the `content_hash` dedup pattern already used by `writing_sources`, so
re-approval or a re-parse cannot duplicate a row.

### Decided 2026-08-11 — separate profile

Social copy gets its own `Amira Social` writing profile, with `channel` distinguishing
platforms inside it. The existing `Amira Marketing Voice` profile is built from
whitepapers, product overviews and enablement docs — a different register — and
because `writing_rules` are **profile-scoped**, sharing one profile would leak social
conventions ("Link in bio", hashtags, character limits) into document drafting.

Angela owns the existing profile and its 38 pending rule proposals; the new profile
leaves her queue untouched.

### Decided 2026-08-11 — capture edits and rejections, not just approvals

`Jen wrote X, we changed it to Y` is a stronger training signal than approved-only
examples: it is the house style actually being applied. The pipeline sees both versions
at approval time, so capturing the delta is cheap now and **unreconstructable later**.

Store the vendor's original alongside the approved final, plus the approver's Slack note
when changes are requested. Rejections carry the rationale, which is the part that
teaches.

**This pushes work into slice B, not just C.** By the time someone clicks Approve, the
doc holds only the current text — Jen's original is already overwritten. The before/after
pair cannot be reconstructed at approval time.

Mechanism: an **append-only copy version log**, one row per `(card_identity, copy_hash)`
first-seen. The poller already computes `copy_hash` on every pass, so it writes a row
only when the hash changes — cheap, and it needs no diffing logic. "Jen wrote X, we
changed it to Y" then falls out of the log by reading the first and last versions for a
card. Slice B writes the log; slice D reads it.

Record who was observed changing it only to the extent the doc reveals it (the export
carries no authorship); the approver and their note come from Slack, where we do know.

### Channel normalization — the platform dropdown is not a channel

The `Platform` chip carries **multi-platform combos** as single opaque values:
`FB/IG`, `All`, `Facebook, IG, X`, `FB, LI, & X`, `Facebook/LinkedIn`,
`Facebook/IG, LinkedIn…`, plus `TBD`. Writing `FB, LI, & X` straight into
`writing_examples.channel` would make a retrieval for "LinkedIn examples" miss it
entirely — the corpus would look emptier than it is.

So harvesting needs an alias map from the vendor's string to a canonical channel set:

| Vendor value | Canonical channels |
|---|---|
| `Facebook`, `FB` | `facebook` |
| `Instagram`, `IG` | `instagram` |
| `LinkedIn`, `LI` | `linkedin` |
| `X` (also `Twitter`, `TWITTER(X)` in body text) | `x` |
| `FB/IG` | `facebook`, `instagram` |
| `Facebook/LinkedIn` | `facebook`, `linkedin` |
| `FB, LI, & X` | `facebook`, `linkedin`, `x` |
| `All` | every canonical channel (define explicitly, do not infer) |
| `TBD` | none — do not harvest |

**Fan out to one `writing_examples` row per canonical channel.** A combo post really was
approved for each of those channels, and per-channel rows are what retrieval asks for.
Dedup therefore keys on `(copy_hash, channel)`, not `copy_hash` alone.

**The list is user-editable, so it will grow.** An unrecognized platform value must
raise an alert, never be silently dropped or silently harvested as a literal channel —
otherwise the corpus quietly rots as Jen adds options. Same loud-failure rule as the
label parsing.

Also unresolved: whether Writing Studio retrieval over `writing_examples` is semantic
(needs an embedding on insert) or a profile-scoped fetch-all. With 7 rows today it may
well be the latter; confirm before assuming an embedding is required.

## Authorization change required

`docs/` and the Kai rules currently restrict side-effecting actions to Jon and Missy.
Letting Angela, Hannah, or Jaclyn click Approve — and have that write into Jen's doc
— widens that deliberately. This must be an explicit allowlist for this pipeline
only, not a general loosening.

## Rejected options, and why

- **Google Apps Script.** Docs has no edit trigger (only open + time-driven), so a
  bound script could not react to a dropdown anyway. Worse, it would mean installing
  code inside a file Jon does not own: Jen's Workspace admin could block it, and it
  would vanish silently if she copied or moved the doc.
- **Docs API for chip values.** Proven impossible (finding 1).
- **Migrating to a Google Sheet.** Technically the best substrate — real `onEdit`
  triggers, readable dropdowns, deep links to a row, and it reuses the proven
  Apps Script → `/api/enablement/ingest` wire. Rejected because Jen prefers the doc
  and it is her file. Revisit only if she asks.
- **Inserting replacement images into the doc.** Possible (`insertInlineImage` + a
  publicly fetchable URL, which the tunnel could serve). Rejected for v1: placing an
  image inside her card is index math on someone else's layout, and the result would
  land at the wrong size in the wrong place, so Jen would redo it. Callie attaches
  the new visual to the email and the Slack thread instead.

## Open items

- [x] Hannah's and Jaclyn's emails — `hannah.slater@`, `jaclyn.wright@` (2026-08-11).
- [x] Status vocabulary confirmed — see the table above (2026-08-11).
- [ ] Jon re-consents to Google to pick up the `drive` scope:
      `https://app.artemisos.me/api/google/oauth/start?purpose=personal`

      **First attempt failed and the failure is instructive.** Jon completed consent
      at 20:58 UTC but Google granted only the old 8 scopes. Cause: the launchd
      `me.artemisos.app` uvicorn runs **without `--reload`**, so the process was still
      serving the pre-edit `GOOGLE_PERSONAL_SCOPES`. The granted list is echoed in the
      callback query string in `~/Library/Logs/artemisos/app.out.log` — grep
      `oauth/callback` and read the `scope=` param to verify a grant, rather than
      trusting that the click worked. Service restarted 21:03 UTC; retry pending.

      Second risk on retry: full `drive` is a Google **restricted** scope. Grantable
      for an app in Testing mode with Jon as a test user; blocked for a published-
      but-unverified client. If it is refused, drop the `@mention` and rely on the
      Gmail notification, which needs no new scope.
- [ ] Tell Jen that `Platform:`, `Asset for review`, and `Copy review` are now
      load-bearing label text. Renaming them blinds the parser (we alert, but the
      pipeline stops until the labels are fixed).
