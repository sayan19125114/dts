# Baseline — current system inventory (Phase 0)

Generated from real codebase in `/home/sayan/Downloads/test`. No workflow changes in Phase 0.

## Frontend entry points
- `app/static/index.html` — SaaS shell: sidebar (`#sidebar`, `#nav`, `#nav-admin`, `#side-user`), topbar (breadcrumb `#crumb-page/#crumb-proj`, `#top-search-form/#top-search`, `#theme-toggle`, `#profile-btn/#profile-menu`), `main#main` with `#status`, `#toasts`, `#app`, modal `#modal-backdrop`.
- `app/static/js/api.js` — `api(path,{method,body,form,csrf})` same-origin cookies, `X-CSRF-Token`, 401 handler; `el()`, `showStatus()`; never stores tokens in localStorage.
- `app/static/js/app.js` — hash router: `#/login`, `#/dashboard`, `#/projects`, `#/projects/:id`, `#/projects/:id/bugs`, `#/projects/:id/bugs/new`, `#/bugs/:id`, `#/assigned`, `#/search/:q`, `#/reports`, `#/admin`, `#/password`. All rendering via `textContent` (no `innerHTML` for user data). Theme only in `localStorage dts-theme`.
- `app/static/css/style.css` — design tokens via CSS vars, light default + `[data-theme=dark]`, `[hidden]{display:none!important}`.

## Backend routers (`app/main.py` mounts)
- `GET /api/health`, `GET /api/v1/health`
- `app/api/v1/auth.py` prefix `/api/v1/auth`: `POST /login`, `POST /logout`, `GET /me`, `GET /csrf`, `POST /change-password`, `POST /reset-password`
- `app/api/v1/projects.py` prefix `/api/v1/projects`: `GET ""`, `POST ""`, `GET /{id}`, `PATCH /{id}`, `POST /{id}/archive`, `POST /{id}/restore`, `GET /{id}/members`, `POST /{id}/members`, `PATCH /{id}/members/{uid}`, `DELETE /{id}/members/{uid}`
- `app/api/v1/bugs.py` prefix `/api/v1`: `GET /projects/{pid}/bugs`, `POST /projects/{pid}/bugs`, `GET /bugs/{id}`, `PATCH /bugs/{id}`, `POST /bugs/{id}/transition`, `POST /bugs/{id}/assign`, `POST /bugs/{id}/archive`, `POST /bugs/{id}/restore`, `GET /bugs/{id}/history`
- `app/api/v1/comments.py` prefix `/api/v1`: `GET /bugs/{id}/comments`, `POST /bugs/{id}/comments`, `PATCH /comments/{cid}`, `DELETE /comments/{cid}`
- `app/api/v1/attachments.py` prefix `/api/v1`: `POST /bugs/{id}/attachments`, `GET /attachments/{aid}/download`, `DELETE /attachments/{aid}`, `GET /bugs/{id}/attachments`
- `app/api/v1/dashboard.py` prefix `/api/v1/dashboard`: `GET /summary`, `GET /assigned`
- `app/api/v1/reports.py` prefix `/api/v1/reports`: `GET /bugs.csv`
- `app/api/v1/admin.py` prefix `/api/v1/admin`: `GET /users`, `POST /users`, `GET /users/{id}`, `PATCH /users/{id}`, `POST /users/{id}/disable`, `POST /users/{id}/enable`, `POST /users/{id}/password-reset`
- `app/api/v1/deps.py` — `get_auth_context` (cookie session + CSRF + idle/absolute expiry), `require_admin`, `require_project_access`, `get_project_role`.

## Persistence
- SQLite via SQLAlchemy 2.x (`app/db/session.py` WAL, FK ON, busy_timeout 5000). Models in `app/db/models.py`: `users`, `sessions`, `projects`, `project_members`, `bugs`, `comments`, `bug_history`, `attachments`, `password_reset_tokens`. Migrations in `migrations/versions/0001_initial.py` via Alembic. Tests use `Base.metadata.create_all` on temp files.
- Uploads: `storage/uploads/` random hex `storage_key`, signature-checked, auth-checked download.

## Auth / roles
- Argon2id via `pwdlib`, 12–128 chars. DB sessions hash-only, rotation on login/password change, revoke on logout/disable. Throttle 5/300s. CSRF synchronizer per session.
- Global `admin/user`; project `manager/developer/reporter/viewer`. Server-side checks in `app/core/permissions.py` + `deps.py`. Last-admin protection. IDOR tested by 404 on cross-project access.

## Defect lifecycle (current default — preserved)
- Statuses: `Open`, `In Progress`, `Fixed`, `Ready for Test`, `Reopened`, `Closed`, `Rejected`.
- Allowed: Open→In Progress,Rejected; In Progress→Fixed,Open,Rejected; Fixed→Ready for Test,Reopened; Ready for Test→Closed,Reopened; Reopened→In Progress,Rejected; Closed→Reopened (manager/admin); Rejected→Reopened (manager/admin).
- `version` optimistic concurrency → 409 on stale. Every change writes `bug_history`. Archived projects block writes (409).

## API contracts / errors
- JSON `{"detail": msg}`; codes: 401 unauth, 403 no perm/CSRF, 404 missing/inaccessible (IDOR-safe), 409 conflict/transition/version, 422 validation, 413 oversized, 429 throttled. No stack traces in prod. CSV escapes `=+-@TAB CR` with leading `'`.

## Env / deploy
- `app/core/config.py` single settings module (`.env`, `UPLOAD_DIR`, `DATABASE_URL`, session/cookie, CORS, throttling, pagination). `uvicorn app.main:app`. `alembic upgrade head`. `python scripts/create_admin.py`. Tests: `python -m pytest -q` (38 tests).

## Tests (safety net)
- `tests/api/test_auth.py` (9), `test_authorization.py` (9), `test_projects_bugs.py` (7), `test_comments_attachments.py` (4), `test_security_reports.py` (9). Covers §2.3 journeys 1–8.
