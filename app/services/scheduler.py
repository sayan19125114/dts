"""Phase 4 background worker.

Two independent passes, both idempotent and retry-safe:
* SLA pass: evaluate every live SLA instance (warnings/breaches/escalations).
* Export pass: process queued export jobs.

A single daemon thread drives them every ``settings.sla_tick_seconds`` when
``settings.sla_scheduler_enabled`` is true. The same passes are exposed via
``POST /api/v1/admin/sla/run`` (and happen opportunistically elsewhere), so the
thread is a convenience, never a correctness requirement.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Bug, BugSla
from app.db.session import get_session_factory
from app.services import sla as sla_service

log = logging.getLogger(__name__)


def run_sla_pass(db: Session) -> dict:
    rows = db.execute(select(BugSla)).scalars().all()
    evaluated = 0
    emitted = 0
    for row in rows:
        bug = db.get(Bug, row.bug_id)
        if bug is None or bug.is_archived:
            continue
        if row.resolved_at is not None and not row.is_paused:
            continue
        try:
            flags = sla_service.evaluate_bug(db, bug)
            emitted += sum(1 for v in (flags or {}).values() if v.get("breached") or v.get("warned"))
            evaluated += 1
        except Exception:  # pragma: no cover - keep the pass resilient
            log.exception("sla evaluate failed for bug %s", row.bug_id)
    db.commit()
    return {"evaluated": evaluated, "flagged": emitted}


def run_export_pass(db: Session) -> dict:
    from app.services.exports import process_pending

    if not get_settings().feature_async_exports:
        return {"processed": 0}
    return {"processed": process_pending(db)}


def run_all(db: Session) -> dict:
    return {"sla": run_sla_pass(db), "exports": run_export_pass(db)}


class SlaScheduler:
    def __init__(self, interval_seconds: int | None = None):
        settings = get_settings()
        self.interval = interval_seconds or settings.sla_tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sla-scheduler", daemon=True)
        self._thread.start()
        log.info("SLA scheduler started (interval=%ss)", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("SLA scheduler stopped")

    def _loop(self) -> None:
        factory = get_session_factory()
        while not self._stop.wait(self.interval):
            db = factory()
            try:
                run_all(db)
            except Exception:  # pragma: no cover
                log.exception("scheduler tick failed")
                db.rollback()
            finally:
                db.close()


_scheduler: SlaScheduler | None = None


def start_scheduler() -> SlaScheduler | None:
    global _scheduler
    if not get_settings().sla_scheduler_enabled:
        return None
    if _scheduler is None:
        _scheduler = SlaScheduler()
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
