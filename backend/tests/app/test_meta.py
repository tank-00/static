from fastapi.testclient import TestClient

from tariff_app.main import create_app
from tariff_engine import ENGINE_VERSION


def test_meta_endpoint_reports_versions_and_scope_notice() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["engine_version"] == ENGINE_VERSION
    assert (
        body["check_scope_notice"] == "Invoice-based check; underlying usage quantities unverified"
    )
    assert isinstance(body["ai_available"], bool)
