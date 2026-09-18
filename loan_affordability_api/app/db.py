"""
PostgreSQL + JSONB storage layer (Section 1.3). Uses psycopg3's async pool.
Two write paths (raw_observations, consensus_records) and one TTL job that
purges expired raw observations *after* summarizing them into a daily rollup.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/affordability"
)

RAW_OBSERVATION_TTL_SECONDS = int(os.environ.get("RAW_TTL_SECONDS", 3600))       # 1 hour
CONSENSUS_RECORD_TTL_SECONDS = int(os.environ.get("CONSENSUS_TTL_SECONDS", 86400))  # 1 day

_pool: Optional[AsyncConnectionPool] = None


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
        await _pool.open()
    return _pool


async def init_schema() -> None:
    """Run schema.sql once at startup (idempotent — all statements are IF NOT EXISTS)."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
    with open(schema_path, "r") as f:
        ddl = f.read()
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(ddl)


async def store_raw_observation(obs) -> None:
    """obs: app.sources.RawObservation"""
    pool = await get_pool()
    ttl_expires = obs.retrieved_at + timedelta(seconds=RAW_OBSERVATION_TTL_SECONDS)
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO raw_observations
                (source_id, publisher, payload, observed_at, retrieved_at,
                 ttl_seconds, ttl_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                obs.source_id,
                obs.publisher,
                json.dumps(obs.raw_payload, default=str),
                obs.observed_at,
                obs.retrieved_at,
                RAW_OBSERVATION_TTL_SECONDS,
                ttl_expires,
            ),
        )


async def store_consensus_record(
    request_id: str,
    product_id: str,
    request_input: dict,
    consensus_summary: dict,
    response_data: dict,
    trust: dict,
    provenance: list,
    warnings: list,
    latency_ms: int,
    served_at: datetime,
) -> None:
    pool = await get_pool()
    ttl_expires = served_at + timedelta(seconds=CONSENSUS_RECORD_TTL_SECONDS)
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO consensus_records
                (request_id, product_id, request_input, consensus, response_data,
                 trust, provenance, warnings, latency_ms, served_at,
                 ttl_seconds, ttl_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request_id,
                product_id,
                json.dumps(request_input, default=str),
                json.dumps(consensus_summary, default=str),
                json.dumps(response_data, default=str),
                json.dumps(trust, default=str),
                json.dumps(provenance, default=str),
                json.dumps(warnings, default=str),
                latency_ms,
                served_at,
                CONSENSUS_RECORD_TTL_SECONDS,
                ttl_expires,
            ),
        )


async def purge_expired_raw_observations() -> dict:
    """
    TTL enforcement (Section 1.3): summarize expired rows into
    rate_daily_rollups (per source_id/day: count, avg, min, max), then delete them.
    Returns a small report dict, useful for logging / tests.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        # 1. Roll up expired rows into daily aggregates, upserting into the rollup table.
        await conn.execute(
            """
            INSERT INTO rate_daily_rollups
                (source_id, rollup_date, sample_count, avg_rate, min_rate, max_rate)
            SELECT
                source_id,
                (retrieved_at AT TIME ZONE 'UTC')::date AS rollup_date,
                COUNT(*),
                                AVG(COALESCE(payload->>'rate', payload->>'value')::numeric),
                                MIN(COALESCE(payload->>'rate', payload->>'value')::numeric),
                                MAX(COALESCE(payload->>'rate', payload->>'value')::numeric)
            FROM raw_observations
            WHERE ttl_expires_at < now()
                            AND (payload ? 'rate' OR payload ? 'value')
            GROUP BY source_id, (retrieved_at AT TIME ZONE 'UTC')::date
            ON CONFLICT (source_id, rollup_date) DO UPDATE SET
                sample_count = rate_daily_rollups.sample_count + EXCLUDED.sample_count,
                avg_rate = (rate_daily_rollups.avg_rate * rate_daily_rollups.sample_count
                            + EXCLUDED.avg_rate * EXCLUDED.sample_count)
                           / (rate_daily_rollups.sample_count + EXCLUDED.sample_count),
                min_rate = LEAST(rate_daily_rollups.min_rate, EXCLUDED.min_rate),
                max_rate = GREATEST(rate_daily_rollups.max_rate, EXCLUDED.max_rate)
            """
        )

        # 2. Purge expired detail rows from both tables.
        raw_deleted = await conn.execute(
            "DELETE FROM raw_observations WHERE ttl_expires_at < now()"
        )
        consensus_deleted = await conn.execute(
            "DELETE FROM consensus_records WHERE ttl_expires_at < now()"
        )

    return {
        "raw_observations_purged": raw_deleted.rowcount,
        "consensus_records_purged": consensus_deleted.rowcount,
        "purged_at": datetime.now(timezone.utc).isoformat(),
    }
