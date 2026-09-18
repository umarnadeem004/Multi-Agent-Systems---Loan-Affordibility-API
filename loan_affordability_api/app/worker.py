"""
Standalone TTL worker (Section 1.3: "automated process via background worker
or database triggers/CRON that purges expired records ... or summarizes
fine-grained micro-data into historical rollups").

Run separately from the API process: `python -m app.worker`
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ttl_worker")

PURGE_INTERVAL_SECONDS = 300  # run every 5 minutes


async def run_purge_job() -> None:
    report = await db.purge_expired_raw_observations()
    logger.info("TTL purge/rollup complete: %s", report)


async def main() -> None:
    await db.init_schema()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_purge_job, "interval", seconds=PURGE_INTERVAL_SECONDS)
    scheduler.start()
    logger.info("TTL worker started, purging every %ss", PURGE_INTERVAL_SECONDS)

    # Run once immediately on startup, then let the scheduler take over.
    await run_purge_job()

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
