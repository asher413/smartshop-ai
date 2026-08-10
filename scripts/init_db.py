"""Run once to create tables in dev. Use Alembic migrations for production."""
import sys
from pathlib import Path

# Make `python scripts/init_db.py` work regardless of CWD / sys.path:
# the project root must be importable for `app.*` packages to resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import engine
from app.core.models import Base

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tables created.")
