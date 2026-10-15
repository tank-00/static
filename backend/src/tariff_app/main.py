"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from tariff_app import __version__
from tariff_app.api import meta

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Utility Tariff Interpreter",
        version=__version__,
        description="Invoice validation and bounded tariff comparison (MVP).",
    )
    app.include_router(meta.router, prefix=API_PREFIX)
    return app


app = create_app()
