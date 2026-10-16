"""S02 - expression language (A§4.3, R03, R05, PRD §9.1).

Expected values are hand-derived; the derivation is in the comment next to each case. Offsets in
position tests are counted by hand on the formula text (0-based characters; columns are 1-based).
"""

from __future__ import annotations

import dataclasses
import warnings
from collections.abc import Mapping
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from typing import Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tariff_engine.dates import MAX_BILLING_DAYS
from tariff_engine.decimals import RoundingMode, canonical_str, quantize
from tariff_engine.errors import (
    CalcError,
    DateError,
    EngineError,
    ExpressionError,
    MissingInputError,
    UnitError,
)
from tariff_engine.expressions import (
    BUILTIN_NAMES,
    FUNCTION_NAMES,
    MAX_DEPTH,
    MAX_SOURCE_LENGTH,
    MISSING,
    Binary,
    BinaryOperator,
    Conditional,
    Constant,
    Conversion,
    EnvValue,
    Expr,
    Function,
    MissingValue,
    Name,
    Substitution,
    Unary,
    UnaryOperator,
    Value,
    builtin_values,
    children,
    evaluate,
    evaluate_traced,
    parse_expression,
    referenced_names,
    walk,
)
from tariff_engine.units import DIMENSIONLESS, Quantity, Unit
from tests.strategies import dec

# The only binary float in this module: it exists solely to prove that floats are rejected.
A_FLOAT = float("1.5")

KW = Unit({"kW": 1})
KVAR = Unit({"kVAR": 1})
USD = Unit({"USD": 1})


def run(source: str, env: Mapping[str, EnvValue] | None = None) -> Value:
    return evaluate(parse_expression(source), {} if env is None else env)


def number(source: str, env: Mapping[str, EnvValue] | None = None) -> Quantity:
    result = run(source, env)
    assert isinstance(result, Quantity)
    return result


def kw(value: str) -> Quantity:
    return Quantity(value, "kW")


def parse_error(source: str) -> ExpressionError:
    with pytest.raises(ExpressionError) as info:
        parse_expression(source)
    return info.value


def july() -> dict[str, EnvValue]:
    return builtin_values("2026-07", 31)


# ----------------------------------------------------------------------------------------- literals


@pytest.mark.req("R05-UNITS-ROUNDING", "P-FIXED-LINEAR")
def test_literal_arithmetic_is_exact() -> None:
    # As binary floats 0.1 + 0.2 is 0.30000000000000004; literals are read from their source text.
    assert run("0.1 + 0.2 == 0.3") is True
    total = number("0.1 + 0.2")
    assert total.value == Decimal("0.3")
    assert canonical_str(total.value) == "0.3"
    assert total.unit == DIMENSIONLESS


@pytest.mark.req("R05-UNITS-ROUNDING", "P-FIXED-LINEAR")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("0.06912", "0.06912"),  # the text, never the float 0.069119999999999...
        ("1.10", "1.10"),  # scale kept
        ("1_000.5", "1000.5"),  # digit separators stripped before D()
        ("1e3", "1000"),  # 1 x 10^3
        ("2.5E-2", "0.025"),  # 2.5 / 100
        (".5", "0.5"),
        ("5.", "5"),
        ("00", "0"),
        # 37 significant digits are kept exactly (only arithmetic rounds to 34)
        (
            "1234567890123456789012345678901234567",
            "1234567890123456789012345678901234567",
        ),
        # ... and so are they with a sign: the "-" is part of the literal, not a rounding negation
        (
            "-1234567890123456789012345678901234567",
            "-1234567890123456789012345678901234567",
        ),
        ("-1.5", "-1.5"),
    ],
)
def test_number_literals_read_their_source_text(source: str, expected: str) -> None:
    result = number(source)
    assert canonical_str(result.value) == expected
    assert result.unit == DIMENSIONLESS


@pytest.mark.req("R03-FORMULA", "R05-UNITS-ROUNDING")
def test_arithmetic_on_a_long_literal_rounds_to_34_digits() -> None:
    # 1234567890123456789012345678901234|567890: the 35th digit is 5 followed by 67890 > 0, so
    # HALF_EVEN rounds up: 1.234567890123456789012345678901235E+39.
    result = number("1234567890123456789012345678901234567890 + 0")
    assert result.value == Decimal("1.234567890123456789012345678901235E+39")


@pytest.mark.req("R03-FORMULA", "R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "-1234567890123456789012345678901234567",
            "-1234567890123456789012345678901234567",
        ),
        ("- 1.50", "-1.50"),  # the text keeps the space; the value keeps the scale
        ("-(2.5)", "-2.5"),
        ("--5", "5"),  # two signs fold to +5
    ],
)
def test_a_sign_before_a_number_literal_is_part_of_the_literal(source: str, expected: str) -> None:
    body = parse_expression(source).body
    assert isinstance(body, Constant)
    assert body.text == source
    assert canonical_str(body.value.value) == expected
    assert [type(node) for node in walk(body)] == [Constant]


@pytest.mark.req("R03-FORMULA", "AT-ZERO-MISSING")
def test_a_negated_zero_literal_is_still_the_polymorphic_zero() -> None:
    body = parse_expression("-0.00").body
    assert isinstance(body, Constant)
    assert body.value.is_zero_literal
    assert not body.value.value.is_signed()  # never -0
    assert canonical_str(body.value.value) == "0.00"


@pytest.mark.req("R03-FORMULA", "R05-UNITS-ROUNDING")
def test_negating_a_q_quantity_stays_an_operation_that_keeps_the_label() -> None:
    expr = parse_expression('-q(6.912, "cents/kWh")')
    assert isinstance(expr.body, Unary)
    assert expr.body.op is UnaryOperator.NEG
    assert str(evaluate(expr, {})) == "-6.912 cents/kWh"


@pytest.mark.req("R03-FORMULA", "R05-UNITS-ROUNDING")
def test_negative_q_amounts_keep_every_entered_digit() -> None:
    digits = "1234567890123456789012345678901234567"  # 37 significant digits
    signed = number(f'q(-{digits}, "kW")')
    assert canonical_str(signed.value) == f"-{digits}"
    assert signed == number(f'q("-{digits}", "kW")')
    # -1234567890123456789012345678901234567 cents = -12345678901234567890123456789012345.67 USD
    in_cents = number(f'q(-{digits}, "cents")')
    assert canonical_str(in_cents.value) == "-12345678901234567890123456789012345.67"
    assert in_cents.label_value == Decimal(f"-{digits}")


# Every formula below appears nowhere else in this module, so its first parse (which is cached by
# text) happens under the hostile context; the second evaluation reuses that cached tree.
@pytest.mark.req("R05-DETERMINISM", "R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # -1234.5678 + 0.0001 = -1234.5677 (5 significant digits would give -1234.6)
        ('q(-1234.5678, "USD") + q(0.0001, "USD")', Quantity("-1234.5677", "USD")),
        ("-98765.4321 + 0", Quantity("-98765.4321")),
        ("1 / 3 + 0", Quantity("0.3333333333333333333333333333333333")),  # 34 threes
        ('round_to(-2.3456789, 4, "HALF_EVEN")', Quantity("-2.3457")),  # next digits 789
        # -6.912 cents/kWh x 1000 kWh = -6912 cents = -69.12 USD
        ('q(-6.912, "cents/kWh") * q(1000, "kWh") + 0', Quantity("-69.12", "USD")),
    ],
)
def test_results_do_not_depend_on_the_ambient_decimal_context(
    source: str, expected: Quantity
) -> None:
    with localcontext(Context(prec=3, rounding=ROUND_FLOOR)):
        assert number(source) == expected
    assert number(source) == expected


@pytest.mark.req("R05-DETERMINISM")
@given(dec)
def test_a_decimal_literal_evaluates_to_exactly_itself(value: Decimal) -> None:
    result = number(canonical_str(value))
    assert result.value == value
    assert canonical_str(result.value) == canonical_str(value)


@pytest.mark.req("R05-UNITS-ROUNDING")
@given(dec, dec, dec)
def test_arithmetic_agrees_with_exact_decimal_arithmetic(
    a: Decimal, b: Decimal, c: Decimal
) -> None:
    # |x| <= 1e9 with 6 places: a*b has at most 31 digits, so a*b + c - a is exact at 34 digits and
    # must equal plain Decimal arithmetic carried out with ample precision.
    env: dict[str, EnvValue] = {"a": Quantity(a), "b": Quantity(b), "c": Quantity(c)}
    with localcontext() as context:
        context.prec = 100
        expected = a * b + c - a
    assert number("a * b + c - a", env).value == expected
    assert run("a < b", env) is (a < b)
    assert run("a == b", env) is (a == b)


# ----------------------------------------------------------------------------- the polymorphic zero


@pytest.mark.req("AT-ZERO-MISSING", "P-HISTORY-FLOORS")
@pytest.mark.parametrize(
    ("source", "x", "expected_kw"),
    [
        ("max(x_kW, 0)", "-5", "0"),  # max(-5 kW, 0) = 0 kW: the zero takes kW
        ("max(x_kW, 0)", "5", "5"),
        ("min(x_kW, 0)", "5", "0"),
        ("max(x_kW, 0.00)", "-5", "0"),  # any zero-valued literal is the polymorphic zero
        ("max(x_kW, -0)", "-5", "0"),  # unary minus keeps it polymorphic
        ("x_kW + 0", "5", "5"),
        ("0 - x_kW", "5", "-5"),
        ("max(x_kW - 2 * x_kW, 0)", "5", "0"),  # 5 - 10 = -5 kW -> 0 kW
        # zero literals stay polymorphic through unary -, abs and + - min max among themselves
        ("x_kW + 0e5", "3", "3"),
        ("x_kW + abs(0)", "3", "3"),
        ("x_kW + -(-0)", "3", "3"),
        ("x_kW + (0 + 0)", "3", "3"),
        ("x_kW + max(0, 0.0)", "3", "3"),
    ],
)
def test_the_zero_literal_is_unit_polymorphic(source: str, x: str, expected_kw: str) -> None:
    result = number(source, {"x_kW": kw(x)})
    assert result == kw(expected_kw)
    assert result.unit == KW
    assert not result.is_zero_literal


@pytest.mark.req("AT-ZERO-MISSING")
@pytest.mark.parametrize(
    ("source", "expected"),
    [("x_kW > 0", False), ("x_kW < 0", True), ("x_kW == 0", False), ("x_kW != 0", True)],
)
def test_comparisons_with_the_zero_literal(source: str, expected: bool) -> None:
    assert run(source, {"x_kW": kw("-5")}) is expected


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    "source",
    [
        "x_kW + 1",  # a non-zero literal is dimensionless
        "max(x_kW, 1)",
        "x_kW > 1",
        'q(0, "kW") + q(1, "kWh")',  # q(0, "kW") is a zero with a unit, not the literal
        '(x_kW - x_kW) + q(1, "kWh")',  # a computed zero keeps its unit
        "round_to(0, 2) + x_kW",  # rounding a zero literal yields a plain dimensionless zero
        "0 * 1 + x_kW",  # computed zeros are dimensionless, not polymorphic
        "ceil(0) + x_kW",
        "floor(0) + x_kW",
        "sqrt(0) + x_kW",
    ],
)
def test_only_the_zero_literal_is_unit_polymorphic(source: str) -> None:
    with pytest.raises(UnitError):
        run(source, {"x_kW": kw("-5")})


@pytest.mark.req("R03-FORMULA", "AT-ZERO-MISSING")
def test_a_formula_may_evaluate_to_the_zero_literal() -> None:
    result = number("0")
    assert result.is_zero_literal
    assert result.value == 0


# --------------------------------------------------------------------------- tariff-shaped formulas


@pytest.mark.req("P-HISTORY-FLOORS", "R05-REPORTED-VS-DERIVED")
@pytest.mark.parametrize(
    ("measured", "contract", "expected"),
    [
        ("1500", "1600", "1520"),  # 0.95 x 1600 = 1520 > 1500: the contract floor binds
        ("1500", "1500", "1500"),  # 0.95 x 1500 = 1425 < 1500: measured demand binds
        ("1520", "1600", "1520"),  # a tie: 1520 = 1520
    ],
)
def test_max_of_measured_demand_and_a_contract_floor(
    measured: str, contract: str, expected: str
) -> None:
    env: dict[str, EnvValue] = {
        "demand_measured_kw": kw(measured),
        "demand_contract_kw": kw(contract),
    }
    result = number("max(demand_measured_kw, 0.95 * demand_contract_kw)", env)
    assert result == kw(expected)


@pytest.mark.req("P-CALENDAR-CONDITIONAL", "P-FIXED-LINEAR")
@pytest.mark.parametrize(
    ("month", "expected"),
    [
        ("2026-05", "12375.00"),  # winter: 1500 kW x 8.25 USD/kW = 12375.00
        ("2026-06", "18750.00"),  # summer: 1500 kW x 12.50 USD/kW = 18750.00
        ("2026-07", "18750.00"),
        ("2026-09", "18750.00"),
        ("2026-10", "12375.00"),
        ("2027-01", "12375.00"),
    ],
)
def test_a_seasonal_demand_charge_uses_a_ternary_and_in_months(month: str, expected: str) -> None:
    source = (
        'demand_billed_kw * (q(12.50, "USD/kW") if in_months(6, 7, 8, 9) else q(8.25, "USD/kW"))'
    )
    env = {"demand_billed_kw": kw("1500"), **builtin_values(month, 30)}
    result = number(source, env)
    assert result == Quantity(expected, "USD")
    assert result.unit == USD


@pytest.mark.req("P-ADJUSTMENTS-MINIMUM", "R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    ("kvar", "kw_value", "excess", "charge"),
    [
        # allowance = 1500 kW -> 1500 kVAR / 3 = 500 kVAR; 600 - 500 = 100 kVAR; x 1.25 = 125.00 USD
        ("600", "1500", "100", "125.00"),
        ("450", "1500", "0", "0"),  # 450 - 500 = -50 kVAR -> max(-50 kVAR, 0) = 0 kVAR
        ("500", "1500", "0", "0"),  # exactly at the allowance: 500 - 500 = 0 kVAR
    ],
)
def test_excess_reactive_demand(kvar: str, kw_value: str, excess: str, charge: str) -> None:
    env: dict[str, EnvValue] = {
        "reactive_demand_kvar": Quantity(kvar, "kVAR"),
        "demand_billed_kw": kw(kw_value),
    }
    formula = "max(reactive_demand_kvar - as_reactive_equiv(demand_billed_kw) / 3, 0)"
    result = number(formula, env)
    assert result == Quantity(excess, "kVAR")
    assert result.unit == KVAR
    assert not result.is_zero_literal
    assert number(f'q(1.25, "USD/kVAR") * {formula}', env) == Quantity(charge, "USD")


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_reactive_power_needs_an_explicit_conversion() -> None:
    env: dict[str, EnvValue] = {"kvar": Quantity("600", "kVAR"), "kw": kw("1500")}
    with pytest.raises(UnitError):
        run("kvar - kw / 3", env)


@pytest.mark.req("P-CALENDAR-CONDITIONAL", "R05-UNITS-ROUNDING")
def test_proration_by_billing_days() -> None:
    # 100 USD x 15 day / 30 day (June 2026) = 50 USD; day / day cancels.
    env = builtin_values("2026-06", 15)
    result = number('q(100, "USD") * billing_days / days_in_billing_month', env)
    assert result == Quantity("50", "USD")
    assert result.unit == USD
    # 10 USD/day x 31 day = 310 USD
    assert number('q(10, "USD/day") * billing_days', july()) == Quantity("310", "USD")


# ---------------------------------------------------------------------------------------- functions


@pytest.mark.req("R05-UNITS-ROUNDING", "AT-REFCALC")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('round_to(-1.235, 2, "DOWN")', "-1.23"),  # toward zero: the trailing 5 is dropped
        ("round_to(-1.235, 2)", "-1.24"),  # default HALF_UP: a tie goes away from zero
        ('round_to(-1.235, 2, "HALF_UP")', "-1.24"),
        ('round_to(-1.235, 2, "HALF_EVEN")', "-1.24"),  # tie to the even neighbour: 3 -> 4
        ('round_to(-1.245, 2, "HALF_EVEN")', "-1.24"),  # tie to the even neighbour: 4 stays
        ('round_to(-1.235, 2, "UP")', "-1.24"),  # away from zero
        ('round_to(-1.235, 2, "CEILING")', "-1.23"),  # toward +infinity
        ('round_to(-1.235, 2, "FLOOR")', "-1.24"),  # toward -infinity
        ('round_to(1.235, 2, "FLOOR")', "1.23"),
        ('round_to(1.231, 2, "CEILING")', "1.24"),
        ("round_to(1250, -2)", "1300"),  # 12.50 hundreds -> 13 hundreds (HALF_UP)
        ("round_to(1234.5, -2)", "1200"),  # 12.345 hundreds -> 12 hundreds
        ('round_to(2.5, 0, "HALF_EVEN")', "2"),
        ("round_to(2.5, 0)", "3"),
        ("round_to(1.2, 2)", "1.20"),  # the result carries exactly 2 places
        ("round_to(-0.004, 2)", "0.00"),  # a zero result is never -0
        ("ceil(1499.2)", "1500"),
        ("floor(1499.8)", "1499"),
        ("ceil(-1.5)", "-1"),  # toward +infinity
        ("floor(-1.5)", "-2"),  # toward -infinity
        ("abs(-1.5)", "1.5"),
    ],
)
def test_rounding_functions(source: str, expected: str) -> None:
    assert canonical_str(number(source).value) == expected


@pytest.mark.req("R05-UNITS-ROUNDING", "AT-REFCALC")
def test_round_to_down_in_the_language() -> None:
    # DOWN truncates toward zero: -1.235 -> -1.23 (HALF_UP would give -1.24)
    assert run('round_to(-1.235, 2, "DOWN") == -1.23') is True
    assert run("round_to(-1.235, 2) == -1.23") is False


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_rounding_applies_to_the_canonical_magnitude_and_keeps_the_label() -> None:
    # 6.912 cents/kWh = 0.06912 USD/kWh; 4 places of USD/kWh -> 0.0691 (HALF_UP, next digit 2),
    # which is 6.91 cents/kWh.
    rate = number('round_to(q(6.912, "cents/kWh"), 4)')
    assert rate.value == Decimal("0.0691")
    assert rate.label == "cents/kWh"
    assert rate.label_value == Decimal("6.91")
    # ceil(1499.2 kW) = 1500 kW, still kW
    assert number('ceil(q(1499.2, "kW"))') == kw("1500")
    # abs keeps the label: |-6.912| cents/kWh
    assert number('abs(q(-6.912, "cents/kWh"))').label_value == Decimal("6.912")


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_rounding_a_zero_literal_gives_a_plain_zero() -> None:
    result = number("round_to(0, 2)")
    assert not result.is_zero_literal
    assert canonical_str(result.value) == "0.00"


@pytest.mark.req("P-ADJUSTMENTS-MINIMUM", "R05-UNITS-ROUNDING")
def test_power_factor_hand_computed() -> None:
    # p = 900 kW, q = 436 kVAR: p^2 + q^2 = 810000 + 190096 = 1000096, so
    # pf = 900 / sqrt(1000096) = 0.9 / sqrt(1 + x) with x = 96 / 1000000 = 0.000096.
    # Binomial series (1 + x)^(-1/2) = 1 - x/2 + 3x^2/8 - 5x^3/16 + 35x^4/128 - ...
    #   = 1 - 0.000048 + 0.000000003456 - 0.00000000000027648 + 0.0000000000000000232... - ...
    #   = 0.99995200345572354...
    # pf = 0.9 x 0.99995200345572354... = 0.89995680311015118...
    pf = number('power_factor(q(900, "kW"), q(436, "kVAR"))')
    assert pf.unit == DIMENSIONLESS
    assert quantize(pf.value, 15, RoundingMode.HALF_UP) == Decimal("0.899956803110151")
    rounded = number('round_to(power_factor(q(900, "kW"), q(436, "kVAR")), 10)')
    assert rounded.value == Decimal("0.8999568031")  # the 11th place is 1: rounds down
    # pf^2 x 1000096 = 810000 up to the 34-digit rounding of sqrt and division
    with localcontext() as context:
        context.prec = 100
        assert abs(pf.value * pf.value * 1000096 - 810000) < Decimal("1E-26")


@pytest.mark.req("P-ADJUSTMENTS-MINIMUM")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('power_factor(q(800, "kW"), q(600, "kVAR"))', "0.8"),  # 800 / sqrt(640000 + 360000)
        ('power_factor(q(800, "kW"), q(-600, "kVAR"))', "0.8"),  # leading: q^2 is the same
        ('power_factor(q(3000, "kWh"), q(4000, "kVARh"))', "0.6"),  # 3000 / 5000
        ('power_factor(q(1, "MW"), q(0, "kVAR"))', "1"),  # 1000 kW / sqrt(1000^2) = 1
    ],
)
def test_power_factor_exact_cases(source: str, expected: str) -> None:
    result = number(source)
    assert result.value == Decimal(expected)
    assert result.unit == DIMENSIONLESS


@pytest.mark.req("P-ADJUSTMENTS-MINIMUM")
@pytest.mark.parametrize(
    ("source", "expected_value", "expected_unit", "label"),
    [
        ('apparent(q(800, "kW"), q(600, "kVAR"))', "1000", "kVA", "kVA"),  # sqrt(1000000)
        ('apparent(q(3, "kWh"), q(4, "kVARh"))', "5", "kVAh", "kVAh"),  # sqrt(9 + 16)
        ('apparent(q(0, "kW"), q(0, "kVAR"))', "0", "kVA", "kVA"),
        ('as_reactive_equiv(q(1500, "kW"))', "1500", "kVAR", "kVAR"),  # same magnitude
        ('as_reactive_equiv(q(2000, "kWh"))', "2000", "kVARh", "kVARh"),
        ('as_reactive_equiv(q(1.5, "MW"))', "1500", "kVAR", "kVAR"),  # 1.5 MW = 1500 kW
    ],
)
def test_apparent_and_reactive_equivalent(
    source: str, expected_value: str, expected_unit: str, label: str
) -> None:
    result = number(source)
    assert result == Quantity(expected_value, expected_unit)
    assert result.label == label


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    "source",
    [
        'power_factor(q(800, "kW"), q(600, "kVARh"))',  # different time basis
        'power_factor(q(800, "kWh"), q(600, "kVAR"))',
        'power_factor(q(600, "kVAR"), q(800, "kW"))',  # arguments swapped
        'power_factor(q(800, "kW"), q(600, "kVA"))',
        'power_factor(q(800, "kW"), q(600, "kW"))',
        "power_factor(0, 0)",  # the zero literal is polymorphic only in + - min max and comparisons
        'apparent(q(800, "kW"), q(600, "kW"))',
        'as_reactive_equiv(q(600, "kVAR"))',
        'as_reactive_equiv(q(6, "USD"))',
        'sqrt(q(2, "kW"))',  # an odd exponent has no square root
    ],
)
def test_conversion_functions_check_units(source: str) -> None:
    with pytest.raises(UnitError):
        run(source)


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_sqrt() -> None:
    # sqrt(2) = 1.41421356237309504880168872420969807856967..., 34 significant digits, the 35th is 0
    assert number("sqrt(2)").value == Decimal("1.414213562373095048801688724209698")
    assert number('sqrt(q(4, "kW^2"))') == kw("2")
    assert number("sqrt(0)").value == 0
    with pytest.raises(CalcError, match="negative"):
        run("sqrt(-4)")


@pytest.mark.req("AT-ZERO-MISSING")
def test_power_factor_of_zero_power_is_a_calc_error() -> None:
    with pytest.raises(CalcError, match="undefined"):
        run('power_factor(q(0, "kW"), q(0, "kVAR"))')


@pytest.mark.req("P-FIXED-LINEAR", "R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    ("source", "value", "unit"),
    [
        ('q(200, "h") * q(1500, "kW")', "300000", "kWh"),  # 200 h x 1500 kW = 300000 kWh
        ('q("200", "h") * q(1500, "kW")', "300000", "kWh"),
        ('q(6.912, "cents/kWh") * q(1000, "kWh")', "69.12", "USD"),  # 0.06912 x 1000
        ('q(6.912, "¢/kWh") * q(1000, "kWh")', "69.12", "USD"),
        ('q(21.5, "mills/kWh") * q(1000, "kWh")', "21.5", "USD"),  # 0.0215 x 1000
        ('q(0.06912, "USD/kWh") * q(1000, "kWh")', "69.12", "USD"),  # dollars vs cents agree
        ('q(-5, "kW")', "-5", "kW"),
        ('q("-5", "kW")', "-5", "kW"),
        ('q(1_500, "kW")', "1500", "kW"),
        ('q(1.5, "MW")', "1500", "kW"),  # 1.5 x 1000
        ('q(1000, "kWh") / q(1, "MW")', "1", "h"),  # 1000 kWh / 1000 kW = 1 h
    ],
)
def test_q_attaches_units(source: str, value: str, unit: str) -> None:
    assert number(source) == Quantity(value, unit)


@pytest.mark.req("R05-UNITS-ROUNDING", "P-FIXED-LINEAR")
def test_q_keeps_the_entered_unit_as_its_label() -> None:
    rate = number('q(6.912, "cents/kWh")')
    assert rate.value == Decimal("0.06912")
    assert rate.label == "cents/kWh"
    assert rate.label_value == Decimal("6.912")


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('q(1000, "kWh") == q(1, "MWh")', True),  # equality is on canonical values
        ('q(1000, "kWh") != q(1, "MWh")', False),
        ('q(6.912, "cents/kWh") == q(0.06912, "USD/kWh")', True),
        ('q(1, "kW") < q(2, "kW") <= q(2, "kW")', True),  # chains
        ('q(1, "kW") < q(2, "kW") < q(2, "kW")', False),
        ('q(1, "kW") >= q(2, "kW")', False),
        ('q(3, "kW") > q(2, "kW")', True),
    ],
)
def test_unit_aware_comparisons(source: str, expected: bool) -> None:
    assert run(source) is expected


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    "source",
    [
        "a_kw + b_kwh",
        "a_kw - c_kvar",
        "a_kw == c_kvar",  # == and != are unit-checked too (Quantity.compare)
        "a_kw != c_kvar",
        "a_kw < b_kwh",
        "0 < a_kw < b_kwh",
        "max(a_kw, c_kvar)",
        "min(a_kw, b_kwh, a_kw)",
    ],
)
def test_incompatible_units_are_unit_errors(source: str) -> None:
    env: dict[str, EnvValue] = {
        "a_kw": kw("1"),
        "b_kwh": Quantity("1", "kWh"),
        "c_kvar": Quantity("1", "kVAR"),
    }
    with pytest.raises(UnitError):
        run(source, env)


# ----------------------------------------------------------------------------- in_months, built-ins


@pytest.mark.req("P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("month", "expected"),
    [("2026-05", False), ("2026-06", True), ("2026-09", True), ("2026-10", False)],
)
def test_in_months_tests_the_billing_month_number(month: str, expected: bool) -> None:
    assert run("in_months(6, 7, 8, 9)", builtin_values(month, 30)) is expected


@pytest.mark.req("P-CALENDAR-CONDITIONAL")
def test_builtin_values() -> None:
    # 2024 is a leap year: February has 29 calendar days; the service period had 31 billing days.
    assert builtin_values("2024-02", 31) == {
        "billing_month": 2,
        "billing_days": Quantity("31", "day"),
        "days_in_billing_month": Quantity("29", "day"),
        "period_year": 2024,
    }
    assert builtin_values("2026-02", 28)["days_in_billing_month"] == Quantity("28", "day")
    assert set(builtin_values("2026-07", 31)) == BUILTIN_NAMES
    # 31 billing days over February 2026 (28 days): 31 / 28 = 1.107142857142857142857142857...,
    # 34 significant digits; the 35th is 1, so it rounds down.
    ratio = number("billing_days / days_in_billing_month", builtin_values("2026-02", 31))
    assert ratio == Quantity("1.107142857142857142857142857142857")


@pytest.mark.req("P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("source", "month", "expected"),
    [
        ("period_year >= 2026 and billing_month >= 6", "2026-07", True),
        ("period_year >= 2026 and billing_month >= 6", "2025-07", False),
        ("billing_month == 7", "2026-07", True),
        ("billing_days / days_in_billing_month == 0.5", "2026-06", True),  # 15 / 30
    ],
)
def test_builtins_in_formulas(source: str, month: str, expected: bool) -> None:
    assert run(source, builtin_values(month, 15)) is expected


@pytest.mark.req("P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("2026-13", 30), DateError),
        (("2026-07", 0), DateError),
        (("2026-07", MAX_BILLING_DAYS + 1), DateError),
        (("2026-07", True), TypeError),
        (("2026-07", "31"), TypeError),
    ],
)
def test_builtin_values_validates_its_arguments(
    args: tuple[str, int], error: type[Exception]
) -> None:
    with pytest.raises(error):
        builtin_values(*args)


@pytest.mark.req("P-CALENDAR-CONDITIONAL", "R03-FORMULA")
@pytest.mark.parametrize("value", [13, 0, Quantity("7", "kW"), Quantity("6.5"), True])
def test_in_months_needs_a_month_number(value: EnvValue) -> None:
    with pytest.raises(ExpressionError, match="month number"):
        run("in_months(6)", {"billing_month": value})


@pytest.mark.req("R05-PARTIAL-BLOCKED")
def test_in_months_without_a_billing_month_is_missing_input() -> None:
    with pytest.raises(MissingInputError) as info:
        run("in_months(6)")
    assert info.value.name == "billing_month"


# ----------------------------------------------------------------------------------- missing inputs


@pytest.mark.req("R05-PARTIAL-BLOCKED", "AT-ZERO-MISSING")
def test_a_missing_input_raises_missing_input_error_naming_the_key() -> None:
    with pytest.raises(MissingInputError) as info:
        run(
            "max(demand_measured_kw, 0.95 * demand_contract_kw)",
            {"demand_contract_kw": kw("1600")},
        )
    assert info.value.name == "demand_measured_kw"
    assert info.value.names == ("demand_measured_kw",)
    assert "demand_measured_kw" in str(info.value)
    assert isinstance(info.value, EngineError)


@pytest.mark.req("R05-PARTIAL-BLOCKED", "AT-ZERO-MISSING")
@pytest.mark.parametrize("marker", [MISSING, MissingValue("not on the invoice")])
@pytest.mark.parametrize(
    "source", ["max(demand_measured_kw, 0.95 * demand_contract_kw)", "demand_measured_kw + 0"]
)
def test_a_null_input_is_missing_not_zero(marker: MissingValue, source: str) -> None:
    # Were null read as 0 kW, max(0, 0.95 x 1600) would silently give 1520 kW.
    env: dict[str, EnvValue] = {"demand_measured_kw": marker, "demand_contract_kw": kw("1600")}
    with pytest.raises(MissingInputError) as info:
        run(source, env)
    assert info.value.names == ("demand_measured_kw",)


@pytest.mark.req("R05-PARTIAL-BLOCKED")
def test_every_needed_missing_name_is_reported_once_in_evaluation_order() -> None:
    env: dict[str, EnvValue] = {
        "energy_rate": Quantity("0.05", "USD/kWh"),
        "demand_rate": Quantity("12", "USD/kW"),
    }
    source = "energy_kwh * energy_rate + demand_kw * demand_rate + energy_kwh * energy_rate"
    with pytest.raises(MissingInputError) as info:
        run(source, env)
    assert info.value.name == "energy_kwh"
    assert info.value.names == ("energy_kwh", "demand_kw")


@pytest.mark.req("R05-PARTIAL-BLOCKED", "P-CALENDAR-CONDITIONAL")
@pytest.mark.parametrize(
    ("source", "month", "expected"),
    [
        # only the selected branch is evaluated
        ("summer_rate if in_months(6, 7, 8, 9) else winter_rate", "2026-01", "winter"),
        ("summer_rate if not in_months(1, 2) else winter_rate", "2026-01", "winter"),
        # a definite False decides "and"; a definite True decides "or" -- in either position
        ("x > 0 and in_months(6)", "2026-01", False),
        ("in_months(6) and x > 0", "2026-01", False),
        ("x > 0 or in_months(1)", "2026-01", True),
        ("in_months(1) or x > 0", "2026-01", True),
        # a chain is a conjunction: 1 kW > 2 kW is False whatever x is
        ('q(1, "kW") > q(2, "kW") > x', "2026-01", False),
        ('x > q(2, "kW") > q(3, "kW")', "2026-01", False),
    ],
)
def test_unneeded_missing_inputs_do_not_block(source: str, month: str, expected: object) -> None:
    env: dict[str, EnvValue] = {
        **builtin_values(month, 31),
        "winter_rate": Quantity("8.25", "USD/kW"),
    }
    result = run(source, env)
    if expected == "winter":
        assert result == Quantity("8.25", "USD/kW")
    else:
        assert result is expected


@pytest.mark.req("R05-PARTIAL-BLOCKED")
@pytest.mark.parametrize(
    ("source", "month", "names"),
    [
        ("summer_rate if in_months(6, 7, 8, 9) else winter_rate", "2026-07", ("summer_rate",)),
        ("x > 0 or in_months(6)", "2026-01", ("x",)),  # False does not decide "or"
        ("in_months(1) and x > 0", "2026-01", ("x",)),  # True does not decide "and"
        ("not x > 0", "2026-01", ("x",)),
        ("a if x > 0 else b", "2026-01", ("x",)),  # unknown condition: branches not consulted
        ('q(3, "kW") > x > q(2, "kW")', "2026-01", ("x",)),
        ("min(a, b, a)", "2026-01", ("a", "b")),
    ],
)
def test_needed_missing_inputs_are_reported(
    source: str, month: str, names: tuple[str, ...]
) -> None:
    with pytest.raises(MissingInputError) as info:
        run(source, builtin_values(month, 31))
    assert info.value.names == names


@pytest.mark.req("R05-PARTIAL-BLOCKED")
def test_missing_input_error_lists_distinct_names() -> None:
    error = MissingInputError("a", "b", "a")
    assert error.name == "a"
    assert error.names == ("a", "b")
    assert str(error) == "missing inputs: a, b"
    assert str(MissingInputError("a")) == "missing input: a"


GUARD_ENV: dict[str, EnvValue] = {"kwh": Quantity("1000", "kWh"), "kw": kw("0"), "x": kw("5")}


@pytest.mark.req("R05-PARTIAL-BLOCKED", "AT-ZERO-MISSING")
@pytest.mark.parametrize(
    ("source", "names"),
    [
        # has_meter false -> False without dividing; true -> 1000 kWh / 0 kW fails. The answer
        # depends on the missing input, so it is requested instead of reporting an error.
        ("has_meter and kwh / kw > q(200, 'h')", ("has_meter",)),
        ("not has_meter or kwh / kw > q(200, 'h')", ("has_meter",)),
        # a chain is a conjunction: m < 1 false stops it before the failing comparator
        ("m < 1 < kwh / kw", ("m",)),
        # a later definite False cannot decide: with has_meter true, evaluation fails first
        ("has_meter and kwh / kw > q(200, 'h') and 1 > 2", ("has_meter",)),
        # later operands are not consulted, so b is not requested
        ("a and kwh / kw > q(200, 'h') and b", ("a",)),
        # a unit mismatch or a kind error behind the guard behaves the same way
        ("m < 1 < q(1, 'kW')", ("m",)),
        ("m > 0 and x", ("m",)),
        # the conditional form agrees: its branches are skipped when the condition is unknown
        ("(kwh / kw > q(200, 'h')) if has_meter else 1 > 2", ("has_meter",)),
    ],
)
def test_an_error_behind_a_missing_guard_is_missing_input(
    source: str, names: tuple[str, ...]
) -> None:
    with pytest.raises(MissingInputError) as info:
        run(source, GUARD_ENV)
    assert info.value.names == names


@pytest.mark.req("R05-PARTIAL-BLOCKED", "AT-ZERO-MISSING")
@pytest.mark.parametrize(
    ("source", "env", "outcome"),
    [
        # known guards short-circuit exactly like Python
        ("has_meter and kwh / kw > q(200, 'h')", {"has_meter": False}, False),
        ("has_meter or kwh / kw > q(200, 'h')", {"has_meter": True}, True),
        ("2 < 1 < kwh / kw", {}, False),
        ("has_meter and kwh / kw > q(200, 'h')", {"has_meter": True}, CalcError),
        # an error met before any unknown operand happens whatever the missing value is
        ("kwh / kw > q(200, 'h') and has_meter", {}, CalcError),
        ("m < kwh / kw", {}, CalcError),  # the first comparator is always evaluated
        ("max(m, kwh / kw)", {}, CalcError),  # so are all function arguments
    ],
)
def test_errors_that_do_not_depend_on_a_missing_input_are_reported(
    source: str, env: dict[str, EnvValue], outcome: object
) -> None:
    full_env = {**GUARD_ENV, **env}
    if outcome is CalcError:
        with pytest.raises(CalcError, match="division by zero"):
            run(source, full_env)
    else:
        assert run(source, full_env) is outcome


# --------------------------------------------------------------------------------------- arithmetic


@pytest.mark.req("AT-ZERO-MISSING")
@pytest.mark.parametrize(
    "source",
    [
        "x / 0",
        "x / 0.00",
        "x / zero_kw",
        "1 / (x - x)",
        'q(5, "USD") / q(0, "kWh")',
    ],
)
def test_division_by_zero_is_a_calc_error(source: str) -> None:
    with pytest.raises(CalcError, match="division by zero"):
        run(source, {"x": kw("5"), "zero_kw": kw("0")})


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "error", "offset", "line", "column"),
    [
        ("a + b / c", CalcError, 4, 1, 5),  # the failing node is "b / c" (offset 4), not the "+"
        ("a + (b + c_kwh)", UnitError, 5, 1, 6),  # spans exclude parentheses: "b + c_kwh"
        ("(a +\n  b / c)", CalcError, 7, 2, 3),  # line 2 starts at offset 5; b is its 3rd character
        ("max(a, c_kwh) * 2", UnitError, 0, 1, 1),
        ("2 * power_factor(q(0, 'kW'), q(0, 'kVAR'))", CalcError, 4, 1, 5),
        ("a < c_kwh", UnitError, 0, 1, 1),  # a comparison is located at its chain
        ("sqrt(-4) + 1", CalcError, 0, 1, 1),
    ],
)
def test_evaluation_errors_carry_the_position_of_the_failing_node(
    source: str, error: type[EngineError], offset: int, line: int, column: int
) -> None:
    env: dict[str, EnvValue] = {
        "a": kw("1"),
        "b": kw("1"),
        "c": Quantity("0"),
        "c_kwh": Quantity("1", "kWh"),
    }
    with pytest.raises(error) as info:
        run(source, env)
    assert type(info.value) is error  # the class S04 classifies by is unchanged
    assert (info.value.offset, info.value.line, info.value.column) == (offset, line, column)
    assert str(info.value).endswith(f"(line {line}, column {column})")


@pytest.mark.req("P-FIXED-LINEAR")
def test_operators() -> None:
    # (1500 kW - 100 kW) x 2 / 4 = 700 kW; -(700 kW) = -700 kW
    assert number('-((x - q(100, "kW")) * 2 / 4)', {"x": kw("1500")}) == kw("-700")
    assert run("not (1 > 2)") is True
    assert run("1 < 2 and 2 < 3 or 1 > 2") is True


@pytest.mark.req("R03-FORMULA", "P-FIXED-LINEAR")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("10 - 3 - 2 == 5", True),  # left-associative: (10 - 3) - 2
        ("24 / 4 / 2 == 3", True),  # (24 / 4) / 2
        ("2 * 3 + 4 * 5 == 26", True),  # * before +
        ("-2 * 3 == -6", True),
        ("2 - -3 == 5", True),
        ("not 1 < 2", False),  # not binds looser than <
        ("(1 + 2 if 1 > 2 else 3) == 3", True),  # the conditional binds loosest
        ("(1 + 2 if 2 > 1 else 3) == 3", True),
        ("(5 if 1 > 2 else 6 if 1 > 2 else 7) == 7", True),  # right-nested conditionals
        ("1 < 2 == 2", True),  # a chain: 1 < 2 and 2 == 2
        ("1 < 2 == 3", False),
        ("1 < 2 > 0", True),
        ("1 > 2 or 2 > 1 and 3 > 4", False),  # and before or: False or (True and False)
        ("1 / 3 * 3 == 1", False),  # 0.333...3 (34 digits) x 3 = 0.999...9
    ],
)
def test_operator_precedence_and_associativity(source: str, expected: bool) -> None:
    assert run(source) is expected


# ------------------------------------------------------------------------------- environment values


@pytest.mark.req("R03-FORMULA", "AT-ZERO-MISSING")
def test_environment_value_types() -> None:
    assert number("n + 1", {"n": 7}) == Quantity("8")  # an int is dimensionless
    assert run("flag", {"flag": True}) is True
    assert run("flag == (x > 0)", {"flag": True, "x": kw("1")}) is True  # conditions support ==
    assert run("flag != flag", {"flag": False}) is False
    for bad in (A_FLOAT, Decimal("1"), "1", None):
        with pytest.raises(TypeError, match="environment value"):
            run("x", {"x": bad})  # type: ignore[dict-item]


@pytest.mark.req("R03-FORMULA")
def test_an_environment_int_outside_the_engine_range_is_a_calc_error() -> None:
    # 10^6144 has 6145 digits, the most an engine decimal can have (A§4.1); 10^6145 has one more.
    assert number("n", {"n": 10**6144}).value == Decimal("1E+6144")
    with pytest.raises(CalcError, match="outside the engine range") as info:
        run("1 + n", {"n": 10**6145})
    assert info.value.offset == 4  # the name n


# --------------------------------------------------------------------------------- referenced names


@pytest.mark.req("PROC-DEPENDENCIES-FIRST")
def test_referenced_names_exclude_functions_and_builtins() -> None:
    expr = parse_expression(
        "round_to(max(demand_measured_kw, 0.95 * demand_contract_kw) * demand_rate, 2)"
        ' if in_months(6, 7, 8, 9) and billing_days > q(0, "day") and period_year >= 2026'
        ' and billing_month >= 6 and days_in_billing_month > q(0, "day")'
        " else abs(x_lips_floor_kw) * demand_rate"
    )
    assert referenced_names(expr) == (
        "demand_contract_kw",
        "demand_measured_kw",
        "demand_rate",
        "x_lips_floor_kw",
    )
    assert referenced_names(expr.body) == referenced_names(expr)


@pytest.mark.req("PROC-DEPENDENCIES-FIRST")
@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("billing_days", ()),
        ('q(1, "kW")', ()),
        ("in_months(6)", ()),
        ("min(b, a, b)", ("a", "b")),  # sorted and distinct
        ("energy_kwh * fuel_factor", ("energy_kwh", "fuel_factor")),
        ("x if c else y", ("c", "x", "y")),  # both branches: the graph is conservative
    ],
)
def test_referenced_names_cases(source: str, names: tuple[str, ...]) -> None:
    assert referenced_names(parse_expression(source)) == names


@pytest.mark.req("R03-FORMULA", "PROC-DEPENDENCIES-FIRST")
def test_walk_is_pre_order_in_source_order() -> None:
    expr = parse_expression("a + b * c")
    nodes = list(walk(expr))
    assert [type(node).__name__ for node in nodes] == ["Binary", "Name", "Binary", "Name", "Name"]
    assert [node.id for node in nodes if isinstance(node, Name)] == ["a", "b", "c"]
    root = expr.body
    assert isinstance(root, Binary)
    assert root.op is BinaryOperator.ADD
    assert children(root) == (root.left, root.right)
    assert children(root.left) == ()


@pytest.mark.req("R03-FORMULA", "PROC-DEPENDENCIES-FIRST")
@pytest.mark.parametrize(
    ("source", "names"),
    [
        ("x if c else y", ["x", "c", "y"]),  # source order, not evaluation order (c first)
        ("(a if b > c else d) + e", ["a", "b", "c", "d", "e"]),
        ("max(p, q1) if in_months(6) else g", ["p", "q1", "g"]),
    ],
)
def test_walk_visits_a_conditional_in_source_order(source: str, names: list[str]) -> None:
    nodes = list(walk(parse_expression(source)))
    assert [node.id for node in nodes if isinstance(node, Name)] == names
    starts = [node.span.start for node in nodes]
    assert starts == sorted(starts)  # pre-order in source order: every node starts after its parent
    conditional = next(node for node in nodes if isinstance(node, Conditional))
    assert children(conditional) == (
        conditional.if_true,
        conditional.condition,
        conditional.if_false,
    )


@pytest.mark.req("R03-FORMULA", "R03-CHARGE-TO-PASSAGE")
def test_spans_locate_nodes_in_the_source() -> None:
    expr = parse_expression('  energy_kwh * q(6.912, "cents/kWh")')
    root = expr.body
    assert isinstance(root, Binary)
    assert isinstance(root.right, Constant)
    # the q(...) call starts after "  energy_kwh * " (2 + 10 + 3 = 15 characters)
    assert root.right.span.start == 15
    assert expr.source[root.right.span.start : root.right.span.end] == 'q(6.912, "cents/kWh")'
    assert root.right.text == 'q(6.912, "cents/kWh")'
    assert (root.span.line, root.span.column) == (1, 3)


@pytest.mark.req("R03-FORMULA")
def test_spans_across_lines_and_multibyte_characters() -> None:
    source = '  (q(1, "¢") *\n   alpha\n + beta)'
    nodes = list(walk(parse_expression(source)))
    for node in nodes:
        span = node.span
        line = source.count("\n", 0, span.start) + 1
        column = span.start - (source.rfind("\n", 0, span.start) + 1) + 1
        assert (span.line, span.column) == (line, column)
    # line 2 starts at offset 15 ('  (q(1, "¢") *' is 14 characters, then the newline); alpha
    # follows 3 spaces. Line 3 starts at 24 (" + beta)"); beta is its 4th character.
    names = [(node.id, node.span) for node in nodes if isinstance(node, Name)]
    assert [(name, span.start, span.end, span.line, span.column) for name, span in names] == [
        ("alpha", 18, 23, 2, 4),
        ("beta", 27, 31, 3, 4),
    ]


# ------------------------------------------------------------------------------ rejected constructs

REJECTED = [
    # (source, message fragment, offset)
    ("__import__('os')", "unknown function", 0),
    ("__import__", "invalid name", 0),
    ("_private", "invalid name", 0),
    ("x.y", "attribute", 0),
    ("q(1, 'kW').value", "attribute", 0),
    ("x ** 2", "operator **", 2),
    ("x[0]", "subscript", 0),
    ("lambda: 1", "lambda", 0),
    ("[y for y in z]", "comprehension", 0),
    ("{y for y in z}", "comprehension", 0),
    ("{k: v for k, v in z}", "comprehension", 0),
    ("max(a, (y for y in z))", "comprehension", 7),
    ("max(x, key=y)", "keyword", 7),
    ("round_to(x, 2, mode='DOWN')", "keyword", 15),
    ("max(*xs)", "starred", 4),
    ("foo(x)", "unknown function", 0),
    ("eval('1')", "unknown function", 0),
    ("getattr(x, 'y')", "unknown function", 0),
    ("print(x)", "unknown function", 0),
    ("max(x, y)(1)", "only these functions", 0),
    ("ｍａｘ(a, b)", "unknown function", 0),  # fullwidth letters NFKC-normalize to "max"
    ("ﬁx", "invalid name", 0),  # NFKC would silently read "fix"
    ("f'{x}'", "f-string", 0),
    ("F'{x}'", "f-string", 0),
    ("rf'{x}'", "f-string", 0),
    ("x + f'{1if y else 2}'", "f-string", 4),  # refused before the parser scans its code
    ("(x := 1)", "assignment", 1),
    ("(x, y)", "tuple", 0),
    ("[x]", "list", 0),
    ("{x}", "set", 0),
    ("{1: 2}", "dict", 0),
    ("(yield x)", "yield", 1),
    ("await x", "await", 0),
    ("x // 2", "operator //", 2),
    ("x % 2", "operator %", 2),
    ("x @ y", "operator @", 2),
    ("x & y", "operator &", 2),
    ("x | y", "operator |", 2),
    ("x ^ y", "operator ^", 2),
    ("x << 1", "operator <<", 2),
    ("x >> 1", "operator >>", 2),
    ("~x", "unary ~", 0),
    ("+x", "unary +", 0),
    ("x is y", "'is'", 2),
    ("x is not y", "'is not'", 2),
    ("x in y", "'in'", 2),
    ("x not in y", "'not in'", 2),
    ("True", "True is not allowed", 0),
    ("False", "False is not allowed", 0),
    ("None", "None is not allowed", 0),
    ("...", "... is not allowed", 0),
    ('"kWh"', "text is only allowed", 0),
    ("b'kW'", "bytes", 0),
    ("1j", "decimal literals", 0),
    ("0x1F", "decimal literals", 0),
    ("0o17", "decimal literals", 0),
    ("0b101", "decimal literals", 0),
    ("1e9999", "outside the engine range", 0),
    ("max", "must be called", 0),
    ("q", "must be called", 0),
    ("x # a comment can hide arithmetic", "comments", 2),
    ("x \\\n + 1", "backslash", 2),
    ("x\r+ 1", "U+000D", 1),
    ("x\x00", "U+0000", 1),
    ("x + 1", "U+2028", 1),
    ("x\ud800", "U+D800", 1),  # a lone surrogate cannot be encoded for the parser
    ("x + 1", "syntax error", 1),
    ("x +", "syntax error", 3),  # unexpected end: reported at the end of the line
    ("x if y", "syntax error", 0),
    ("", "empty expression", 0),
    ("  \n ", "empty expression", 4),
    # functions: arity and literal-only arguments
    ("max(x)", "at least 2 arguments", 0),
    ("abs(x, y)", "exactly 1 argument", 0),
    ("power_factor(x)", "exactly 2 arguments", 0),
    ("q(1)", "q() takes", 0),
    ("q(1, 'h', 2)", "q() takes", 0),
    ("q(x, 'h')", "amount of q()", 2),
    ("q(1 + 1, 'h')", "amount of q()", 2),
    ("q(-'5', 'h')", "amount of q()", 2),
    ("q('1 000', 'h')", "not a decimal number", 2),
    ("q('abc', 'h')", "not a decimal number", 2),
    ("q(1, h)", "unit of q()", 5),
    # text must be one plain quoted literal: Python would silently join "kW" "h" into "kWh"
    ('q(1, "kW" "h")', "one quoted text literal", 5),
    ("q(1, u'kW')", "one quoted text literal", 5),
    ("q(1, r'kW')", "one quoted text literal", 5),
    ('q(1, """kW""")', "one quoted text literal", 5),
    ("q('1' '5', 'kW')", "one quoted decimal", 2),
    ("q(u'15', 'kW')", "one quoted decimal", 2),
    ('1 + q(1, "furlong")', "invalid unit", 9),
    ('q("1E+6144", "MW")', "outside the engine range", 2),  # 1E+6147 kW after scaling
    ("round_to(x)", "round_to() takes", 0),
    ("round_to(x, 2, 'UP', 1)", "round_to() takes", 0),
    ("round_to(x, n)", "whole-number literal", 12),
    ("round_to(x, 2.0)", "whole-number literal", 12),
    ("round_to(x, 0x2)", "whole-number literal", 12),
    ("round_to(x, 7000)", "places must lie between", 12),
    ("round_to(x, -6145)", "places must lie between", 12),
    ("round_to(x, 2, 'down')", "unknown rounding mode", 15),
    ("round_to(x, 2, DOWN)", "mode of round_to()", 15),
    ('round_to(x, 2, "DO" "WN")', "one quoted text literal", 15),
    ("round_to(x, 2, u'DOWN')", "one quoted text literal", 15),
    ("in_months()", "at least one month", 0),
    ("in_months(0)", "not between 1 and 12", 10),
    ("in_months(6, 13)", "not between 1 and 12", 13),
    ("in_months(-1)", "whole-number literal", 10),
    ("in_months(6.5)", "whole-number literal", 10),
    ("in_months(x)", "whole-number literal", 10),
    ("in_months('6')", "whole-number literal", 10),
    ("in_months(6, 6)", "listed twice", 13),
]


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(("source", "fragment", "offset"), REJECTED)
def test_rejected_constructs_raise_positioned_errors(
    source: str, fragment: str, offset: int
) -> None:
    error = parse_error(source)
    assert fragment in error.message
    assert error.offset == offset
    assert isinstance(error, EngineError)


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "offset"),
    [
        ("(a > b) + 1", 1),
        ("1 - (a > b)", 5),
        ("-(a > b)", 2),
        ("not 5", 4),
        ("5 and x", 0),
        ("x or in_months(6) or 1", 21),
        ("x if 1 else y", 5),
        ("1 if x else (a > b)", 13),  # branches must agree when both kinds are evident
        ("(a > b) < c", 1),
        ("1 == (a > b)", 6),
        ("max(a > b, c)", 4),
        ("round_to(in_months(6), 2)", 9),
        ("in_months(6) + 1", 0),
        ("q(1, 'kW') if x else 1 > 2", 21),  # q(1, 'kW') is 10 chars; " if x else " is 11
    ],
)
def test_evident_kind_errors_are_rejected_at_parse_time(source: str, offset: int) -> None:
    error = parse_error(source)
    assert "condition" in error.message  # every kind message names numbers vs conditions
    assert error.offset == offset


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "offset", "fragment"),
    [
        ("x + flag", 4, "expected a number"),  # f of flag is at offset 4
        ("-flag", 1, "expected a number"),
        ("round_to(flag, 2)", 9, "expected a number"),
        ("max(x, flag)", 7, "expected a number"),
        ("x if x else x", 5, "expected a condition"),
        ("not x", 4, "expected a condition"),
        ("flag and x", 9, "expected a condition"),
        ("flag < flag", 0, "only support == and !="),
        ("flag == x", 0, "cannot compare a number with a condition"),
    ],
)
def test_kind_errors_found_at_evaluation_carry_positions(
    source: str, offset: int, fragment: str
) -> None:
    with pytest.raises(ExpressionError) as info:
        run(source, {"flag": True, "x": kw("5")})
    assert fragment in info.value.message
    assert info.value.offset == offset


@pytest.mark.req("R03-FORMULA", "R03-CHARGE-TO-PASSAGE")
@pytest.mark.parametrize(
    ("source", "offset", "line", "column"),
    [
        ("a + b ** 2", 6, 1, 7),  # a(0) (1) +(2) (3) b(4) (5) *(6)
        ("  x ** 2", 4, 1, 5),  # leading blanks are skipped; positions refer to the original text
        ('q(1, "¢/kWh") ** 2', 14, 1, 15),  # columns count characters: ¢ is 1 (2 UTF-8 bytes)
        ("(a +\n b ** 2)", 8, 2, 4),  # line 2 " b ** 2)" starts at offset 5; ** is its 4th char
        ("\n\n  ﬁx", 4, 3, 3),
        ('1 + q(1, "furlong")', 9, 1, 10),
        ("max(x, key=y)", 7, 1, 8),
        ("x +", 3, 1, 4),
        ("in_months(6, 13)", 13, 1, 14),
        # line 3 starts at offset 16 (after '\n\t (q(1, "¢") +\n'); "\t q(2, "¢") + " is 14
        # characters, so x.y starts at offset 30, column 15
        ('\n\t (q(1, "¢") +\n\t q(2, "¢") + x.y)', 30, 3, 15),
    ],
)
def test_errors_report_offset_line_and_column(
    source: str, offset: int, line: int, column: int
) -> None:
    error = parse_error(source)
    assert (error.offset, error.line, error.column) == (offset, line, column)
    assert f"(line {line}, column {column})" in str(error)


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "offset"),
    [
        ("1if x else 2", 1),  # Python reads "1 if x else 2" with only a SyntaxWarning
        ("x if y else 2or z", 13),  # x(0) (1) if(2-3) (4) y(5) (6) else(7-10) (11) 2(12) or(13)
        ("1_000and x", 5),
        ("1else", 1),  # "e" starts no exponent here: the number is 1
        ("1e5if x else 2", 3),
        ("1.if x else 2", 2),
        (".5or x", 2),
        ("0x1for x", 4),  # hexadecimal digits include f
        ("1jif x else 2", 2),
        ("x.5if y else 2", 3),
        ("q(1, 'kW') if 1else 2", 15),
        ("1x", 1),  # Python refuses this one outright; the message is the same
    ],
)
@pytest.mark.parametrize("action", ["error", "default"])
def test_a_number_glued_to_a_word_is_rejected_without_warnings(
    source: str, offset: int, action: Literal["error", "default"]
) -> None:
    # Refused by a lexical scan before ast.parse runs, so Python never warns: nothing reaches
    # stderr, and the message and position are the same whatever the warning filters say.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter(action)
        error = parse_error(source)
    assert caught == []
    assert "separated" in error.message
    assert error.offset == offset


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    "source", ["x1if", 'q(1, "kW") + x2', "x + 1_000.5e-1_0", "max(1,2)", "1 if x else 2"]
)
def test_digits_inside_words_and_complete_numbers_are_accepted(source: str) -> None:
    assert isinstance(parse_expression(source), Expr)


@pytest.mark.req("R03-FORMULA")
def test_errors_without_a_position_print_only_the_message() -> None:
    assert str(ExpressionError("bad")) == "bad"
    # unit and arithmetic errors raised outside a formula carry no position either
    for error in (CalcError("division by zero"), UnitError("units differ")):
        assert error.offset is None
        assert str(error) == error.args[0]


# ------------------------------------------------------------------------------------------- limits


@pytest.mark.req("R03-FORMULA")
def test_source_length_limit() -> None:
    at_limit = "x" * MAX_SOURCE_LENGTH  # a 2,000-character name is still a valid formula
    assert referenced_names(parse_expression(at_limit)) == (at_limit,)
    error = parse_error("x" * (MAX_SOURCE_LENGTH + 1))
    assert "2000 characters" in error.message
    assert error.offset == MAX_SOURCE_LENGTH
    huge = parse_error("x" * 10_000_000)  # rejected before any parsing work
    assert huge.offset == MAX_SOURCE_LENGTH


@pytest.mark.req("R03-FORMULA")
@pytest.mark.parametrize(
    ("source", "accepted"),
    [
        ("-" * 49 + "x", True),  # 49 negations above the name: depth 50
        ("-" * 50 + "x", False),  # depth 51
        ("abs(" * 49 + "x" + ")" * 49, True),
        ("abs(" * 50 + "x" + ")" * 50, False),
        (" + ".join(f"x{i}" for i in range(50)), True),  # 49 nested additions + a name = 50
        (" + ".join(f"x{i}" for i in range(51)), False),
        ("(" * 60 + "x" + ")" * 60, True),  # parentheses add no node
        ("(" * 300 + "x" + ")" * 300, False),  # beyond Python's own parenthesis limit
        ("-" * 1999 + "x", False),  # 2,000 characters: never a RecursionError
        ("x" + "+x" * 999, False),
        ("not " * 499 + "x", False),
    ],
)
def test_depth_limit(source: str, accepted: bool) -> None:
    assert MAX_DEPTH == 50
    if accepted:
        assert isinstance(parse_expression(source), Expr)
    else:
        assert "nest" in parse_error(source).message


@pytest.mark.req("R03-FORMULA")
def test_parse_requires_text() -> None:
    with pytest.raises(TypeError):
        parse_expression(b"x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        evaluate("x", {})  # type: ignore[arg-type]


# ------------------------------------------------------------------------------- trace, determinism


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_trace_records_substitutions_and_conversions() -> None:
    expr = parse_expression(
        'q(1.25, "USD/kVAR") * max(reactive_demand_kvar - as_reactive_equiv(demand_billed_kw) / 3,'
        ' 0) if in_months(6, 7, 8, 9) else q(0, "USD")'
    )
    env = {
        **july(),
        "reactive_demand_kvar": Quantity("600", "kVAR"),
        "demand_billed_kw": kw("1500"),
        "unused": kw("1"),
    }
    evaluation = evaluate_traced(expr, env)
    # 1.25 USD/kVAR x (600 - 1500 / 3) kVAR = 1.25 x 100 = 125.00 USD
    assert evaluation.value == Quantity("125.00", "USD")
    assert evaluation.substitutions == (
        Substitution("billing_month", Quantity("7")),  # the condition is evaluated first
        Substitution("reactive_demand_kvar", Quantity("600", "kVAR")),
        Substitution("demand_billed_kw", kw("1500")),
    )
    (conversion,) = evaluation.conversions
    assert conversion.function is Function.AS_REACTIVE_EQUIV
    assert conversion.arguments == (kw("1500"),)
    assert conversion.result == Quantity("1500", "kVAR")
    span = conversion.span
    assert expr.source[span.start : span.end] == "as_reactive_equiv(demand_billed_kw)"
    assert isinstance(conversion, Conversion)


@pytest.mark.req("R05-UNITS-ROUNDING", "R03-CHARGE-TO-PASSAGE")
def test_trace_lists_each_name_once() -> None:
    evaluation = evaluate_traced(parse_expression("x + x * 2"), {"x": kw("2")})
    assert evaluation.value == kw("6")  # 2 + 2 x 2
    assert evaluation.substitutions == (Substitution("x", kw("2")),)
    pf = evaluate_traced(parse_expression('power_factor(p, q(600, "kVAR"))'), {"p": kw("800")})
    assert [conversion.function for conversion in pf.conversions] == [Function.POWER_FACTOR]
    assert pf.conversions[0].result == Quantity("0.8")


@pytest.mark.req("R05-DETERMINISM")
def test_parsing_and_evaluation_are_deterministic_and_immutable() -> None:
    source = 'max(demand_measured_kw, 0.95 * demand_contract_kw) * q(12.5, "USD/kW")'
    first, second = parse_expression(source), parse_expression(source)
    assert first == second
    assert str(first) == source
    env: dict[str, EnvValue] = {
        "demand_measured_kw": kw("1500"),
        "demand_contract_kw": kw("1600"),
    }
    # max(1500, 1520) kW x 12.5 USD/kW = 19000 USD
    assert evaluate_traced(first, env) == evaluate_traced(second, env)
    assert evaluate(first, env) == Quantity("19000", "USD")
    # the environment's insertion order does not matter: the trace follows the formula
    reordered = dict(reversed(list({**env, **july(), "unused": kw("1")}.items())))
    assert evaluate_traced(first, reordered) == evaluate_traced(first, env)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.source = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.body.span = first.body.span  # type: ignore[misc]


FUZZ_TOKENS = [
    *("x", "y", "z", "c", "0", "1", "2.5", "1e9999", "6", "12", "13", "-", "+", "*", "/"),
    *("(", ")", ",", " ", ".", "[", "]", "\n", "and", "or", "not", "if", "else", "<", "<="),
    *("==", "!=", ">", "min(", "max(", "abs(", "round_to(", "q(", '"kW"', '"kWh"', '"kVAR"'),
    *('"DOWN"', "in_months(", "power_factor(", "apparent(", "as_reactive_equiv(", "sqrt("),
    *("ceil(", "floor(", "billing_month", "billing_days", "¢", "'", "f", "_"),
]
FUZZ_VALUES: list[EnvValue] = [
    kw("5"),
    Quantity("0"),
    Quantity("-3"),
    Quantity("2", "kVAR"),
    Quantity("1", "kWh"),
    True,
    False,
    MISSING,
    7,
]


@pytest.mark.req("R03-FORMULA", "R05-DETERMINISM")
@settings(max_examples=600)
@given(
    pieces=st.lists(
        st.tuples(st.sampled_from(FUZZ_TOKENS), st.sampled_from(["", " "])), min_size=1, max_size=15
    ),
    values=st.lists(st.sampled_from(FUZZ_VALUES), min_size=4, max_size=4),
    month=st.integers(1, 12),
)
def test_any_token_soup_parses_and_evaluates_safely(
    pieces: list[tuple[str, str]], values: list[EnvValue], month: int
) -> None:
    # Tokens are sometimes glued together ("1" + "if"), the case Python only warns about.
    source = "".join(token + separator for token, separator in pieces)
    env = dict(zip(("x", "y", "z", "c"), values, strict=True)) | builtin_values(
        f"2026-{month:02d}", 30
    )
    parse_failure: ExpressionError | None = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            expr = parse_expression(source)
        except ExpressionError as error:
            parse_failure = error
    assert caught == []  # the engine never makes Python print a warning
    if parse_failure is not None:
        assert parse_failure.offset is not None
        assert 0 <= parse_failure.offset <= len(source)
        assert (parse_failure.line, parse_failure.column) != (None, None)
        return
    eval_failure: EngineError | None = None
    try:
        evaluate(expr, env)
    except MissingInputError:
        pass
    except EngineError as error:  # any other exception would escape S04's error handling
        eval_failure = error
    if eval_failure is not None:
        assert eval_failure.offset is not None  # every evaluation failure is located


@pytest.mark.req("R03-FORMULA")
def test_function_and_builtin_name_sets() -> None:
    assert {
        "min",
        "max",
        "abs",
        "round_to",
        "ceil",
        "floor",
        "sqrt",
        "q",
        "in_months",
        "power_factor",
        "apparent",
        "as_reactive_equiv",
    } == FUNCTION_NAMES
    assert {
        "billing_month",
        "billing_days",
        "days_in_billing_month",
        "period_year",
    } == BUILTIN_NAMES
    assert FUNCTION_NAMES.isdisjoint(BUILTIN_NAMES)
