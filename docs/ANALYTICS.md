# Analytics formulas (Phase 4)

All metrics are computed on the caller's visible projects (admins see all) and
the requested window `[date_from, date_to]` (inclusive, UTC).

| Metric | Definition |
|---|---|
| `totals.opened` | Defects with `created_at` in the window |
| `totals.resolved` | Defects whose first resolution instant falls in the window |
| `totals.backlog` | Non-archived defects not in a resolved state, as of the window end |
| `totals.reopened` | Distinct defects with a `Reopened` status transition |
| `mttr_hours` | mean/median of `resolved_at − created_at` (wall hours) for defects resolved in the window |
| `time_to_triage_hours` | mean/median of `triage_at − created_at` where a triage signal exists |
| `reopen_rate` | distinct reopened defects ÷ defects ever resolved |
| `backlog_age_days` | mean/max of `now − created_at` over the backlog |
| `throughput[bucket]` | `opened` / `resolved` counts grouped by day/week/month |
| `sla.compliance_pct` | met clocks ÷ (met + missed) across SLA instances |
| `releases.escape_rate` | defects reported against a release *after* its actual ship date ÷ all defects linked to it |

## SLA clock definitions
- **first_response** — first human action by someone other than the reporter
  (status change, assignment, comment, or attachment), from `bug_history`.
- **triage** — first assignment by someone other than the reporter, or first
  transition to `In Progress`.
- **resolution** — first transition into a resolved state
  (`Fixed`, `Ready for Test`, `Closed`). Reopening clears the live resolution
  clock; `bugs.resolved_at` retains the first-resolution instant for MTTR.

Times are **business minutes** inside the policy calendar (timezone + weekday
windows + holidays). E.g. a `2880` resolution target is 6 business days at 8h/day,
not 48 wall-clock hours.

## Notes / limitations
- There is no team entity; "team" is approximated by project.
- Distributions reflect the current state of visible defects (not windowed by
  creation) unless a project filter narrows scope.
- `escape_rate` is only meaningful once a release has an `actual_date`.
