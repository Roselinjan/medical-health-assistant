# Medical Health Assistant

An AI-powered assistant that helps patients understand their blood test reports. Patients upload a PDF report, get an AI-generated summary with critical values automatically flagged, and can ask follow-up questions through a RAG-based chatbot — with every chat answer backed by a source citation from their own report.

Built as a hands-on AWS/AI portfolio project during a career transition into cloud and AI engineering.

**Live app:** https://medical-health-assistant-uwxy6pczvzlfsjbveosni5.streamlit.app/

## Features

* **Upload & summarize** — upload a blood report PDF, get Textract-extracted values and an AI-generated plain-language summary, with abnormal values flagged.
* **Stable patient identity** — returning patients (by email) always get the same patient ID, so their report history persists across sessions.
* **RAG-based chat** — ask questions about your report; answers are grounded in the actual uploaded document via a Bedrock Knowledge Base, not the model's general knowledge.
* **Source citations** — every KB-grounded chat answer includes an expandable "Source" section showing the exact report text the answer was based on.
* **Guardrailed fallback** — questions outside the report's scope (general health guidance) get a safe, scoped answer; questions asking for diagnosis or medication advice are blocked by Bedrock Guardrails.
* **Report caching** — re-processing an already-summarized report returns the cached result instead of re-running Textract/Nova Pro.
* **Deterministic critical values in chat** — questions like "list everything abnormal" are answered directly from pre-computed, regex-verified values in DynamoDB instead of letting the LLM compare numbers freehand, eliminating a hallucination risk found during testing.
* **Conversational memory** — follow-up questions ("is it concerning?", "why is it high?") are understood in context. Every exchange is saved to a `medical-chat-history` table, and a lightweight rewrite step resolves pronouns/references before retrieval.

## Flow

```
Patient → Streamlit
   │
   ├─► Login (get-or-create patient_id) ──► DynamoDB (medical-patients)
   │
   ├─► Upload PDF ──► Presigned URL ──► S3
   │                                     │
   │                                     ▼ (S3 Event Notification)
   │                          Textract → extract text
   │                                     │
   │                          Nova Pro → AI summary
   │                                     │
   │                     Regex/dictionary → critical values
   │                                     │
   │                     Save to DynamoDB (medical-reports)
   │                     Save .txt + metadata to S3
   │                                     │
   │                          Bedrock KB ingestion job
   │
   └─► Chat ──► API Gateway ──► Chat Handler Lambda
                                    │
                        ┌───────────┼────────────┐
                        │           │             │
                 Off-topic?    Multi-value?   Follow-up?
                 (reject)    (DynamoDB read,   (rewrite using
                              skip RAG)         last exchange)
                        │           │             │
                        └─────┬─────┘             │
                              ▼                    ▼
                    Bedrock retrieve_and_generate
                    (scoped by patient_id + document_id)
                              │
                    Guardrail (content + grounding check)
                              │
                    Sufficient context? ──No──► Fallback
                              │                (general education or
                             Yes                missing-report)
                              │
                    Answer + citations
                              │
                    Save exchange → DynamoDB (medical-chat-history)
```

## Tech Stack

| Layer | Service |
|---|---|
| Frontend | Streamlit |
| API | API Gateway (Lambda proxy integration) |
| Compute | AWS Lambda (Python) |
| OCR | Amazon Textract |
| LLM | Amazon Bedrock — Nova Pro |
| Retrieval | Bedrock Knowledge Bases (S3 Vectors backend) |
| Safety | Bedrock Guardrails |
| Storage | DynamoDB, S3 |

## Conversational Memory — Design Notes

**How it works:**
1. Every question/answer pair is saved to `medical-chat-history` (PK: `patient_id`, SK: `document_id#timestamp`).
2. A lightweight keyword check (`is_likely_followup`) flags short, pronoun-heavy questions ("it", "that", "why").
3. If flagged, a small Nova Pro call rewrites the question into a standalone form using only the single most recent exchange — e.g. *"is it concerning?"* → *"is my MCH level of 33.70 pg concerning?"*
4. The rewritten question flows through the existing RAG/fallback pipeline unchanged.

**Why this design, not the first attempt:** the initial approach injected raw history directly into the RAG prompt template, which exceeded the Guardrail's contextual-grounding query-length limit and never reached the fallback functions. Rewriting the question *before* retrieval avoided both problems with a smaller, more surgical change.

## Known Limitations

1. **Critical value extraction gaps** — regex + a hardcoded ~15-parameter dictionary can capture the wrong number or miss parameters outside the dictionary (e.g. Sodium, Potassium on a metabolic panel). Fix: Textract `AnalyzeDocument` with `FeatureTypes=['TABLES']` for structured row/column extraction.
2. **Guardrail grounding non-determinism** — the same question can occasionally trigger a false-positive Guardrail intervention due to LLM output variance. Mitigated with `temperature=0`, not eliminated.
3. **Follow-up rewrite broadening** — some follow-ups (e.g. "explain it") after a broad summary answer can still pull in unrelated values instead of narrowing to one topic.

## Planned Improvements

* Finer-grained source citations — filter cited chunks down to the relevant line rather than the full retrieved passage.
* Tamil/English language toggle for summaries and chat.
* Notifications & automation (Phase 3): SES email summaries, SNS critical-value alerts, SQS+DLQ between S3 and the report Lambda, EventBridge-driven follow-up reminders.

## Test Cases

See [`test_cases.md`](./test_cases.md) for ground-truth-verified test coverage across retrieval, memory, fallback logic, and document scoping.

## Setup

1. Clone the repo and install dependencies: `pip install -r requirements.txt`
2. Create `.streamlit/secrets.toml` with:

```
API_BASE_URL = "https://<your-api-id>.execute-api.<region>.amazonaws.com/prod"
```

3. Run locally: `python -m streamlit run app.py`
4. Backend (Lambdas, DynamoDB tables, S3 bucket, Bedrock KB/Guardrails) is provisioned separately in AWS — see the flow diagram above for the full component list.
