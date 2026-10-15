"""S01 - exact decimals, rounding and canonical forms (A§4.1, A§3.1, A§3.6).

Expected values are hand-derived; the derivation is in the comment next to each case.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import (
    ROUND_HALF_EVEN,
    ROUND_UP,
    Decimal,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    Underflow,
    getcontext,
    localcontext,
)

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, ValidationError

from tariff_engine.decimals import (
    DECIMAL_JSON_PATTERN,
    ENGINE_EMAX,
    ENGINE_EMIN,
    ENGINE_ETINY,
    ENGINE_PRECISION,
    D,
    DecimalStr,
    RoundingMode,
    canonical_json,
    canonical_sha256,
    canonical_str,
    engine_context,
    in_engine_range,
    quantize,
)
from tariff_engine.errors import CalcError
from tests.strategies import dec, money

# The only binary float in this module: it exists solely to prove that floats are rejected.
A_FLOAT = float("0.1")

HALF_UP = RoundingMode.HALF_UP
HALF_EVEN = RoundingMode.HALF_EVEN
DOWN = RoundingMode.DOWN
UP = RoundingMode.UP
CEILING = RoundingMode.CEILING
FLOOR = RoundingMode.FLOOR

PLAIN_DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")

# Engine range edges (decimal128): the largest value has 6145 integer digits, the smallest step is
# 10^-6176. "1E+99999999999" is a short text whose plain form would be 10^11 characters long.
LARGEST = "9" * 34 + "E+6111"  # 9.99...9 (34 nines) * 10^6144: adjusted exponent 6144
SMALLEST_STEP = "1E-6176"
FAR_BEYOND = ["1E+99999999999", "1E-99999999999", "0E+99999999999", "0E-99999999999"]


# ---------------------------------------------------------------------------------------------- D()


@pytest.mark.req("P-FIXED-LINEAR", "R05-UNITS-ROUNDING")
def test_d_builds_exact_decimals_from_str_int_and_decimal() -> None:
    # Digits are kept exactly as written: 6.912 -> coefficient 6912, exponent -3.
    assert D("6.912").as_tuple() == (0, (6, 9, 1, 2), -3)
    assert D(1000) == Decimal(1000)
    original = Decimal("1.50")
    assert D(original) is original
    # Decimal arithmetic is exact: 0.1 + 0.2 = 0.3 (binary floats give 0.30000000000000004).
    assert D("0.1") + D("0.2") == D("0.3")
    assert D("-0.00") == 0


@pytest.mark.req("P-FIXED-LINEAR")
@pytest.mark.parametrize("bad", [A_FLOAT, True, False, None, [1], b"1"])
def test_d_rejects_floats_bools_and_other_types(bad: object) -> None:
    with pytest.raises(TypeError):
        D(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3", "NaN", "Infinity", "-inf", "sNaN", "1,000"])
def test_d_rejects_non_numbers_and_non_finite_strings(bad: str) -> None:
    with pytest.raises(ValueError, match=r"decimal"):
        D(bad)


def test_d_rejects_non_finite_decimals() -> None:
    with pytest.raises(ValueError, match="finite"):
        D(Decimal("NaN"))
    with pytest.raises(ValueError, match="finite"):
        D(Decimal("-Infinity"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("6.912", (0, (6, 9, 1, 2), -3)),
        ("-1", (1, (1,), 0)),
        ("+1", (0, (1,), 0)),
        (".5", (0, (5,), -1)),  # Python-literal spellings that S02 may pass through
        ("5.", (0, (5,), 0)),
        ("1E+3", (0, (1,), 3)),
        ("-2.5e-3", (1, (2, 5), -4)),
    ],
)
def test_d_accepts_ascii_decimal_text(text: str, expected: tuple[object, ...]) -> None:
    assert D(text).as_tuple() == expected


@pytest.mark.parametrize(
    "bad",
    [" 1", "1 ", "\t-2.5\n", "1_000", "١٢", "１２", "²", "0x10", "1e", "e3", "--1", "+-1", "."],
)
def test_d_rejects_whitespace_underscores_and_non_ascii_digits(bad: str) -> None:
    # decimal.Decimal itself accepts most of these (" 1 ", "1_000", Arabic-Indic "١٢" == 12).
    with pytest.raises(ValueError, match="not a decimal number"):
        D(bad)


@pytest.mark.req("P-FIXED-LINEAR")
def test_d_accepts_the_whole_engine_range() -> None:
    assert ENGINE_EMAX == 6144  # IEEE 754 decimal128
    assert ENGINE_EMIN == -6143
    assert ENGINE_ETINY == ENGINE_EMIN - (ENGINE_PRECISION - 1) == -6176
    assert D(LARGEST).adjusted() == ENGINE_EMAX
    assert D(SMALLEST_STEP).as_tuple().exponent == ENGINE_ETINY
    assert D("-" + LARGEST) == D(LARGEST).copy_negate()
    assert D(10**6145 - 1).adjusted() == ENGINE_EMAX  # 6145 nines
    assert D("0." + "0" * 6175 + "1") == D(SMALLEST_STEP)
    assert D("0E-6176") == 0
    # A coefficient longer than 34 digits is kept exactly (only arithmetic rounds to 34 digits).
    assert D("1.234567890123456789012345678901234567").as_tuple().exponent == -36


@pytest.mark.parametrize(
    "bad",
    [
        *FAR_BEYOND,
        "1E+6145",  # adjusted exponent 6145
        "1" + "0" * 6145,  # 6146 integer digits
        "1E-6177",  # exponent below ENGINE_ETINY
        "0." + "0" * 6176 + "1",  # 6177 decimal places
        "0E-6177",  # a zero is bounded by its exponent too
        "1e999999999999999999999",  # beyond what decimal.Decimal can represent at all
    ],
)
def test_d_rejects_text_outside_the_engine_range(bad: str) -> None:
    with pytest.raises(ValueError, match="engine range"):
        D(bad)


def test_d_rejects_ints_and_decimals_outside_the_engine_range() -> None:
    for huge in (10**6145, -(10**6145), 10**100_000):
        with pytest.raises(ValueError, match="engine range"):  # never str() of a huge int
            D(huge)
    for text in FAR_BEYOND:
        assert not in_engine_range(Decimal(text))
        with pytest.raises(ValueError, match="engine range"):
            D(Decimal(text))


def test_in_engine_range() -> None:
    assert in_engine_range(Decimal(LARGEST))
    assert in_engine_range(Decimal(SMALLEST_STEP))
    assert in_engine_range(Decimal("-0.00"))
    assert not in_engine_range(Decimal("1E+6145"))
    assert not in_engine_range(Decimal("1E-6177"))
    assert not in_engine_range(Decimal("NaN"))
    assert not in_engine_range(Decimal("Infinity"))
    assert not in_engine_range("1")  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------- engine context


@pytest.mark.req("R05-DETERMINISM", "AT-REFCALC")
def test_engine_context_precision_rounding_and_traps() -> None:
    with engine_context() as ctx:
        assert ctx.prec == ENGINE_PRECISION == 34
        assert ctx.rounding == ROUND_HALF_EVEN
        assert (ctx.Emax, ctx.Emin, ctx.Etiny()) == (ENGINE_EMAX, ENGINE_EMIN, ENGINE_ETINY)
        for trap in (InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation):
            assert ctx.traps[trap]
        # 1/3 = 0.333... -> 34 significant threes (35th digit 3 rounds down).
        assert Decimal(1) / Decimal(3) == Decimal("0." + "3" * 34)
        # 2/3 = 0.666... -> 33 sixes then 7 (35th digit 6 rounds the 34th up).
        assert Decimal(2) / Decimal(3) == Decimal("0." + "6" * 33 + "7")
        # Ties at the 35th digit go to the even neighbour: ...2|5 -> ...2 and ...3|5 -> ...4.
        assert +Decimal("1." + "0" * 32 + "25") == Decimal("1." + "0" * 32 + "2")
        assert +Decimal("1." + "0" * 32 + "35") == Decimal("1." + "0" * 32 + "4")


def test_engine_context_traps_raise() -> None:
    with engine_context(), pytest.raises(DivisionByZero):
        _ = Decimal(1) / Decimal(0)
    with engine_context(), pytest.raises(InvalidOperation):
        _ = Decimal(0) / Decimal(0)
    with engine_context(), pytest.raises(Overflow):
        _ = Decimal("9E+6144") * Decimal(10)  # 9E+6145 is beyond Emax


@pytest.mark.req("AT-ZERO-MISSING")
def test_engine_context_never_silently_rounds_a_tiny_result_to_zero() -> None:
    # 10^-6000 * 10^-6000 = 10^-12000 is far below the smallest step 10^-6176: it would round to 0.
    with engine_context(), pytest.raises(Underflow):
        _ = Decimal("1E-6000") * Decimal("1E-6000")
    # 10^-6176 / 3 cannot keep its digit either.
    with engine_context(), pytest.raises(Underflow):
        _ = Decimal(SMALLEST_STEP) / Decimal(3)
    # An exact tiny (subnormal) result is fine: 2 * 10^-6150.
    with engine_context():
        assert Decimal("1E-6150") * Decimal(2) == Decimal("2E-6150")


@given(dec, dec)
def test_engine_arithmetic_results_stay_in_the_engine_range(x: Decimal, y: Decimal) -> None:
    with engine_context():
        results = [x + y, x - y, x * y, -x, abs(x)] + ([x / y] if y else [])
    assert all(in_engine_range(result) for result in results)


@pytest.mark.req("P-FIXED-LINEAR")
def test_engine_context_traps_implicit_float_mixing() -> None:
    with engine_context(), pytest.raises(FloatOperation):
        Decimal(A_FLOAT)
    with engine_context(), pytest.raises(FloatOperation):
        _ = Decimal(1) < A_FLOAT


@pytest.mark.req("R05-DETERMINISM")
def test_engine_context_ignores_and_restores_the_ambient_context() -> None:
    before = getcontext().prec
    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_UP
        with engine_context() as ctx:
            assert ctx.prec == 34
            assert Decimal(2) / Decimal(3) == Decimal("0." + "6" * 33 + "7")
            ctx.prec = 5  # mutate the engine context...
        assert getcontext().prec == 3  # ...the ambient context is untouched
    assert getcontext().prec == before
    with engine_context() as fresh:
        assert fresh.prec == 34  # ...and the next engine context is fresh


# ----------------------------------------------------------------------------------------- quantize

# (value, places, mode, expected text). A "tie" means the dropped part is exactly half a unit.
ROUNDING_CASES = [
    # 1.005 at 2 places: tie between 1.00 and 1.01.
    ("1.005", 2, HALF_UP, "1.01"),  # tie -> away from zero
    ("1.005", 2, HALF_EVEN, "1.00"),  # tie -> even last digit (0)
    ("1.005", 2, DOWN, "1.00"),  # toward zero
    ("1.005", 2, UP, "1.01"),  # away from zero
    ("1.005", 2, CEILING, "1.01"),  # toward +inf
    ("1.005", 2, FLOOR, "1.00"),  # toward -inf
    # 1.015 at 2 places: tie between 1.01 and 1.02; the even neighbour is 1.02.
    ("1.015", 2, HALF_UP, "1.02"),
    ("1.015", 2, HALF_EVEN, "1.02"),
    ("1.015", 2, DOWN, "1.01"),
    ("1.015", 2, UP, "1.02"),
    ("1.015", 2, CEILING, "1.02"),
    ("1.015", 2, FLOOR, "1.01"),
    # Negative ties: "toward zero" is now upward and CEILING/FLOOR swap roles.
    ("-1.005", 2, HALF_UP, "-1.01"),
    ("-1.005", 2, HALF_EVEN, "-1.00"),
    ("-1.005", 2, DOWN, "-1.00"),
    ("-1.005", 2, UP, "-1.01"),
    ("-1.005", 2, CEILING, "-1.00"),
    ("-1.005", 2, FLOOR, "-1.01"),
    ("-1.015", 2, HALF_UP, "-1.02"),
    ("-1.015", 2, HALF_EVEN, "-1.02"),
    ("-1.015", 2, DOWN, "-1.01"),
    ("-1.015", 2, UP, "-1.02"),
    ("-1.015", 2, CEILING, "-1.01"),
    ("-1.015", 2, FLOOR, "-1.02"),
    # Not a tie: 1.0049 is below the midpoint 1.005.
    ("1.0049", 2, HALF_UP, "1.00"),
    ("1.0049", 2, HALF_EVEN, "1.00"),
    ("1.0049", 2, DOWN, "1.00"),
    ("1.0049", 2, UP, "1.01"),
    ("1.0049", 2, CEILING, "1.01"),
    ("1.0049", 2, FLOOR, "1.00"),
    ("-1.0049", 2, HALF_UP, "-1.00"),
    ("-1.0049", 2, HALF_EVEN, "-1.00"),
    ("-1.0049", 2, DOWN, "-1.00"),
    ("-1.0049", 2, UP, "-1.01"),
    ("-1.0049", 2, CEILING, "-1.00"),
    ("-1.0049", 2, FLOOR, "-1.01"),
    # Whole units (e.g. billing demand to whole kW): 2.5 / 3.5 / -2.5 are ties.
    ("2.5", 0, HALF_UP, "3"),
    ("2.5", 0, HALF_EVEN, "2"),
    ("3.5", 0, HALF_EVEN, "4"),
    ("-2.5", 0, HALF_UP, "-3"),
    ("-2.5", 0, HALF_EVEN, "-2"),
    ("-2.5", 0, DOWN, "-2"),
    ("-2.5", 0, UP, "-3"),
    ("-2.5", 0, CEILING, "-2"),
    ("-2.5", 0, FLOOR, "-3"),
    # Negative places round to tens/hundreds: 1250 is a tie between 1200 and 1300.
    ("1250", -2, HALF_UP, "1300"),
    ("1250", -2, HALF_EVEN, "1200"),
    ("-1250", -2, HALF_UP, "-1300"),
    ("1349.99", -2, DOWN, "1300"),
    # Already exact: only the scale changes.
    ("1.2", 2, DOWN, "1.20"),
    ("7", 3, HALF_UP, "7.000"),
]


@pytest.mark.req("R05-UNITS-ROUNDING", "AT-REFCALC")
@pytest.mark.parametrize(("value", "places", "mode", "expected"), ROUNDING_CASES)
def test_quantize_every_mode_with_negatives_and_ties(
    value: str, places: int, mode: RoundingMode, expected: str
) -> None:
    result = quantize(Decimal(value), places, mode)
    assert result == Decimal(expected)
    assert format(result, "f") == expected  # exact scale, plain notation


@pytest.mark.req("R05-UNITS-ROUNDING")
def test_quantize_never_returns_negative_zero() -> None:
    # -0.004 at 2 places HALF_UP rounds to zero; the sign of a zero must not survive.
    result = quantize(Decimal("-0.004"), 2, HALF_UP)
    assert result == 0
    assert not result.is_signed()
    assert format(result, "f") == "0.00"
    # CEILING of -0.001 is also zero (0.00), whereas FLOOR moves away to -0.01.
    assert format(quantize(Decimal("-0.001"), 2, CEILING), "f") == "0.00"
    assert format(quantize(Decimal("-0.001"), 2, FLOOR), "f") == "-0.01"


def test_quantize_accepts_mode_names_and_rejects_unknown_modes() -> None:
    assert quantize(Decimal("1.005"), 2, "HALF_EVEN") == Decimal("1.00")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="BANKERS"):
        quantize(Decimal("1.005"), 2, "BANKERS")  # type: ignore[arg-type]


def test_quantize_errors() -> None:
    with pytest.raises(TypeError):
        quantize("1.005", 2, HALF_UP)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        quantize(Decimal("1.005"), True, HALF_UP)
    # 1E+40 at 2 places would need 43 significant digits > 34.
    with pytest.raises(CalcError, match="cannot round"):
        quantize(Decimal("1E+40"), 2, HALF_UP)
    with pytest.raises(CalcError):
        quantize(Decimal("NaN"), 2, HALF_UP)


@pytest.mark.parametrize(
    "places",
    [10**20, -(10**20), 2**63, -(2**63), -ENGINE_ETINY + 1, -ENGINE_EMAX - 1],
)
def test_quantize_rejects_places_beyond_the_engine_range(places: int) -> None:
    # A raw OverflowError ("int too large to convert to C ssize_t") would escape the engine's
    # error handling; every rounding failure is a CalcError (A§4.1).
    for value in (Decimal("1"), Decimal("0")):
        with pytest.raises(CalcError, match="cannot round"):
            quantize(value, places, HALF_UP)


def test_quantize_at_the_edges_of_the_places_range() -> None:
    # Zero at the smallest step (6176 places) and at the largest power of ten (-6144 places).
    assert quantize(Decimal("0"), -ENGINE_ETINY, HALF_UP).as_tuple().exponent == ENGINE_ETINY
    assert quantize(Decimal("0"), -ENGINE_EMAX, HALF_UP) == 0
    # 34 significant digits is the limit: 33 places on 1 is fine, 34 places is not.
    assert len(format(quantize(Decimal("1"), 33, HALF_UP), "f").replace(".", "")) == 34
    with pytest.raises(CalcError):
        quantize(Decimal("1"), 34, HALF_UP)


@given(dec, st.integers(min_value=0, max_value=4))
def test_quantize_mode_laws(value: Decimal, places: int) -> None:
    half_unit = Decimal((0, (5,), -places - 1))  # 0.5 * 10**-places
    assert quantize(value, places, FLOOR) <= value <= quantize(value, places, CEILING)
    for mode in (DOWN, UP, HALF_UP, HALF_EVEN):  # symmetric in sign
        assert quantize(-value, places, mode) == -quantize(value, places, mode)
    for mode in (HALF_UP, HALF_EVEN):  # nearest rounding is within half a unit
        assert abs(quantize(value, places, mode) - value) <= half_unit


# ------------------------------------------------------------------------------------ canonical_str


@pytest.mark.req("P-FIXED-LINEAR", "R06-EXACT-VS-DISPLAY")
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1E+3", "1000"),  # exponent expanded
        ("1.2E+2", "120"),
        ("-0.00", "0.00"),  # negative zero rendered as zero, scale kept
        ("-0", "0"),
        ("0E-5", "0.00000"),
        ("1.50", "1.50"),  # trailing zero (scale) preserved
        ("-12.340", "-12.340"),
        ("1E-7", "0.0000001"),
        ("-5E-3", "-0.005"),
        ("123456789012345678.123456789", "123456789012345678.123456789"),
    ],
)
def test_canonical_str(value: str, expected: str) -> None:
    assert canonical_str(Decimal(value)) == expected


def test_canonical_str_rejects_non_decimals_and_non_finite() -> None:
    with pytest.raises(TypeError):
        canonical_str(A_FLOAT)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        canonical_str(5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-finite"):
        canonical_str(Decimal("NaN"))
    with pytest.raises(ValueError, match="non-finite"):
        canonical_str(Decimal("Infinity"))


def test_canonical_str_covers_the_engine_range_and_refuses_beyond_it() -> None:
    assert canonical_str(Decimal(LARGEST)) == "9" * 34 + "0" * 6111  # 6145 characters
    assert canonical_str(Decimal(SMALLEST_STEP)) == "0." + "0" * 6175 + "1"
    for text in [*FAR_BEYOND, "1E+6145", "1E-6177"]:
        # A clean ValueError instead of building a 10^11-character string (MemoryError).
        with pytest.raises(ValueError, match="engine range"):
            canonical_str(Decimal(text))


@given(dec)
def test_canonical_str_is_plain_and_exact(value: Decimal) -> None:
    text = canonical_str(value)
    assert PLAIN_DECIMAL.fullmatch(text)
    assert Decimal(text) == value


# --------------------------------------------------------------------------------------- DecimalStr


class Charge(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: DecimalStr
    rate: DecimalStr | None = None


class StrictCharge(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    amount: DecimalStr


@pytest.mark.req("P-FIXED-LINEAR")
def test_decimal_str_round_trips_json_strings_exactly() -> None:
    charge = Charge.model_validate_json('{"amount":"1.50","rate":"0.06912"}')
    assert charge.amount == Decimal("1.50")
    assert charge.amount.as_tuple().exponent == -2  # scale kept
    assert charge.rate == Decimal("0.06912")
    assert charge.model_dump_json() == '{"amount":"1.50","rate":"0.06912"}'
    assert isinstance(charge.model_dump()["amount"], Decimal)  # python mode keeps Decimal
    assert StrictCharge.model_validate_json('{"amount":"-3"}').amount == Decimal(-3)


def test_decimal_str_serializes_canonically() -> None:
    assert Charge(amount=Decimal("1E+3")).model_dump(mode="json") == {
        "amount": "1000",
        "rate": None,
    }
    assert Charge(amount=Decimal("-0.00")).model_dump(mode="json")["amount"] == "0.00"


@pytest.mark.req("AT-ZERO-MISSING")
def test_decimal_str_null_is_missing_not_zero() -> None:
    charge = Charge.model_validate_json('{"amount":"0","rate":null}')
    assert charge.rate is None
    assert charge.amount == 0
    assert charge.model_dump_json() == '{"amount":"0","rate":null}'


@pytest.mark.req("P-FIXED-LINEAR")
@pytest.mark.parametrize("number", ["1.5", "1", "0", "-3", "1e3", "100000.00", "true", "false"])
def test_decimal_str_rejects_json_numbers_and_bools(number: str) -> None:
    for model in (Charge, StrictCharge):
        with pytest.raises(ValidationError) as caught:
            model.model_validate_json(f'{{"amount":{number}}}')
        (error,) = caught.value.errors()
        assert error["type"] == "decimal_must_be_string"
        assert "decimal_must_be_string" in error["msg"]
        assert error["loc"] == ("amount",)


def test_decimal_str_rejects_numbers_in_optional_fields() -> None:
    with pytest.raises(ValidationError) as caught:
        Charge.model_validate_json('{"amount":"1","rate":0.5}')
    (error,) = caught.value.errors()
    assert (error["type"], error["loc"]) == ("decimal_must_be_string", ("rate",))


@pytest.mark.parametrize("bad", [A_FLOAT, 1, 0, True, None, [1]])
def test_decimal_str_rejects_python_numbers_bools_and_null(bad: object) -> None:
    with pytest.raises(ValidationError) as caught:
        Charge.model_validate({"amount": bad})
    assert caught.value.errors()[0]["type"] == "decimal_must_be_string"


@pytest.mark.parametrize(
    "bad",
    ["1e3", "1E+3", "+1", " 1", "1 ", "1.", ".5", "1,000", "NaN", "Infinity", "", "١٢", "12\n"],
)
def test_decimal_str_rejects_malformed_strings(bad: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Charge.model_validate({"amount": bad})
    assert caught.value.errors()[0]["type"] == "decimal_invalid_format"


def test_decimal_str_accepts_decimals_but_not_non_finite_ones() -> None:
    assert Charge.model_validate({"amount": Decimal("2.5")}).amount == Decimal("2.5")
    with pytest.raises(ValidationError) as caught:
        Charge.model_validate({"amount": Decimal("NaN")})
    assert caught.value.errors()[0]["type"] == "decimal_not_finite"


@pytest.mark.parametrize(
    "bad",
    ["1" + "0" * 6145, "-" + "9" * 6146, "0." + "0" * 6176 + "1", "0." + "0" * 6177],
)
def test_decimal_str_rejects_strings_outside_the_engine_range(bad: str) -> None:
    for model in (Charge, StrictCharge):
        with pytest.raises(ValidationError) as caught:
            model.model_validate_json(f'{{"amount":"{bad}"}}')
        (error,) = caught.value.errors()
        assert error["type"] == "decimal_out_of_range"


def test_decimal_str_rejects_decimals_outside_the_engine_range() -> None:
    for text in [*FAR_BEYOND, "1E+6145"]:
        with pytest.raises(ValidationError) as caught:
            Charge.model_validate({"amount": Decimal(text)})
        assert caught.value.errors()[0]["type"] == "decimal_out_of_range"


def test_decimal_str_round_trips_the_edges_of_the_engine_range() -> None:
    for text in (LARGEST, "-" + LARGEST, SMALLEST_STEP):
        charge = Charge(amount=Decimal(text))
        restored = Charge.model_validate_json(charge.model_dump_json())
        assert restored.amount == Decimal(text)


def test_decimal_str_json_schema_is_a_patterned_string() -> None:
    properties = Charge.model_json_schema()["properties"]
    assert properties["amount"]["type"] == "string"
    assert properties["amount"]["pattern"] == DECIMAL_JSON_PATTERN == r"^-?\d+(\.\d+)?$"
    assert {"type": "null"} in properties["rate"]["anyOf"]


@given(money)
def test_decimal_str_json_round_trip_is_exact(amount: Decimal) -> None:
    restored = Charge.model_validate_json(Charge(amount=amount).model_dump_json())
    assert restored.amount == amount
    assert restored.amount.as_tuple().exponent == amount.as_tuple().exponent


# ----------------------------------------------------------------------------------- canonical_json


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    period_end: date
    amount: DecimalStr
    note: str | None = None
    tags: tuple[str, ...] = ()


@pytest.mark.req("R05-DETERMINISM")
def test_canonical_json_sorts_keys_and_has_no_whitespace() -> None:
    data = {"b": 1, "a": [True, None, "¢"], "z": {"y": 1, "x": 2}}
    # keys sorted at every level; list order kept; "¢" not escaped (ensure_ascii=False)
    assert canonical_json(data) == '{"a":[true,null,"¢"],"b":1,"z":{"x":2,"y":1}}'


@pytest.mark.req("R05-DETERMINISM")
def test_canonical_json_is_byte_stable_across_insertion_order() -> None:
    first = {"amount": "1.50", "lines": [{"id": "l2", "amt": "-3"}], "month": "2026-07"}
    second = {"month": "2026-07", "lines": [{"amt": "-3", "id": "l2"}], "amount": "1.50"}
    assert canonical_json(first).encode("utf-8") == canonical_json(second).encode("utf-8")
    assert canonical_json(first) == canonical_json(first)


@pytest.mark.req("R05-DETERMINISM")
def test_canonical_json_dumps_models_in_json_mode() -> None:
    snapshot = Snapshot(
        period_end=date(2026, 6, 14), amount=Decimal("-0.00"), note="¢ unit", tags=("b", "a")
    )
    expected = '{"amount":"0.00","note":"¢ unit","period_end":"2026-06-14","tags":["b","a"]}'
    assert canonical_json(snapshot) == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"x": A_FLOAT},
        {"x": [1, {"y": A_FLOAT}]},
        {"x": Decimal("1.5")},  # must be dumped to a string first
        {"x": date(2026, 7, 1)},
        {"x": {1, 2}},
        {1: "non-string key"},
        A_FLOAT,
    ],
)
def test_canonical_json_rejects_non_json_native_values(bad: object) -> None:
    with pytest.raises(TypeError):
        canonical_json(bad)


@pytest.mark.req("R05-DETERMINISM")
def test_canonical_sha256_hashes_the_canonical_utf8_bytes() -> None:
    expected = hashlib.sha256('{"a":1,"b":"¢"}'.encode()).hexdigest()
    assert canonical_sha256({"b": "¢", "a": 1}) == expected
    # Same value, different scale text -> different content hash (scale is part of the content).
    assert canonical_sha256({"x": "1.5"}) != canonical_sha256({"x": "1.50"})


@pytest.mark.parametrize(
    "bad",
    [
        json.loads('{"note": "\\ud800"}'),  # valid JSON text that decodes to a lone surrogate
        {"\udfff": "key"},
        {"x": ["ok", "\ud83d"]},
    ],
)
def test_canonical_json_rejects_text_without_a_utf8_encoding(bad: object) -> None:
    # A clean ValueError instead of a UnicodeEncodeError deep inside canonical_sha256.
    with pytest.raises(ValueError, match="lone surrogate"):
        canonical_json(bad)
    with pytest.raises(ValueError, match="lone surrogate"):
        canonical_sha256(bad)
    # A proper astral character (a surrogate pair in JSON) is fine.
    assert canonical_json(json.loads('{"note": "\\ud83d\\ude00"}')) == '{"note":"\U0001f600"}'


json_scalars = st.none() | st.booleans() | st.integers() | st.text(max_size=6)


@given(st.dictionaries(st.text(max_size=6), json_scalars | st.lists(json_scalars, max_size=3)))
def test_canonical_json_is_order_independent_and_round_trips(data: dict[str, object]) -> None:
    reversed_order = dict(reversed(list(data.items())))
    text = canonical_json(data)
    assert canonical_json(reversed_order) == text
    assert json.loads(text) == data
