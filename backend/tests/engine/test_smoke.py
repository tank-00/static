import tariff_engine


def test_engine_version_is_semver() -> None:
    parts = tariff_engine.ENGINE_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
