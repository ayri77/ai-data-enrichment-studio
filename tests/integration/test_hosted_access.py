"""A hosted deployment must be closed without the access code and usable with it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_data_enrichment import api
from ai_data_enrichment.api import create_app
from ai_data_enrichment.hosted import create_hosted_app

CODE = "demo-code-1234"
WORKBOOK = Path("samples/capitals.xlsx").read_bytes()


@pytest.fixture
def site(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>Studio</title>", "utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('app')", "utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "ACCESS_FAILURE_DELAY_SECONDS", 0.0)


def upload(client: TestClient, **headers: str):
    return client.post(
        "/api/datasets", params={"filename": "capitals.xlsx"}, content=WORKBOOK, headers=headers
    )


def test_every_api_route_is_closed_without_the_code_and_open_with_it(site: Path) -> None:
    with TestClient(create_app(access_code=CODE, static_dir=site)) as client:
        # Liveness and the access probe are the only open API paths.
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/access").json() == {"required": True, "granted": False}
        for method, path in (
            ("POST", "/api/datasets?filename=a.xlsx"),
            ("POST", "/api/plans"),
            ("GET", "/api/plans/x"),
            ("GET", "/api/jobs/x"),
            ("GET", "/api/jobs/x/artifacts/result"),
            ("POST", "/api/jobs/x/resume"),
            ("GET", "/api/provider-settings"),
            ("PUT", "/api/provider-settings"),
        ):
            response = client.request(method, path)
            assert response.status_code == 401, path
            assert response.json()["error"]["code"] == "access_required"
        assert upload(client, **{"X-Access-Code": "wrong-code-0000"}).status_code == 401
        granted = {"X-Access-Code": CODE}
        assert client.get("/api/access", headers=granted).json() == {
            "required": True,
            "granted": True,
        }
        assert upload(client, **granted).status_code == 201
        readiness = client.get("/api/provider-settings", headers=granted).json()
        # The key and model come from the environment; a visitor cannot change them.
        assert readiness["configuration_enabled"] is False
        assert client.put("/api/provider-settings", headers=granted).status_code == 403


def test_the_frontend_is_served_from_the_same_origin_without_a_code(site: Path) -> None:
    with TestClient(create_app(access_code=CODE, static_dir=site)) as client:
        assert "<title>Studio</title>" in client.get("/").text
        assert client.get("/assets/app.js").text == "console.log('app')"
        assert client.get("/api/unknown", headers={"X-Access-Code": CODE}).status_code == 404


def test_wrong_codes_are_throttled_and_a_right_code_cannot_bypass_the_wait(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "ACCESS_MAX_FAILURES", 3)
    with TestClient(create_app(access_code=CODE, static_dir=site)) as client:
        for _ in range(3):
            assert upload(client, **{"X-Access-Code": "wrong-code-0000"}).status_code == 401
        blocked = upload(client, **{"X-Access-Code": "wrong-code-0000"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "access_throttled"
        assert client.get("/api/access", headers={"X-Access-Code": "nope"}).status_code == 429
        # Requests without any code are refused but not counted as guesses.
        assert client.get("/api/jobs/x").status_code in {401, 429}
        # The correct code keeps working for people who already have it.
        assert upload(client, **{"X-Access-Code": CODE}).status_code == 201


def test_the_local_launcher_stays_open(site: Path) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/api/access").json() == {"required": False, "granted": True}
        assert upload(client).status_code == 201
        assert client.get("/").status_code == 404  # No frontend is mounted locally.


def test_hosted_entry_point_refuses_to_start_open_or_without_a_frontend(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENRICHMENT_STATIC_DIR", str(site))
    for bad in ("", "short", "has space in it", "кириллица-код"):
        monkeypatch.setenv("ENRICHMENT_ACCESS_CODE", bad)
        with pytest.raises(RuntimeError, match="ENRICHMENT_ACCESS_CODE"):
            create_hosted_app()
    monkeypatch.setenv("ENRICHMENT_ACCESS_CODE", CODE)
    monkeypatch.setenv("ENRICHMENT_STATIC_DIR", str(site / "missing"))
    with pytest.raises(RuntimeError, match="frontend"):
        create_hosted_app()
    monkeypatch.setenv("ENRICHMENT_STATIC_DIR", str(site))
    with TestClient(create_hosted_app()) as client:
        assert client.get("/api/access").json()["required"] is True
        assert upload(client).status_code == 401
