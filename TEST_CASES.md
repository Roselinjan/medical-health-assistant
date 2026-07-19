# Medical Health Assistant — Test Cases
 
**Patient used for testing:** `PAT-4E953C`
**Reports used:** `cbcreport.pdf` (digital PDF) and `scannedreport.pdf` (scanned/OCR)
 
---
 
## Ground Truth Reference
 
### cbcreport.pdf (Krishna Maurya)
| Parameter | Value | Range | Status |
|---|---|---|---|
| Haemoglobin | 15.5 g% | 14-16 | Normal |
| RBC Count | 4.6 | 4.0-6.0 | Normal |
| MCV | 82.17 fl | 80-99 | Normal |
| MCH | 33.70 pg | 27-31 | **HIGH** |
| MCHC | 41.01% | 32-37 | **HIGH** |
| Eosinophils | 07% | 00-06 | **HIGH** |
| RDW-SD | 50 fl | 37-54 | Normal |
| RDW-CV | 20% | 11-16 | **HIGH** |
 
**True abnormal list: MCH, MCHC, Eosinophils, RDW-CV**
 
### scannedreport.pdf (Ketan Chavan)
| Parameter | Value | Range | Status |
|---|---|---|---|
| Haemoglobin | 15 | 14-16 | Normal |
| MCV | 72.00 | 80-99 | **LOW** |
| MCH | 30.00 | 28-32 | Normal |
| MCHC | 41.67 | 30-34 | **HIGH** |
| Platelet Count | 1550000 | 150000-450000 | **HIGH** |
 
**True abnormal list: MCV, MCHC, Platelet Count**
 
---
 
## Test Cases
 
| Test ID | Scenario | Input Question | Report Name | Expected Result | Actual Response | Status | Notes / Root Cause |
|---|---|---|---|---|---|---|---|
| TC-001 | Normal RAG Retrieval | What is my hemoglobin level? | cbcreport.pdf | Returns correct hemoglobin value with source citation. | "Based on the retrieved results, your hemoglobin level is 15.5 g%, which is within the normal range of 14-16 g% for males." | ✅ Pass | Matches ground truth exactly (15.5 g%, normal). |
| TC-002 | Conversation Memory | Is it normal? (after asking hemoglobin) | cbcreport.pdf | Understands "it" refers to hemoglobin and answers accordingly. | "Yes, a hemoglobin level of 15.5 g% is normal... falls within this range." | ✅ Pass | Correctly resolved pronoun via `get_chat_history` + `rewrite_followup_question`. |
| TC-003 | Question Rewriting | Why is it high? (after asking MCH level) | cbcreport.pdf | Rewrites internally to "Why is my MCH high?" and returns correct explanation. | "An elevated MCH level, such as 33.70 pg, can be due to several factors including vitamin B12 or folate deficiency, certain types of anemia, liver disease, or an overactive thyroid..." | ✅ Pass | Rewrite correctly carried MCH/33.70 pg context into a focused explanation. |
| TC-004 | Metadata Filtering | What is my hemoglobin level? (scannedreport.pdf selected) | scannedreport.pdf | Returns 15 g% from the scanned report, not 15.5 g% from cbcreport.pdf. | "Based on the retrieved results, your hemoglobin level is 15 g%, which is within the normal range of 14-16 g% for males." | ✅ Pass | Confirms `andAll` filter on `patient_id` + `document_id` correctly scopes retrieval to the active document only. |
| TC-005 | Off-topic Detection | Who is Virat Kohli? | any | Rejects the question and displays the medical-only response. | "I'm designed to answer questions only about your uploaded medical report. Feel free to ask me about your test results, values, or what they mean!" | ✅ Pass | `is_health_related` correctly classified as off-topic. |
| TC-006 | Missing Report Information | What is my cholesterol level? | cbcreport.pdf | States value not available in report + general medical information. | "This information is not available in your uploaded medical report. The following is general medical information. Cholesterol is a waxy, fat-like substance..." | ✅ Pass | `fallback_missing_report` triggered correctly — cholesterol genuinely not in this report. |
| TC-007 | General Medical Education | What is diabetes? | any | Returns general explanation instead of searching the report. | "Diabetes is a chronic condition where your body has trouble regulating blood sugar levels..." | ✅ Pass | `fallback_general_education` triggered via `is_general_guidance_question`; never references the report. |
| TC-008 | Multi-value Synthesis (DynamoDB Cache) | What are my critical values as per report? | scannedreport.pdf | Retrieves abnormal values directly from DynamoDB without KB retrieval. | "⚠️ Platelet Count: 1550000 /cmm (HIGH) ⚠️ MCV: 72 fL (LOW) ⚠️ MCHC: 41.67 g/dL (HIGH)" | ✅ Pass | Matches ground truth exactly (MCV, MCHC, Platelet Count) — deterministic `get_stored_critical_values` path, zero hallucination risk. |
 
---
 
## Status Legend
- ✅ Pass
- ❌ Fail
- ⚠️ Partial / known limitation
## Known Limitations Summary (for README)
 
1. **Critical value extraction coverage gap** — regex + hardcoded `NORMAL_RANGES` dictionary misses any lab parameter outside ~15 predefined entries (e.g., Sodium, Potassium, Glucose on a metabolic panel), and can occasionally capture the wrong number near a parameter name. Fix: migrate to Textract `AnalyzeDocument` with `FeatureTypes=['TABLES']` for structured row/column extraction.
2. **Guardrail contextual grounding non-determinism** — intermittent false-positive interventions on correctly-grounded answers, root-caused to LLM generation variance (confirmed via controlled A/B test: Guardrail disabled → 100% consistent; enabled → intermittent). Mitigated with `temperature=0`; not eliminated without further threshold tuning — a deliberate safety/consistency trade-off.
3. **Follow-up rewrite broadening** — some follow-up phrasings (e.g., "explain it") after a broad/summary answer can still pull in unrelated values instead of narrowing to one topic, since the rewrite step relies on the single most recent exchange.
