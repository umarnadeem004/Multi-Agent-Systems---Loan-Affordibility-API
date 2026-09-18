"""
SLA & Reliability tests (Section 1.4):
  - API availability under repeated polling
  - p95 latency < 200ms
  - Freshness SLA: age_seconds <= ttl_seconds always holds
  - Source failover: one provider down -> still 200, degraded confidence, no crash

All tests mock the two upstream sources and the DB layer so they run fully
offline/deterministically — no real network or Postgres required in CI.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app import db
from app.errors import SourceUnavailableError
from app.main import app
from app.sources import RawObservation

VALID_PAYLOAD = {
    "income": 120000,
    "monthly_debt": 2500,
    "requested_amount": 300000,
    "term_months": 360,
}


def _fake_observation(source_id: str, publisher: str, rate: float) -> RawObservation:
    now = datetime.now(timezone.utc)
    return RawObservation(
        source_id=source_id,
        publisher=publisher,
        rate_percent=rate,
        observed_at=now,
        retrieved_at=now,
        raw_payload={"rate": rate},
    )


@pytest.fixture(autouse=True)
def patch_db(monkeypatch):
    """No real Postgres needed for these tests — no-op the storage calls."""
    async def _noop_init_schema():
        return None

    async def _noop_store_raw(obs):
        return None

    async def _noop_store_consensus(**kwargs):
        return None

    monkeypatch.setattr(db, "init_schema", _noop_init_schema)
    monkeypatch.setattr(db, "store_raw_observation", _noop_store_raw)
    monkeypatch.setattr(db, "store_consensus_record", _noop_store_consensus)


@pytest.fixture
def patch_both_sources_healthy(monkeypatch):
    async def fake_fred():
        return _fake_observation("SRC-FRED-01", "FRED", 6.90)

    async def fake_mnd():
        return _fake_observation("SRC-MND-01", "Mortgage News Daily", 6.95)

    monkeypatch.setattr("app.consensus.fetch_fred_rate", fake_fred)
    monkeypatch.setattr("app.consensus.fetch_mnd_rate", fake_mnd)


@pytest.fixture
def patch_one_source_down(monkeypatch):
    async def fake_fred():
        return _fake_observation("SRC-FRED-01", "FRED", 6.90)

    async def failing_mnd():
        raise SourceUnavailableError("SRC-MND-01", "simulated outage")

    monkeypatch.setattr("app.consensus.fetch_fred_rate", fake_fred)
    monkeypatch.setattr("app.consensus.fetch_mnd_rate", failing_mnd)


async def _post_affordability(payload=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/v1/credit/affordability", json=payload or VALID_PAYLOAD
        )


@pytest.mark.asyncio
async def test_availability_under_repeated_polling(patch_both_sources_healthy):
    """API stays available across a burst of sequential requests."""
    for _ in range(20):
        resp = await _post_affordability()
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_p95_latency_under_200ms(patch_both_sources_healthy):
    durations_ms = []
    for _ in range(30):
        start = time.perf_counter()
        resp = await _post_affordability()
        durations_ms.append((time.perf_counter() - start) * 1000)
        assert resp.status_code == 200

    durations_ms.sort()
    p95_index = int(len(durations_ms) * 0.95) - 1
    p95 = durations_ms[p95_index]
    assert p95 < 200, f"p95 latency {p95:.1f}ms exceeded 200ms SLA"


@pytest.mark.asyncio
async def test_freshness_sla_always_holds(patch_both_sources_healthy):
    resp = await _post_affordability()
    body = resp.json()
    freshness = body["meta"]["freshness"]
    assert freshness["age_seconds"] <= freshness["ttl_seconds"]
    assert freshness["stale"] is False


@pytest.mark.asyncio
async def test_source_failover_degrades_gracefully(patch_one_source_down):
    """When one provider is down, the API must still return 200 with a
    lower-confidence, unverified consensus — never crash, never silently
    pretend both sources agreed."""
    resp = await _post_affordability()
    assert resp.status_code == 200

    body = resp.json()
    assert body["meta"]["trust"]["verified"] is False
    assert body["meta"]["trust"]["confidence"] < 0.9
    assert any("only 1/2 sources" in w for w in body["meta"]["warnings"])


@pytest.mark.asyncio
async def test_response_matches_canonical_envelope(patch_both_sources_healthy):
    resp = await _post_affordability()
    body = resp.json()

    assert set(body.keys()) == {"data", "meta"}
    for field in ("eligible", "estimated_dti", "max_affordable", "reasons", "disclaimer"):
        assert field in body["data"]
    for field in (
        "request_id", "product_id", "version", "served_at",
        "source_last_updated_at", "freshness", "provenance",
        "trust", "license", "api", "warnings",
    ):
        assert field in body["meta"]


@pytest.mark.asyncio
async def test_affordability_math_is_sane(patch_both_sources_healthy):
    """Sanity check against the sample from the task spec (not an exact
    reproduction, since the consensus rate is mocked at 6.9-6.95%, but the
    shape/direction of the numbers should make sense)."""
    resp = await _post_affordability()
    data = resp.json()["data"]

    assert isinstance(data["eligible"], bool)
    assert 0 <= data["estimated_dti"] <= 3
    assert data["max_affordable"] >= 0
    assert isinstance(data["reasons"], list) and len(data["reasons"]) > 0
