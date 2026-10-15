"""Shared pytest configuration."""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

# Deterministic, CI-friendly Hypothesis profile (see docs/ARCHITECTURE.md §10).
settings.register_profile(
    "default",
    deadline=None,
    max_examples=60,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("thorough", deadline=None, max_examples=500, derandomize=False)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))
