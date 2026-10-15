"""Bounded Hypothesis strategies for exact-decimal arithmetic laws.

Unbounded decimals break algebraic laws at the engine's 34-digit precision (e.g. 1E-30 + 1E+10),
so property tests must use these bounded strategies.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from tariff_engine.units import BASE_SYMBOLS, MAX_UNIT_EXPONENT, Unit

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

# Units whose exponents stay within +/-3, so a product or quotient of up to three of them stays
# within the +/-9 exponent bound of every Unit (A§4.2) and unit algebra laws never hit the bound.
small_units = st.dictionaries(st.sampled_from(BASE_SYMBOLS), st.integers(-3, 3), max_size=4).map(
    Unit
)
# Any constructible unit: every base symbol, the full +/-9 exponent range.
any_units = st.dictionaries(
    st.sampled_from(BASE_SYMBOLS),
    st.integers(-MAX_UNIT_EXPONENT, MAX_UNIT_EXPONENT),
    max_size=len(BASE_SYMBOLS),
).map(Unit)
