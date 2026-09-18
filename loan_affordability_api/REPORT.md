# Credit Affordability Agent API Report

## 1. System Architecture

The implementation follows the assigned catalog task, `POST /v1/credit/affordability`. The request accepts annual income, existing monthly debt, requested principal, and term in months. The service obtains the current 30-year fixed mortgage rate from two independent providers: FRED's `MORTGAGE30US` series and Mortgage News Daily's rate index page.

The two source calls run concurrently. Each source returns its identifier, publisher, raw payload, observed time, and retrieval time. Network failures, rate limits, and schema changes become typed source errors. A failed source is listed in the response warnings; an all-source failure returns a structured `502` response. No stale or fabricated value is substituted.

The consensus layer computes a freshness-weighted average. It calculates confidence from source agreement and marks the result `verified` only when both sources are available and their spread is within the configured tolerance. The consensus rate is then used in the standard amortization formula. Estimated DTI is `(monthly debt + monthly payment) / monthly income`; eligibility is true at or below the configured 43% threshold. Maximum affordable principal is solved from the same amortization formula at that threshold.

FastAPI serves the canonical `data` and `meta` response envelope. The metadata includes request ID, product version, source timestamps, freshness, provenance, trust metrics, license, latency, rate limit, and warnings. PostgreSQL stores raw payloads and consensus/audit records in JSONB columns with GIN indexes. A separate worker summarizes expired raw rate observations into daily rollups before deleting expired raw and consensus rows.

The tests mock the network and database boundaries so they are deterministic and offline. They cover repeated availability, p95 latency below 200 ms, freshness, one-source failover, response shape, and calculation sanity.

## 2. Use of AI

This report must be completed honestly before submission with the actual tools and prompts used by the student. Record the model or assistant name, the date, and the prompts that materially influenced the implementation. Do not claim that an assistant performed tests, design decisions, or source verification that you performed yourself.

Suggested record format:

| Item | Student record |
| --- | --- |
| Model/tool | Add the model or coding assistant actually used |
| Date | Add the date of use |
| Main prompt | Paste the actual prompt used for architecture or implementation help |
| Verification | Explain which code, tests, and calculations you personally checked |
| Changes made after review | List corrections made after testing |

The final submission should also state that the live provider behavior, FRED credentials, source licenses, SQL schema, and SLA measurements were independently checked before submission.