# Backup & recovery (Phases 0–1)

## What to back up
- SQLite file (`DATABASE_URL`, default `./defect_tracker.db` + `-wal/-shm` if present)
- `storage/uploads/` (must be restored together with DB)
- `.env` / config needed to run (store securely, never commit secrets)

## Commands
```bash
python scripts/backup.py --out ./backups/dts-2026-10-14.tgz
python scripts/restore.py --in ./backups/dts-2026-10-14.tgz --confirm
alembic upgrade head
python scripts/verify_db.py
```

## RPO / RTO (to agree with stakeholders)
- Suggested initial: RPO ≤ 24h (daily backup), RTO ≤ 2h (restore + verify + restart). Adjust per business need; record decision here.
- Retention: keep 7 daily + 4 weekly (encrypted, access-restricted). Encrypt backups at rest.

## Drills
- Quarterly restore to clean dir + `alembic upgrade head` + login smoke + count compare. Record results.
