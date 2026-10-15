"""Engine exception hierarchy (A§4.2, A§4.7 step 4(e)).

Every failure raised by the pure engine derives from :class:`EngineError` so the calculator can turn
arithmetic, unit and date failures into a component status ``error`` with a message.
"""

from __future__ import annotations

__all__ = ["CalcError", "DateError", "EngineError", "UnitError"]


class EngineError(Exception):
    """Base class for every error raised by ``tariff_engine``."""


class CalcError(EngineError):
    """Arithmetic failure: division by zero, overflow, or an invalid decimal operation."""


class UnitError(EngineError):
    """Unknown or malformed unit text, or an operation on incompatible units (names both)."""


class DateError(EngineError, ValueError):
    """Malformed ``YYYY-MM`` month, out-of-range month arithmetic or an impossible period."""
