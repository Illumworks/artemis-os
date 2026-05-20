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

### Original seed (Scout team baseline)

| Code | Source Scout | Description |
|---|---|---|
| `RFP_EFFICACY_LANGUAGE` | starbridge_researcher, procurement_scout | RFP mentions efficacy proof, measurable growth requirements |
| `RFP_OUTCOMES_BASED_LANGUAGE` | starbridge_researcher, procurement_scout | RFP mentions performance guarantees, risk-share, contingent payment |
| `STATE_OBC_LEGISLATION` | starbridge_researcher, legislative_scout | State has active OBC framework / legislation |
| `STATE_DYSLEXIA_MANDATE` | starbridge_researcher, legislative_scout, state_doe_scout | State has dyslexia screener requirements |
| `STATE_BILITERACY_INITIATIVE` | starbridge_researcher, legislative_scout, state_doe_scout | State has dual-language / biliteracy initiative |
| `BOARD_BUDGET_PRESSURE` | regional_news_scout, board_minutes_scout | Board minutes reference budget constraints, ESSER cliff |
| `BOARD_VENDOR_ACCOUNTABILITY` | regional_news_scout, board_minutes_scout | Board questioning ed-tech ROI, vendor performance |
| `BOARD_OBC_DISCUSSION` | regional_news_scout, board_minutes_scout | Board discussing OBC concept (not yet RFP) |
| `BOARD_OBC_RFP_APPROVED` | regional_news_scout, board_minutes_scout | Board approved an OBC-structured RFP |
| `ESSER_CLIFF_REFERENCE` | starbridge_researcher, regional_news_scout, board_minutes_scout | District publicly referencing ESSER funding loss |
| `SUPERINTENDENT_TRANSITION` | regional_news_scout, leadership_transition_scout | New supe announced, often with reading-program churn history |
| `EXISTING_CUSTOMER_EXPANSION` | (multiple) | A school in district already seeing growth with Amira |
| `LINKEDIN_LEADER_ENGAGEMENT` | linkedin_observer | Watched leader posts content matching campaign theme |

### New — Legislative Scout (1.4)

| Code | Description |
|---|---|
| `BILL_INTRODUCED` | Literacy / curriculum / assessment bill filed in legislature |
| `BILL_PASSED_CHAMBER` | Bill passed one chamber, on the move |
| `BILL_ENACTED` | Bill signed into law |

### New — Federal Funding Scout (1.5)

| Code | Description |
|---|---|
| `FEDERAL_GRANT_OPEN` | Federal grant window open with literacy / curriculum alignment |
| `FEDERAL_GRANT_DEADLINE` | Deadline within 30 days, hot urgency |
| `CLSD_ANNOUNCEMENT` | Comprehensive Literacy State Development grant news |

### New — State DoE Scout (1.6)

| Code | Description |
|---|---|
| `STATE_GUIDANCE_ISSUED` | Non-binding state DoE guidance document |
| `STATE_MANDATE_ISSUED` | Binding state-level mandate, hot |
| `GUBERNATORIAL_EO_LITERACY` | Governor signs EO related to literacy / curriculum |

### New — Procurement Scout (1.7)

| Code | Description |
|---|---|
| `RFP_LITERACY_POSTED` | Literacy / reading RFP posted to portal |
| `RFP_ASSESSMENT_POSTED` | Assessment vendor RFP posted |
| `RFP_TUTORING_POSTED` | Tutoring vendor RFP posted |
| `RFP_DEADLINE_CRITICAL` | Days-to-close ≤ 14 |

### New — Board Minutes Scout (1.8)

| Code | Description |
|---|---|
| `BOARD_LITERACY_CURRICULUM_REVIEW` | Board discussing curriculum review |
| `BOARD_VENDOR_REVIEW` | Board reviewing current vendor performance |
| `BOARD_RFP_AUTHORIZATION` | Board votes to authorize an RFP |

### New — Leadership Transition Scout (1.9)

| Code | Description |
|---|---|
| `SUPE_SEARCH_ANNOUNCED` | Outgoing announced, search underway |
| `SUPE_INTERIM_NAMED` | Interim supe appointed |
| `SUPE_FORMAL_HIRE` | New supe hired and confirmed (hot — first 90 days buying window) |
| `SENIOR_LEADER_TRANSITION` | Curriculum director, asst supe, or other senior role change |

## Seed SQL

```sql
INSERT INTO reason_code_registry (code, description, source_scout, seeded) VALUES
  ('RFP_EFFICACY_LANGUAGE', 'RFP mentions efficacy proof, measurable growth requirements', 'starbridge_researcher,procurement_scout', true),
  ('RFP_OUTCOMES_BASED_LANGUAGE', 'RFP mentions performance guarantees, risk-share, contingent payment', 'starbridge_researcher,procurement_scout', true),
  ('STATE_OBC_LEGISLATION', 'State has active OBC framework / legislation', 'starbridge_researcher,legislative_scout', true),
  ('STATE_DYSLEXIA_MANDATE', 'State has dyslexia screener requirements', 'starbridge_researcher,legislative_scout,state_doe_scout', true),
  ('STATE_BILITERACY_INITIATIVE', 'State has dual-language / biliteracy initiative', 'starbridge_researcher,legislative_scout,state_doe_scout', true),
  ('BOARD_BUDGET_PRESSURE', 'Board minutes reference budget constraints, ESSER cliff', 'regional_news_scout,board_minutes_scout', true),
  ('BOARD_VENDOR_ACCOUNTABILITY', 'Board questioning ed-tech ROI, vendor performance', 'regional_news_scout,board_minutes_scout', true),
  ('BOARD_OBC_DISCUSSION', 'Board discussing OBC concept (not yet RFP)', 'regional_news_scout,board_minutes_scout', true),
  ('BOARD_OBC_RFP_APPROVED', 'Board approved an OBC-structured RFP', 'regional_news_scout,board_minutes_scout', true),
  ('ESSER_CLIFF_REFERENCE', 'District publicly referencing ESSER funding loss', 'starbridge_researcher,regional_news_scout,board_minutes_scout', true),
  ('SUPERINTENDENT_TRANSITION', 'New supe announced, often with reading-program churn history', 'regional_news_scout,leadership_transition_scout', true),
  ('EXISTING_CUSTOMER_EXPANSION', 'A school in district already seeing growth with Amira', NULL, true),
  ('LINKEDIN_LEADER_ENGAGEMENT', 'Watched leader posts content matching campaign theme', 'linkedin_observer', true),
  ('BILL_INTRODUCED', 'Literacy/curriculum/assessment bill filed in legislature', 'legislative_scout', true),
  ('BILL_PASSED_CHAMBER', 'Bill passed one chamber, on the move', 'legislative_scout', true),
  ('BILL_ENACTED', 'Bill signed into law', 'legislative_scout', true),
  ('FEDERAL_GRANT_OPEN', 'Federal grant window open with literacy/curriculum alignment', 'federal_funding_scout', true),
  ('FEDERAL_GRANT_DEADLINE', 'Deadline within 30 days, hot urgency', 'federal_funding_scout', true),
  ('CLSD_ANNOUNCEMENT', 'Comprehensive Literacy State Development grant news', 'federal_funding_scout', true),
  ('STATE_GUIDANCE_ISSUED', 'Non-binding state DoE guidance document', 'state_doe_scout', true),
  ('STATE_MANDATE_ISSUED', 'Binding state-level mandate, hot', 'state_doe_scout', true),
  ('GUBERNATORIAL_EO_LITERACY', 'Governor signs EO related to literacy/curriculum', 'state_doe_scout', true),
  ('RFP_LITERACY_POSTED', 'Literacy/reading RFP posted to portal', 'procurement_scout', true),
  ('RFP_ASSESSMENT_POSTED', 'Assessment vendor RFP posted', 'procurement_scout', true),
  ('RFP_TUTORING_POSTED', 'Tutoring vendor RFP posted', 'procurement_scout', true),
  ('RFP_DEADLINE_CRITICAL', 'Days-to-close <= 14', 'procurement_scout', true),
  ('BOARD_LITERACY_CURRICULUM_REVIEW', 'Board discussing curriculum review', 'board_minutes_scout', true),
  ('BOARD_VENDOR_REVIEW', 'Board reviewing current vendor performance', 'board_minutes_scout', true),
  ('BOARD_RFP_AUTHORIZATION', 'Board votes to authorize an RFP', 'board_minutes_scout', true),
  ('SUPE_SEARCH_ANNOUNCED', 'Outgoing announced, search underway', 'leadership_transition_scout', true),
  ('SUPE_INTERIM_NAMED', 'Interim supe appointed', 'leadership_transition_scout', true),
  ('SUPE_FORMAL_HIRE', 'New supe hired and confirmed (hot — first 90 days buying window)', 'leadership_transition_scout', true),
  ('SENIOR_LEADER_TRANSITION', 'Curriculum director, asst supe, or other senior role change', 'leadership_transition_scout', true);
```
