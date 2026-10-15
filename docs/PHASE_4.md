# Phase 4 — SLA, analytics, releases, exports

All additive. Existing routes, fields, and statuses are unchanged. New tables,
new nullable columns, and new feature flags only. Non-negotiable rules honoured:
server-side authz everywhere, no retroactive SLA application, no fabricated
sample data, and every capability is behind a flag.

## Schema (migration `0003_phase4`)
- `bugs.resolved_at` (nullable, first resolution instant; never cleared on reopen)
- `bugs.affected_release_id`, `bugs.fixed_release_id` (nullable; FK enforced at app level on SQLite ALTER)
- `sla_policies` — versioned; one active row per project; immutable per version
- `bug_sla` — per-defect SLA instance with a full snapshot of the policy (calendar, targets, pause statuses, escalation) so later policy edits never change existing clocks
- `sla_events` — append-only timeline; `idempotency_key` unique
- `releases`, `release_gates` — configurable quality gates (advisory by default)
- `export_jobs` — queued/inline exports with a hashed, time-limited download token

## Flags (`.env`)
`FEATURE_SLA`, `FEATURE_ANALYTICS`, `FEATURE_RELEASES`, `FEATURE_ASYNC_EXPORTS`
(plus `SLA_SCHEDULER_ENABLED`, `SLA_TICK_SECONDS`, `DEFAULT_TIMEZONE`,
`EXPORT_DIR`, `EXPORT_EXPIRE_SECONDS`, `EXPORT_INLINE`).

The scheduler thread is **off by default** (never spawned on import/tests). Turn
it on in single-server deployments, or drive passes manually via
`POST /api/v1/admin/sla/run`. Passes are idempotent and retry-safe.

## SLA (`app/services/sla.py`)
- Business-calendar clocks (timezone + weekday windows + holidays), in *business minutes*.
- Clocks: first response, triage, resolution. Definitions in `docs/ANALYTICS.md`.
- Pause/resume on configured statuses; deadlines shift forward by business time spent paused.
- Warning at `warning_pct%`, breach at deadline, escalation notification to manager/lead/admin (idempotent).
- Endpoints: `GET/POST /api/v1/projects/{id}/sla-policies`, `.../sla-policies/active`,
  `POST .../sla-policies/{pid}/activate`, `POST .../sla/reapply`,
  `GET .../sla/at-risk`, `GET /api/v1/bugs/{id}/sla`, `POST /api/v1/bugs/{id}/sla/apply`,
  `POST /api/v1/admin/sla/run`.
- A defect auto-receives the active policy on creation. A new policy version never
  touches existing defects until a manager explicitly re-applies it.

## Releases & gates (`app/api/v1/releases.py`)
- `GET/POST /api/v1/projects/{id}/releases`, `GET/PATCH/DELETE /api/v1/releases/{id}`
- `GET /releases/{id}/readiness`, `POST /releases/{id}/gates`, `.../gates/{gid}/evaluate`,
  `.../gates/{gid}/waive`, `DELETE .../gates/{gid}`
- `POST /releases/{id}/bugs` (link affected/fixed), `DELETE .../bugs/{bug_id}`
- `POST /releases/{id}/release` — enforces blocking gates only when the release
  has `gates_blocking`; `force=true&reason=...` records an audited `force_released`.
- `GET /releases/{id}/notes`

Rule types: `unresolved_critical`, `unresolved_high`, `max_open_by_severity`,
`sla_compliance`. Config supports `{"scope": "release"|"project", "max": N}` etc.

## Analytics (`app/services/analytics.py`, `/api/v1/analytics/*`)
`GET /summary`, `GET /sla`, `GET /releases`. Permission-scoped to visible projects
(admins see all). Formulas: see `docs/ANALYTICS.md`. "Team" is approximated by project.

## Exports (`app/services/exports.py`, `/api/v1/reports/exports*`)
- `POST /api/v1/reports/exports` `{kind, params}` — kinds: `bugs`, `sla_compliance`,
  `release_notes`, `analytics`. Processed inline by default; queued when
  `EXPORT_INLINE=False` (worker drains the queue).
- `GET /exports`, `GET /exports/{id}`, `GET /exports/{id}/download?token=...`.
  Downloads re-authorize (owner/admin or valid non-expired token) and set
  `X-Content-Type-Options`; CSV cells are formula-injection escaped.

## Deploy / rollback
```bash
python scripts/backup.py --out ./backups/pre-0003.tgz
alembic upgrade head
python scripts/verify_db.py
python -m pytest -q
```
Rollback: disable the Phase 4 flags first. Schema is additive; forward-fix is
preferred. `alembic downgrade 0002_phases_1_3` drops only Phase 4 tables/columns.
