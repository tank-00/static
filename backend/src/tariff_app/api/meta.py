"""Metadata endpoint: app/engine versions and capability flags."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tariff_app import __version__
from tariff_app.config import Settings, get_settings
from tariff_engine import ENGINE_VERSION

router = APIRouter(tags=["meta"])


class MetaOut(BaseModel):
    app_version: str
    engine_version: str
    ai_available: bool
    ai_model: str
    check_scope_notice: str


CHECK_SCOPE_NOTICE = "Invoice-based check; underlying usage quantities unverified"


@router.get("/meta", response_model=MetaOut)
def get_meta(settings: Annotated[Settings, Depends(get_settings)]) -> MetaOut:
    return MetaOut(
        app_version=__version__,
        engine_version=ENGINE_VERSION,
        ai_available=settings.ai_available,
        ai_model=settings.ai_model,
        check_scope_notice=CHECK_SCOPE_NOTICE,
    )
