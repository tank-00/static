"""S01 - billing days, billing-month arithmetic and half-open effective intervals (A§4.0).

Expected values are hand-derived; the derivation is in the comment next to each case.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tariff_engine.dates import (
    MAX_BILLING_DAYS,
    MONTH_PATTERN,
    DayCountConvention,
    billing_days,
    format_month,
    in_effective_interval,
    interval_intersects_period,
    is_month,
    month_add,
    month_bounds,
    month_days,
    month_of,
    month_range,
    months_between,
    parse_month,
    resolve_billing_days,
)
from tariff_engine.errors import DateError

INCLUSIVE = DayCountConvention.INCLUSIVE
READ_TO_READ = DayCountConvention.READ_TO_READ

months = st.builds(
    lambda year, month: f"{year:04d}-{month:02d}",
    st.integers(min_value=1900, max_value=2200),
    st.integers(min_value=1, max_value=12),
)


# ------------------------------------------------------------------------------------- billing days


@pytest.mark.req("AT-DATES")
@pytest.mark.parametrize(
    ("start", "end", "inclusive", "read_to_read"),
    [
        # May 15..31 = 17 days + June 1..14 = 14 days -> 31 inclusive; (end - start).days = 30.
        (date(2026, 5, 15), date(2026, 6, 14), 31, 30),
        # Leap February: Feb 1..29 = 29 inclusive; 28 read-to-read.
        (date(2024, 2, 1), date(2024, 2, 29), 29, 28),
        # Year boundary: Dec 20..31 = 12 + Jan 1..19 = 19 -> 31 inclusive; 30 read-to-read.
        (date(2025, 12, 20), date(2026, 1, 19), 31, 30),
        # Meter read on consecutive days: 2 days inclusive, 1 read-to-read.
        (date(2026, 7, 1), date(2026, 7, 2), 2, 1),
    ],
)
def test_billing_days_under_both_conventions(
    start: date, end: date, inclusive: int, read_to_read: int
) -> None:
    assert billing_days(start, end) == inclusive  # inclusive is the default
    assert billing_days(start, end, INCLUSIVE) == inclusive
    assert billing_days(start, end, READ_TO_READ) == read_to_read
    assert billing_days(start, end, "read_to_read") == read_to_read  # stored account-setting text


def test_billing_days_single_day_period() -> None:
    assert billing_days(date(2026, 7, 1), date(2026, 7, 1), INCLUSIVE) == 1
    with pytest.raises(DateError, match="no billing days"):
        billing_days(date(2026, 7, 1), date(2026, 7, 1), READ_TO_READ)  # 0 days


def test_billing_days_rejects_reversed_periods_and_unknown_conventions() -> None:
    with pytest.raises(DateError, match="before it starts"):
        billing_days(date(2026, 7, 2), date(2026, 7, 1))
    with pytest.raises(DateError, match="convention"):
        billing_days(date(2026, 7, 1), date(2026, 7, 31), "thirty_360")


@pytest.mark.req("AT-DATES", "R05-REPORTED-VS-DERIVED")
def test_reported_billing_days_win_over_the_computed_count() -> None:
    start, end = date(2026, 5, 15), date(2026, 6, 14)  # computed: 31 inclusive
    assert resolve_billing_days(start, end, INCLUSIVE, reported=Decimal("29")) == 29
    assert resolve_billing_days(start, end, INCLUSIVE, reported=30) == 30
    assert resolve_billing_days(start, end, INCLUSIVE, reported=None) == 31
    assert resolve_billing_days(start, end, READ_TO_READ) == 30


@pytest.mark.parametrize("bad", [Decimal("29.5"), Decimal("0"), Decimal("-3"), 0, Decimal("NaN")])
def test_reported_billing_days_must_be_whole_and_positive(bad: Decimal | int) -> None:
    with pytest.raises(DateError):
        resolve_billing_days(date(2026, 5, 15), date(2026, 6, 14), INCLUSIVE, reported=bad)


@pytest.mark.parametrize("reported", [None, 30, Decimal("30")])
def test_an_unknown_convention_fails_whether_or_not_days_are_reported(
    reported: Decimal | int | None,
) -> None:
    # A corrupt account setting must not pass silently just because this invoice reports the count.
    with pytest.raises(DateError, match="unknown day-count convention 'bogus'"):
        resolve_billing_days(date(2026, 1, 1), date(2026, 1, 31), "bogus", reported=reported)


@pytest.mark.req("AT-DATES", "R05-REPORTED-VS-DERIVED")
def test_reported_billing_days_are_bounded_by_the_calendar() -> None:
    # 0001-01-01..9999-12-31 holds 3,652,059 days; no service period can report more.
    assert MAX_BILLING_DAYS == (date(9999, 12, 31) - date(1, 1, 1)).days + 1 == 3_652_059
    start, end = date(2026, 5, 15), date(2026, 6, 14)
    assert resolve_billing_days(start, end, reported=MAX_BILLING_DAYS) == MAX_BILLING_DAYS
    assert resolve_billing_days(start, end, reported=Decimal("3.0E+1")) == 30
    too_many: list[Decimal | int] = [
        MAX_BILLING_DAYS + 1,
        Decimal(MAX_BILLING_DAYS + 1),
        10**5000,  # str() of this int would itself raise ValueError (4300-digit limit)
        Decimal("1E+999999999999999999"),  # int() of this would never finish
        Decimal("Infinity"),
    ]
    for bad in too_many:
        with pytest.raises(DateError, match="between 1 and 3652059"):
            resolve_billing_days(start, end, reported=bad)


def test_reported_billing_days_reject_bools_and_text() -> None:
    with pytest.raises(TypeError):
        resolve_billing_days(date(2026, 5, 15), date(2026, 6, 14), reported=True)
    with pytest.raises(TypeError):
        resolve_billing_days(date(2026, 5, 15), date(2026, 6, 14), reported="30")  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- months


@pytest.mark.req("P-HISTORY-FLOORS", "P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("month", "delta", "expected"),
    [
        ("2026-07", -11, "2025-08"),  # 2026*12+6 = 24318; -11 -> 24307 = 2025*12+7 -> 2025-08
        ("2026-07", 0, "2026-07"),
        ("2025-12", 1, "2026-01"),  # year roll-over forwards
        ("2026-01", -1, "2025-12"),  # and backwards
        ("2024-02", 24, "2026-02"),
        ("2026-10", -4, "2026-06"),
        ("2026-03", -27, "2023-12"),  # 2026*12+2 = 24314; -27 -> 24287 = 2023*12+11 -> 2023-12
    ],
)
def test_month_add(month: str, delta: int, expected: str) -> None:
    assert month_add(month, delta) == expected


@pytest.mark.req("P-HISTORY-FLOORS")
def test_months_between_is_the_signed_difference() -> None:
    assert months_between("2025-08", "2026-07") == 11  # 5 months to 2026-01, +6 to 2026-07
    assert months_between("2026-07", "2025-08") == -11
    assert months_between("2026-07", "2026-07") == 0
    assert months_between("2024-03", "2026-07") == 28  # 2 years (24) + 4 months


@pytest.mark.req("P-HISTORY-FLOORS")
def test_month_range_is_inclusive_and_ascending() -> None:
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert month_range("2026-07", "2026-07") == ["2026-07"]
    assert month_range("2026-08", "2026-07") == []


@pytest.mark.req("P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("month", "days"),
    [
        ("2026-01", 31),
        ("2026-02", 28),  # 2026 is not divisible by 4
        ("2024-02", 29),  # divisible by 4
        ("2100-02", 28),  # divisible by 100 but not 400
        ("2000-02", 29),  # divisible by 400
        ("2026-04", 30),
        ("2026-06", 30),
        ("2026-07", 31),
        ("2026-12", 31),
    ],
)
def test_month_days(month: str, days: int) -> None:
    assert month_days(month) == days


def test_month_helpers() -> None:
    assert parse_month("2026-07") == (2026, 7)
    assert format_month(2026, 7) == "2026-07"
    assert format_month(987, 3) == "0987-03"
    assert month_of(date(2026, 6, 14)) == "2026-06"
    # A comparison month's whole-month period: 2026-02 has 28 days.
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert billing_days(*month_bounds("2024-02")) == 29


VALID_MONTHS = [
    "2026-07",
    "0001-01",
    "0009-12",
    "0010-01",
    "0099-06",
    "0100-02",
    "0987-03",
    "1000-10",
    "9999-12",
]
INVALID_MONTHS = [
    "2026-13",
    "2026-00",
    "2026-7",
    "26-07",
    "2026/07",
    " 2026-07",
    "2026-07 ",
    "2026-07\n",
    "2026-07-01",
    "0000-01",
    "0000-12",
    "10000-01",
    "",
    "२०२६-०७",
]


@pytest.mark.parametrize("bad", INVALID_MONTHS)
def test_invalid_months_are_rejected(bad: str) -> None:
    assert not is_month(bad)
    with pytest.raises(DateError):
        parse_month(bad)
    with pytest.raises(DateError):
        month_add(bad, 1)


def test_month_validation_edges() -> None:
    assert is_month("2026-07")
    assert not is_month(202607)
    assert not is_month(None)
    with pytest.raises(DateError):
        parse_month(202607)  # type: ignore[arg-type]
    with pytest.raises(DateError):
        month_add("9999-12", 1)  # beyond year 9999
    with pytest.raises(DateError):
        month_add("0001-01", -1)  # before year 0001
    with pytest.raises(TypeError):
        month_add("2026-07", True)
    with pytest.raises(DateError):
        format_month(2026, 13)


@pytest.mark.parametrize("text", VALID_MONTHS + INVALID_MONTHS)
def test_month_pattern_agrees_with_the_parser(text: str) -> None:
    # The JSON-schema pattern later API models advertise must accept exactly what the engine
    # accepts. In ECMA-262 and Rust regex "$" only matches at the end of the input, which
    # re.fullmatch reproduces (Python's re.search would also accept a trailing newline).
    assert (re.fullmatch(MONTH_PATTERN, text) is not None) is is_month(text)
    assert is_month(text) is (text in VALID_MONTHS)


@given(st.text(alphabet="0123456789-", min_size=6, max_size=8))
def test_month_pattern_agrees_with_the_parser_on_generated_text(text: str) -> None:
    assert (re.fullmatch(MONTH_PATTERN, text) is not None) is is_month(text)


@given(months, st.integers(min_value=-600, max_value=600))
def test_month_add_and_months_between_are_inverse(month: str, delta: int) -> None:
    later = month_add(month, delta)
    assert months_between(month, later) == delta
    assert month_add(later, -delta) == month
    assert len(month_range(min(month, later), max(month, later))) == abs(delta) + 1


# ------------------------------------------------------------------------------ effective intervals


@pytest.mark.req("AT-DATES", "SRC-EFFECTIVE-PERIOD")
def test_effective_intervals_are_half_open() -> None:
    v_from, v_to = date(2024, 8, 30), date(2026, 6, 1)
    assert in_effective_interval(date(2024, 8, 30), v_from, v_to)  # start included
    assert in_effective_interval(date(2026, 5, 31), v_from, v_to)  # last day included
    assert not in_effective_interval(date(2026, 6, 1), v_from, v_to)  # end excluded
    assert not in_effective_interval(date(2024, 8, 29), v_from, v_to)
    assert in_effective_interval(date(2099, 1, 1), date(2026, 6, 1), None)  # open-ended


@pytest.mark.req("AT-DATES", "SRC-EFFECTIVE-PERIOD")
@pytest.mark.parametrize(
    ("effective_from", "effective_to", "intersects"),
    [
        # Service period 2026-05-15..2026-06-14 (inclusive) against versions:
        (date(2024, 8, 30), date(2026, 6, 1), True),  # covers May 15..31
        (date(2026, 6, 1), None, True),  # covers June 1..14
        (date(2026, 6, 14), None, True),  # covers the last service day only
        (date(2026, 6, 15), None, False),  # starts after the period
        (date(2024, 8, 30), date(2026, 5, 15), False),  # ends (exclusive) on the first day
        (date(2024, 8, 30), date(2026, 5, 16), True),  # covers May 15 only
    ],
)
def test_interval_intersects_inclusive_service_period(
    effective_from: date, effective_to: date | None, intersects: bool
) -> None:
    period = (date(2026, 5, 15), date(2026, 6, 14))
    assert interval_intersects_period(effective_from, effective_to, *period) is intersects


def at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """A datetime (a subclass of date) that the date helpers must refuse."""
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.mark.req("AT-DATES")
def test_billing_days_reject_datetimes() -> None:
    # 23:00 on Jan 1 to 01:00 on Jan 2 covers two service dates, yet (end - start).days == 0.
    late, early = at(2026, 1, 1, 23), at(2026, 1, 2, 1)
    with pytest.raises(TypeError, match="period_start must be a date, not datetime"):
        billing_days(late, early)
    with pytest.raises(TypeError, match="period_end must be a date, not datetime"):
        billing_days(date(2026, 1, 1), early)  # mixing used to be a raw comparison TypeError
    with pytest.raises(TypeError):
        resolve_billing_days(late, early)
    with pytest.raises(TypeError):
        resolve_billing_days(late, early, reported=2)
    with pytest.raises(TypeError):
        billing_days("2026-01-01", "2026-01-31")  # type: ignore[arg-type]


@pytest.mark.req("AT-DATES", "SRC-EFFECTIVE-PERIOD")
@pytest.mark.parametrize(
    "call",
    [
        # As dates, Feb 1 12:00 would make Feb 1 effective in [Jan 1, Feb 1 12:00).
        lambda: in_effective_interval(at(2026, 2, 1), at(2026, 1, 1), at(2026, 2, 1, 12)),
        lambda: in_effective_interval(at(2026, 2, 1), date(2026, 1, 1), None),
        lambda: in_effective_interval(date(2026, 2, 1), date(2026, 1, 1), at(2026, 3, 1)),
        lambda: interval_intersects_period(
            at(2026, 1, 1), None, date(2026, 1, 1), date(2026, 1, 31)
        ),
        lambda: interval_intersects_period(
            date(2026, 1, 1), None, date(2026, 1, 1), at(2026, 1, 31)
        ),
        lambda: month_of(at(2026, 7, 31, 23)),
    ],
)
def test_effective_intervals_and_months_reject_datetimes(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError, match="must be a date, not datetime"):
        call()


def test_empty_or_reversed_intervals_and_periods_are_rejected() -> None:
    with pytest.raises(DateError, match="empty effective interval"):
        in_effective_interval(date(2026, 6, 1), date(2026, 6, 1), date(2026, 6, 1))
    with pytest.raises(DateError, match="empty effective interval"):
        interval_intersects_period(
            date(2026, 6, 1), date(2026, 5, 1), date(2026, 5, 15), date(2026, 6, 14)
        )
    with pytest.raises(DateError, match="before it starts"):
        interval_intersects_period(date(2026, 6, 1), None, date(2026, 6, 14), date(2026, 5, 15))
