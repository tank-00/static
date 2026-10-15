"""S01 - ``DecimalStr`` as an API model field (A§3.1, A§4.1, A§8).

FastAPI parses the request body with ``json.loads`` and validates it in *python* mode, so this
checks that JSON numbers (integers included) are rejected on that path as well, with the error type
``decimal_must_be_string``. The S10 error envelope is not wired yet; FastAPI's default 422 body is
used here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from tariff_engine.decimals import DECIMAL_JSON_PATTERN, DecimalStr


class ThresholdIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    threshold: DecimalStr
    abs_tolerance: DecimalStr | None = None


class ThresholdOut(BaseModel):
    threshold: DecimalStr
    abs_tolerance: DecimalStr | None
    doubled: DecimalStr


def make_client() -> TestClient:
    app = FastAPI(separate_input_output_schemas=False)

    @app.post("/echo", response_model=ThresholdOut)
    def echo(body: ThresholdIn) -> ThresholdOut:
        assert isinstance(body.threshold, Decimal)
        return ThresholdOut(
            threshold=body.threshold,
            abs_tolerance=body.abs_tolerance,
            doubled=body.threshold * 2,
        )

    return TestClient(app)


@pytest.mark.req("P-FIXED-LINEAR")
def test_api_accepts_decimal_strings_and_returns_canonical_strings() -> None:
    response = make_client().post("/echo", json={"threshold": "0.010", "abs_tolerance": None})
    assert response.status_code == 200
    # 0.010 * 2 = 0.020 (scale kept); null stays null (missing, not zero)
    assert response.json() == {"threshold": "0.010", "abs_tolerance": None, "doubled": "0.020"}


@pytest.mark.req("P-FIXED-LINEAR")
@pytest.mark.parametrize("raw", ["0.01", "1", "0", "true"])
def test_api_rejects_json_numbers_with_decimal_must_be_string(raw: str) -> None:
    response = make_client().post(
        "/echo", content=f'{{"threshold": {raw}}}', headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "decimal_must_be_string"
    assert error["loc"] == ["body", "threshold"]
    assert "decimal_must_be_string" in error["msg"]


def test_api_schema_advertises_decimal_strings() -> None:
    schema = make_client().app.openapi()  # type: ignore[attr-defined]
    threshold = schema["components"]["schemas"]["ThresholdIn"]["properties"]["threshold"]
    assert threshold["type"] == "string"
    assert threshold["pattern"] == DECIMAL_JSON_PATTERN


@pytest.mark.req("P-FIXED-LINEAR")
def test_api_rejects_decimal_strings_outside_the_engine_range() -> None:
    # 6146 integer digits: one more than the engine range (A§4.1) allows.
    response = make_client().post("/echo", json={"threshold": "1" + "0" * 6145})
    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["type"] == "decimal_out_of_range"
    assert error["loc"] == ["body", "threshold"]
