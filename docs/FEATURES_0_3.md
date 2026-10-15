# Phases 0–3 — what changed, API impact, flags, deploy/rollback

Phases 0–3. All changes additive; old routes/fields unchanged.
Phase 4 (SLA, analytics, releases, exports) is documented separately in `docs/PHASE_4.md` and `docs/ANALYTICS.md`.

## Phase 0 — safety
- `docs/BASELINE.md`, `docs/MIGRATIONS.md`, `docs/BACKUP.md`
- Request IDs: `X-Request-ID` response header, logs include `[rid]`, secrets redacted (`app/core/logging_config.py`, `app/main.py`)
- `app/core/config.py` single settings + 7 feature flags (see `.env.example`)
- CI `.github/workflows/ci.yml` (pytest + verify_db)
- Scripts: `backup.py`, `restore.py`, `verify_db.py`, `import_json.py` (dry-run first, originals kept)

## Phase 1 — audit/hardening
- New `audit_logs` (append-only, no secrets, request_id/source). Written in same transaction as project/member/bug/comment/user ops.
- Read: `GET /api/v1/admin/audit` (admin only, paginated, filters `entity_type,project_id`).
- Validation/errors unchanged (`{"detail"}` + codes). No stack traces. Backup/restore tested below.

## Phase 2 — projects/workflow
- `organizations` + default `id=1`; `projects.org_id` nullable backfilled to 1. `GET/POST /api/v1/orgs`.
- Permission service `app/services/authorization.py` (`defect.read/create/edit/assign/transition/archive`, `comment.create`, `project.manage`, `audit.read`, `report.export` …). Old `can_*` wrappers kept.
- Workflow service `app/services/workflow.py`: default = existing `ALLOWED_TRANSITIONS`. `GET/PUT /api/v1/projects/{id}/workflow` (PUT requires `FEATURE_CUSTOM_WORKFLOWS=True` + manager/admin). Existing `POST /bugs/{id}/transition` routes through service, same contract + optional `reason` (required when leaving Fixed/Ready/Closed/Rejected → Reopened/In Progress/Open).
- Custom fields: `custom_field_defs/values`. `GET/POST /projects/{id}/custom-fields`, `GET /bugs/{id}/custom-fields`; `BugCreateIn/UpdateIn.custom_fields?`, `BugOut.component/affected_version/fixed_version?`.
- Links: `bug_links`. `GET/POST /bugs/{id}/links`, `DELETE /links/{id}`, `POST /bugs/{id}/mark-duplicate` (sets Rejected + preserves history, checks both sides).
- Bulk: `POST /projects/{id}/bugs/bulk` `{bug_ids[≤100], action: assign|set_priority|transition, ...}` → `{results:[{id,ok,error}]}` with per-item perm checks + history/audit. Flag `FEATURE_BULK_OPS`.

## Phase 3 — productivity
- Notifications `notifications/notification_prefs` (idempotency_key, read/unread, prefs `assigned/mentioned/commented/status_changed` default on). Event interface `app/services/notifications.py` called AFTER commit; failures never fail op. `GET /api/v1/notifications`, `POST /.../read`, `POST /mark-all-read`, `GET/PUT /notification-prefs`.
- Mentions `@user` in comments notify only active members/admins, no self-notify, no loops. `is_internal` comments: create restricted to manager/admin/developer; list hides internal from reporter/viewer.
- Watchers `bug_watchers`: `GET /bugs/{id}/watchers`, `POST /watch`, `POST /unwatch`.
- Saved views `saved_views` (validated keys only): `GET/POST /api/v1/saved-views`, `DELETE /...`. Project access re-checked on create; views never grant access.

## Deploy
```bash
python scripts/backup.py --out ./backups/pre-0002.tgz
alembic upgrade head
python scripts/verify_db.py
python -m pytest -q
```
## Rollback
- Disable flags (`.env`) to turn off custom_workflows/notifications/saved_views without stopping core defects.
- Schema is additive — forward-fix preferred. To restore: stop app, `python scripts/restore.py --in ./backups/pre-0002.tgz --confirm`, `alembic upgrade head`, verify, restart.
- Never drop columns in same release; no IDs reused; old records get safe defaults (`org_id=1`, `is_internal=False`, custom fields optional).
