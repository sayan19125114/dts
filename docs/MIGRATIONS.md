# Migration convention (Phase 0)

## Rules
1. Each migration has unique ID (`0002_<name>`) and clear purpose header.
2. Migrations are additive first: add nullable columns/tables, backfill, verify — never drop in same release.
3. Idempotent/safe to run once: use `IF NOT EXISTS`-style checks where Alembic allows, guard backfills by null checks.
4. No destructive changes in routine migrations. Drops require separate approved release.
5. Backup before migrate; test restore; log migration output.

## Procedure
```bash
# 1. backup
python scripts/backup.py --out ./backups/pre-0002.tgz
# 2. dry-run / review
alembic upgrade head --sql | head -n 100
# 3. migrate
alembic upgrade head
# 4. verify
python scripts/verify_db.py
# 5. rollback plan: restore backup (forward-fix preferred once data written)
python scripts/restore.py --in ./backups/pre-0002.tgz --confirm
```

## Pattern for schema change
1. Add nullable/new structures.
2. Deploy code reading old+new.
3. Backfill batches, record progress.
4. Verify counts/nulls/relationships/samples.
5. Switch reads/writes; monitor.
6. Make required/remove old only later.

## Logging
- Alembic output + `verify_db.py` counts saved per release. Never log secrets.
