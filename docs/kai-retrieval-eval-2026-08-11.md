# Do AI-written summaries actually improve Kai's retrieval?

**Run:** 2026-08-11. **Question from Jon:** the summaries are for AI searching so Kai
surfaces the right content — is that right, and is it working?

**Short answer:** partly. They help on vague questions, do nothing on specific ones, and
the mechanism explains exactly why. One incidental finding is worth more than the headline.

---

## Method

17 questions taken **verbatim** from `#enablement-library` (2026-06-16 → 2026-08-01), 14 real
asset requests plus 3 known content gaps from F4 as negative controls — where the right
answer is "nothing", and the risk is that enrichment manufactures a confident wrong match.

Enriched the **entire competitive set**: every asset appearing in any question's top-10,
122 assets. Enriching only the hoped-for answers would hand them retrieval text their rivals
lack and rig the result toward "summaries help".

Measured top-5 before and after. Baseline and after JSON, plus the harness, are in the
session scratchpad.

## Results

| Outcome | Count | Examples |
|---|---|---|
| Improved | 3 | "anything on onboarding?", "quick start guides", Elkonin supporting results |
| Unchanged | 8 | District Admin guide, Evaluar video, reading risk, family summer reading |
| Noisier in slots 2-3 | 3 | Lectura ILP, "Benchmark on grade level?", SODA reordering |
| Negative controls | 3 | No increase in confidence — see below |

**The clearest win.** "do you have anything on onboarding?" went from *Champions Recruitment
Flyer* (junk, relevance 0 across the board) to **Launch Checklist for Admin** at #1. Nothing
in that asset's title contains "onboarding"; its new summary does.

**The clearest miss.** "video of a Lectura ILP lesson" kept the correct #1
(`STU-First-Enseñar-0001 - ILP.mp4`, the asset Sara explicitly corrected Kai toward on
06-24), but slots 2-3 degraded: *Amira Lectura Individualized Learning Pathways* was replaced
by an **Evaluar** video and a generic student prep video. Wrong product.

## Why: the summaries are generic where the titles are specific

Generated summaries share heavy boilerplate — "student-facing video showing...",
"walkthrough of...", "training deck for teachers...". That vocabulary is *what helps* a vague
query find anything at all, and it is *what hurts* a specific one, because every video now
looks semantically like every other video.

Two structural facts sharpen this:

1. **The reranker never sees the summary.** `_relevance_scores` scores title, tags, and
   asset_name only. Summaries change which candidates enter the pool via vector search, then
   a summary-blind reranker orders them. That is why top-1 relevance scores are almost
   entirely unchanged (see table) and why specific queries were untouched — the title-based
   reranker overrides.
2. **Negative controls did not get worse.** The three known gaps held at relevance 3, 6, 3.
   Enrichment did not manufacture confident wrong matches, which was the real risk.

## The finding that matters more than the headline

F4 lists "quick-start / implementation-sequence one-pager" as a **content gap** for Enablement
to go create. After enrichment, that query returns **Implementation Guide (HMH)** at #1.

The catalog also contains a plain **Implementation Guide** (audience: Teachers) which still
has no summary and did not surface.

So at least one of the ten "content gaps" is not a gap. It is an asset nobody could find.
Before Enablement writes anything from that list, the list should be re-run against search.

## Recommendation

Do **not** enrich the remaining ~290 assets yet. The measured benefit is concentrated in
vague queries, and the generic-boilerplate problem will scale with the batch.

Two cheap changes first, then re-measure on this same 17-question set:

1. **Add summary to the reranker** with a low weight. Right now summaries influence
   candidate selection but not ordering, which wastes most of their signal.
2. **Push the generator toward distinctiveness** — name the product, grade, and language, and
   ban the boilerplate openers. "Student-facing video showing..." on 40 assets is noise;
   "Enseñar ILP lesson, 1st grade, Spanish" is signal.

The 122 enriched assets stay. They are a net improvement and cost nothing to keep.
