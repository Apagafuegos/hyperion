"""Cron expression parsing and next-run computation.

Implements the standard five-field cron grammar plus the @-shortcuts that
the VPS cron files actually use (from crontab(5)): numbers, ranges, lists,
steps, and the common month/weekday names. `next_run` is computed at minute
granularity against the current time; a literal cron expression that does not
match within the search horizon yields None rather than a fabricated time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_SEARCH_HORIZON_DAYS = 366

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

_SHORTCUTS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_SPECIAL = {"@reboot"}


class CronParseError(ValueError):
    """Raised when a cron expression cannot be parsed."""


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    description: str
    next_run: datetime | None


def _expand(
    field: str, low: int, high: int, names: dict[str, int] | None = None
) -> set[int]:
    """Expand one cron field into the set of matching values."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip().lower()
        if part == "*":
            values.update(range(low, high + 1))
            continue
        if part.startswith("*/") or part.startswith("* /"):
            step_text = part.split("/")[-1]
            if not step_text.isdigit():
                raise CronParseError(f"invalid step {part!r}")
            step = int(step_text)
            if step < 1:
                raise CronParseError(f"invalid step {part!r}")
            values.update(range(low, high + 1, step))
            continue
        step = 1
        if "/" in part:
            base, _, step_text = part.partition("/")
            if not step_text.isdigit():
                raise CronParseError(f"invalid step {part!r}")
            step = int(step_text)
            part = base
        if "-" in part:
            left, _, right = part.partition("-")
            lo = _value(left, low, high, names)
            hi = _value(right, low, high, names)
            if lo > hi:
                raise CronParseError(f"inverted range {part!r}")
            values.update(range(lo, hi + 1, step))
        else:
            values.add(_value(part, low, high, names))
    if not values:
        raise CronParseError(f"empty field {field!r}")
    return values


def _value(text: str, low: int, high: int, names: dict[str, int] | None) -> int:
    if names and text in names:
        value = names[text]
    elif text.isdigit():
        value = int(text)
    else:
        raise CronParseError(f"invalid value {text!r}")
    if not (low <= value <= high):
        raise CronParseError(f"value {value} out of range {low}-{high}")
    return value


def parse_cron_expression(expression: str, now: datetime | None = None) -> CronSchedule:
    """Parse a five-field cron expression and compute the next run."""
    now = now or datetime.now(UTC)
    raw = expression.strip()
    if raw in _SPECIAL:
        return CronSchedule(expression=raw, description="@reboot", next_run=None)
    if raw in _SHORTCUTS:
        raw = _SHORTCUTS[raw]
    fields = raw.split()
    if len(fields) != 5:
        raise CronParseError(f"expected 5 fields, got {len(fields)}")
    minutes = _expand(fields[0], 0, 59)
    hours = _expand(fields[1], 0, 23)
    days = _expand(fields[2], 1, 31)
    months = _expand(fields[3], 1, 12, _MONTHS)
    weekdays = _expand(fields[4], 0, 7, _DAYS)
    # In standard cron, 7 is Sunday; normalize to 0 (cron's Sunday).
    weekdays = {0 if d == 7 else d for d in weekdays}

    # Per crontab(5): if both DOM and DOW are restricted, the day matches
    # when either matches (OR); if one is *, it must match (AND).
    dom_restricted = "*" not in fields[2].split(",")
    dow_restricted = "*" not in fields[4].split(",")
    use_or = dom_restricted and dow_restricted

    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    horizon = now + timedelta(days=_SEARCH_HORIZON_DAYS)
    while candidate <= horizon:
        if candidate.month in months:
            dom_ok = candidate.day in days
            # Python weekday() is Monday=0..Sunday=6; cron DOW is Sunday=0.
            cron_dow = (candidate.weekday() + 1) % 7
            dow_ok = cron_dow in weekdays
            if use_or:
                day_ok = dom_ok or dow_ok
            elif dom_restricted:
                day_ok = dom_ok
            elif dow_restricted:
                day_ok = dow_ok
            else:
                day_ok = True
            if day_ok and candidate.hour in hours and candidate.minute in minutes:
                return CronSchedule(expression=expression, description=raw, next_run=candidate)
        candidate += timedelta(minutes=1)
    return CronSchedule(expression=expression, description=raw, next_run=None)


def humanize_cron(expression: str) -> str:
    """Short human-readable summary of a cron expression."""
    try:
        parsed = parse_cron_expression(expression)
    except CronParseError:
        return expression
    if parsed.description.startswith("@"):
        return parsed.description
    fields = parsed.description.split()
    if len(fields) != 5:
        return expression
    minute, hour, dom, month, dow = fields
    if minute == "0" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "every hour"
    if minute == "*" and hour == "*" and dom == "*" and month == "*" and dow == "*":
        return "every minute"
    if minute == "0" and hour != "*" and dom == "*" and month == "*" and dow == "*":
        return "hourly at :00"
    return " ".join(fields)
