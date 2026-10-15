"""Dimensional units and unit-checked quantities (A§4.2).

A :class:`Unit` is an immutable canonical mapping base symbol -> integer exponent over
``USD, kW, kVAR, kVA, kV, h, day``. Reactive (kVAR) and apparent (kVA) power are their own
dimensions, never interchangeable with kW. Unit text such as ``"cents/kWh"`` parses into a unit
plus an exact scale to the canonical symbols (``USD/(kW*h)`` with scale ``0.01``). A
:class:`Quantity` stores its value already multiplied by that scale, so ``6.912 cents/kWh`` is held
as ``0.06912 USD/kWh`` and rounding always applies to the canonical-unit magnitude.

Unit-text grammar (case-sensitive aliases, whitespace ignored, ``*``/``/`` left-associative)::

    product := power (("*" | "·" | "/") power)*
    power   := atom ("^" ["-"] digits)?          # integer exponent, 1 <= |n| <= 9
    atom    := alias | "1" | "(" product ")"     # "" (empty text) is dimensionless

Exponents are bounded everywhere by :data:`MAX_UNIT_EXPONENT` (9): every :class:`Unit` keeps each
base exponent within ``-9..9`` (so ``str(unit)`` always parses back), and in unit text the
*effective* exponent of each unit name -- the product of the ``^n`` applied to it through
parentheses -- is bounded too, so nesting cannot multiply the limit (``(kW^3)^3`` is ``kW^9``;
``(kW^3)^4`` and ``((MW/kW)^9)^9`` are rejected). Scales are combined exactly: any inexact,
overflowing or underflowing scale is a ``UnitError``; every scale is therefore an exact power of
ten, and re-expressing a value in another unit's scale is exact (no digit is ever rounded away).
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    Subnormal,
    Underflow,
    localcontext,
)
from functools import lru_cache
from typing import Final, NamedTuple

from tariff_engine.decimals import (
    D,
    RoundingMode,
    canonical_str,
    engine_context,
    in_engine_range,
    quantize,
)
from tariff_engine.errors import CalcError, UnitError

__all__ = [
    "BASE_SYMBOLS",
    "DIMENSIONLESS",
    "MAX_UNIT_EXPONENT",
    "MAX_UNIT_TEXT_LENGTH",
    "UNIT_ALIASES",
    "Quantity",
    "Unit",
    "parse_unit",
    "qmax",
    "qmin",
]

BASE_SYMBOLS: Final = ("USD", "kW", "kVAR", "kVA", "kV", "h", "day")
"""Base dimensions, in the order used for canonical display."""

_BASE_ORDER: Final = {symbol: index for index, symbol in enumerate(BASE_SYMBOLS)}
MAX_UNIT_EXPONENT: Final = 9
"""Largest exponent magnitude of a base symbol in a :class:`Unit` (and of a name in unit text)."""
MAX_UNIT_TEXT_LENGTH: Final = 100

# Energy-like products shown with their familiar compound name ("kWh", not "kW*h").
_ENERGY_NAMES: Final = (("kW", "kWh"), ("kVAR", "kVARh"), ("kVA", "kVAh"))


@dataclass(frozen=True, slots=True, init=False)
class Unit:
    """Immutable product of base symbols with int exponents in ``-9..9``; ``Unit({})`` is unitless.

    Constructing a unit (directly or through ``*``, ``/`` and :meth:`power`) whose exponent of
    some symbol exceeds :data:`MAX_UNIT_EXPONENT` in magnitude raises ``UnitError``, so every unit
    has a display that :func:`parse_unit` reads back.
    """

    items: tuple[tuple[str, int], ...]

    def __init__(self, exponents: Mapping[str, int] | None = None) -> None:
        collected: list[tuple[str, int]] = []
        for symbol, exponent in (exponents or {}).items():
            if symbol not in _BASE_ORDER:
                raise UnitError(
                    f"unknown base unit {symbol!r}; base units are {', '.join(BASE_SYMBOLS)}"
                )
            if isinstance(exponent, bool) or not isinstance(exponent, int):
                raise UnitError(
                    f"exponent of {symbol} must be an int, not {type(exponent).__name__}"
                )
            if abs(exponent) > MAX_UNIT_EXPONENT:
                raise UnitError(
                    f"exponent {exponent} of {symbol} is beyond the limit of "
                    f"{MAX_UNIT_EXPONENT} in magnitude"
                )
            if exponent != 0:
                collected.append((symbol, exponent))
        collected.sort(key=lambda item: _BASE_ORDER[item[0]])
        object.__setattr__(self, "items", tuple(collected))

    @property
    def exponents(self) -> dict[str, int]:
        """A fresh ``{symbol: exponent}`` dict (mutating it does not affect the unit)."""
        return dict(self.items)

    @property
    def is_dimensionless(self) -> bool:
        return not self.items

    def _combined(self, other: Unit, sign: int) -> Unit:
        exponents = dict(self.items)
        for symbol, exponent in other.items:
            exponents[symbol] = exponents.get(symbol, 0) + sign * exponent
        try:
            return Unit(exponents)
        except UnitError as exc:
            verb = "multiply" if sign > 0 else "divide"
            raise UnitError(f"cannot {verb} {self} by {other}: {exc}") from None

    def __mul__(self, other: Unit) -> Unit:
        if not isinstance(other, Unit):
            return NotImplemented
        return self._combined(other, 1)

    def __truediv__(self, other: Unit) -> Unit:
        if not isinstance(other, Unit):
            return NotImplemented
        return self._combined(other, -1)

    def power(self, exponent: int) -> Unit:
        """``unit ** exponent`` for an integer exponent."""
        if isinstance(exponent, bool) or not isinstance(exponent, int):
            raise UnitError(f"unit exponent must be an int, not {type(exponent).__name__}")
        try:
            return Unit({symbol: power * exponent for symbol, power in self.items})
        except UnitError as exc:
            raise UnitError(f"cannot raise {self} to the power {exponent}: {exc}") from None

    def __str__(self) -> str:
        """Deterministic display that :func:`parse_unit` reads back with scale 1 (``USD/kWh``)."""
        numerator = [(symbol, exponent) for symbol, exponent in self.items if exponent > 0]
        denominator = [(symbol, -exponent) for symbol, exponent in self.items if exponent < 0]
        top = _format_side(numerator)[0] if numerator else "1"
        if not denominator:
            return top
        bottom, factor_count = _format_side(denominator)
        return f"{top}/{bottom}" if factor_count == 1 else f"{top}/({bottom})"

    def __repr__(self) -> str:
        return f"Unit({dict(self.items)!r})"


def _format_side(factors: list[tuple[str, int]]) -> tuple[str, int]:
    """Render one side of a fraction (positive exponents); returns the text and its factor count."""
    exponents = dict(factors)
    parts = list(factors)
    if exponents.get("h") == 1:
        for power_symbol, energy_name in _ENERGY_NAMES:
            if exponents.get(power_symbol) == 1:
                parts = [
                    (energy_name, 1) if symbol == power_symbol else (symbol, exponent)
                    for symbol, exponent in parts
                    if symbol != "h"
                ]
                break
    text = "*".join(
        symbol if exponent == 1 else f"{symbol}^{exponent}" for symbol, exponent in parts
    )
    return text, len(parts)


DIMENSIONLESS: Final = Unit()

_USD = Unit({"USD": 1})
_KW = Unit({"kW": 1})
_KVAR = Unit({"kVAR": 1})
_KVA = Unit({"kVA": 1})
_HOUR = Unit({"h": 1})

UNIT_ALIASES: Final[Mapping[str, tuple[Unit, Decimal]]] = {
    "USD": (_USD, Decimal(1)),
    "$": (_USD, Decimal(1)),
    "cents": (_USD, Decimal("0.01")),
    "¢": (_USD, Decimal("0.01")),
    "mills": (_USD, Decimal("0.001")),
    "kW": (_KW, Decimal(1)),
    "MW": (_KW, Decimal(1000)),
    "kVAR": (_KVAR, Decimal(1)),
    "kvar": (_KVAR, Decimal(1)),
    "kVAr": (_KVAR, Decimal(1)),
    "kVA": (_KVA, Decimal(1)),
    "kWh": (_KW * _HOUR, Decimal(1)),
    "MWh": (_KW * _HOUR, Decimal(1000)),
    "kVARh": (_KVAR * _HOUR, Decimal(1)),
    "kvarh": (_KVAR * _HOUR, Decimal(1)),
    "kVAh": (_KVA * _HOUR, Decimal(1)),
    "h": (_HOUR, Decimal(1)),
    "hour": (_HOUR, Decimal(1)),
    "hours": (_HOUR, Decimal(1)),
    "day": (Unit({"day": 1}), Decimal(1)),
    "days": (Unit({"day": 1}), Decimal(1)),
    "kV": (Unit({"kV": 1}), Decimal(1)),
    "%": (DIMENSIONLESS, Decimal("0.01")),
    "percent": (DIMENSIONLESS, Decimal("0.01")),
}
"""Alias -> (canonical unit, exact scale to canonical). Case-sensitive: ``kw`` is not ``kW``."""

_TOKEN_RE: Final = re.compile(
    r"(?P<space>\s+)|(?P<symbol>[A-Za-z$¢%]+)|(?P<number>[0-9]+)|(?P<op>[-*/^()·])"
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN_RE.match(text, position)
        if match is None:
            raise UnitError(f"unexpected character {text[position]!r} in unit {text!r}")
        kind = match.lastgroup or ""
        if kind != "space":
            tokens.append((kind, match.group()))
        position = match.end()
    return tokens


_SCALE_TRAPS: Final = (Inexact, Underflow, Subnormal, Clamped)
"""Extra traps for scale arithmetic: alias scales combine exactly, so any of these is bad text."""


class _Term(NamedTuple):
    """A parsed sub-expression of unit text."""

    unit: Unit
    scale: Decimal
    peak: int  # largest |effective exponent| of a unit name inside (0 when there is none)


class _UnitParser:
    """Recursive-descent parser for the module-docstring grammar; scales are combined exactly."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._tokens = _tokenize(text)
        self._position = 0

    def parse(self) -> tuple[Unit, Decimal]:
        if not self._tokens:
            return DIMENSIONLESS, Decimal(1)
        term = self._product()
        if self._position < len(self._tokens):
            raise self._error(f"unexpected {self._tokens[self._position][1]!r}")
        return term.unit, term.scale

    def _error(self, reason: str) -> UnitError:
        return UnitError(f"invalid unit {self._text!r}: {reason}")

    def _peek(self) -> str | None:
        if self._position < len(self._tokens):
            return self._tokens[self._position][1]
        return None

    def _take(self) -> tuple[str, str]:
        if self._position >= len(self._tokens):
            raise self._error("unexpected end of unit text")
        token = self._tokens[self._position]
        self._position += 1
        return token

    def _exact_scale(
        self, op: Callable[[Decimal, Decimal], Decimal], left: Decimal, right: Decimal
    ) -> Decimal:
        with engine_context() as context:
            for signal in _SCALE_TRAPS:
                context.traps[signal] = True
            try:
                return op(left, right)
            except DecimalException as exc:
                raise self._error(
                    f"its scale is not exactly representable ({type(exc).__name__})"
                ) from None

    def _product(self) -> _Term:
        term = self._power()
        while self._peek() in ("*", "·", "/"):
            _, op = self._take()
            right = self._power()
            try:
                unit = term.unit / right.unit if op == "/" else term.unit * right.unit
            except UnitError as exc:
                raise self._error(str(exc)) from None
            scale_op = operator.truediv if op == "/" else operator.mul
            scale = self._exact_scale(scale_op, term.scale, right.scale)
            term = _Term(unit, scale, max(term.peak, right.peak))
        return term

    def _power(self) -> _Term:
        term = self._atom()
        if self._peek() != "^":
            return term
        self._take()
        sign = 1
        if self._peek() == "-":
            self._take()
            sign = -1
        kind, digits = self._take()
        if kind != "number":
            raise self._error(f"exponent must be an integer, got {digits!r}")
        exponent = sign * int(digits)
        if not 1 <= abs(exponent) <= MAX_UNIT_EXPONENT:
            raise self._error(f"exponent must be between 1 and {MAX_UNIT_EXPONENT} in magnitude")
        peak = term.peak * abs(exponent)
        if peak > MAX_UNIT_EXPONENT:
            raise self._error(
                f"nested exponents raise a unit to the power {peak}; the limit is "
                f"{MAX_UNIT_EXPONENT} in magnitude"
            )
        try:
            unit = term.unit.power(exponent)
        except UnitError as exc:
            raise self._error(str(exc)) from None
        scale = self._exact_scale(operator.pow, term.scale, Decimal(exponent))
        return _Term(unit, scale, peak)

    def _atom(self) -> _Term:
        kind, text = self._take()
        if kind == "symbol":
            alias = UNIT_ALIASES.get(text)
            if alias is None:
                raise UnitError(f"unknown unit {text!r} in {self._text!r}")
            return _Term(alias[0], alias[1], 1)
        if kind == "number":
            if text != "1":
                raise self._error(f"only '1' may stand for a number, got {text!r}")
            return _Term(DIMENSIONLESS, Decimal(1), 0)
        if text == "(":
            term = self._product()
            if self._peek() != ")":
                raise self._error("missing ')'")
            self._take()
            return term
        raise self._error(f"unexpected {text!r}")


@lru_cache(maxsize=1024)
def _parse_unit_cached(text: str) -> tuple[Unit, Decimal]:
    return _UnitParser(text).parse()


def parse_unit(text: str) -> tuple[Unit, Decimal]:
    """Parse unit text into ``(canonical unit, exact scale)``.

    ``"cents/kWh"`` -> ``(USD/kWh, 0.01)``; raises ``UnitError`` for unknown or malformed text.
    """
    if not isinstance(text, str):
        raise TypeError(f"unit text must be str, not {type(text).__name__}")
    if len(text) > MAX_UNIT_TEXT_LENGTH:
        raise UnitError(f"unit text longer than {MAX_UNIT_TEXT_LENGTH} characters")
    return _parse_unit_cached(text)


# --------------------------------------------------------------------------------------- quantities


def _arith(op: Callable[[Decimal, Decimal], Decimal], left: Decimal, right: Decimal) -> Decimal:
    with engine_context():
        try:
            return op(left, right)
        except DecimalException as exc:
            raise CalcError(
                f"arithmetic failed ({type(exc).__name__}): {left} and {right}"
            ) from exc


def _arith1(op: Callable[[Decimal], Decimal], operand: Decimal) -> Decimal:
    with engine_context():
        try:
            return op(operand)
        except DecimalException as exc:
            raise CalcError(f"arithmetic failed ({type(exc).__name__}): {operand}") from exc


def _rescaled(value: Decimal, scale: Decimal, *, divide: bool) -> Decimal:
    """``value * scale`` (or ``/ scale``) exactly, for an exact power-of-ten ``scale``.

    The context has enough digits for the exact result and an unbounded exponent range, so this
    never rounds (``Inexact`` is trapped as a guard) and never overflows; callers range-check.
    """
    digits = len(value.as_tuple().digits) + len(scale.as_tuple().digits) + 1
    context = Context(
        prec=digits,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[Inexact, InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation],
    )
    with localcontext(context):
        return value / scale if divide else value * scale


@dataclass(frozen=True, slots=True, init=False, eq=False)
class Quantity:
    """An exact decimal magnitude in canonical units, with the unit text it was entered in.

    ``value`` is in canonical base units; ``label`` is the display unit text and always parses to
    ``unit``; ``label_value`` re-expresses ``value`` in ``label`` units (``6.912 cents/kWh``).
    Normalizing an entered value to the canonical scale is exact (every digit is kept, whatever the
    unit's scale) and the result must lie in the engine range (else ``CalcError``).

    Arithmetic runs in :func:`engine_context`. ``+``, ``-``, :func:`qmin`, :func:`qmax` and the
    ordering comparisons / :meth:`compare` require identical units (``UnitError`` naming both),
    except that a *zero literal* (:meth:`zero_literal`, the expression literal ``0``) adopts the
    other operand's unit. ``*`` and ``/`` combine units; dividing by zero raises ``CalcError``.
    ``==`` is structural equality (value, unit, zero-literal flag; not ``label``) and never raises.
    """

    value: Decimal
    unit: Unit
    label: str
    is_zero_literal: bool

    def __init__(
        self,
        value: str | int | Decimal,
        unit: Unit | str = DIMENSIONLESS,
        label: str | None = None,
    ) -> None:
        magnitude = D(value)
        if isinstance(unit, str):
            parsed, scale = parse_unit(unit)
            if scale != 1:
                entered, magnitude = magnitude, _rescaled(magnitude, scale, divide=False)
                if not in_engine_range(magnitude):  # str(Decimal) stays short at any magnitude
                    raise CalcError(
                        f"{entered} {unit} is {magnitude} in canonical units, outside the engine "
                        "range"
                    )
            text = unit
        elif isinstance(unit, Unit):
            parsed, text = unit, str(unit)
        else:
            raise TypeError(f"unit must be a Unit or unit text, not {type(unit).__name__}")
        if label is not None:
            if parse_unit(label)[0] != parsed:
                raise UnitError(f"label {label!r} is not a unit of dimension {parsed}")
            text = label
        _init_quantity(self, magnitude, parsed, text, is_zero_literal=False)

    @classmethod
    def zero_literal(cls, value: str | int | Decimal = 0) -> Quantity:
        """The unit-polymorphic literal ``0`` of the expression language (A§4.2)."""
        magnitude = D(value)
        if not magnitude.is_zero():
            raise ValueError(f"a zero literal must be zero, got {value!r}")
        return _make_quantity(magnitude, DIMENSIONLESS, "1", is_zero_literal=True)

    # -- display -------------------------------------------------------------------------------

    @property
    def label_value(self) -> Decimal:
        """``value`` in ``label`` units (``0.06912 USD/kWh`` labelled cents/kWh -> ``6.912``).

        Exact and never raises: the label's scale is a power of ten.
        """
        scale = parse_unit(self.label)[1]
        if scale == 1:
            return self.value
        return _rescaled(self.value, scale, divide=True)

    def __str__(self) -> str:
        """``6.912 cents/kWh``; never fails.

        In the one case where the label re-expression leaves the engine range (a value at the
        edge of the range shown in a smaller label unit) the canonical value and unit are shown.
        """
        label_value = self.label_value
        if not in_engine_range(label_value):
            return _describe(self)
        number = canonical_str(label_value)
        return number if self.label in ("", "1") else f"{number} {self.label}"

    def __repr__(self) -> str:
        flag = ", zero_literal" if self.is_zero_literal else ""
        value, unit = canonical_str(self.value), str(self.unit)
        return f"Quantity({value!r}, {unit!r}, label={self.label!r}{flag})"

    # -- structural equality ---------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return (
            self.value == other.value
            and self.unit == other.unit
            and self.is_zero_literal == other.is_zero_literal
        )

    def __hash__(self) -> int:
        return hash((self.value, self.unit, self.is_zero_literal))

    # -- unit-checked arithmetic -----------------------------------------------------------------

    def _common_unit(self, other: Quantity, action: str) -> Unit:
        if not isinstance(other, Quantity):
            raise TypeError(f"cannot {action} a Quantity and {type(other).__name__}")
        if self.unit == other.unit:
            return self.unit
        if self.is_zero_literal:
            return other.unit
        if other.is_zero_literal:
            return self.unit
        raise UnitError(f"cannot {action} {self.unit} and {other.unit}: units differ")

    def __add__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        unit = self._common_unit(other, "add")
        value = _arith(operator.add, self.value, other.value)
        both_literal = self.is_zero_literal and other.is_zero_literal
        return _make_quantity(value, unit, str(unit), is_zero_literal=both_literal)

    def __sub__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        unit = self._common_unit(other, "subtract")
        value = _arith(operator.sub, self.value, other.value)
        both_literal = self.is_zero_literal and other.is_zero_literal
        return _make_quantity(value, unit, str(unit), is_zero_literal=both_literal)

    def __mul__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        unit = self.unit * other.unit
        return _make_quantity(_arith(operator.mul, self.value, other.value), unit, str(unit))

    def __truediv__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        if other.value.is_zero():
            raise CalcError(f"division by zero: {_describe(self)} / {_describe(other)}")
        unit = self.unit / other.unit
        return _make_quantity(_arith(operator.truediv, self.value, other.value), unit, str(unit))

    def __neg__(self) -> Quantity:
        value = _arith1(operator.neg, self.value)
        return _make_quantity(value, self.unit, self.label, is_zero_literal=self.is_zero_literal)

    def __abs__(self) -> Quantity:
        value = _arith1(abs, self.value)
        return _make_quantity(value, self.unit, self.label, is_zero_literal=self.is_zero_literal)

    def compare(self, other: Quantity) -> int:
        """Unit-checked three-way comparison: -1, 0 or 1 (expression ``==``/``!=`` use this)."""
        self._common_unit(other, "compare")
        return (self.value > other.value) - (self.value < other.value)

    def __lt__(self, other: Quantity) -> bool:
        return self.compare(other) < 0

    def __le__(self, other: Quantity) -> bool:
        return self.compare(other) <= 0

    def __gt__(self, other: Quantity) -> bool:
        return self.compare(other) > 0

    def __ge__(self, other: Quantity) -> bool:
        return self.compare(other) >= 0

    def quantize(self, places: int, mode: RoundingMode) -> Quantity:
        """Round the canonical-unit magnitude (``places=5`` on USD/kWh = 0.001 cents).

        The result is never a zero literal: polymorphism survives only unary ``-``, ``abs`` and
        ``+ - min max`` among zero literals (A§4.2), so a rounded literal ``0`` is a plain value.
        """
        value = quantize(self.value, places, mode)
        return _make_quantity(value, self.unit, self.label)


def _describe(quantity: Quantity) -> str:
    """Canonical value and unit (error messages, display fallback).

    It never re-parses the label, and every quantity's ``value`` is in the engine range (so its
    plain text is at most about 12,000 characters), so it cannot fail.
    """
    number = canonical_str(quantity.value)
    return number if quantity.unit.is_dimensionless else f"{number} {quantity.unit}"


def _init_quantity(
    target: Quantity, value: Decimal, unit: Unit, label: str, *, is_zero_literal: bool
) -> None:
    object.__setattr__(target, "value", value)
    object.__setattr__(target, "unit", unit)
    object.__setattr__(target, "label", label)
    object.__setattr__(target, "is_zero_literal", is_zero_literal)


def _make_quantity(
    value: Decimal, unit: Unit, label: str, *, is_zero_literal: bool = False
) -> Quantity:
    instance = object.__new__(Quantity)
    _init_quantity(instance, value, unit, label, is_zero_literal=is_zero_literal)
    return instance


def _extreme(
    name: str, operands: tuple[Quantity, ...], wins: Callable[[Decimal, Decimal], bool]
) -> Quantity:
    reference: Quantity | None = None
    for operand in operands:
        if not isinstance(operand, Quantity):
            raise TypeError(f"{name}() needs Quantity operands, not {type(operand).__name__}")
        if operand.is_zero_literal:
            continue
        if reference is None:
            reference = operand
        elif operand.unit != reference.unit:
            raise UnitError(
                f"cannot take {name} of {reference.unit} and {operand.unit}: units differ"
            )
    best = operands[0]
    for operand in operands[1:]:
        if wins(operand.value, best.value):
            best = operand  # strict comparison: the first of equal candidates is kept
    if reference is None or not best.is_zero_literal:
        return best
    return _make_quantity(best.value, reference.unit, str(reference.unit))


def qmin(first: Quantity, *rest: Quantity) -> Quantity:
    """Unit-checked ``min`` (zero literals adopt the common unit); ties keep the first operand."""
    return _extreme("min", (first, *rest), operator.lt)


def qmax(first: Quantity, *rest: Quantity) -> Quantity:
    """Unit-checked ``max`` (zero literals adopt the common unit); ties keep the first operand."""
    return _extreme("max", (first, *rest), operator.gt)
