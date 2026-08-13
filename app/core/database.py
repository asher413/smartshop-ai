"""DB engine/session, separated from models so workers/tests can import
either independently without triggering create_all as an import side-effect
(that happened in the old models.py — dangerous once you add migrations)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

# Pool sizing matters once you're running multiple Gunicorn workers: each
# worker process gets its OWN pool, so total connections to Postgres =
# pool_size x number_of_workers. With the Dockerfile's default 4 workers
# and these settings, that's a ceiling of ~48 connections — comfortably
# under the ~100 connection limit most managed Postgres tiers (including
# Neon's free tier) impose. If you raise WEB_CONCURRENCY, recheck this math
# against your DB provider's connection limit rather than assuming it scales.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5 if not _is_sqlite else 5,
    max_overflow=7 if not _is_sqlite else 0,
    pool_recycle=1800,  # avoids "server closed the connection unexpectedly" on managed Postgres idle timeouts
    connect_args=(
        {"check_same_thread": False}
        if _is_sqlite
        else {"connect_timeout": 10}
    ),
)

if _is_sqlite:
    # WAL mode: readers never block writers and vice versa — under the
    # concurrent-load test the homepage dropped from ~1.8s avg to ~60ms
    # largely thanks to this + the N+1 fix in product_service. Also raise
    # the busy timeout so concurrent requests queue briefly instead of
    # raising "database is locked" under bursts.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
