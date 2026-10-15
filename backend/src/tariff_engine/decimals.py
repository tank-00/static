"""Exact decimals, rounding and canonical forms (A§4.1, A§3.1, A§3.6).

* :func:`D` is the only sanctioned way to turn ``str``/``int`` into a ``Decimal``; floats and bools
  are rejected so a binary float can never leak into money, rates or quantities.
* :func:`engine_context` fixes the arithmetic context (34 digits, banker's rounding, the IEEE 754
  decimal128 exponent range, traps) so identical inputs always produce identical results.
* Every engine decimal lies in the *engine range* (:func:`in_engine_range`): exactly the finite
  values that context can hold, so displaying one is always cheap and never fails.
* :func:`canonical_str`, :data:`DecimalStr` and :func:`canonical_json` are the transport and
  hashing forms.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import AbstractContextManager
from decimal import (
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, PlainSerializer, WithJsonSchema
from pydantic_core import PydanticCustomError

from tariff_engine.errors import CalcError

__all__ = [
    "DECIMAL_JSON_PATTERN",
    "ENGINE_EMAX",
    "ENGINE_EMIN",
    "ENGINE_ETINY",
    "ENGINE_PRECISION",
    "D",
    "DecimalStr",
    "RoundingMode",
    "canonical_json",
    "canonical_sha256",
    "canonical_str",
    "engine_context",
    "in_engine_range",
    "quantize",
]

ENGINE_PRECISION: Final = 34
"""Significant digits of every engine arithmetic result (IEEE 754 decimal128)."""

ENGINE_EMAX: Final = 6_144
"""Largest adjusted exponent (power of ten of the leading digit) of an engine decimal."""

ENGINE_EMIN: Final = -6_143
"""Smallest adjusted exponent of a normal engine decimal (decimal128)."""

ENGINE_ETINY: Final = ENGINE_EMIN - (ENGINE_PRECISION - 1)
"""Smallest exponent (power of ten of the last digit) of any engine decimal: ``-6176``."""

DECIMAL_JSON_PATTERN: Final = r"^-?\d+(\.\d+)?$"
"""JSON-schema pattern advertised for :data:`DecimalStr` (plain decimal, no exponent)."""

# ``re.fullmatch`` with ASCII digits only: ``$`` would accept a trailing newline and ``\d`` would
# accept non-ASCII digits (which ``Decimal`` parses), so the runtime check is deliberately stricter.
_PLAIN_DECIMAL_RE: Final = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")

# Text accepted by D(): ASCII digits with an optional sign, point and exponent ("6.912", "-1",
# ".5", "5.", "1E+3"). No whitespace, underscores, non-ASCII digits or NaN/Infinity spellings.
_D_TEXT_RE: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")

# |int| below this has at most ENGINE_EMAX + 1 digits (checked before converting int -> Decimal).
_INT_LIMIT: Final = 10 ** (ENGINE_EMAX + 1)

_ENGINE_TRAPS: Final = (InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation)

# A lone UTF-16 surrogate (e.g. from JSON "\ud800") is a valid Python str but has no UTF-8 encoding.
_SURROGATE_RE: Final = re.compile(r"[\ud800-\udfff]")


def in_engine_range(value: Decimal) -> bool:
    """True iff ``value`` is a finite Decimal the engine context can hold exactly.

    That is ``value.adjusted() <= ENGINE_EMAX`` (at most 6145 integer digits) and an exponent of at
    least ``ENGINE_ETINY`` (at most 6176 decimal places). Every engine arithmetic result is in
    range by construction; :func:`D`, :data:`DecimalStr` and :func:`canonical_str` refuse values
    outside it, so a short text such as ``"1E+99999999999"`` can never become a gigabyte string.
    """
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value.adjusted() <= ENGINE_EMAX
        and value.as_tuple().exponent >= ENGINE_ETINY  # type: ignore[operator]  # int when finite
    )


def _range_message(value: Decimal) -> str:
    # str(Decimal) uses exponent notation, so this message stays short for any magnitude.
    return (
        f"decimal {value} is outside the engine range (at most {ENGINE_EMAX + 1} integer digits "
        f"and {-ENGINE_ETINY} decimal places)"
    )


def D(value: str | int | Decimal) -> Decimal:
    """Build an exact, finite Decimal in the engine range from ``str``, ``int`` or ``Decimal``.

    Text must be ASCII: optional sign, digits with an optional point, optional exponent
    (``"6.912"``, ``"-1"``, ``".5"``, ``"1E+3"``). Raises ``TypeError`` for any other type
    (notably floats and bools) and ``ValueError`` for other text (whitespace, underscores,
    non-ASCII digits, NaN/Infinity), non-finite Decimals, and values outside
    :func:`in_engine_range`.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TypeError(f"D() accepts str, int or Decimal, not {type(value).__name__}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        if not -_INT_LIMIT < value < _INT_LIMIT:  # never format a huge int (str() would refuse)
            raise ValueError(
                f"integer with more than {ENGINE_EMAX + 1} digits is outside the engine range"
            )
        result = Decimal(value)
    else:
        if _D_TEXT_RE.fullmatch(value) is None:
            raise ValueError(f"not a decimal number: {value[:50]!r}")
        try:
            result = Decimal(value)
        except InvalidOperation:  # an exponent beyond what Decimal itself can represent
            raise ValueError(f"decimal is outside the engine range: {value[:50]!r}") from None
    if not result.is_finite():
        raise ValueError(f"decimal must be finite, got {value!r}")
    if not in_engine_range(result):
        raise ValueError(_range_message(result))
    return result


def _new_engine_context() -> Context:
    # Every field is explicit so a mutated global DefaultContext cannot change engine arithmetic.
    return Context(
        prec=ENGINE_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=ENGINE_EMIN,
        Emax=ENGINE_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=list(_ENGINE_TRAPS),
    )


def engine_context() -> AbstractContextManager[Context]:
    """Local decimal context for all engine arithmetic.

    ``prec=34``, ``ROUND_HALF_EVEN``, decimal128 exponents (``Emax=6144``, ``Emin=-6143``,
    ``clamp=0``); traps ``InvalidOperation``, ``DivisionByZero``, ``Overflow``, ``Underflow`` (a
    tiny result is never silently rounded toward zero) and ``FloatOperation`` (any implicit
    float/Decimal mixing raises). A fresh context is created on every call.
    """
    return localcontext(_new_engine_context())


class RoundingMode(StrEnum):
    """Tariff rounding modes (A§4.1). Values are the names used in rules and ``round_to``."""

    HALF_UP = "HALF_UP"  # ties away from zero
    HALF_EVEN = "HALF_EVEN"  # ties to the even neighbour
    DOWN = "DOWN"  # toward zero (truncate)
    UP = "UP"  # away from zero
    CEILING = "CEILING"  # toward +infinity
    FLOOR = "FLOOR"  # toward -infinity


_DECIMAL_ROUNDING: Final[dict[RoundingMode, str]] = {
    RoundingMode.HALF_UP: ROUND_HALF_UP,
    RoundingMode.HALF_EVEN: ROUND_HALF_EVEN,
    RoundingMode.DOWN: ROUND_DOWN,
    RoundingMode.UP: ROUND_UP,
    RoundingMode.CEILING: ROUND_CEILING,
    RoundingMode.FLOOR: ROUND_FLOOR,
}


def quantize(value: Decimal, places: int, mode: RoundingMode) -> Decimal:
    """Round ``value`` to ``places`` decimal places (negative = tens, hundreds, ...) with ``mode``.

    The result carries exactly ``places`` decimal places (``1.2`` at 2 places is ``1.20``). A zero
    result is always non-negative (``-0.004`` -> ``0.00``) so signs never depend on rounding
    artefacts. Raises ``CalcError`` when the result cannot be represented in 34 digits or
    ``places`` lies outside ``-ENGINE_EMAX..-ENGINE_ETINY`` (``-6144..6176``).
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"quantize() needs a Decimal, not {type(value).__name__}")
    if isinstance(places, bool) or not isinstance(places, int):
        raise TypeError(f"places must be an int, not {type(places).__name__}")
    if not value.is_finite():  # a quiet NaN would otherwise pass through quantize() unsignalled
        raise CalcError(f"cannot round non-finite value {value}")
    rounding = _DECIMAL_ROUNDING[RoundingMode(mode)]
    if not ENGINE_ETINY <= -places <= ENGINE_EMAX:  # checked before building the template
        raise CalcError(f"cannot round to {places} places: beyond the engine exponent range")
    template = Decimal((0, (1,), -places))
    with engine_context():
        try:
            result = value.quantize(template, rounding=rounding)
        except DecimalException as exc:
            raise CalcError(
                f"cannot round {value} to {places} places: {type(exc).__name__}"
            ) from exc
    if result.is_zero() and result.is_signed():
        result = result.copy_abs()
    return result


def canonical_str(value: Decimal) -> str:
    """Canonical transport/storage text: plain notation, no exponent, scale kept, no ``-0``.

    ``Decimal("1E+3")`` -> ``"1000"``; ``Decimal("1.50")`` -> ``"1.50"``;
    ``Decimal("-0.00")`` -> ``"0.00"``. A value outside :func:`in_engine_range` raises
    ``ValueError`` (its plain text could be gigabytes long).
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"canonical_str() needs a Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"cannot serialize non-finite decimal {value!r}")
    if not in_engine_range(value):
        raise ValueError(_range_message(value))
    if value.is_zero() and value.is_signed():
        value = value.copy_abs()
    return format(value, "f")


def _validate_decimal_input(value: object) -> Decimal:
    """Pydantic before-validator for :data:`DecimalStr` (python and JSON mode alike).

    Accepts a plain decimal string (the transport form) or an already-built finite ``Decimal``
    (programmatic construction), either within :func:`in_engine_range`. Every JSON number --
    integer or not -- and every bool is rejected, because a JSON number may already have passed
    through a binary float (A§3.1).
    """
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PydanticCustomError("decimal_not_finite", "Decimal value must be finite")
        return _in_range_for_model(value)
    if isinstance(value, str):
        if _PLAIN_DECIMAL_RE.fullmatch(value) is None:
            raise PydanticCustomError(
                "decimal_invalid_format",
                "Decimal strings must be plain decimals such as '-12.34' "
                "(ASCII digits, optional leading '-', no exponent or whitespace)",
            )
        return _in_range_for_model(Decimal(value))
    raise PydanticCustomError(
        "decimal_must_be_string",
        "decimal_must_be_string: decimal values must be JSON strings such as '12.34', "
        "not {input_type}",
        {"input_type": type(value).__name__},
    )


def _in_range_for_model(value: Decimal) -> Decimal:
    if not in_engine_range(value):
        raise PydanticCustomError(
            "decimal_out_of_range",
            "Decimal value is outside the engine range (at most {integer_digits} integer digits "
            "and {decimal_places} decimal places)",
            {"integer_digits": ENGINE_EMAX + 1, "decimal_places": -ENGINE_ETINY},
        )
    return value


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_validate_decimal_input),
    PlainSerializer(canonical_str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_JSON_PATTERN}),
]
"""Decimal field type for every engine and API model: string in JSON, ``Decimal`` in Python."""


def _check_canonical_json_text(text: str, path: str) -> None:
    if _SURROGATE_RE.search(text) is not None:
        raise ValueError(f"text at {path} is not valid Unicode (lone surrogate)")


def _check_canonical_json_value(value: object, path: str) -> None:
    # Positive allow-list of JSON-native types. Binary floats, Decimals, dates, sets ... are refused
    # so callers go through ``model_dump(mode="json")`` (decimals rendered by ``canonical_str``).
    if isinstance(value, str):
        _check_canonical_json_text(value, path)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_canonical_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"canonical JSON object keys must be str, got {type(key).__name__} at {path}"
                )
            _check_canonical_json_text(key, f"{path} (key)")
            _check_canonical_json_value(item, f"{path}.{key}")
        return
    raise TypeError(f"value of type {type(value).__name__} at {path} is not canonical JSON")


def canonical_json(obj: object) -> str:
    """Byte-stable JSON: sorted keys, no whitespace, UTF-8 text kept as-is.

    Pydantic models are dumped with ``model_dump(mode="json")`` first. Plain data must already be
    JSON-native (``None``/bool/int/str/list/tuple/dict with str keys); anything else, including
    floats, raises ``TypeError``. Text that is not valid Unicode (a lone surrogate) raises
    ``ValueError``, so the result always encodes to UTF-8. This is the only input to fingerprints
    and content hashes.
    """
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    _check_canonical_json_value(data, "$")
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_sha256(obj: object) -> str:
    """Hex SHA-256 of the UTF-8 bytes of :func:`canonical_json` (fingerprints, content hashes)."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
