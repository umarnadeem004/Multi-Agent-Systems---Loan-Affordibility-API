# Loan Affordability Agent API — AI4012 Assignment #1 (Task #62)

`POST /v1/credit/affordability` — estimates loan affordability from applicant
inputs, backed by a live two-source consensus on the current 30-year fixed
mortgage interest rate.

## Architecture (one line each)

1. **`app/sources.py`** — fetches the live rate from two independent providers:
   FRED's official API, and a scrape of Mortgage News Daily's rate index page.
2. **`app/consensus.py`** — reconciles the two readings into one consensus rate
   + confidence/quality score (Section 1.1), then runs the actual affordability
   math (amortization → DTI → eligibility → max affordable).
3. **`app/db.py` + `schema.sql`** — PostgreSQL/JSONB storage for raw
   observations and full consensus/audit records, each with a TTL.
4. **`app/worker.py`** — background job that rolls up expired raw observations
   into `rate_daily_rollups` and purges them (Section 1.3).
5. **`app/main.py`** — the FastAPI endpoint, returning the canonical
   `data` / `meta` envelope from Section 3 of the spec.
6. **`tests/test_api.py`** — SLA suite: availability, p95 latency, freshness,
   source failover (Section 1.4). Fully mocked/offline, no live network or DB
   needed to run it.

## Setup (PowerShell)

```powershell
cd loan_affordability_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

Copy-Item .env.example .env
# Open .env and replace the FRED_API_KEY placeholder with your new key.

docker compose up -d postgres
```

The FRED key must remain in `.env`; never paste it into source code or commit it.

## Run

```powershell
# terminal 1 - run the API
cd loan_affordability_api
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

```powershell
# terminal 2 - run the TTL worker
cd loan_affordability_api
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

```powershell
# terminal 3 - verify the API is alive
Invoke-RestMethod http://127.0.0.1:8000/healthz

# call the affordability endpoint
$body = @{ income = 120000; monthly_debt = 2500; requested_amount = 300000; term_months = 360 } | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/v1/credit/affordability -Method Post -ContentType 'application/json' -Body $body
```

## Tests

```bash
pytest tests/ -v
```

## Known limitations (be upfront about these in your report)

- The Mortgage News Daily scraper (`sources.py::fetch_mnd_rate`) parses live
  page text with a regex, since MND has no public API. If they change their
  page wording, the scraper will correctly raise `SourceSchemaError` (per the
  "no silent fallbacks" rule) rather than return a wrong number — but you
  should re-check the regex against the live page before your demo.
- `MAX_QUALIFYING_DTI` (0.43) is a fixed constant based on the CFPB's
  Qualified Mortgage back-end DTI threshold — documented as a regulatory
  reference, not something pulled live, since it doesn't change day to day.
- FRED updates weekly, so under normal conditions the two sources will show
  a small, real spread — this is expected and is exactly what the confidence
  score should reflect, not a bug.
