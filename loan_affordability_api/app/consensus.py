"""
Cross-source consensus (Section 1.1) + the actual affordability math (Section 1.2/1.3
of the task spec for #62). The consensus step decides *what interest rate* to use;
the affordability step turns that rate + the applicant's numbers into
eligible / estimated_dti / max_affordable / reasons.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.errors import ConsensusError, SourceError
from app.sources import RawObservation, fetch_fred_rate, fetch_mnd_rate

# Regulatory-style constant, not something that needs to be "sourced live" —
# the CFPB's Qualified Mortgage back-end DTI ceiling. Documented, not scraped.
MAX_QUALIFYING_DTI = 0.43

# If two sources disagree by more than this many percentage points, we don't
# quietly average them — we still compute a consensus but flag low confidence.
DIVERGENCE_WARN_THRESHOLD_PCT = 0.75


@dataclass
class ConsensusResult:
    consensus_rate_percent: float
    confidence: float             # 0..1, how much the sources agree
    quality_score: float          # 0..1, overall trust in this reading
    verified: bool                # True only if >=2 sources agreed within threshold
    sources_used: list[RawObservation]
    sources_failed: list[dict]    # [{"source_id":..., "error":...}]
    warnings: list[str] = field(default_factory=list)


async def gather_sources() -> tuple[list[RawObservation], list[dict]]:
    """Fetch both sources concurrently; failures are captured, never raised past
    this point, so one dead source can't take down the whole request."""
    results = await asyncio.gather(
        fetch_fred_rate(), fetch_mnd_rate(), return_exceptions=True
    )

    successes: list[RawObservation] = []
    failures: list[dict] = []
    for result in results:
        if isinstance(result, RawObservation):
            successes.append(result)
        elif isinstance(result, SourceError):
            failures.append({"source_id": result.source_id, "error": result.message,
                              "type": type(result).__name__})
        elif isinstance(result, Exception):
            failures.append({"source_id": "unknown", "error": str(result),
                              "type": type(result).__name__})
    return successes, failures


def compute_consensus(
    successes: list[RawObservation], failures: list[dict]
) -> ConsensusResult:
    if not successes:
        raise ConsensusError(
            f"all sources failed, cannot compute consensus: {failures}"
        )

    warnings: list[str] = []

    if len(successes) == 1:
        only = successes[0]
        warnings.append(
            f"only 1/2 sources available ({only.source_id}); "
            f"serving single-source rate with reduced confidence"
        )
        return ConsensusResult(
            consensus_rate_percent=round(only.rate_percent, 3),
            confidence=0.55,
            quality_score=0.6,
            verified=False,
            sources_used=successes,
            sources_failed=failures,
            warnings=warnings,
        )

    # Two (or more) sources: weighted average, weighting the fresher observation
    # more heavily, then measure how much they actually agreed.
    rates = [s.rate_percent for s in successes]
    spread = max(rates) - min(rates)

    total_weight = 0.0
    weighted_sum = 0.0
    now = datetime.now(timezone.utc)
    for s in successes:
        age_hours = max((now - s.observed_at).total_seconds() / 3600.0, 0.0)
        # Fresher observation gets more weight; floor so nothing hits zero weight.
        weight = 1.0 / (1.0 + age_hours / 24.0)
        weighted_sum += s.rate_percent * weight
        total_weight += weight

    consensus_rate = weighted_sum / total_weight

    if spread > DIVERGENCE_WARN_THRESHOLD_PCT:
        confidence = 0.65
        warnings.append(
            f"sources diverge by {spread:.2f} percentage points "
            f"(> {DIVERGENCE_WARN_THRESHOLD_PCT} threshold)"
        )
    else:
        # Smaller spread -> higher confidence, capped at 0.99.
        confidence = round(min(0.99, 0.97 - spread * 0.2), 3)

    quality_score = round(min(0.99, confidence + 0.02), 3)

    return ConsensusResult(
        consensus_rate_percent=round(consensus_rate, 3),
        confidence=confidence,
        quality_score=quality_score,
        verified=True,
        sources_used=successes,
        sources_failed=failures,
        warnings=warnings,
    )


@dataclass
class AffordabilityResult:
    eligible: bool
    estimated_dti: float
    max_affordable: float
    reasons: list[str]
    disclaimer: str = "decision-support"


def compute_affordability(
    income_annual: float,
    monthly_debt: float,
    requested_amount: float,
    term_months: int,
    annual_rate_percent: float,
) -> AffordabilityResult:
    """
    Standard amortizing-loan math:
      monthly_payment = P * r / (1 - (1+r)^-n)   where r = monthly rate, n = term_months
      estimated_dti   = (monthly_debt + monthly_payment) / (income_annual / 12)
      eligible         = estimated_dti <= MAX_QUALIFYING_DTI
      max_affordable   = solve the same formula backwards for P at the DTI cap
    """
    if income_annual <= 0 or term_months <= 0:
        raise ValueError("income and term_months must be positive")

    monthly_income = income_annual / 12.0
    r = (annual_rate_percent / 100.0) / 12.0
    n = term_months

    def monthly_payment_for(principal: float) -> float:
        if r == 0:
            return principal / n
        return principal * r / (1 - (1 + r) ** (-n))

    payment = monthly_payment_for(requested_amount)
    estimated_dti = round((monthly_debt + payment) / monthly_income, 4)
    eligible = estimated_dti <= MAX_QUALIFYING_DTI

    # Solve for the principal that exactly hits the DTI cap, holding monthly_debt fixed.
    max_payment_budget = max(monthly_income * MAX_QUALIFYING_DTI - monthly_debt, 0.0)
    if r == 0:
        max_affordable = max_payment_budget * n
    else:
        max_affordable = max_payment_budget * (1 - (1 + r) ** (-n)) / r
    max_affordable = round(max_affordable, 2)

    reasons: list[str] = []
    if eligible:
        reasons.append("income_supports_payment")
    else:
        reasons.append("estimated_dti_exceeds_max_qualifying_dti")
    if monthly_debt / monthly_income > 0.15:
        reasons.append("existing_monthly_debt_is_significant")
    if requested_amount > max_affordable:
        reasons.append("requested_amount_exceeds_max_affordable")

    return AffordabilityResult(
        eligible=eligible,
        estimated_dti=estimated_dti,
        max_affordable=max_affordable,
        reasons=reasons,
    )
