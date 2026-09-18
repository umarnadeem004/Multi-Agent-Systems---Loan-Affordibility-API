"""
POST /v1/credit/affordability

Implements the canonical agent-servicing envelope from Section 3 of the
assignment spec, adapted to task #62's data/meta fields:

    { "data": {...task-specific fields...}, "meta": {...lineage/freshness/trust...} }
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.consensus import (
    ConsensusError,
    compute_affordability,
    compute_consensus,
    gather_sources,
)

PRODUCT_ID = "credit.affordability.v1"
API_VERSION = "1.0.0"
RESPONSE_TTL_SECONDS = 3600
RATE_LIMIT_PER_WINDOW = 100
RATE_LIMIT_WINDOW_SECONDS = 60


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await db.init_schema()
    yield


app = FastAPI(title="Loan Affordability Agent API", lifespan=lifespan)


class AffordabilityRequest(BaseModel):
    income: float = Field(..., gt=0, description="Applicant's gross annual income")
    monthly_debt: float = Field(..., ge=0, description="Existing monthly debt obligations")
    requested_amount: float = Field(..., gt=0, description="Requested loan principal")
    term_months: int = Field(..., gt=0, description="Loan term in months")


@app.post("/v1/credit/affordability")
async def affordability(req: AffordabilityRequest):
    start = time.perf_counter()
    request_id = "req_" + uuid.uuid4().hex[:20]

    # --- 1. Live multi-source ingestion + consensus on the interest rate ---
    successes, failures = await gather_sources()
    for obs in successes:
        await db.store_raw_observation(obs)

    try:
        consensus = compute_consensus(successes, failures)
    except ConsensusError as exc:
        # No silent fallback: if every source failed, we fail the request loudly
        # with a clean error structure instead of guessing a rate.
        raise HTTPException(
            status_code=502,
            detail={
                "error": "consensus_unavailable",
                "message": str(exc),
                "sources_failed": failures,
            },
        )

    # --- 2. Run the affordability calculation against the consensus rate ---
    result = compute_affordability(
        income_annual=req.income,
        monthly_debt=req.monthly_debt,
        requested_amount=req.requested_amount,
        term_months=req.term_months,
        annual_rate_percent=consensus.consensus_rate_percent,
    )

    served_at = datetime.now(timezone.utc)
    latest_source_update = max(s.retrieved_at for s in consensus.sources_used)
    age_seconds = int((served_at - latest_source_update).total_seconds())

    data_block = {
        "eligible": result.eligible,
        "estimated_dti": result.estimated_dti,
        "max_affordable": result.max_affordable,
        "reasons": result.reasons,
        "disclaimer": result.disclaimer,
        # extra transparency fields, harmless additions to the required shape
        "consensus_rate_percent": consensus.consensus_rate_percent,
    }

    provenance = [
        {
            "source_id": s.source_id,
            "publisher": s.publisher,
            "retrieved_at": s.retrieved_at.isoformat(),
        }
        for s in consensus.sources_used
    ]

    latency_ms = int((time.perf_counter() - start) * 1000)

    response = {
        "data": data_block,
        "meta": {
            "request_id": request_id,
            "product_id": PRODUCT_ID,
            "version": API_VERSION,
            "served_at": served_at.isoformat(),
            "source_last_updated_at": latest_source_update.isoformat(),
            "freshness": {
                "age_seconds": age_seconds,
                "ttl_seconds": RESPONSE_TTL_SECONDS,
                "stale": age_seconds > RESPONSE_TTL_SECONDS,
            },
            "provenance": provenance,
            "trust": {
                "confidence": consensus.confidence,
                "quality_score": consensus.quality_score,
                "verified": consensus.verified,
            },
            "license": {"type": "commercial", "usage": "agent_runtime"},
            "api": {
                "latency_ms": latency_ms,
                "rate_limit": {
                    "limit": RATE_LIMIT_PER_WINDOW,
                    "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
                },
            },
            "warnings": consensus.warnings
            + [f"source_failed: {f['source_id']} ({f['type']})" for f in failures],
        },
    }

    await db.store_consensus_record(
        request_id=request_id,
        product_id=PRODUCT_ID,
        request_input=req.model_dump(),
        consensus_summary={
            "rate_percent": consensus.consensus_rate_percent,
            "confidence": consensus.confidence,
            "verified": consensus.verified,
        },
        response_data=data_block,
        trust=response["meta"]["trust"],
        provenance=provenance,
        warnings=response["meta"]["warnings"],
        latency_ms=latency_ms,
        served_at=served_at,
    )

    return response


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
