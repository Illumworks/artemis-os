# Procurement Intelligence — build-out plan (duplicate StarBridge for our territory)

**Date:** 2026-06-16  **Status:** Plan (approved by Jon — "I don't like gaps, let's get this done")
**Author:** Opus Lead, with Jon
**Context:** StarBridge (paid vendor; key deferred on credit limits) aggregates state/district
procurement + legislative + funding intelligence nationally. Jon's directive: **don't defer —
beat or duplicate it for OUR territory.** This doc is the actionable build-out. Grounded in the
2026-06-16 landscape research (territory + platform map below).

---

## 1. Goal & scope

Match StarBridge's **procurement RFP signal for Jon's ~6-state territory** (TX, FL, IL, IN, MD, MO) —
NOT nationally. Scoping to our footprint is what makes this finite. Plus close the StarBridge
*capability* gaps (§5) where they're worth it.

## 2. The core insight (why this is tractable)

K-12 procurement portals **cluster onto a handful of SaaS platforms** — the same pattern that let
the board_minutes scout crack BoardDocs once and cover every BoardDocs district. **Build one adapter
per PLATFORM, not per district.** Bonus: Bonfire + IonWave + DemandStar all merged into **Euna
Solutions** (still run as separate legacy portals with different access models).

## 3. Territory → platform map (from research)

| District | State | Platform | Access |
|---|---|---|---|
| Dallas ISD, Fort Bend ISD, Austin ISD, Katy ISD (partial) | TX | **Bonfire (Euna)** | **public RSS** `[org].bonfirehub.com/opportunities/rss` |
| Chicago PS, SD U-46 (Elgin) | IL | **Bonfire (Euna)** | public RSS |
| Houston ISD, Fort Worth ISD, Arlington ISD | TX | **IonWave (Euna)** | **login-gated** |
| Cypress-Fairbanks ISD | TX | Public Purchase | login-gated |
| Miami-Dade, Broward, Hillsborough, Duval | FL | **DemandStar (Euna)** | registration-gated |
| Orange (OCPS) | FL | VendorLink | email-only |
| Palm Beach | FL | BidNet Direct | paid aggregator |
| Pinellas | FL | **OpenGov** | **developer API** |
| Montgomery County PS, Prince George's PS | MD | **eMMA (state)** | **public search (state-mandated)** |
| Evansville, Hamilton SE | IN | custom district sites | scrape-only |

**State portals:** TX ESBD/TxSmartBuy (predictable URL + CSV export); MD eMMA (public, mandatory for
all MD districts — high value); FL VBS, IL BidBuy, IN IDOA, MO MissouriBUYS (state-agency-only, most
districts use their own platforms).

## 4. Build order (coverage × access-ease) — the procurement adapters

Adapters land in `artemis/scouts/procurement/portals.py` `PORTAL_REGISTRY` (extend it from
state-keyed stubs to real platform entries) and must feed the **live agentic procurement scout**
(`artemis/scouts/procurement/scout.py` + the tool the procurement agent calls — wire into the LIVE
path like board_minutes did, NOT the dead `marketing/scout_sources/procurement.py` NullAdapter).

- **PHASE 1 — Bonfire RSS — ✅ DONE + LIVE (`6bd1024`, 2026-06-16).** Adapter at
  `artemis/scouts/procurement/bonfire.py`; wired into the LIVE tool `procurement_portal.fetch`
  (NOT the unscheduled ProcurementScout — caught + fixed that dead-path mis-wire). Verified via
  tool_invocations: real Bonfire RFPs (Dallas ISD etc.) reach the agentic scout. Slugs live:
  dallasisd, fortbendisd, cps, u-46, austinisd. `katyisd` slug doesn't resolve — commented out,
  verify before re-enabling.
- **PHASE 2 — eMMA (Maryland) + TX ESBD.**
  - eMMA: MD law mandates ALL districts post here → 100% MD coverage in one form-scrape. ~2–3 days.
  - TX ESBD: state-level TX + TEA + co-op postings; predictable URL + 20K-row CSV export. ~2–3 days.
- **PHASE 3 — OpenGov.** Documented developer API (Pinellas confirmed, Katy piloting, growing K-12
  share). ~3–4 days; needs an API key.
- **DemandStar (Euna):** FL big-4 (Broward/Hillsborough/Duval + Miami-Dade partial) + Springfield MO.
  Registration-gated; public agency pages show limited info. MEDIUM. Sequence after Phase 3 / evaluate
  if a registered account unlocks a feed.
- **IonWave (Euna) — see §6 (the gap decision).** Houston ISD + Fort Worth ISD. Login-gated.

> ### ⚠️ ACCESS-LAYER REALITY (discovered building Phase 2, 2026-06-16) — reshapes the plan
> Phase 2 revealed that **eMMA + TX ESBD are GATED**, not "public/CSV" as assumed:
> - **eMMA:** iValua SPA behind **Google reCAPTCHA Enterprise** (every anon request → 302 browser-check).
> - **TX ESBD:** NetSuite SPA; data via **session-cookie-gated `.ss` endpoints**; robots.txt disallows crawlers.
> The adapters/parsers are **BUILT + tested + wired into the live tool** (`1f3c9fd`) — they return [] until a
> session is injected (`fetch_emma_opportunities(session_cookie=…)` / `fetch_esbd_opportunities(ns_session_cookie=…)`).
>
> **Portals split into two camps — this IS StarBridge's moat:**
> - **OPEN (free, scrape now):** Bonfire RSS ✅ (live), OpenGov (developer API — verify), USASpending (grants).
> - **GATED (need a logged-in session):** eMMA, TX ESBD, IonWave, DemandStar. **Playwright alone won't beat
>   reCAPTCHA Enterprise** — the realistic key is a **logged-in vendor-account session cookie** injected into
>   our (already-built) adapters. This is exactly what StarBridge charges to maintain at scale.
>
> **DECISION for Jon (the access-layer call):** are we willing to register + maintain **free vendor accounts**
> for our priority gated portals (MD eMMA, TX ESBD, IonWave-Houston/FW) and feed their session cookies in?
> - Adapters are ready; a session unlocks them. BUT cookies expire → needs a **session-refresh mechanism**
>   (not a one-shot), plus ToS gray area + fragility.
> - **Recommendation:** (1) lock in the OPEN free wins first (OpenGov + grant-chaining + board-minutes
>   pre-RFP) — guaranteed coverage, no account fragility. (2) Run ONE scoped account experiment on **eMMA**
>   (highest value — all MD districts): Jon creates a free MD eMMA vendor account → we inject the session →
>   confirm data flows through the built adapter → measure session lifetime / refresh burden. If clean,
>   extend the pattern to ESBD + IonWave. If session-mgmt is too fragile, those stay genuine StarBridge-gaps.
> - This generalizes the earlier IonWave (§6) question to the whole gated set — same call, prove it on eMMA.
>
> ### ✅ STRATEGIC DECISION (Jon, 2026-06-17): DURABLE + NO-ACCOUNT. Park the fragile gated portals.
> Jon's call: build only **durable, no-account** sources; don't take on fragile session-refresh treadmills.
> - **PARK (built, not deleted; stop chasing accounts):** eMMA, TX ESBD, IonWave, DemandStar — all need
>   expiring session cookies / logins. Adapters stay in the tree, ready if the calculus changes. **eMMA EIN
>   hunt dropped.** These are deliberately conceded to StarBridge's territory.
> - **OpenGov: PARKED (2026-06-17).** Jon registered a free vendor account but `developer.opengov.com`
>   login failed — the developer/API portal is NOT open to free vendor accounts (appears to need an
>   agency/paid or partner tier). Combined with one-district coverage (Pinellas) + the no-account
>   principle, not worth pursuing. Adapter stays **built + key-optional** (lights up if `OPENGOV_API_KEY`
>   is ever obtained), but it's effectively in the parked-gated camp with eMMA/ESBD.
> - **GO (durable + no-account):** Bonfire (live ✅) + **grant→procurement chaining** (USASpending free API,
>   no key) + **board_minutes pre-RFP** reason-codes (we own the data). This is the focus.

## 5. StarBridge capability gaps — what portals CAN'T give (and the roadmap)

Even with all adapters, portals only see RFPs **after posting**. StarBridge's premium layers + our plan:

1. **Pre-RFP intent (6–18 mo early).** StarBridge mines board minutes for strategic-plan/budget/pain
   signals. **WE ALREADY HAVE THIS** via the board_minutes scout (fixed 2026-06-16 — real agenda items
   flowing). *Action: we LEAD here for our territory; strengthen by adding budget/strategic-plan
   keyword reason-codes to the board_minutes qualifier.* **Lowest-effort, highest-differentiation.**
2. **Incumbent vendor + contract-expiration ("renewal before RFP").** StarBridge uses FOIA-sourced
   contracts/POs. *Roadmap (HARD): a contracts-ingestion source — start with what's free (some states
   publish awarded contracts + expiration on the same portals; e.g. eMMA/ESBD award notices), build a
   "contract expiring in N months → flag" signal. Full FOIA pipeline is a larger, later effort.*
3. **Grant → procurement chaining.** Map federal grant awards to likely upcoming district procurement.
   *Roadmap (MEDIUM): cross-reference USASpending.gov grant data (free API) with district identity +
   our federal_funding scout; flag "district X got grant Y → watch for related RFP."* Good ROI, free data.
4. **Cross-state aggregated search.** *Emerges naturally once the adapters land — add a unified
   "search all portals" query layer over the normalized signals.* Low effort once adapters exist.
5. **Historical spend visibility.** FOIA-sourced prior contracts/$ — expensive/slow. *Defer; revisit
   only if a specific deal needs it (could be an on-demand agent task, not a standing feed).*

**Honest "duplicate vs approximate":** the adapters give ~50–60% of StarBridge's RFP signal for our
territory; board_minutes already beats them on pre-RFP intent for our footprint; grant-chaining (#3)
is a high-ROL free add; FOIA contract/spend (#2 full, #5) is the genuine hard gap we approximate
partially via portal award-notices.

## 6. The IonWave gap decision (Houston ISD + Fort Worth ISD)

These two big TX districts (Fort Worth is an active-contact district) are on **login-gated IonWave**.
Recommendation: **defer the account; exhaust non-login paths first.**
- This is NOT the SAM.gov situation (there the *API* was broken, so the account was moot). Here the
  data genuinely lives behind login, so an account *would* unlock it — BUT authenticated scraping is
  fragile (session expiry, ToS gray area, detection/blocking) and maintenance-heavy.
- **First, free paths:** (a) does Houston/Fort Worth post co-op/large RFPs to **TX ESBD** (Phase 2)
  too? (b) Houston ISD has a legacy **Public Purchase** page — check for a non-login listing. (c) their
  own district procurement pages. If ESBD/legacy catch most of their big RFPs, the IonWave gap shrinks.
- **Only if those fail:** create ONE free vendor account as a *scoped experiment*, treat it as fragile,
  with a graceful fallback to 0 (never break the scout). Decide after Phase 2 with real coverage data.

## 7. Architecture — folds into the territory-driven source model

- `PORTAL_REGISTRY` becomes the **config-driven source of truth**: each entry = platform + access type
  (rss/api/form_scrape) + the per-district slugs. Adding a district = a registry/slug entry.
- **Discovery-assist** (shared with board_minutes): when a new district enters territory, an agent
  probes which platform it uses + proposes the slug/portal for Jon's one-click confirm (never
  auto-trust on customer-facing sources).
- Platform adapters are reusable: one Bonfire adapter serves every Bonfire district; etc.

## 8. Phased summary

| Phase | Build | Territory unlocked | Effort |
|---|---|---|---|
| 1 | Bonfire RSS adapter | Dallas, Fort Bend, Austin, Chicago, U-46, Katy | ~1–2d (in progress) |
| 2 | eMMA + TX ESBD | all MD districts + TX state/co-op | ~4–6d |
| 3 | OpenGov API | Pinellas, Katy pilot, growing | ~3–4d |
| 4 | Grant→procurement chaining (USASpending) | federal-funded districts | ✅ DONE+LIVE `c2d1f4b` |
| 5 | board_minutes pre-RFP reason-codes (strengthen our lead) | territory-wide | ~2d — NEXT (last durable no-account piece) |
| — | DemandStar / IonWave / FOIA contracts | FL big-4, Houston/FW, incumbent intel | gated/HARD — decide per §5/§6 |

## 9. Open decisions for Jon
1. **IonWave account** — recommend defer (try free paths first; §6).
2. **FOIA contract/spend pipeline (#2/#5)** — the hardest StarBridge gap; pursue now or after the free
   wins? Rec: do the free high-ROI items (grant-chaining #3, board-minutes #5) first, then assess FOIA.
</content>
