# Schema — Reason Code Registry

Canonical list of reason codes scouts may emit. **Append-only.** Stored in `reason_code_registry` table.

## Why this is a hard constraint

Scouts cannot invent reason codes mid-flight. If a scout believes a new code is needed:
1. Include the code in the signal with `confidence < 0.5`
2. Add `proposed_new_code` to signal flags
3. Write a row to `proposed_reason_codes` for human review

This is the only way a new code enters the registry — never silently.

## Seed values

Populate at DB initialization. Set `seeded = true` for all initial codes.

These are Josh's canonical 17 codes from `decisions/campaign-signal-spec-v1.md §2`. No other codes are in the seed. The "Emitted by" column is conservative — listed scouts are expected to emit the code as primary emitters, not exhaustive.

| Code | Plain-English trigger | What the scout looks for | Default urgency | Emitted by |
|---|---|---|---|---|
| `POLICY_LIT_MANDATE` | New state law passes requiring screening or literacy intervention | Bills with screening/intervention/dyslexia/structured-literacy keywords reaching INTRODUCED, PASSED_CHAMBER, or ENACTED in a priority state | hot at PASSED_CHAMBER or ENACTED; standard at INTRODUCED | starbridge_researcher, legislative_scout, state_doe_scout |
| `POLICY_EDTECH_TIME_LIMIT` | Legislation reducing time on ed tech, or public dissatisfaction with screen-time on ed tech | Bills, news, or board commentary citing screen-time caps or ed-tech-time reduction; Amira positioned as low-time / high-impact | standard; hot if bill is statewide and includes K–3 | legislative_scout, regional_news_scout |
| `FUNDING_LITERACY_GRANT` | State publishes a literacy grant or funding announcement for high-impact tutoring | Grants.gov, Federal Register, or state DoE press releases announcing literacy / tutoring / HIT funding | hot if deadline ≤ 30 days; standard if 30–90; enrichment otherwise | starbridge_researcher, federal_funding_scout, state_doe_scout |
| `FUNDING_DEADLINE_NEAR` | State notification or selection deadline within 90 days | Any active funding signal where days_until ≤ 90 | hot ≤ 30 days, standard 30–90 | starbridge_researcher, federal_funding_scout, procurement_scout |
| `FUNDING_HB2_ELIA` | District publicly discusses HB 2 Early Literacy Intervention Allotment ($250/student, K–3) spend | TX board minutes / budget docs referencing HB 2, ELIA, or Early Literacy Intervention Allotment | enrichment (context only — not a discrete event) | board_minutes_scout |
| `VENDOR_APPROVED_LIST` | State adds Amira to an approved-vendor list | State DoE procurement / approved-vendor list pages mentioning Amira (or category Amira qualifies for) | hot | starbridge_researcher, state_doe_scout |
| `VENDOR_DISSATISFACTION` | Public dissatisfaction with iReady, Lexia, UCSF Multitudes, or Amplify | News, board minutes, or LinkedIn posts naming the competitor with negative valence (efficacy, cost, fit, renewal) | standard; hot if board votes non-renewal or RFP follows | regional_news_scout, board_minutes_scout, linkedin_observer |
| `DISTRICT_STRATEGIC_LITERACY` | District strategic plan names literacy as a top priority | Strategic plan PDFs, board adoption of plan with literacy as named pillar | standard | regional_news_scout, board_minutes_scout |
| `DISTRICT_PROFICIENCY_GAP` | District publicly cites a literacy achievement gap or proficiency drop | Board minutes, press releases, or local news citing reading-proficiency decline, NAEP drop, or named gap | standard; hot if paired with vendor dissatisfaction or RFP | regional_news_scout, board_minutes_scout |
| `DISTRICT_DLL_EXPANSION` | District announces bilingual or dual-language program expansion | Board votes, press releases, or strategic plan items naming DLL / dual-language / bilingual program expansion | standard | board_minutes_scout |
| `DISTRICT_MTSS_STRAIN` | District announces MTSS or intervention staffing challenges | Board minutes or news citing intervention staffing shortages, MTSS gaps, Tier 2/3 capacity issues | standard | board_minutes_scout |
| `PROCUREMENT_ELA_ADOPTION` | New core ELA adoption cycle opening | Adoption committee formation, public comment windows, ELA materials review on board agenda | standard; hot when RFP posts | procurement_scout, board_minutes_scout |
| `PROCUREMENT_LITERACY_RFP` | Active literacy/assessment/curriculum RFP | RFPs/RFIs on statewide portals or district sites; literacy / reading / assessment / tutoring scope | hot if days_to_close ≤ 14; standard 15–45; reject > 45 unless strategic | starbridge_researcher, procurement_scout |
| `TX_HB1416_WAIVER` | District pursues or is awarded an HB 1416 tutoring waiver | TEA waiver filings, board discussion of HB 1416 waiver, district press; Amira is TEA-approved for HB 1416 | hot | legislative_scout, board_minutes_scout |
| `TX_HB3_DYSLEXIA_COMPLIANCE` | District flags HB 3 dyslexia reporting compliance challenges | Board minutes / TEA correspondence citing HB 3 dyslexia reporting friction; Amira is TEA-approved | hot | legislative_scout, board_minutes_scout |
| `LEADER_TRANSITION_FORMAL` | New superintendent, CAO, or curriculum director formally hired | Two-source confirmed formal hire — board vote OR district press release | hot for 90 days post-hire | regional_news_scout, leadership_transition_scout |
| `LEADER_TRANSITION_INTERIM` | Interim supe / CAO / curriculum lead named | Single-source interim announcement | standard | linkedin_observer, leadership_transition_scout |

## Seed SQL

```sql
INSERT INTO reason_code_registry (code, description, source_scout, seeded) VALUES
  ('POLICY_LIT_MANDATE', 'New state law passes requiring screening or literacy intervention', 'starbridge_researcher,legislative_scout,state_doe_scout', true),
  ('POLICY_EDTECH_TIME_LIMIT', 'Legislation reducing time on ed tech, or public dissatisfaction with screen-time on ed tech', 'legislative_scout,regional_news_scout', true),
  ('FUNDING_LITERACY_GRANT', 'State publishes a literacy grant or funding announcement for high-impact tutoring', 'starbridge_researcher,federal_funding_scout,state_doe_scout', true),
  ('FUNDING_DEADLINE_NEAR', 'State notification or selection deadline within 90 days', 'starbridge_researcher,federal_funding_scout,procurement_scout', true),
  ('FUNDING_HB2_ELIA', 'District publicly discusses HB 2 Early Literacy Intervention Allotment ($250/student, K-3) spend', 'board_minutes_scout', true),
  ('VENDOR_APPROVED_LIST', 'State adds Amira to an approved-vendor list', 'starbridge_researcher,state_doe_scout', true),
  ('VENDOR_DISSATISFACTION', 'Public dissatisfaction with iReady, Lexia, UCSF Multitudes, or Amplify', 'regional_news_scout,board_minutes_scout,linkedin_observer', true),
  ('DISTRICT_STRATEGIC_LITERACY', 'District strategic plan names literacy as a top priority', 'regional_news_scout,board_minutes_scout', true),
  ('DISTRICT_PROFICIENCY_GAP', 'District publicly cites a literacy achievement gap or proficiency drop', 'regional_news_scout,board_minutes_scout', true),
  ('DISTRICT_DLL_EXPANSION', 'District announces bilingual or dual-language program expansion', 'board_minutes_scout', true),
  ('DISTRICT_MTSS_STRAIN', 'District announces MTSS or intervention staffing challenges', 'board_minutes_scout', true),
  ('PROCUREMENT_ELA_ADOPTION', 'New core ELA adoption cycle opening', 'procurement_scout,board_minutes_scout', true),
  ('PROCUREMENT_LITERACY_RFP', 'Active literacy/assessment/curriculum RFP', 'starbridge_researcher,procurement_scout', true),
  ('TX_HB1416_WAIVER', 'District pursues or is awarded an HB 1416 tutoring waiver', 'legislative_scout,board_minutes_scout', true),
  ('TX_HB3_DYSLEXIA_COMPLIANCE', 'District flags HB 3 dyslexia reporting compliance challenges', 'legislative_scout,board_minutes_scout', true),
  ('LEADER_TRANSITION_FORMAL', 'New superintendent, CAO, or curriculum director formally hired', 'regional_news_scout,leadership_transition_scout', true),
  ('LEADER_TRANSITION_INTERIM', 'Interim supe / CAO / curriculum lead named', 'linkedin_observer,leadership_transition_scout', true);
```
