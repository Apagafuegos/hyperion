"""Cron expression parsing and next-run computation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hyperion.providers.cron import CronParseError, parse_cron_expression

NOW = datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC)


def test_daily_at_specific_hour() -> None:
    schedule = parse_cron_expression("0 6 * * *", NOW)
    assert schedule.next_run == datetime(2026, 8, 10, 6, 0, 0, tzinfo=UTC)


def test_every_fifteen_minutes() -> None:
    schedule = parse_cron_expression("*/15 * * * *", NOW)
    assert schedule.next_run == datetime(2026, 8, 9, 9, 15, 0, tzinfo=UTC)


def test_every_minute() -> None:
    schedule = parse_cron_expression("* * * * *", NOW)
    assert schedule.next_run == datetime(2026, 8, 9, 9, 1, 0, tzinfo=UTC)


def test_specific_minute_hour() -> None:
    schedule = parse_cron_expression("30 9 * * *", NOW)
    assert schedule.next_run == datetime(2026, 8, 9, 9, 30, 0, tzinfo=UTC)


def test_dom_restriction() -> None:
    schedule = parse_cron_expression("0 2 1 * *", NOW)
    assert schedule.next_run == datetime(2026, 9, 1, 2, 0, 0, tzinfo=UTC)


def test_month_and_dow_restriction_uses_or() -> None:
    # Both DOM and DOW restricted: standard cron ORs them, so the 13th
    # (a Thursday) matches because the day-of-month field matches.
    schedule = parse_cron_expression("0 0 13 * 5", NOW)
    assert schedule.next_run == datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)


def test_weekday_names() -> None:
    schedule = parse_cron_expression("0 9 * * MON-FRI", NOW)
    assert schedule.next_run == datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)


def test_step_ranges() -> None:
    schedule = parse_cron_expression("5-55/10 * * * *", NOW)
    # Minutes 5, 15, 25, 35, 45, 55; the next at-or-after 09:01 is 09:05.
    assert schedule.next_run == datetime(2026, 8, 9, 9, 5, 0, tzinfo=UTC)


def test_shortcut_daily() -> None:
    schedule = parse_cron_expression("@daily", NOW)
    assert schedule.next_run == datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)


def test_shortcut_hourly() -> None:
    schedule = parse_cron_expression("@hourly", NOW)
    assert schedule.next_run == datetime(2026, 8, 9, 10, 0, 0, tzinfo=UTC)


def test_reboot_is_not_observed() -> None:
    schedule = parse_cron_expression("@reboot", NOW)
    assert schedule.next_run is None


def test_invalid_expression_rejected() -> None:
    with pytest.raises(CronParseError):
        parse_cron_expression("60 * * * *", NOW)
    with pytest.raises(CronParseError):
        parse_cron_expression("* * * *", NOW)  # four fields
    with pytest.raises(CronParseError):
        parse_cron_expression("0 24 * * *", NOW)  # hour out of range


def test_no_match_within_horizon_returns_none() -> None:
    # February 30 never exists; the parser must return None, not fabricate.
    schedule = parse_cron_expression("0 0 30 2 *", NOW)
    assert schedule.next_run is None
