# Loan Affordability API - Restart Guide

This file stores the important steps needed to restart the project after a laptop restart.

## 1) Start Docker Desktop
Open Docker Desktop and make sure it is running.

## 2) Start PostgreSQL database
Open PowerShell and run:

```powershell
cd "C:\Users\umar\OneDrive\Desktop\Semester 07\Multi Agent Systems\A01\loan_affordability_api"
docker compose up -d postgres
```

## 3) Start the API
Run:

```powershell
cd "C:\Users\umar\OneDrive\Desktop\Semester 07\Multi Agent Systems\A01\loan_affordability_api"
python -m uvicorn app.main:app --reload
```

## 4) Test the API
Open in browser:

```text
http://127.0.0.1:8000/docs
```

Or use PowerShell:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/healthz" -Method Get
```

## 5) Test the affordability endpoint
Use Swagger UI at `/docs` and send sample data:

```json
{
  "income": 85000,
  "monthly_debt": 400,
  "requested_amount": 250000,
  "term_months": 360
}
```

## Useful notes
- Docker must be running before the DB starts.
- The project expects a local `.env` file with the FRED API key.
- The app is a FastAPI REST API for loan affordability.
- The project was built to fetch live mortgage data from multiple sources and calculate affordability.

## Quick version

```powershell
cd "C:\Users\umar\OneDrive\Desktop\Semester 07\Multi Agent Systems\A01\loan_affordability_api"
docker compose up -d postgres
python -m uvicorn app.main:app --reload
```

If you want to stop later:

```powershell
docker compose down
```

If you want to stop just the DB without removing data:

```powershell
docker compose stop
```
