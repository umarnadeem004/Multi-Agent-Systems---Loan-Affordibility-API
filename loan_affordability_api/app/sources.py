"""
Live micro-data ingestion for two distinct, independent providers of the
30-year fixed mortgage interest rate. This rate is the key live input that
feeds the loan-affordability consensus calculation (Section 1.1 of the
assignment spec).

Source A — FRED (Federal Reserve Economic Data), series MORTGAGE30US.
    Official US government data, published weekly. Clean JSON API.
    Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html

Source B — Mortgage News Daily's "Daily Rate Index" page.
    Independent industry survey, published on business days (~4pm EST).
    No public API, so this is a live HTML scrape.
    https://www.mortgagenewsdaily.com/mortgage-rates

Both fetchers return a RawObservation with full provenance metadata and
raise a typed SourceError subclass on any failure — nothing here ever
returns "0.0" or a cached value in place of a real failure.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from app.errors import SourceRateLimitError, SourceSchemaError, SourceUnavailableError

load_dotenv()

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_SERIES_ID = "MORTGAGE30US"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

MND_URL = "https://www.mortgagenewsdaily.com/mortgage-rates"

HTTP_TIMEOUT_SECONDS = 6.0


@dataclass
class RawObservation:
    """A single source's raw reading, with everything needed for provenance/audit."""

    source_id: str
    publisher: str
    rate_percent: float          # e.g. 6.98 meaning 6.98%
    observed_at: datetime        # when the *underlying* rate was recorded/published
    retrieved_at: datetime       # when *we* fetched it (now)
    raw_payload: dict            # exact raw ingest sample, stored as-is for audit


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_fred_rate() -> RawObservation:
    """Fetch the most recent 30yr fixed mortgage rate observation from FRED."""
    if not FRED_API_KEY:
        # Missing credentials is a config problem, not a silent-fallback situation —
        # raise explicitly rather than returning a fabricated/default rate.
        raise SourceUnavailableError("SRC-FRED-01", "FRED_API_KEY is not configured")

    params = {
        "series_id": FRED_SERIES_ID,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(FRED_URL, params=params)
    except httpx.TimeoutException as exc:
        raise SourceUnavailableError("SRC-FRED-01", f"request timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise SourceUnavailableError("SRC-FRED-01", f"network error: {exc}") from exc

    if resp.status_code == 429:
        raise SourceRateLimitError("SRC-FRED-01", "rate limited by FRED (HTTP 429)")
    if resp.status_code != 200:
        raise SourceUnavailableError(
            "SRC-FRED-01", f"unexpected HTTP status {resp.status_code}"
        )

    try:
        payload = resp.json()
        obs = payload["observations"][0]
        rate_str = obs["value"]
        date_str = obs["date"]  # "YYYY-MM-DD"
        if rate_str == ".":
            # FRED uses "." for a missing observation — this is a real, documented
            # schema case, not a network failure, so it gets its own explicit error.
            raise KeyError("missing observation value")
        rate_percent = float(rate_str)
        observed_at = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise SourceSchemaError(
            "SRC-FRED-01", f"unexpected response shape from FRED: {exc}"
        ) from exc

    return RawObservation(
        source_id="SRC-FRED-01",
        publisher="Federal Reserve Economic Data (FRED) - MORTGAGE30US",
        rate_percent=rate_percent,
        observed_at=observed_at,
        retrieved_at=_now(),
        raw_payload=obs,
    )


# Matches e.g. "30 Yr. Fixed 7.22% Change: +0.05" in the page's extracted text,
# and is deliberately tolerant of the exact punctuation/spacing around it since
# this is a live scrape and MND can change their markup without notice.
_MND_RATE_PATTERN = re.compile(
    r"30\s*Yr\.?\s*Fixed\D{0,20}?(\d{1,2}\.\d{1,3})\s*%", re.IGNORECASE
)


async def fetch_mnd_rate() -> RawObservation:
    """Scrape the current 30yr fixed rate off MND's daily rate index page."""
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AI4012-affordability-bot/1.0)"},
        ) as client:
            resp = await client.get(MND_URL)
    except httpx.TimeoutException as exc:
        raise SourceUnavailableError("SRC-MND-01", f"request timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise SourceUnavailableError("SRC-MND-01", f"network error: {exc}") from exc

    if resp.status_code == 429:
        raise SourceRateLimitError("SRC-MND-01", "rate limited by MND (HTTP 429)")
    if resp.status_code != 200:
        raise SourceUnavailableError(
            "SRC-MND-01", f"unexpected HTTP status {resp.status_code}"
        )

    soup = BeautifulSoup(resp.text, "html.parser")
    page_text = soup.get_text(separator=" ")

    match = _MND_RATE_PATTERN.search(page_text)
    if not match:
        # The page structure/wording changed and our scraper no longer finds the
        # figure it expects — this is exactly the "bad schema" case the spec
        # requires us to surface explicitly rather than guessing or reusing an
        # old cached value.
        raise SourceSchemaError(
            "SRC-MND-01", "could not locate 30yr fixed rate in page content"
        )

    rate_percent = float(match.group(1))

    return RawObservation(
        source_id="SRC-MND-01",
        publisher="Mortgage News Daily - Daily Rate Index",
        rate_percent=rate_percent,
        observed_at=_now(),  # MND doesn't expose a machine-readable publish timestamp
        retrieved_at=_now(),
        raw_payload={"matched_text": match.group(0), "rate": rate_percent},
    )
