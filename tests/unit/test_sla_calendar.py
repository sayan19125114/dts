"""Unit tests for the Phase 4 business-calendar maths."""
from __future__ import annotations

import datetime as dt

from app.services.sla import DEFAULT_BUSINESS_HOURS, BusinessCalendar, resolve_targets

UTC = dt.timezone.utc


def _cal(holidays=None, tz="UTC"):
    return BusinessCalendar(tz, DEFAULT_BUSINESS_HOURS, holidays or [])


def test_add_business_minutes_spans_weekend():
    cal = _cal()
    # Fri 2026-01-02 16:00 + 120 business minutes -> skip weekend -> Mon 10:00
    start = dt.datetime(2026, 1, 2, 16, 0, tzinfo=UTC)
    assert cal.add_business_minutes(start, 120) == dt.datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def test_add_business_minutes_before_open():
    cal = _cal()
    # Saturday 12:00 + 60 -> Monday 10:00
    start = dt.datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
    assert cal.add_business_minutes(start, 60) == dt.datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def test_business_minutes_between_ignores_off_hours():
    cal = _cal()
    start = dt.datetime(2026, 1, 2, 16, 0, tzinfo=UTC)
    end = dt.datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    assert cal.business_minutes_between(start, end) == 120


def test_holiday_is_skipped():
    cal = _cal(holidays=["2026-01-05"])
    start = dt.datetime(2026, 1, 2, 16, 0, tzinfo=UTC)
    # Monday is a holiday -> next business day is Tuesday
    assert cal.add_business_minutes(start, 120) == dt.datetime(2026, 1, 6, 10, 0, tzinfo=UTC)


def test_timezone_shifts_window():
    cal = BusinessCalendar("America/New_York", DEFAULT_BUSINESS_HOURS, [])
    # 09:00 New York == 14:00 UTC (EST in January)
    start = dt.datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
    result = cal.add_business_minutes(start, 0)
    assert result == start  # zero stays put
    assert cal.add_business_minutes(start, 60) == dt.datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def test_resolve_targets_prefers_severity_then_priority():
    targets = {
        "default": {"first_response": 999, "triage": 999, "resolution": 999, "warning_pct": 80},
        "by_severity": {"Critical": {"first_response": 60, "triage": 120, "resolution": 480}},
        "by_priority": {"P1": {"first_response": 30, "triage": 60, "resolution": 240}},
    }
    crit = resolve_targets(targets, "Critical", "P1")
    assert crit["first_response"] == 60
    pri = resolve_targets(targets, "Low", "P1")
    assert pri["first_response"] == 30
    default = resolve_targets(targets, "Low", "P4")
    assert default["first_response"] == 999
