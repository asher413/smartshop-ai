"""
In-process scheduler (APScheduler) that keeps the catalog fresh with zero
external infra. Good enough for one server; if you outgrow it, swap for
an RQ/Celery beat schedule without touching auto_import_worker.py itself.

Start alongside the API with: python -m app.workers.scheduler
(or run it as a separate container/process — recommended in production so
a slow scrape never blocks a web request).
"""
import logging
from apscheduler.schedulers.blocking import BlockingScheduler

from app.workers.auto_import_worker import run_discovery_cycle, enrich_pending_products
from app.core.database import SessionLocal
from app.services.price_monitor_service import record_daily_prices, check_price_alerts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="Asia/Jerusalem")


@scheduler.scheduled_job("interval", hours=2, id="discover_trending")
def discovery_job():
    logger.info("Running discovery job...")
    result = run_discovery_cycle()
    logger.info("Discovery result: %s", result)


@scheduler.scheduled_job("interval", minutes=15, id="enrich_products")
def enrichment_job():
    logger.info("Running enrichment job...")
    count = enrich_pending_products(batch_size=25)
    logger.info("Enriched %s products", count)


@scheduler.scheduled_job("interval", hours=6, id="price_monitor")
def price_monitor_job():
    """Snapshots today's prices for the history chart, then checks every
    pending price alert against the (possibly just-refreshed) price."""
    logger.info("Running price monitor job...")
    db = SessionLocal()
    try:
        snapshot_count = record_daily_prices(db)
        triggered = check_price_alerts(db)
        logger.info("Recorded %s price snapshots, %s alerts triggered", snapshot_count, len(triggered))
    finally:
        db.close()


@scheduler.scheduled_job("interval", hours=24, id="interest_pull_cleanup")
def interest_pull_cleanup_job():
    """Daily reversal of interest-driven pulls nobody engaged with: any
    product pulled purely because one visitor browsed a related product —
    with zero clicks/views/favorites for 3 days — is deactivated so a
    single browse can't permanently bloat the catalog."""
    logger.info("Running interest-pull cleanup job...")
    from app.services.interest_pull_service import cleanup_stale_pulls
    db = SessionLocal()
    try:
        result = cleanup_stale_pulls(db, max_age_days=3)
        logger.info("Interest-pull cleanup: %s", result)
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("Starting DealBursa background scheduler...")
    scheduler.start()
