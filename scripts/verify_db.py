"""Verify DB counts/relationships. Usage: python scripts/verify_db.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.base import Base
import app.db.models as m  # noqa
from app.db.session import get_engine, get_session_factory


def main() -> int:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)  # no-op if migrated; ensures tables exist for check
    S = get_session_factory()
    db = S()
    try:
        users = db.execute(select(func.count()).select_from(m.User)).scalar_one()
        projects = db.execute(select(func.count()).select_from(m.Project)).scalar_one()
        bugs = db.execute(select(func.count()).select_from(m.Bug)).scalar_one()
        comments = db.execute(select(func.count()).select_from(m.Comment)).scalar_one()
        atts = db.execute(select(func.count()).select_from(m.Attachment)).scalar_one()
        print(f"users={users} projects={projects} bugs={bugs} comments={comments} attachments={atts}")
        # orphan checks (FKs should prevent, but verify)
        orphans = db.execute(select(func.count()).select_from(m.Bug).where(m.Bug.project_id.notin_(select(m.Project.id)))).scalar_one()
        print(f"orphan_bugs={orphans}")
        # new tables if present
        for tbl in (
            "audit_logs", "notifications", "saved_views", "bug_links", "bug_watchers",
            "sla_policies", "bug_sla", "sla_events", "releases", "release_gates", "export_jobs",
        ):
            try:
                n = db.execute(select(func.count()).select_from(Base.metadata.tables[tbl])).scalar_one()
                print(f"{tbl}={n}")
            except Exception:
                print(f"{tbl}=n/a")
        return 0 if orphans == 0 else 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
