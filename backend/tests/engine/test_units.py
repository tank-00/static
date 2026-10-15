"""S01 - units, unit text parsing and unit-checked quantities (A§4.2, PRD §9.1).

Expected values are hand-derived; the derivation is in the comment next to each case.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given

from tariff_engine.decimals import RoundingMode, canonical_str
from tariff_engine.errors import CalcError, EngineError, UnitError
from tariff_engine.units import (
    BASE_SYMBOLS,
    DIMENSIONLESS,
    MAX_UNIT_EXPONENT,
    MAX_UNIT_TEXT_LENGTH,
    UNIT_ALIASES,
    Quantity,
    Unit,
    parse_unit,
    qmax,
    qmin,
)
from tests.strategies import any_units, dec, money, small_units

# The only binary float in this module: it exists solely to prove that floats are rejected.
A_FLOAT = float("1.5")

USD = Unit({"USD": 1})
KW = Unit({"kW": 1})
KVAR = Unit({"kVAR": 1})
KVA = Unit({"kVA": 1})
KV = Unit({"kV": 1})
HOUR = Unit({"h": 1})
DAY = Unit({"day": 1})
KWH = Unit({"kW": 1, "h": 1})
KVARH = Unit({"kVAR": 1, "h": 1})
KVAH = Unit({"kVA": 1, "h": 1})
USD_PER_KWH = Unit({"USD": 1, "kW": -1, "h": -1})

ZERO = Quantity.zero_literal()


# --------------------------------------------------------------------------------------- parse_unit

ALIAS_CASES = [
    # (text, canonical exponents, exact scale to canonical)
    ("USD", {"USD": 1}, "1"),
    ("$", {"USD": 1}, "1"),
    ("cents", {"USD": 1}, "0.01"),  # 1 cent = 0.01 USD
    ("¢", {"USD": 1}, "0.01"),
    ("mills", {"USD": 1}, "0.001"),  # 1 mill = 0.001 USD
    ("kW", {"kW": 1}, "1"),
    ("MW", {"kW": 1}, "1000"),  # 1 MW = 1000 kW
    ("kVAR", {"kVAR": 1}, "1"),
    ("kvar", {"kVAR": 1}, "1"),
    ("kVAr", {"kVAR": 1}, "1"),
    ("kVA", {"kVA": 1}, "1"),
    ("kWh", {"kW": 1, "h": 1}, "1"),
    ("MWh", {"kW": 1, "h": 1}, "1000"),  # 1 MWh = 1000 kWh
    ("kVARh", {"kVAR": 1, "h": 1}, "1"),
    ("kvarh", {"kVAR": 1, "h": 1}, "1"),
    ("kVAh", {"kVA": 1, "h": 1}, "1"),
    ("h", {"h": 1}, "1"),
    ("hour", {"h": 1}, "1"),
    ("hours", {"h": 1}, "1"),
    ("day", {"day": 1}, "1"),
    ("days", {"day": 1}, "1"),
    ("kV", {"kV": 1}, "1"),
    ("%", {}, "0.01"),  # 1% = 0.01, dimensionless
    ("percent", {}, "0.01"),
    ("1", {}, "1"),
    ("", {}, "1"),
]

COMPOUND_CASES = [
    ("USD/kWh", {"USD": 1, "kW": -1, "h": -1}, "1"),
    ("cents/kWh", {"USD": 1, "kW": -1, "h": -1}, "0.01"),  # 0.01 / 1
    ("¢/kWh", {"USD": 1, "kW": -1, "h": -1}, "0.01"),
    ("mills/kWh", {"USD": 1, "kW": -1, "h": -1}, "0.001"),  # 0.001 / 1
    ("USD/MWh", {"USD": 1, "kW": -1, "h": -1}, "0.001"),  # 1 / 1000
    ("$/MWh", {"USD": 1, "kW": -1, "h": -1}, "0.001"),
    ("USD/kW", {"USD": 1, "kW": -1}, "1"),
    ("USD/kVAR", {"USD": 1, "kVAR": -1}, "1"),
    ("USD/kvar", {"USD": 1, "kVAR": -1}, "1"),
    ("USD/day", {"USD": 1, "day": -1}, "1"),
    ("kWh/kW", {"h": 1}, "1"),  # kW*h / kW = h
    ("kW*h", {"kW": 1, "h": 1}, "1"),
    ("kW·h", {"kW": 1, "h": 1}, "1"),
    ("USD / kWh", {"USD": 1, "kW": -1, "h": -1}, "1"),  # whitespace ignored
    ("USD/(kW*h)", {"USD": 1, "kW": -1, "h": -1}, "1"),
    ("USD/kW/h", {"USD": 1, "kW": -1, "h": -1}, "1"),  # left-associative: (USD/kW)/h
    ("kW^2", {"kW": 2}, "1"),
    ("MW^2", {"kW": 2}, "1000000"),  # 1000^2
    ("1/h", {"h": -1}, "1"),
    ("h^-1", {"h": -1}, "1"),
    ("cents/%", {"USD": 1}, "1"),  # 0.01 / 0.01
    ("%/day", {"day": -1}, "0.01"),
    ("kW^9", {"kW": 9}, "1"),
    ("(kW^3)^3", {"kW": 9}, "1"),  # nested exponents multiply: 3 * 3 = 9, at the limit
    ("(MW^3)^-3", {"kW": -9}, "1E-27"),  # (1000^3)^-3 = 10^-27
    ("(cents/kWh)^-1", {"USD": -1, "kW": 1, "h": 1}, "100"),  # 1 / 0.01
    ("%^2", {}, "0.0001"),  # 0.01^2
    ("kW^9/kW^9", {}, "1"),
]


@pytest.mark.req("R05-UNITS-ROUNDING", "P-FIXED-LINEAR")
@pytest.mark.parametrize(("text", "exponents", "scale"), ALIAS_CASES + COMPOUND_CASES)
def test_parse_unit(text: str, exponents: dict[str, int], scale: str) -> None:
    assert parse_unit(text) == (Unit(exponents), Decimal(scale))


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    "text",
    [
        "kw",  # aliases are case-sensitive
        "KW",
        "kwh",
        "furlong",
        "c/kWh",
        "USD//kWh",
        "USD/",
        "/kWh",
        "(USD",
        "USD)",
        "USD kWh",  # two units need an operator
        "kW^",
        "kW^0",
        "kW^10",
        "kW^-10",
        "kW^x",
        "kW^2^3",
        "kW-h",
        "2",
        "0.5",
        "x" * (MAX_UNIT_TEXT_LENGTH + 1),
    ],
)
def test_parse_unit_rejects_unknown_or_malformed_text(text: str) -> None:
    with pytest.raises(UnitError):
        parse_unit(text)


def test_parse_unit_requires_text() -> None:
    with pytest.raises(TypeError):
        parse_unit(None)  # type: ignore[arg-type]


@pytest.mark.req("R05-UNITS-ROUNDING")
@pytest.mark.parametrize(
    "text",
    [
        "kW^9*kW^9",  # kW^18: beyond the +/-9 exponent bound of every Unit
        "kW^5/kW^-5",  # kW^10
        "(kW*kW)^5",  # kW^10
        "(kW^3)^4",  # nested exponents multiply: 3 * 4 = 12 > 9
        "(kW/kW^2)^5",  # 2 * 5 = 10, although the result (kW^-5) would be in range
        "((MW/kW)^9)^9",  # dimensionless, but MW is raised to the 81st power: scale 10^243
        # Each "^9" is within the limit, but nesting multiplies them to 9^6: the scale would be
        # 1000^531441 (overflow), 0.001^-531441 (overflow) or 0.01^531441 (underflow to zero).
        "(((((MW^9)^9)^9)^9)^9)^9",
        "(((((mills^-9)^-9)^-9)^-9)^-9)^-9",
        "(((((%^9)^9)^9)^9)^9)^9",
    ],
)
def test_parse_unit_bounds_effective_exponents(text: str) -> None:
    with pytest.raises(UnitError, match="limit"):
        parse_unit(text)


def test_a_quantity_never_silently_becomes_zero_through_its_unit() -> None:
    # 0.01^(9^6) would underflow to a zero scale and turn 5 into 0 (A§3.1 exactness, A§3.2).
    with pytest.raises(EngineError):
        Quantity("5", "(((((%^9)^9)^9)^9)^9)^9")


@pytest.mark.parametrize(
    ("alias", "scale", "text", "signal"),
    [
        ("third", Decimal(3), "1/third", "Inexact"),  # 1/3 is not exact at 34 digits
        ("huge", Decimal("1E+999990"), "huge^2", "Overflow"),
        ("tiny", Decimal("1E-999990"), "tiny^2", "Underflow"),
    ],
)
def test_scale_arithmetic_is_exact_or_a_unit_error(
    monkeypatch: pytest.MonkeyPatch, alias: str, scale: Decimal, text: str, signal: str
) -> None:
    # No shipped alias can reach these (all scales are small powers of ten); hypothetical aliases
    # prove that an inexact, overflowing or underflowing scale is a UnitError, never a raw
    # decimal signal and never a silently rounded or zero scale.
    monkeypatch.setitem(UNIT_ALIASES, alias, (DIMENSIONLESS, scale))  # type: ignore[arg-type]
    with pytest.raises(UnitError, match=signal):
        parse_unit(text)


@pytest.mark.req("P-FIXED-LINEAR")
def test_real_reactive_and_apparent_power_are_distinct_dimensions() -> None:
    assert len({parse_unit(text)[0] for text in ("kW", "kVAR", "kVA", "kV")}) == 4
    assert len({parse_unit(text)[0] for text in ("kWh", "kVARh", "kVAh")}) == 3
    assert parse_unit("kVA")[0] == KVA
    assert parse_unit("kVARh")[0] == KVARH
    assert parse_unit("kVAh")[0] == KVAH


# --------------------------------------------------------------------------------------------- Unit


def test_unit_is_canonical_and_immutable() -> None:
    assert Unit({"h": 1, "kW": 1}) == Unit({"kW": 1, "h": 1}) == KWH
    assert hash(Unit({"h": 1, "kW": 1})) == hash(KWH)
    assert Unit({"kW": 0}) == Unit({}) == DIMENSIONLESS
    assert DIMENSIONLESS.is_dimensionless
    assert not KW.is_dimensionless
    exponents = KWH.exponents
    exponents["kW"] = 5  # a copy: the unit is unaffected
    assert KWH.exponents == {"kW": 1, "h": 1}
    with pytest.raises(AttributeError):
        KWH.items = ()  # type: ignore[misc]


@pytest.mark.parametrize("bad", [{"kWh": 1}, {"W": 1}, {"kW": "1"}, {"kW": True}])
def test_unit_rejects_non_base_symbols_and_non_int_exponents(bad: dict[str, object]) -> None:
    with pytest.raises(UnitError):
        Unit(bad)  # type: ignore[arg-type]


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_unit_exponents_are_bounded() -> None:
    assert Unit({"kW": MAX_UNIT_EXPONENT, "h": -MAX_UNIT_EXPONENT}).exponents == {"kW": 9, "h": -9}
    with pytest.raises(UnitError, match="limit of 9"):
        Unit({"kW": 10})
    with pytest.raises(UnitError, match="limit of 9"):
        Unit({"h": -10})
    with pytest.raises(UnitError, match=r"cannot multiply kW\^9 by kW"):
        _ = Unit({"kW": 9}) * KW
    with pytest.raises(UnitError, match=r"cannot divide 1/h\^9 by h"):
        _ = Unit({"h": -9}) / HOUR
    with pytest.raises(UnitError, match=r"cannot raise kW\^2 to the power 5"):
        Unit({"kW": 2}).power(5)


@given(any_units, any_units)
def test_unit_products_stay_within_the_bound_or_raise(a: Unit, b: Unit) -> None:
    sums = {
        symbol: a.exponents.get(symbol, 0) + b.exponents.get(symbol, 0) for symbol in BASE_SYMBOLS
    }
    if all(abs(exponent) <= MAX_UNIT_EXPONENT for exponent in sums.values()):
        assert (a * b).exponents == {symbol: e for symbol, e in sums.items() if e != 0}
    else:
        with pytest.raises(UnitError):
            _ = a * b


def test_unit_algebra() -> None:
    assert KW * HOUR == KWH
    assert KWH / KW == HOUR
    assert USD_PER_KWH * KWH == USD
    assert KW.power(2) == Unit({"kW": 2})
    assert KW.power(0) == DIMENSIONLESS
    assert KW / KW == DIMENSIONLESS


@pytest.mark.parametrize(
    ("exponents", "text"),
    [
        ({}, "1"),
        ({"USD": 1, "kW": -1, "h": -1}, "USD/kWh"),
        ({"kW": 1, "h": 1}, "kWh"),
        ({"kVAR": 1, "h": 1}, "kVARh"),
        ({"kVA": 1, "h": 1}, "kVAh"),
        ({"USD": 1, "day": -1}, "USD/day"),
        ({"kW": 2}, "kW^2"),
        ({"h": -1}, "1/h"),
        ({"kW": 2, "h": 1}, "kW^2*h"),
        ({"USD": 1, "kW": -1, "kVAR": -1}, "USD/(kW*kVAR)"),
        ({"USD": 1, "kW": -1, "h": -2}, "USD/(kW*h^2)"),
        ({"kW": 9}, "kW^9"),
        ({"USD": -9, "kW": 9, "h": 9}, "kW^9*h^9/USD^9"),
    ],
)
def test_unit_display(exponents: dict[str, int], text: str) -> None:
    assert str(Unit(exponents)) == text


@given(small_units, small_units)
def test_unit_multiplication_is_commutative(a: Unit, b: Unit) -> None:
    assert a * b == b * a


@given(small_units, small_units, small_units)
def test_unit_multiplication_is_associative(a: Unit, b: Unit, c: Unit) -> None:
    assert (a * b) * c == a * (b * c)


@given(small_units, small_units)
def test_unit_division_inverts_multiplication(a: Unit, b: Unit) -> None:
    assert (a * b) / b == a
    assert a / a == DIMENSIONLESS


@given(any_units)
def test_unit_display_parses_back(unit: Unit) -> None:
    # A§4.2: str(Unit) is a display that parse_unit reads back with scale 1, for every Unit.
    assert parse_unit(str(unit)) == (unit, Decimal(1))
    quantity = Quantity("1.5", unit)
    assert quantity.label_value == Decimal("1.5")


@pytest.mark.parametrize("sign", [1, -1])
def test_the_largest_units_display_and_parse_back(sign: int) -> None:
    unit = Unit(dict.fromkeys(BASE_SYMBOLS, sign * MAX_UNIT_EXPONENT))
    assert len(str(unit)) <= MAX_UNIT_TEXT_LENGTH
    assert parse_unit(str(unit)) == (unit, Decimal(1))


# ----------------------------------------------------------------------------------------- Quantity


@pytest.mark.req("P-FIXED-LINEAR", "R05-UNITS-ROUNDING")
def test_cents_rate_times_energy_is_dollars() -> None:
    rate = Quantity("6.912", "cents/kWh")
    # 6.912 cents/kWh = 6.912 * 0.01 = 0.06912 USD/kWh, held in canonical units.
    assert rate.value == Decimal("0.06912")
    assert rate.unit == USD_PER_KWH
    assert rate.label == "cents/kWh"
    assert rate.label_value == Decimal("6.912")
    assert str(rate) == "6.912 cents/kWh"
    # 0.06912 USD/kWh * 1000 kWh = 69.12 USD
    charge = rate * Quantity("1000", "kWh")
    assert charge.unit == USD
    assert charge.value == Decimal("69.12")
    assert charge == Quantity("69.12", "USD")


@pytest.mark.req("P-FIXED-LINEAR")
def test_dollar_and_cent_rates_are_the_same_quantity() -> None:
    assert Quantity("0.06912", "USD/kWh") == Quantity("6.912", "cents/kWh")
    assert Quantity("6.912", "USD/kWh") != Quantity("6.912", "cents/kWh")  # 100x apart


@pytest.mark.req("P-FIXED-LINEAR")
def test_mills_scale() -> None:
    rate = Quantity("2.5", "mills/kWh")
    assert rate.value == Decimal("0.0025")  # 2.5 * 0.001 USD/kWh
    assert rate * Quantity("1000", "kWh") == Quantity("2.5", "USD")  # 0.0025 * 1000
    assert Quantity("7", "mills") == Quantity("0.007", "USD")


@pytest.mark.req("P-FIXED-LINEAR")
def test_mega_prefixes_and_percent() -> None:
    assert Quantity("1.5", "MW") == Quantity("1500", "kW")  # 1.5 * 1000
    assert Quantity("0.4", "MWh") == Quantity("400", "kWh")  # 0.4 * 1000
    rate = Quantity("45", "USD/MWh")
    assert rate.value == Decimal("0.045")  # 45 / 1000 USD/kWh
    assert rate * Quantity("400", "kWh") == Quantity("18", "USD")  # 0.045 * 400
    pct = Quantity("5", "%")
    assert pct == Quantity("0.05")  # dimensionless fraction
    assert pct * Quantity("200", "USD") == Quantity("10", "USD")  # 0.05 * 200


@pytest.mark.req("R05-UNITS-ROUNDING", "P-FIXED-LINEAR")
def test_kw_plus_kwh_is_a_unit_error() -> None:
    with pytest.raises(UnitError, match=r"kW and kWh"):
        _ = Quantity("10", "kW") + Quantity("10", "kWh")


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_real_and_reactive_power_do_not_mix() -> None:
    with pytest.raises(UnitError, match=r"kW and kVAR"):
        _ = Quantity("10", "kW") + Quantity("10", "kVAR")
    with pytest.raises(UnitError, match=r"kVA and kVAR"):
        _ = Quantity("10", "kVA") - Quantity("10", "kVAR")
    with pytest.raises(UnitError):
        _ = Quantity("10", "kW") < Quantity("10", "kVA")
    with pytest.raises(UnitError, match=r"max of kW and kVAR"):
        qmax(Quantity("10", "kW"), Quantity("10", "kVAR"))
    with pytest.raises(UnitError, match=r"min of kW and kVA"):
        qmin(Quantity("10", "kW"), Quantity("10", "kVA"))


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_usd_per_kw_times_kvar_is_not_usd() -> None:
    product = Quantity("10", "USD/kW") * Quantity("300", "kVAR")
    assert product.unit == Unit({"USD": 1, "kW": -1, "kVAR": 1})
    assert product.unit != USD
    with pytest.raises(UnitError):
        _ = Quantity("0", "USD") + product


@pytest.mark.req("P-TIERS", "R05-UNITS-ROUNDING")
def test_hours_use_and_demand_linked_block_size() -> None:
    assert parse_unit("kWh/kW")[0] == HOUR
    # 300000 kWh / 1500 kW = 200 h
    assert Quantity("300000", "kWh") / Quantity("1500", "kW") == Quantity("200", "h")
    # q(200, h) * 1500 kW = 300000 kWh (a 200-hours-use block for 1500 kW of billing demand)
    block = Quantity(200, "h") * Quantity("1500", "kW")
    assert block.unit == KWH
    assert block == Quantity("300000", "kWh")


def test_same_unit_addition_and_subtraction_are_exact_and_signed() -> None:
    # 100.25 + (-20.50) = 79.75 ; 79.75 - 100.25 = -20.50
    total = Quantity("100.25", "USD") + Quantity("-20.50", "USD")
    assert total == Quantity("79.75", "USD")
    assert total - Quantity("100.25", "USD") == Quantity("-20.50", "USD")
    assert -Quantity("5", "kW") == Quantity("-5", "kW")
    assert abs(Quantity("-5", "kW")) == Quantity("5", "kW")


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_zero_literal_is_unit_polymorphic_in_add_sub_min_max_and_comparisons() -> None:
    negative = Quantity("-5", "kW")
    clipped = qmax(negative, ZERO)  # max(x_kW, 0) with x = -5 -> 0 kW
    assert clipped == Quantity("0", "kW")
    assert clipped.unit == KW
    assert not clipped.is_zero_literal
    assert qmax(Quantity("7", "kW"), ZERO) == Quantity("7", "kW")
    assert qmin(ZERO, negative) == negative
    assert qmin(Quantity("7", "kW"), ZERO) == Quantity("0", "kW")
    assert Quantity("5", "kW") + ZERO == Quantity("5", "kW")
    assert ZERO + Quantity("5", "kW") == Quantity("5", "kW")
    assert ZERO - Quantity("5", "kW") == Quantity("-5", "kW")
    assert Quantity("5", "kW") > ZERO
    assert ZERO.compare(Quantity("5", "kW")) == -1  # zero literal on the left
    assert Quantity("0", "kW").compare(ZERO) == 0
    assert Quantity("-1", "USD").compare(ZERO) == -1


def test_zero_literal_survives_only_among_zero_literals() -> None:
    assert (ZERO + ZERO).is_zero_literal
    assert (-ZERO).is_zero_literal
    assert abs(ZERO).is_zero_literal
    rounded = ZERO.quantize(2, RoundingMode.HALF_UP)  # rounding is not on the A§4.2 list
    assert rounded.value == Decimal("0.00")
    assert not rounded.is_zero_literal
    with pytest.raises(UnitError):
        _ = rounded + Quantity("5", "kW")
    assert qmax(ZERO, ZERO).is_zero_literal
    scaled = ZERO * Quantity("5", "kW")  # * and / combine units: 0 * 5 kW = 0 kW
    assert scaled == Quantity("0", "kW")
    assert not scaled.is_zero_literal
    with pytest.raises(UnitError):
        _ = scaled + Quantity("1", "kWh")
    assert Quantity.zero_literal("0.00").is_zero_literal
    with pytest.raises(ValueError, match="zero"):
        Quantity.zero_literal("1")


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_a_computed_zero_is_not_polymorphic() -> None:
    with pytest.raises(UnitError):
        _ = Quantity("0") + Quantity("5", "kW")
    with pytest.raises(UnitError):
        _ = Quantity("0", "kW") + Quantity("5", "kWh")


def test_division_by_zero_is_a_calc_error() -> None:
    with pytest.raises(CalcError, match="division by zero"):
        _ = Quantity("1", "USD") / Quantity("0", "kWh")
    with pytest.raises(CalcError, match="division by zero"):
        _ = Quantity("1", "USD") / ZERO


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_repeated_multiplication_stops_at_the_exponent_bound() -> None:
    kw = Quantity("2", "kW")
    product = kw
    for _ in range(MAX_UNIT_EXPONENT - 1):
        product = product * kw
    # 2^9 = 512 kW^9: still a valid, printable quantity whose label parses back.
    assert product == Quantity("512", Unit({"kW": 9}))
    assert product.label == "kW^9"
    assert product.label_value == Decimal("512")
    assert str(product) == "512 kW^9"
    # A tenth factor would need kW^10: a clean EngineError, never an unprintable quantity.
    with pytest.raises(UnitError, match=r"cannot multiply kW\^9 by kW"):
        _ = product * kw
    # Division by zero stays a CalcError whatever the operands' units.
    with pytest.raises(CalcError, match=r"division by zero: 512 kW\^9 / 0 kW"):
        _ = product / Quantity("0", "kW")


def test_overflow_is_a_calc_error() -> None:
    with pytest.raises(CalcError, match="Overflow"):
        _ = Quantity("9E+6144", "kW") * Quantity("10")  # 9E+6145 is beyond the engine range


def test_underflow_is_a_calc_error_not_a_silent_zero() -> None:
    # 10^-6000 USD/kWh * 10^-6000 kWh = 10^-12000 USD would round to 0 USD.
    with pytest.raises(CalcError, match="Underflow"):
        _ = Quantity("1E-6000", "USD/kWh") * Quantity("1E-6000", "kWh")


# A dimensionless unit text of 99 characters whose scale is 10^-450 (each "%^9" is 10^-18).
TINY_SCALE_UNIT = "*".join(["%^9"] * 25)


def test_the_extreme_unit_scales_are_exact_powers_of_ten() -> None:
    assert len(TINY_SCALE_UNIT) <= MAX_UNIT_TEXT_LENGTH
    assert parse_unit(TINY_SCALE_UNIT) == (DIMENSIONLESS, Decimal("1E-450"))
    # Every alias scale is an exact power of ten, so every combined scale is one too and
    # re-expressing a value between units only moves the decimal point (A§4.2).
    for scale in [alias_scale for _, alias_scale in UNIT_ALIASES.values()]:
        assert scale == Decimal((0, (1,), scale.adjusted()))


@pytest.mark.req("P-FIXED-LINEAR")
def test_quantities_far_beyond_the_engine_range_are_refused_whatever_the_unit() -> None:
    # Previously accepted in USD/kW (scale 1) and unprintable (a 10^11-character string).
    for unit in ("kW", "USD", "cents", ""):
        for text in ("1E+99999999999", "1E-99999999999"):
            with pytest.raises(ValueError, match="engine range"):
                Quantity(text, unit)
        with pytest.raises(ValueError, match="engine range"):
            Quantity(Decimal("1E+99999999999"), unit)


def test_the_largest_quantity_displays_and_divides_by_zero_cleanly() -> None:
    largest = Quantity("9" * 34 + "E+6111", "kW")  # adjusted exponent 6144
    assert str(largest) == "9" * 34 + "0" * 6111 + " kW"
    assert repr(largest).startswith("Quantity('99")
    with pytest.raises(CalcError, match=r"division by zero: 9+0+ kW / 0 kW"):
        _ = largest / Quantity("0", "kW")
    smallest = Quantity("1E-6176", "USD")
    assert str(smallest) == "0." + "0" * 6175 + "1 USD"


@pytest.mark.req("AT-ZERO-MISSING")
@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("1E+6144", "MW"),  # 10^6147 kW: beyond the largest engine value
        ("1E-6176", "cents"),  # 10^-6178 USD: below the smallest step
        ("1E-6000", TINY_SCALE_UNIT),  # 10^-6450: used to underflow silently to 0
    ],
)
def test_normalization_out_of_the_engine_range_is_a_calc_error(text: str, unit: str) -> None:
    with pytest.raises(CalcError, match="outside the engine range"):
        Quantity(text, unit)


def test_normalization_with_extreme_scales_is_exact() -> None:
    huge_scale_unit = "(" + TINY_SCALE_UNIT[:-4] + ")^-1"  # 100 characters, scale 10^432
    assert parse_unit(huge_scale_unit)[1] == Decimal("1E+432")
    assert Quantity("2.5", huge_scale_unit).value == Decimal("2.5E+432")
    tiny = Quantity("3", TINY_SCALE_UNIT)
    assert tiny.value == Decimal("3E-450")  # never silently zero
    assert tiny.label_value == Decimal("3")


@pytest.mark.req("P-FIXED-LINEAR", "R05-UNITS-ROUNDING")
def test_normalization_keeps_every_digit_of_long_inputs() -> None:
    # 37 significant digits: more than the 34-digit arithmetic precision, yet entering a value is
    # not arithmetic, so it is kept exactly whatever the unit's scale (A§3.1, A§4.2).
    text = "1.234567890123456789012345678901234567"
    in_cents = Quantity(text, "cents")
    assert in_cents.value == Decimal("0.01234567890123456789012345678901234567")
    assert in_cents.label_value == Decimal(text)
    assert str(in_cents) == text + " cents"
    assert Quantity(text, "USD").value == Decimal(text)
    assert Quantity(text, "MWh").value == Decimal("1234.567890123456789012345678901234567")


def test_display_never_fails_at_the_edge_of_the_engine_range() -> None:
    # 9 * 10^6144 USD shown in mills would be 9 * 10^6147, beyond the engine range: the display
    # falls back to the canonical value and unit instead of failing.
    edge = Quantity(Decimal("9E+6144"), USD, label="mills")
    assert edge.label_value == Decimal("9E+6147")  # exact, never an overflow
    assert str(edge) == "9" + "0" * 6144 + " USD"
    huge_in_tiny_label = Quantity("1E+6000", "", label=TINY_SCALE_UNIT)
    assert huge_in_tiny_label.label_value == Decimal("1E+6450")
    assert str(huge_in_tiny_label) == "1" + "0" * 6000
    # Inside the range the label value is shown as usual.
    assert str(Quantity("1E-6176", USD, label="mills")) == "0." + "0" * 6172 + "1 mills"
    assert canonical_str(Quantity("7", "mills").label_value) == "7"


def test_quantity_arithmetic_uses_the_engine_context() -> None:
    # 1 / 3 at 34 significant digits
    third = Quantity("1", "USD") / Quantity("3")
    assert third.value == Decimal("0." + "3" * 34)


@pytest.mark.req("P-FIXED-LINEAR")
def test_quantity_rejects_floats_and_raw_numbers() -> None:
    with pytest.raises(TypeError):
        Quantity(A_FLOAT, "kW")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Quantity(True, "kW")
    with pytest.raises(TypeError):
        _ = Quantity("1", "kW") + Decimal(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        Quantity("1", 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _ = Quantity("1", "kW") < Decimal(1)  # type: ignore[operator]
    with pytest.raises(TypeError):
        Quantity("1", "kW").compare(1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        qmax(Quantity("1", "kW"), Decimal(1))  # type: ignore[arg-type]


def test_quantity_is_immutable() -> None:
    quantity = Quantity("5", "kW")
    with pytest.raises(AttributeError):
        quantity.value = Decimal(6)  # type: ignore[misc]


def test_quantity_label_must_name_the_same_dimension() -> None:
    rate = Quantity(Decimal("0.06912"), USD_PER_KWH, label="cents/kWh")
    assert rate.label_value == Decimal("6.912")  # 0.06912 / 0.01
    with pytest.raises(UnitError):
        Quantity("1", "cents/kWh", label="kW")


@pytest.mark.req("R05-UNITS-ROUNDING", "AT-REFCALC")
def test_rounding_applies_to_the_canonical_magnitude() -> None:
    # 6.91234 cents/kWh = 0.0691234 USD/kWh; 5 places -> 0.06912 (dropped "34" < half).
    rounded = Quantity("6.91234", "cents/kWh").quantize(5, RoundingMode.HALF_UP)
    assert rounded.value == Decimal("0.06912")
    assert rounded.label_value == Decimal("6.912")  # i.e. resolution of 0.001 cents
    # 6.9125 cents/kWh = 0.069125 USD/kWh is a tie at 5 places.
    tie = Quantity("6.9125", "cents/kWh")
    assert tie.quantize(5, RoundingMode.HALF_UP).value == Decimal("0.06913")
    assert tie.quantize(5, RoundingMode.HALF_EVEN).value == Decimal("0.06912")
    # Billing demand to whole kW: 1499.5 kW -> 1500 kW (HALF_UP).
    assert Quantity("1499.5", "kW").quantize(0, RoundingMode.HALF_UP) == Quantity("1500", "kW")


def test_derived_labels() -> None:
    # A§4.2: + - * / results are labelled str(unit); unary -, abs, quantize and qmin/qmax keep
    # the label of their (winning) operand, so a cents rate stays displayed in cents.
    rate = Quantity("6.912", "cents/kWh")
    assert (rate + rate).label == "USD/kWh"
    assert (Quantity("1", "MW") + Quantity("500", "kW")).label == "kW"
    assert (Quantity("2", "MWh") / Quantity("1", "MW")).label == "h"
    assert (rate * Quantity("1000", "kWh")).label == "USD"
    assert (-rate).label == abs(rate).label == "cents/kWh"
    assert str(-rate) == "-6.912 cents/kWh"
    assert str(rate.quantize(4, RoundingMode.HALF_UP)) == "6.91 cents/kWh"  # 0.0691 USD/kWh
    assert qmax(rate, Quantity("0.05", "USD/kWh")).label == "cents/kWh"
    assert qmax(ZERO, Quantity("-1", "MW")).label == "kW"  # a zero literal adopts str(unit)


def test_structural_equality_hashing_and_ties() -> None:
    one = Quantity("1.0", "kW")
    one_again = Quantity("1.00", "kW")
    assert one == one_again
    assert hash(one) == hash(one_again)
    assert Quantity("1", "kW") != Quantity("1", "kVAR")  # structural: no UnitError
    assert len({one, one_again, Quantity("1", "kVAR")}) == 2
    assert qmax(one, one_again) is one  # ties keep the first operand
    assert qmin(one_again, one) is one_again


def test_quantity_display() -> None:
    assert str(Quantity("69.12", "USD")) == "69.12 USD"
    assert str(Quantity("5")) == "5"
    assert str(Quantity("1", "USD") / Quantity("4", "kWh")) == "0.25 USD/kWh"


@given(dec, dec, small_units, small_units)
def test_quantity_multiplication_is_commutative(x: Decimal, y: Decimal, ua: Unit, ub: Unit) -> None:
    a, b = Quantity(x, ua), Quantity(y, ub)
    assert a * b == b * a


@given(money, money, money, small_units, small_units, small_units)
def test_quantity_multiplication_is_associative(
    x: Decimal, y: Decimal, z: Decimal, ua: Unit, ub: Unit, uc: Unit
) -> None:
    # money has <= 9 significant digits, so a triple product (<= 27 digits) is exact at 34 digits.
    a, b, c = Quantity(x, ua), Quantity(y, ub), Quantity(z, uc)
    assert (a * b) * c == a * (b * c)


@given(dec, dec, small_units)
def test_add_then_subtract_restores_the_value(x: Decimal, y: Decimal, unit: Unit) -> None:
    a, b = Quantity(x, unit), Quantity(y, unit)
    assert (a + b) - b == a


@given(money)
def test_cent_scaling_is_exact(amount: Decimal) -> None:
    assert Quantity(amount, "cents").value == amount.scaleb(-2)
    assert Quantity(amount, "cents").label_value == amount
