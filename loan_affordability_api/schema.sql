-- Storage Engine & Lifecycle Management (Section 1.3)
-- Three tables: raw per-source payloads, computed consensus+audit records,
-- and a rollup table that TTL purging summarizes into before deleting detail rows.

CREATE TABLE IF NOT EXISTS raw_observations (
    id              BIGSERIAL PRIMARY KEY,
    source_id       TEXT        NOT NULL,
    publisher       TEXT        NOT NULL,
    payload         JSONB       NOT NULL,   -- exact raw ingest sample from the provider
    observed_at     TIMESTAMPTZ NOT NULL,   -- when the underlying value was published
    retrieved_at    TIMESTAMPTZ NOT NULL,   -- when we fetched it
    ttl_seconds     INTEGER     NOT NULL DEFAULT 3600,
    ttl_expires_at  TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_raw_observations_ttl
    ON raw_observations (ttl_expires_at);
CREATE INDEX IF NOT EXISTS idx_raw_observations_source
    ON raw_observations (source_id, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_observations_payload_gin
    ON raw_observations USING GIN (payload);


CREATE TABLE IF NOT EXISTS consensus_records (
    id                    BIGSERIAL PRIMARY KEY,
    request_id            TEXT        NOT NULL,
    product_id            TEXT        NOT NULL,   -- e.g. "credit.affordability.v1"
    request_input         JSONB       NOT NULL,   -- applicant's raw POST body
    consensus             JSONB       NOT NULL,   -- rate, confidence, quality_score, verified
    response_data         JSONB       NOT NULL,   -- the "data" block actually served
    trust                 JSONB       NOT NULL,
    provenance            JSONB       NOT NULL,   -- array of {source_id, publisher, retrieved_at}
    warnings              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    latency_ms            INTEGER,
    served_at             TIMESTAMPTZ NOT NULL,
    ttl_seconds           INTEGER     NOT NULL DEFAULT 3600,
    ttl_expires_at        TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_consensus_records_ttl
    ON consensus_records (ttl_expires_at);
CREATE INDEX IF NOT EXISTS idx_consensus_records_request
    ON consensus_records (request_id);
CREATE INDEX IF NOT EXISTS idx_consensus_records_data_gin
    ON consensus_records USING GIN (response_data);


-- Where expired raw_observations get summarized into instead of just deleted,
-- satisfying "summarizes fine-grained micro-data into historical rollups".
CREATE TABLE IF NOT EXISTS rate_daily_rollups (
    id              BIGSERIAL PRIMARY KEY,
    source_id       TEXT        NOT NULL,
    rollup_date     DATE        NOT NULL,
    sample_count    INTEGER     NOT NULL,
    avg_rate        NUMERIC(6,3) NOT NULL,
    min_rate        NUMERIC(6,3) NOT NULL,
    max_rate        NUMERIC(6,3) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, rollup_date)
);
