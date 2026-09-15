# What rules govern the alerts

**Written 2026-09-15**, after Sarah Hais asked in #policy-watch what rules are
built into these alerts, having spotted a Worcester item that was fourteen months
old. Regenerate by re-reading the files cited against each rule.

This is what stops a bad item reaching a channel. It is deliberately explicit
about what we do NOT check, because a list of only the successes reads as
complete and is worse than no list.

## How old an item may be

| Rule | Where |
|---|---|
| News searches ask the source for the **last 42 days**, rather than filtering stale results afterwards | `tools/news.py` |
| State DoE feeds return only the last 42 days, newest first | `tools/state_doe.py` |
| A signal whose article is **older than 42 days is refused at write time** | `marketing/article_recency.py` |
| The policy-watch digest excludes articles published more than 42 days ago | `screentime/reporting.py` |

All four use the same 42-day bar on purpose, so one is not handing the next work
to throw away.

**The date comes from the feed, not from opening the article.** Every source
publishes a date with each item and we read that. Until 2026-09-15 we read it
wrongly — feeds use a format (`"Tue, 11 Apr 2023 07:00:00 GMT"`) that our parser
could not handle, so it recorded *no date at all* on all 1,742 stored items. The
brief then ordered by when our scout happened to *find* something, which is how a
fourteen-month-old story reached the top. Fixed and backfilled; the corpus turned
out to be **71% older than 180 days, with the oldest article from 2003**.

**The limit:** if a publisher stamps an item with the wrong date, we inherit it.
We do not open the article to check, and for the Google News items we could not
even if we wanted to — see below.

## Not showing the same thing twice

| Rule | Where |
|---|---|
| One stored signal per source URL | `screentime/repository.py` |
| Content fingerprint on source + URL + title, for items with no URL | `screentime/filters.py` |
| The market-signals queue de-duplicates on URL, with an override when a bill's activity hash changes so real progress still surfaces | `tools/signal_queue.py` |
| An item already reported is not reported again | `screentime/reporting.py` |

URL alone was added on 2026-09-15. The fingerprint included the title, and Google
News does not keep titles stable — the same article arrived in August as
"… - capitolnewsillinois.com" and in September as "… - Capitol News Illinois", so
it stored twice. **556 rows, 32% of the table, were copies**; they have been
removed and the guard prevents more.

## Whether the source is real

| Rule | Where |
|---|---|
| A source URL must be the **exact** URL the item carried — never constructed, completed, shortened or guessed | scout instruction, `builders/executor.py` |
| The URL is fetched before the signal is stored; a definitive 404/400/410 refuses the write | `tools/_source_url_check.py` |
| A publisher's **homepage is refused** — a signal is about a specific item, so a URL with no path cannot be its source | `tools/_source_url_check.py` |
| Each scout may emit only its own allowed reason codes | `builders/executor.py` |
| Scouts cannot delegate to a sub-agent | `providers/claude_code/adapter.py` |
| A scan that fetched data and then neither wrote nor stated a result is recorded as **failed**, not success | `marketing/scout_conclusion.py` |

The URL check exists because 149 fabricated signals were once written and 52 got
past review. Sub-agent delegation was removed on 2026-09-14 after one invented six
district signals; the scout caught them, but nothing in the system would have.

**Zero signals is always a valid answer.** None of these rules pushes a scout to
produce something rather than nothing, and that is deliberate: one invented signal
costs the credibility of every real one beside it.

## What we cannot check

- **Google News links cannot be resolved to the article.** They are opaque
  redirects, and three independent methods fail to decode them. Worse, a dead one
  still answers HTTP 200 to us — it only fails once a browser runs it — so our
  URL check can never certify one. They are therefore rendered as a search for
  the exact headline, narrowed to the publisher, with the outlet named beside it.
- **We do not read the article.** Relevance is judged from the headline, the feed
  summary and the source, not the body text.
- **A wrong date at the publisher is inherited.**
- **Board documents** are read, including scanned ones via OCR since 2026-09-14,
  but an image-only page with poor scan quality can still yield little.
