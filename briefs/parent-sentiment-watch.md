# Brief — Parent-sentiment / narrative watch

**Written:** 2026-08-20 by Opus Lead · **Owner decisions from Jon captured inline**

## The ask (Angela, verbatim)

> "Please scan for parent groups / parent chatter anti-Amira and the themes akin
> to what we are hearing on: voice recordings, children being used to train AI,
> Amira is a chatbot. We are focused on Georgia, NYC, Florida — and where else
> are these outcries growing? An initial report would be good but maybe also a
> slack channel like the Market Signal Tracker?"

Two deliverables: **an initial report**, and **an ongoing watch**.

## What is reachable — probed, not assumed (2026-08-20)

| Source | Status | Note |
|---|---|---|
| Google News RSS | ✅ working | already the proven path for the brand lane |
| Reddit | ❌ **403 unauthenticated** | needs a free OAuth app — Jon creates, 2 min, no cost |
| Change.org | ⚠️ HTML 200 | scrapable but no API; check ToS before relying on it |
| Facebook parent groups | ❌ **not reachable, at all** | mostly private groups; Meta's API does not expose them and scraping breaches terms |
| X / Twitter | ❌ paid | ~$100+/mo for meaningful search volume |
| Vista Social | ⏸ parked | team account exists; Jon lacks admin/API. Ask IT — do not block on it |

⚠️ **Say the Facebook gap out loud to Angela.** "Parent groups" most likely means
Facebook groups in her head, and that is the one place we structurally cannot
see. Everything below is a genuine but partial substitute. Setting that
expectation now is cheaper than discovering it after the first report.

## Owner decisions (Jon, 2026-08-20)

- **Build our own**, do not wait on the crisis agency. Vista Social only if easy
  and cheap.
- **v1 sources: news themes + Reddit.** No paid X for now.
- **Its own Slack channel**, not folded into `#market-signals`.
- **Named states deep (GA, NY, FL, NM) + a lighter national sweep** — "where
  else is this growing" is the question with the most strategic value and a
  named-states-only scan structurally cannot answer it.

## Design

### 1. The theme layer is the durable asset — build it first

The three narratives Angela named are **narrative frames**, not keywords, and
they are what makes this portable: encode them once and every source we add
later inherits them.

- `voice_recording` — voice capture, recording children's voices, audio data,
  biometric, consent
- `training_ai_on_children` — kids training AI, student data trains the model,
  children as training data
- `is_a_chatbot` — "it's just a chatbot", ChatGPT for kids, AI teacher replacing
  teachers
- `privacy_surveillance` — data privacy, COPPA/FERPA, surveillance, who sees the data
- `screen_time_harm` — already partly covered by the existing policy gate

Each theme carries **multi-word anchors only**, for the reason already documented
in `topic_config`: the gate does plain substring matching, so short tokens match
inside ordinary words and flood the result. Store the matched theme on the signal
so "which narrative is growing" is a query, not a re-read.

**Precision note:** these themes are ABOUT Amira only sometimes. "Children being
used to train AI" is a national conversation. Theme match alone is a topic
signal; theme + brand or theme + a district/state we care about is an *Amira*
signal. Keep those distinguishable or the report will overstate the threat.

### 2. Sources

- **News (now):** run the theme anchors through the existing `national_news`
  path. Deep queries for GA/NY/FL/NM, lighter sweep for the other 47.
- **Reddit (after Jon creates the app):** search + subreddit monitoring.
  Education and parenting subs plus state/city local subs for the named
  geographies. Reddit's API is free with an OAuth app; respect its rate limits
  and identify the client honestly in the User-Agent.
- **Petitions (stretch):** Change.org is the canonical venue for organised parent
  action and a petition is a strong escalation signal. HTML-only — check ToS
  first and treat as best-effort.

### 3. Scoring — what makes something worth an interrupt

Volume alone is the wrong trigger. What matters is:
- **velocity** — acceleration, not count (the tell that something is spreading)
- **organisation** — a petition or an organised group beats scattered comments
- **proximity** — a district where Amira is deployed (we now have this: 3,096
  customer districts by NCES id, 102 of them in NM)
- **specificity** — names Amira, versus general AI-in-schools unease

### 4. Delivery

- **Its own channel**, and **alert-only / thresholded**. Silent unless something
  is genuinely moving. If it posts every mention it will be muted within a week
  and we will have built another firehose.
- **A daily one-line roll-up into `#market-signals`** ("parent sentiment: quiet /
  2 states heating") so it is visible without being noisy.
- **The initial report** is a one-off deep scan across all themes and geographies,
  written for Angela — findings and where the growth is, not a data dump.

## Scope guardrails

- **Read-only, public sources only.** No private groups, no logged-in scraping,
  no impersonation.
- **No individual profiling.** We track themes, venues and volume — not named
  private individuals. Public officials speaking publicly are in scope; a parent
  in a comment thread is a data point, not a dossier. This is a company already
  under scrutiny for how it handles children's data; the monitoring must not
  become the next story.
- Store the source URL for everything so any claim is checkable.

## Out of scope for v1

Paid X/Twitter · Facebook · Vista Social (parked pending IT) · sentiment scoring
beyond theme + stance · anything that requires a login.
