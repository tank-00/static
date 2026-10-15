"""Billing periods, billing months and effective intervals (A§4.0).

* Service periods ``[period_start, period_end]`` are **inclusive** dates.
* Billing months are ``"YYYY-MM"`` strings; they drive seasons, ``by_period`` factors and
  lookback windows.
* Version/member effective intervals are **half-open** ``[effective_from, effective_to)``;
  ``effective_to=None`` means open-ended.
* Every day argument must be a ``datetime.date`` and not a ``datetime`` (``TypeError``): a
  datetime would count 24-hour spans instead of calendar service dates.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from tariff_engine.errors import DateError

__all__ = [
    "MAX_BILLING_DAYS",
    "MONTH_PATTERN",
    "DayCountConvention",
    "billing_days",
    "format_month",
    "in_effective_interval",
    "interval_intersects_period",
    "is_month",
    "month_add",
    "month_bounds",
    "month_days",
    "month_of",
    "month_range",
    "months_between",
    "parse_month",
    "resolve_billing_days",
]

MONTH_PATTERN: Final = r"^(?:[1-9][0-9]{3}|0[1-9][0-9]{2}|00[1-9][0-9]|000[1-9])-(?:0[1-9]|1[0-2])$"
"""JSON-schema pattern for a billing month ``YYYY-MM`` (years 0001-9999, ASCII digits).

It accepts exactly what :func:`parse_month` accepts (which validates with it), and avoids
look-arounds so every regex engine (ECMA-262, Python, Rust) reads it the same way.
"""

_MONTH_RE: Final = re.compile(MONTH_PATTERN)
_MIN_YEAR: Final = 1
_MAX_YEAR: Final = 9999

MAX_BILLING_DAYS: Final = (date.max - date.min).days + 1
"""Upper bound of a reported ``billing_days``: the calendar days in years 0001-9999 (3,652,059)."""


def _require_date(value: object, name: str) -> date:
    """``value`` if it is a calendar ``date``; a ``datetime`` or other type is a ``TypeError``."""
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{name} must be a date, not {type(value).__name__}")
    return value


def _require_period(period_start: object, period_end: object) -> None:
    start = _require_date(period_start, "period_start")
    end = _require_date(period_end, "period_end")
    if end < start:
        raise DateError(f"service period ends ({end}) before it starts ({start})")


class DayCountConvention(StrEnum):
    """How ``billing_days`` is computed when the invoice does not report it (account setting)."""

    INCLUSIVE = "inclusive"  # (end - start).days + 1 -- the default
    READ_TO_READ = "read_to_read"  # (end - start).days


# ------------------------------------------------------------------------------------------- months


def parse_month(month: str) -> tuple[int, int]:
    """Split ``"YYYY-MM"`` into ``(year, month)``; raises ``DateError`` on any other shape."""
    if not isinstance(month, str) or _MONTH_RE.fullmatch(month) is None:
        raise DateError(f"invalid billing month {month!r}: expected 'YYYY-MM' (years 0001-9999)")
    return int(month[:4]), int(month[5:])


def format_month(year: int, month: int) -> str:
    """Render ``(year, month)`` as ``"YYYY-MM"`` (years 0001-9999)."""
    if not (_MIN_YEAR <= year <= _MAX_YEAR and 1 <= month <= 12):
        raise DateError(f"month out of range: year={year}, month={month}")
    return f"{year:04d}-{month:02d}"


def is_month(value: object) -> bool:
    """True iff ``value`` is a valid ``"YYYY-MM"`` billing month string."""
    if not isinstance(value, str):
        return False
    try:
        parse_month(value)
    except DateError:
        return False
    return True


def _month_index(month: str) -> int:
    year, number = parse_month(month)
    return year * 12 + (number - 1)


def _month_from_index(index: int) -> str:
    year, zero_based = divmod(index, 12)
    return format_month(year, zero_based + 1)


def month_add(month: str, months: int) -> str:
    """Calendar month arithmetic: ``month_add("2026-07", -11) == "2025-08"``."""
    if isinstance(months, bool) or not isinstance(months, int):
        raise TypeError(f"months must be an int, not {type(months).__name__}")
    return _month_from_index(_month_index(month) + months)


def months_between(start: str, end: str) -> int:
    """Signed month difference ``end - start``.

    ``month_add(start, months_between(start, end)) == end``.

    ``months_between("2025-08", "2026-07") == 11``; reversed arguments give ``-11``.
    """
    return _month_index(end) - _month_index(start)


def month_range(first: str, last: str) -> list[str]:
    """Months ``first`` through ``last`` inclusive, ascending; empty when ``first > last``."""
    start, stop = _month_index(first), _month_index(last)
    return [_month_from_index(index) for index in range(start, stop + 1)]


def month_days(month: str) -> int:
    """Calendar days in the billing month (leap years included)."""
    year, number = parse_month(month)
    return calendar.monthrange(year, number)[1]


def month_of(day: date) -> str:
    """Billing month containing ``day`` (e.g. the default proposal from ``period_end``)."""
    _require_date(day, "day")
    return format_month(day.year, day.month)


def month_bounds(month: str) -> tuple[date, date]:
    """First and last calendar day of ``month`` (a comparison month's service period)."""
    year, number = parse_month(month)
    return date(year, number, 1), date(year, number, month_days(month))


# ------------------------------------------------------------------------------------- billing days


def _day_count_rule(convention: DayCountConvention | str) -> DayCountConvention:
    try:
        return DayCountConvention(convention)
    except ValueError:
        raise DateError(f"unknown day-count convention {convention!r}") from None


def billing_days(
    period_start: date,
    period_end: date,
    convention: DayCountConvention | str = DayCountConvention.INCLUSIVE,
) -> int:
    """Billing days of an inclusive service period under the account's day-count convention.

    ``inclusive``: ``(end - start).days + 1``; ``read_to_read``: ``(end - start).days``. A period
    ending before it starts, or one with zero billing days, raises ``DateError`` (billing days is
    a proration denominator, so it must be at least 1).
    """
    rule = _day_count_rule(convention)
    _require_period(period_start, period_end)
    span = (period_end - period_start).days
    days = span + 1 if rule is DayCountConvention.INCLUSIVE else span
    if days < 1:
        raise DateError(
            f"service period {period_start}..{period_end} has no billing days under {rule.value}"
        )
    return days


def resolve_billing_days(
    period_start: date,
    period_end: date,
    convention: DayCountConvention | str = DayCountConvention.INCLUSIVE,
    reported: Decimal | int | None = None,
) -> int:
    """The reported ``billing_days`` when present (it wins), else :func:`billing_days`.

    A reported value must be a whole number in ``1..MAX_BILLING_DAYS``; anything else raises
    ``DateError``. ``None`` means "not reported", never zero. The convention and the period are
    validated either way, so a corrupt account setting fails the same way whether or not the
    invoice reports the count.
    """
    _day_count_rule(convention)
    if reported is None:
        return billing_days(period_start, period_end, convention)
    if isinstance(reported, bool) or not isinstance(reported, (int, Decimal)):
        raise TypeError(
            f"reported billing days must be int or Decimal, not {type(reported).__name__}"
        )
    _require_period(period_start, period_end)
    expected = f"a whole number between 1 and {MAX_BILLING_DAYS}"
    if isinstance(reported, int):
        if not 1 <= reported <= MAX_BILLING_DAYS:  # never format a huge int (str() would refuse)
            raise DateError(f"reported billing days must be {expected}")
        return reported
    # Range first: to_integral_value() and int() on an exponent such as 1E+999999999 would hang.
    if not reported.is_finite() or not 1 <= reported <= MAX_BILLING_DAYS:
        raise DateError(f"reported billing days must be {expected}, got {reported}")
    if reported != reported.to_integral_value():
        raise DateError(f"reported billing days must be {expected}, got {reported}")
    return int(reported)


# ------------------------------------------------------------------------------ effective intervals


def _check_interval(effective_from: date, effective_to: date | None) -> None:
    _require_date(effective_from, "effective_from")
    if effective_to is not None:
        _require_date(effective_to, "effective_to")
    if effective_to is not None and effective_to <= effective_from:
        raise DateError(f"empty effective interval [{effective_from}, {effective_to})")


def in_effective_interval(day: date, effective_from: date, effective_to: date | None) -> bool:
    """True iff ``effective_from <= day < effective_to`` (half-open; ``None`` = no end)."""
    _check_interval(effective_from, effective_to)
    _require_date(day, "day")
    return effective_from <= day and (effective_to is None or day < effective_to)


def interval_intersects_period(
    effective_from: date,
    effective_to: date | None,
    period_start: date,
    period_end: date,
) -> bool:
    """True iff a service day of the inclusive period lies in ``[effective_from, effective_to)``."""
    _check_interval(effective_from, effective_to)
    _require_period(period_start, period_end)
    first_common_day = max(effective_from, period_start)
    return first_common_day <= period_end and (
        effective_to is None or first_common_day < effective_to
    )
