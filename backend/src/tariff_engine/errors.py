"""Engine exception hierarchy (A§4.2, A§4.3, A§4.7 step 4(e)).

Every failure raised by the pure engine derives from :class:`EngineError` so the calculator can turn
arithmetic, unit, date and formula failures into a component status ``error`` with a message, and
missing inputs (:class:`MissingInputError`) into ``missing_input`` with one request per name.
"""

from __future__ import annotations

__all__ = [
    "CalcError",
    "DateError",
    "EngineError",
    "ExpressionError",
    "MissingInputError",
    "UnitError",
]


class EngineError(Exception):
    """Base class for every error raised by ``tariff_engine``.

    ``offset`` (0-based character offset into a formula), ``line`` and ``column`` (1-based,
    counting characters, not bytes) locate the error in formula text when one applies: always for
    :class:`ExpressionError`, and for a ``UnitError``/``CalcError`` raised while evaluating a
    formula (the span of the innermost node whose own operation failed, A§4.3). They are ``None``
    otherwise, and ``str()`` then prints only the message.
    """

    offset: int | None = None
    line: int | None = None
    column: int | None = None

    def locate(self, offset: int, line: int, column: int) -> None:
        """Attach a formula position (the exception type and message are unchanged)."""
        self.offset, self.line, self.column = offset, line, column

    def __str__(self) -> str:
        text = super().__str__()
        if self.line is None or self.column is None:
            return text
        return f"{text} (line {self.line}, column {self.column})"


class CalcError(EngineError):
    """Arithmetic failure: division by zero, overflow, or an invalid decimal operation."""


class UnitError(EngineError):
    """Unknown or malformed unit text, or an operation on incompatible units (names both)."""


class DateError(EngineError, ValueError):
    """Malformed ``YYYY-MM`` month, out-of-range month arithmetic or an impossible period."""


class ExpressionError(EngineError):
    """A formula that is not valid in the expression language (A§4.3), with its source position.

    Raised by ``parse_expression`` for syntax errors, disallowed constructs, bad function arguments
    and the size/depth limits, and by ``evaluate`` when a value of the wrong kind reaches an
    operator (a condition used as a number or the reverse). ``offset`` is the 0-based character
    offset into the formula text; ``line`` and ``column`` are 1-based (columns count characters,
    not bytes). All three are ``None`` only when no single position applies.
    """

    def __init__(
        self,
        message: str,
        *,
        offset: int | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.line = line
        self.column = column


class MissingInputError(EngineError):
    """A value needed to evaluate a formula is absent or null (A§3.2: missing is never zero).

    ``name`` is the first missing name met in evaluation order; ``names`` lists every missing name
    the evaluation proved it needs (no duplicates), so a caller can request all of them at once.
    """

    def __init__(self, name: str, *more: str) -> None:
        names = tuple(dict.fromkeys((name, *more)))
        super().__init__(*names)
        self.name = name
        self.names = names

    def __str__(self) -> str:
        label = "input" if len(self.names) == 1 else "inputs"
        return f"missing {label}: {', '.join(self.names)}"
