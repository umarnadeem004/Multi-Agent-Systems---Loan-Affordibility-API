"""
Explicit error types for the ingestion layer.

The assignment spec forbids silent fallbacks: if a source fails, that
failure must be raised as a clean, typed error and surfaced to the
caller (never swallowed and quietly replaced with stale/cached data).
"""


class SourceError(Exception):
    """Base class for anything that goes wrong talking to an upstream source."""

    def __init__(self, source_id: str, message: str):
        self.source_id = source_id
        self.message = message
        super().__init__(f"[{source_id}] {message}")


class SourceRateLimitError(SourceError):
    """Upstream provider returned HTTP 429 / throttled us."""


class SourceUnavailableError(SourceError):
    """Upstream provider is down, timed out, or unreachable (network-level)."""


class SourceSchemaError(SourceError):
    """Upstream provider responded, but the payload didn't match what we expect
    (e.g. the scraped page's HTML structure changed, or the API's JSON shape changed).
    This is deliberately a *different* error class from SourceUnavailableError so
    the caller can tell 'they're down' apart from 'they changed their format on us'.
    """


class ConsensusError(Exception):
    """Raised when consensus cannot be computed at all (e.g. both sources failed)."""
