"""DB init helper (dev/test). Production should use Alembic migrations."""
from __future__ import annotations

from app.db.base import Base
from app.db.session import get_engine

# import models so metadata is populated
import app.db.models  # noqa


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
