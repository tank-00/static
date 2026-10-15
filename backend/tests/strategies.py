"""Bounded Hypothesis strategies for exact-decimal arithmetic laws.

Unbounded decimals break algebraic laws at the engine's 34-digit precision (e.g. 1E-30 + 1E+10),
so property tests must use these bounded strategies.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

dec = st.decimals(
    min_value=Decimal("-1000000000"),
    max_value=Decimal("1000000000"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)
non_negative_dec = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1000000000"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)
money = st.decimals(
    min_value=Decimal("-10000000"),
    max_value=Decimal("10000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
