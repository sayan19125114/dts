# Defect Tracking System

Secure, maintainable, multi-user, multi-project defect tracking web app.
Implements `plan.md` phases 0–9: auth/sessions/CSRF, projects/members, defect lifecycle,
comments/attachments/history, dashboard/reports, vanilla-JS frontend, tests, docs.

## Stack
- Backend: FastAPI + SQLAlchemy 2.x + SQLite (WAL, FK, busy timeout) + Alembic + Pydantic v2
- Auth: Argon2id (`pwdlib`), DB-backed sessions (hash-only tokens), rotation, idle+absolute expiry, throttling, CSRF synchronizer token
- Frontend: semantic HTML5 + CSS3 + vanilla JS ES modules, same-origin cookies, `textContent` rendering
- Tests: pytest + TestClient

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit SECRET_KEY, COOKIE_SECURE=True behind HTTPS
alembic upgrade head
python scripts/create_admin.py --username admin --email admin@example.com --display-name Admin
uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000/
```
password - Admin@12456789

Dev DB without alembic:
```bash
python -c "from app.db.init_db import init_db; init_db()"
```

## Config
See `.env.example`. Never commit real secrets. Production: `DEBUG=False`, `COOKIE_SECURE=True`, HTTPS at reverse proxy, `TRUSTED_HOSTS` narrowed, `CORS_ORIGINS` explicit.

## Migrations
```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

## Tests
```bash
pytest
```

## Roles / permissions
| Action | admin | manager | developer | reporter | viewer | non-member |
|---|---|---|---|---|---|---|
| manage project/members | Y | Y | N | N | N | N |
| create defect | Y | Y | Y | Y | N | N |
| read defect | Y | Y | Y | Y | Y | N |
| edit fields | Y | Y | assigned only | own+Open only | N | N |
| transition | Y | Y | assigned only | own (Closed/Rejected→Reopened restricted) | N | N |
| assign | Y | Y | N | N | N | N |
| comment/attach | Y | Y | Y | Y | N | N |
| archive/restore | Y | Y | N | N | N | N |

Code: `app/core/permissions.py`. Last active admin cannot be disabled/demoted.

## Status transitions
- Open → In Progress, Rejected
- In Progress → Fixed, Open, Rejected
- Fixed → Ready for Test, Reopened
- Ready for Test → Closed, Reopened
- Reopened → In Progress, Rejected
- Closed → Reopened (manager/admin only)
- Rejected → Reopened (manager/admin only)

Invalid transitions → 409. Every edit bumps `version`; stale `version` → 409.

## Archived projects
Read-only for ordinary users; defect create/edit/transition blocked with 409 until restored. Admins/managers can view + restore.

## Attachments
Stored under `storage/uploads/` (outside `app/static/`), random hex names, signature-validated (PNG/JPEG/WebP/PDF/TXT/LOG), 10MB default, `Content-Disposition: attachment`, `nosniff`, auth-checked downloads. Soft-delete metadata + unlink file.

## CSV export
`GET /api/v1/reports/bugs.csv` — same auth filters as list; cells starting `= + - @ TAB CR` prefixed with `'` to block formula injection.

## Password reset
- Admin generates one-time 1h token: `POST /api/v1/admin/users/{id}/password-reset` → deliver securely.
- User redeems: `POST /api/v1/auth/reset-password {token, new_password}` → revokes all sessions.
- No email configured: use admin-assisted flow above (documented). No predictable URLs.

## Backup / restore
SQLite + uploads must be backed up together:
```bash
sqlite3 defect_tracker.db ".backup 'backup.db'"
tar -czf backup.tgz backup.db storage/uploads
# restore: stop app, restore db + uploads, alembic upgrade head, restart, verify login
```

## SQLite limits
Single-server, WAL, short transactions. Do not share file over NFS/multi-host. If write contention or multi-instance needed → migrate to PostgreSQL (models use portable types; Alembic history provides path).

## Structure
```
app/main.py app/core/ app/db/ app/schemas/ app/api/v1/ app/static/ migrations/ tests/ scripts/ storage/uploads/
```

## Definition of done
All plan §11 items implemented + tested. Run `pytest` and verify. Known deferred (per plan §13): email delivery, WebSockets, SSO, rich-text, public portal, multi-tenant, custom workflows, mobile-native.
# dts
