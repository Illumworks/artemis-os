"""A/B the scout prompt, reporting each stage separately.

Three distinct failure modes get conflated if you only report pass/fail:
  fence   — did the model wrap output in a code fence, and close it?
  json    — does it parse at all?
  schema  — does it satisfy ScoutEmittedSignal, the scout's real contract?
"""
import asyncio, json, sys, time
from sqlalchemy import text as sqltext

# Article-shaped input, matching what the news client actually supplies
# (title, description, url, published_at, source_name, content). The bodies are
# synthetic but the SHAPE is production's -- the earlier version passed a bare
# headline and a fake URL, which Claude correctly refused to invent from, so the
# test was scoring willingness-to-confabulate rather than quality.
ITEMS = [
    ("Champaign Unit 4 School District Names New Superintendent",
     "The Champaign Unit 4 school board voted 6-1 Monday to appoint Dr. Marcus Webb as "
     "superintendent, effective July 1. Webb comes from Rockford Public Schools where he "
     "served as chief academic officer. Board president Elena Ortiz said at the 2026-09-02 "
     "meeting that Webb's literacy record was decisive: 'He moved third-grade reading "
     "proficiency eleven points in four years.' Webb succeeds Dr. Susan Zola, who retired "
     "in June after nine years.",
     "https://news-gazette.com/champaign-unit-4-superintendent", "2026-09-03", "News-Gazette"),
    ("Michigan reading scores drop again; state superintendent calls for improvement",
     "Michigan's third-grade reading proficiency fell to 39.6% on the 2026 M-STEP, down 1.8 "
     "points from 2025 and the fourth consecutive annual decline. State Superintendent "
     "Michael Rice said in a 2026-09-05 statement that districts 'must treat this as the "
     "emergency it is.' The department has not announced new funding or a curriculum "
     "directive. Several districts are reviewing intervention contracts independently.",
     "https://michiganadvance.com/m-step-reading-2026", "2026-09-05", "Michigan Advance"),
    ("San Antonio ISD bilingual enrollment grows amid dual-language teacher shortage",
     "San Antonio ISD reported 14,200 students in dual-language programs this year, up 8% "
     "from 2025. The district is 62 certified bilingual teachers short of its staffing "
     "target, according to a human resources presentation to trustees on 2026-08-27. "
     "Deputy Superintendent Ana Villarreal said the district is 'evaluating supplemental "
     "instructional tools' to support uncertified classroom aides.",
     "https://sareport.org/saisd-dual-language-2026", "2026-08-28", "San Antonio Report"),
    ("Pinellas County Schools issues Instructional Services RFP for K-5 reading intervention",
     "Pinellas County Schools has posted RFP 26-014-DS for Instructional Services supporting "
     "K-5 reading intervention and dyslexia screening. Responses are due October 14, 2026 at "
     "2:00 PM ET. The solicitation covers approximately 38,000 students across 74 elementary "
     "schools and references the district's 2026-29 Strategic Plan literacy targets. A "
     "mandatory pre-bid conference is scheduled for September 22.",
     "https://pcsb.org/procurement/rfp-26-014-DS", "2026-09-04", "Pinellas County Schools"),
    ("Baltimore County Public Schools names new superintendent",
     "The Baltimore County Board of Education voted unanimously on 2026-09-08 to name Dr. "
     "Rachel Nkemdirim as superintendent, effective immediately. Nkemdirim has served as "
     "interim since March. In her first remarks she named early literacy and chronic "
     "absenteeism as her two priorities for the year.",
     "https://baltimoresun.com/bcps-superintendent-named", "2026-09-08", "Baltimore Sun"),
]


async def main() -> None:
    from artemis.agent.client import CompletionRequest
    from artemis.agent.types import Message, TextBlock
    from artemis.db import SessionLocal
    from artemis.marketing.scout_runner import reason_code_system_suffix
    from artemis.marketing.scout_schemas import ScoutEmittedSignal
    from artemis.providers.fallback import complete_with_fallback
    from artemis.providers.feature_catalog import _LM_STUDIO_MODEL
    from artemis.providers.gemini.adapter import _strip_wrapping_code_fence
    from artemis.providers.lm_studio.adapter import LMStudioAdapter

    which = sys.argv[1]
    async with SessionLocal() as s:
        row = (await s.execute(sqltext(
            "select system_prompt, reason_codes_emitted, model from agents "
            "where agent_id='marketing.scout.regional_news'"))).one()
    system_prompt = "\n\n".join(p for p in [row[0] or "", reason_code_system_suffix(row[1])] if p)
    model = _LM_STUDIO_MODEL if which == "local" else row[2]
    adapter = LMStudioAdapter()

    fenced = closed = parsed = valid = 0
    reasons: list[str] = []
    t_all = 0.0
    for headline, body, url, published, publisher in ITEMS:
        content = (f"{headline}\n\n{body}\n\nSource: {publisher}, published {published}")
        user = "\n\n".join([
            f"Item:\n{content}", f"URL: {url}",
            "Return JSON: headline, sourceType, sourceUrl, campaignFamily, urgencyTier, "
            "reasonCodes, whyFlagged, evidence. sourceType in: "
            "manual|starbridge|news_article|board_minutes|state_doe|linkedin_post|legiscan.",
        ])
        req = CompletionRequest(
            messages=[Message(role="user", content=[TextBlock(text=user)])],
            system=system_prompt, model=model, max_tokens=8192)
        t0 = time.time()
        if which == "local":
            resp = await adapter.complete(req)
        else:
            resp = await complete_with_fallback(req, primary="claude-code", fallback="anthropic")
        t_all += time.time() - t0
        raw = "".join(getattr(b, "text", "") for b in resp.message.content).strip()

        if raw.startswith("```"):
            fenced += 1
            if raw.rstrip().endswith("```"):
                closed += 1
        stripped = _strip_wrapping_code_fence(raw)
        try:
            payload = json.loads(stripped)
            parsed += 1
        except Exception as exc:
            reasons.append(f"JSON: {type(exc).__name__} (len={len(raw)}, "
                           f"starts={raw[:14]!r}, ends={raw[-14:]!r})")
            continue
        try:
            _sig = ScoutEmittedSignal.model_validate(payload)
            valid += 1
            print(f"  [{_sig.urgency_tier}] district={_sig.district_id!r} state={_sig.state_code!r}")
            print(f"      codes={[c.code for c in _sig.reason_codes]}")
            print(f"      why={(_sig.why_flagged or '')[:150]}")
            print(f"      evidence={(_sig.evidence or '')[:170]}")
        except Exception as exc:
            first = str(exc).split("\n")[1:3]
            reasons.append("SCHEMA: " + " | ".join(x.strip() for x in first))

    n = len(ITEMS)
    print(f"=== {which} :: {model} ===")
    print(f"  fenced {fenced}/{n}   closed-fence {closed}/{n}   json-parsed {parsed}/{n}   "
          f"schema-valid {valid}/{n}")
    print(f"  {t_all/n:.1f}s per item")
    for r in reasons:
        print(f"  - {r[:190]}")

asyncio.run(main())
