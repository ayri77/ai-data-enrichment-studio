"""A hosted deployment must be closed without the access code and usable with it."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_data_enrichment import api
from ai_data_enrichment.api import create_app
from ai_data_enrichment.evaluation.plan_run_fixtures import offline_executor
from ai_data_enrichment.hosted import create_hosted_app, visitor_codes
from ai_data_enrichment.plan_run import PlanRunSettings

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


def test_examples_are_listed_downloadable_and_pass_the_ordinary_upload_checks(
    site: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    granted = {"X-Access-Code": CODE}
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    with TestClient(create_app(access_code=CODE, static_dir=site)) as client:
        # Examples are part of the protected API like everything else.
        assert client.get("/api/samples").status_code == 401
        listed = client.get("/api/samples", headers=granted).json()["samples"]
        assert [s["sample_id"] for s in listed] == ["capitals", "mountains"]
        capitals = listed[0]
        assert capitals["filename"] == "capitals.xlsx" and capitals["has_plan"] is True
        assert capitals["outputs"] == [{"name": "distance", "column": "E"}]
        assert "distance" in capitals["instruction"]
        workbook = client.get("/api/samples/capitals/workbook", headers=granted)
        assert workbook.content == WORKBOOK
        uploaded = client.post(
            "/api/datasets",
            params={"filename": capitals["filename"]},
            content=workbook.content,
            headers=granted,
        ).json()
        # The prepared plan is valid for exactly this workbook and output column.
        plan = client.get("/api/samples/capitals/plan", headers=granted).json()
        draft = client.post(
            "/api/plans",
            headers=granted,
            json={
                "dataset_id": uploaded["dataset_id"],
                "sheet_name": "Sheet1",
                "range_ref": "A1:E11",
                "instruction": capitals["instruction"],
                "output_columns": {o["name"]: o["column"] for o in capitals["outputs"]},
                "plan": plan,
            },
        )
        assert draft.status_code == 201 and draft.json()["validation"]["valid"] is True
        # The three-column example needs a road distance: it is listed only where
        # that is offered, and its prepared plan is valid for its own workbook.
        monkeypatch.setenv("OPENROUTESERVICE_API_KEY", "key-for-the-offline-fixture")
        listed = client.get("/api/samples", headers=granted).json()["samples"]
        assert [s["sample_id"] for s in listed] == ["capitals", "mountains", "capitals_roads"]
        roads = listed[2]
        assert roads["has_plan"] is True
        assert roads["outputs"] == [
            {"name": "distance", "column": "E"},
            {"name": "road", "column": "F"},
            {"name": "detour", "column": "G"},
        ]
        uploaded = client.post(
            "/api/datasets",
            params={"filename": roads["filename"]},
            content=client.get("/api/samples/capitals_roads/workbook", headers=granted).content,
            headers=granted,
        ).json()
        assert uploaded["worksheets"][0]["table_range"] == "A1:G11"
        draft = client.post(
            "/api/plans",
            headers=granted,
            json={
                "dataset_id": uploaded["dataset_id"],
                "sheet_name": "Sheet1",
                "range_ref": "A1:G11",
                "instruction": roads["instruction"],
                "output_columns": {o["name"]: o["column"] for o in roads["outputs"]},
                "plan": client.get("/api/samples/capitals_roads/plan", headers=granted).json(),
            },
        )
        assert draft.status_code == 201 and draft.json()["validation"]["valid"] is True
        steps = [step["capability"] for step in draft.json()["plan"]["steps"]]
        assert steps == ["geocode", "geocode", "geodesic_distance", "road_distance", "ratio"]
        for path in ("/api/samples/unknown/workbook", "/api/samples/unknown/plan"):
            response = client.get(path, headers=granted)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "sample_not_found"
        # An installation without the bundled files simply offers no examples.
        monkeypatch.setenv("ENRICHMENT_DATA_DIR", str(tmp_path / "elsewhere"))
        assert client.get("/api/samples", headers=granted).json() == {"samples": []}
        assert client.get("/api/samples/capitals/workbook", headers=granted).status_code == 404


ADMIN = "admin-code-for-tests"
VISITORS = {"guest01": "first-visitor-code", "guest02": "second-visitor-code"}


def code_of(label: str) -> dict[str, str]:
    return {"X-Access-Code": VISITORS[label]}


def usage(client: TestClient, code: str = ADMIN):
    return client.get("/api/usage", headers={"X-Admin-Code": code} if code else {})


def test_each_visitor_has_a_code_of_their_own_and_what_they_use_is_counted(site: Path) -> None:
    app = create_app(
        settings=PlanRunSettings(),
        offline_executor_factory=offline_executor,
        access_codes=VISITORS,
        admin_code=ADMIN,
        static_dir=site,
    )
    with TestClient(app) as client:
        assert client.get("/api/access").json() == {"required": True, "granted": False}
        assert upload(client, **{"X-Access-Code": "nobody-has-this-code"}).status_code == 401
        for label in VISITORS:
            assert client.get("/api/access", headers=code_of(label)).json()["granted"] is True

        # The first visitor runs the Capitals example from its prepared plan.
        first = code_of("guest01")
        workbook = client.get("/api/samples/capitals/workbook", headers=first).content
        dataset = client.post(
            "/api/datasets",
            params={"filename": "A very private file name.xlsx"},
            content=workbook,
            headers=first,
        ).json()
        plan = client.post(
            "/api/plans",
            headers=first,
            json={
                "dataset_id": dataset["dataset_id"],
                "sheet_name": "Sheet1",
                "range_ref": "A1:E11",
                "instruction": "A private instruction nobody else should read",
                "output_columns": {"distance": "E"},
                "plan": client.get("/api/samples/capitals/plan", headers=first).json(),
            },
        ).json()
        approved = client.post(
            f"/api/plans/{plan['plan_id']}/approve",
            headers=first,
            json={"approved": True, "fingerprint": plan["fingerprint"]},
        )
        assert approved.status_code == 200, approved.text
        job = client.post(
            f"/api/plans/{plan['plan_id']}/runs", headers=first, json={"timeout_seconds": 60}
        ).json()
        for _ in range(600):
            job = client.get(f"/api/jobs/{job['job_id']}", headers=first).json()
            if job["status"] in {"completed", "needs_review", "failed"}:
                break
        assert job["status"] == "needs_review"
        assert client.get(job["artifacts"]["result"], headers=first).status_code == 200
        # The second visitor only uploads.
        assert upload(client, **code_of("guest02")).status_code == 201

        summary = usage(client)
        assert summary.status_code == 200
        assert summary.headers["cache-control"] == "no-store"
        body = summary.json()
        assert body["labels"] == ["guest01", "guest02"]
        by_label = {visitor["who"]: visitor for visitor in body["visitors"]}
        guest = by_label["guest01"]
        assert (guest["visits"], guest["examples"], guest["uploads"]) == (1, 1, 1)
        assert (guest["plans"], guest["runs"], guest["downloads"]) == (1, 1, 1)
        assert guest["rows"] == 10 and 0 < guest["rows_filled"] < 10
        # Requests that left for the source during the run, counted for the run's visitor
        # although the run ended outside any request of theirs.
        assert guest["source_requests"]["open-meteo"] > 0
        assert guest["model_requests"] == 0
        other = by_label["guest02"]
        assert (other["visits"], other["uploads"], other["runs"]) == (1, 1, 0)
        kinds = [event["kind"] for event in body["recent"] if event["who"] == "guest01"]
        assert kinds[::-1] == [
            "visit",
            "example",
            "upload",
            "plan",
            "run_started",
            "run_finished",
            "download",
        ]
        planned = next(event for event in body["recent"] if event["kind"] == "plan")
        assert planned["source"] == "supplied" and planned["valid"] is True
        assert planned["capabilities"] == ["geocode", "geodesic_distance"]
        # Numbers and fixed words only: no file name, no instruction, no cell
        # value, no code.
        text = summary.text
        for secret in (
            "private",
            "Kyiv",
            "London",
            "Capital_From",
            *VISITORS.values(),
            ADMIN,
            dataset["dataset_id"],
            job["job_id"],
        ):
            assert secret not in text, secret


def test_the_usage_summary_opens_with_the_admin_code_and_with_nothing_else(site: Path) -> None:
    with TestClient(create_app(access_codes=VISITORS, admin_code=ADMIN, static_dir=site)) as client:
        assert usage(client, "").status_code == 401
        assert usage(client, "wrong-admin-code").json()["error"]["code"] == "admin_required"
        # A visitor's code is not the admin code, in either header.
        visitor = VISITORS["guest01"]
        assert usage(client, visitor).status_code == 401
        assert client.get("/api/usage", headers={"X-Access-Code": visitor}).status_code == 401
        # And the admin code opens nothing but the summary.
        assert upload(client, **{"X-Access-Code": ADMIN}).status_code == 401
        assert client.get("/api/access", headers={"X-Access-Code": ADMIN}).json() == {
            "required": True,
            "granted": False,
        }
        assert usage(client).status_code == 200
        # Wrong admin codes are limited together with wrong visitor codes.
        for _ in range(api.ACCESS_MAX_FAILURES):
            usage(client, "guessing-the-admin-code")
        assert usage(client, "guessing-the-admin-code").status_code == 429
        assert usage(client).status_code == 200


def test_without_an_admin_code_there_is_no_usage_summary(site: Path) -> None:
    with TestClient(create_app(access_codes=VISITORS, static_dir=site)) as client:
        assert usage(client).status_code == 404
        assert client.get("/api/usage", headers=code_of("guest01")).status_code == 404
    # One code given the old way is a visitor called "shared".
    with TestClient(create_app(access_code=CODE, admin_code=ADMIN, static_dir=site)) as client:
        assert upload(client, **{"X-Access-Code": CODE}).status_code == 201
        assert [v["who"] for v in usage(client).json()["visitors"]] == ["shared"]
    # Locally nothing is required and everything counts as "local".
    with TestClient(create_app(admin_code=ADMIN)) as client:
        assert client.get("/api/access").json() == {"required": False, "granted": True}
        assert [v["who"] for v in usage(client).json()["visitors"]] == ["local"]


def test_visitor_codes_are_read_from_one_line_and_checked() -> None:
    text = "guest01:first-visitor-code, guest02:second-visitor-code;\n team_b:a-third:with-colon"
    assert visitor_codes(text) == {
        **VISITORS,
        "team_b": "a-third:with-colon",
    }
    assert visitor_codes("") == {} and visitor_codes(" ,\n; ") == {}
    for bad, problem in (
        ("guest01", "is not label:code"),
        ("gue st:first-visitor-code", "is not label:code"),
        ("-guest:first-visitor-code", "label other than"),
        ("x" * 41 + ":first-visitor-code", "label other than"),
        ("shared:first-visitor-code", "reserved label"),
        ("a:first-visitor-code,a:second-visitor-code", "repeats a label"),
        ("a:short", "shorter than 8"),
        ("a:кодовеслово", "shorter than 8"),
        ("a:first-visitor-code,b:first-visitor-code", "repeats a code"),
    ):
        with pytest.raises(RuntimeError) as refused:
            visitor_codes(bad)
        assert problem in str(refused.value), bad
        # The message names the position, never the entry: an entry holds a code.
        assert "visitor-code" not in str(refused.value)


def test_the_hosted_app_starts_only_with_codes_that_cannot_be_confused(
    site: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENRICHMENT_STATIC_DIR", str(site))
    for name in ("ENRICHMENT_ACCESS_CODE", "ENRICHMENT_ACCESS_CODES", "ENRICHMENT_ADMIN_CODE"):
        monkeypatch.delenv(name, raising=False)

    def refused(**env: str) -> str:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(RuntimeError) as error:
            create_hosted_app()
        for name in env:
            monkeypatch.delenv(name)
        return str(error.value)

    entries = ",".join(f"{label}:{code}" for label, code in VISITORS.items())
    assert "ENRICHMENT_ACCESS_CODES" in refused()
    assert "at least 12" in refused(ENRICHMENT_ACCESS_CODES=entries, ENRICHMENT_ADMIN_CODE="short")
    assert "must differ" in refused(
        ENRICHMENT_ACCESS_CODES=entries, ENRICHMENT_ADMIN_CODE=VISITORS["guest01"] + "x"[:0]
    )
    assert "repeats a code" in refused(
        ENRICHMENT_ACCESS_CODES=entries, ENRICHMENT_ACCESS_CODE=VISITORS["guest02"]
    )

    monkeypatch.setenv("ENRICHMENT_ACCESS_CODES", entries)
    monkeypatch.setenv("ENRICHMENT_ACCESS_CODE", CODE)
    monkeypatch.setenv("ENRICHMENT_ADMIN_CODE", ADMIN)
    with TestClient(create_hosted_app()) as client:
        for code in (CODE, *VISITORS.values()):
            assert upload(client, **{"X-Access-Code": code}).status_code == 201
        assert sorted(v["who"] for v in usage(client).json()["visitors"]) == [
            "guest01",
            "guest02",
            "shared",
        ]
