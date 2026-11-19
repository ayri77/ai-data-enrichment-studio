"""New rows from a source over HTTP: the sources, the candidates, the preview and the run."""

import ast
import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from tests.integration.test_mail_dataset import FIXTURES, fixture_zip, local_app, send

from ai_data_enrichment import api as api_module
from ai_data_enrichment import rows_api
from ai_data_enrichment.api import MAX_DATASETS, create_app
from ai_data_enrichment.mail import upload as mail_upload
from ai_data_enrichment.mail.workbook import RECEIVED_FORMAT
from ai_data_enrichment.plan_run import PlanRunSettings
from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.mail_files import mail_candidates

NOW = datetime(2026, 11, 19, 12, 0)
MAPPING = {"received": "A", "sender": "B", "subject": "C"}
KEY = ["received", "sender"]
ROOT = Path(__file__).parents[2]


def fixture_candidates() -> list:
    """The candidates of the fixture mails, as the route makes them."""
    messages, _ = mail_upload.read_mail_upload(fixture_zip(), archive=True)
    return mail_candidates(messages)


def register_bytes(
    picks: tuple[int, ...] = (0, 2, 5), *, text_dates: bool = False, stray: str | None = None
) -> bytes:
    """A hand-made register: Received, From, Subject, Amount; its rows are fixture mails."""
    candidates = fixture_candidates()
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Register"
    sheet.append(["Received", "From", "Subject", "Amount"])
    for number, pick in enumerate(picks, start=1):
        values = candidates[pick].values
        received = values["received"]
        assert isinstance(received, datetime)
        sheet.append(
            [
                received.strftime("%Y-%m-%d %H:%M") if text_dates else received,
                values["sender"],
                values["subject"],
                number * 10,
            ]
        )
        if not text_dates:
            sheet.cell(row=number + 1, column=1).number_format = RECEIVED_FORMAT
    if stray is not None:
        sheet[stray] = "stray"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def upload(client: TestClient, content: bytes, **headers: str):
    answer = client.post(
        "/api/datasets", params={"filename": "register.xlsx"}, content=content, headers=headers
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["dataset_id"]


def candidates_of(client: TestClient, name: str = "mails.zip", **headers: str) -> str:
    answer = client.post(
        "/api/row-sources/mail_files/candidates",
        params={"filename": name},
        content=fixture_zip(),
        headers=headers,
    )
    assert answer.status_code == 201, answer.text
    return answer.json()["candidates_id"]


def body(candidates_id: str, **more) -> dict:
    return {"candidates_id": candidates_id, "sheet_name": "Register", "range_ref": "A1:D4", **more}


def run_body(candidates_id: str, **more) -> dict:
    return body(candidates_id, **{"mapping": MAPPING, "key": KEY, **more})


def preview_of(client: TestClient, dataset_id: str, sheet: str = "Register", ref: str = "A1:D4"):
    answer = client.post(
        f"/api/datasets/{dataset_id}/preview", json={"sheet_name": sheet, "range_ref": ref}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def stored_files(app) -> list[str]:
    return sorted(p.name for p in app.state.directory.glob("*.xlsx"))


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rows_api, "_now", lambda: NOW)


def test_the_sources_are_listed_and_available_only_where_mail_is_allowed() -> None:
    with TestClient(local_app()) as client:
        listed = client.get("/api/row-sources").json()["sources"]
    assert [s["source_id"] for s in listed] == ["mail_files"]
    source = listed[0]
    assert source["connect"] == "files" and source["listing"] == "given"
    assert source["file_suffixes"] == [".eml", ".zip"]
    assert source["default_key"] == KEY
    assert source["fields"] == [
        {"field_id": "received", "kind": "moment", "label": "Received"},
        {"field_id": "sender", "kind": "text", "label": "Sender"},
        {"field_id": "subject", "kind": "text", "label": "Subject"},
    ]
    assert source["available"] is True
    off = [
        create_app(local_browser_configuration=True, settings=PlanRunSettings()),
        # A hosted app never has the switch, whatever the settings say.
        create_app(settings=PlanRunSettings(allow_mail=True)),
    ]
    for app in off:
        with TestClient(app) as client:
            assert client.get("/api/row-sources").json()["sources"][0]["available"] is False


def test_candidates_are_read_from_a_zip_and_refused_where_they_must_be() -> None:
    with TestClient(local_app()) as client:
        answer = client.post(
            "/api/row-sources/mail_files/candidates",
            params={"filename": "mails.zip"},
            content=fixture_zip(extra={"notes.txt": b"x"}),
        )
        assert answer.status_code == 201, answer.text
        assert set(answer.json()) == {"candidates_id", "source_id", "count", "skipped"}
        assert answer.json()["count"] == 10 and answer.json()["skipped"] == 1
        one = (FIXTURES / "10_payrelay_card_phone_me.eml").read_bytes()
        single = client.post(
            "/api/row-sources/mail_files/candidates", params={"filename": "a.EML"}, content=one
        )
        assert single.status_code == 201 and single.json()["count"] == 1
        unknown = client.post(
            "/api/row-sources/nothing/candidates", params={"filename": "a.zip"}, content=b"x"
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "rows_unknown_source"
        wrong = client.post(
            "/api/row-sources/mail_files/candidates", params={"filename": "a.txt"}, content=b"x"
        )
        assert wrong.status_code == 422
        assert wrong.json()["error"]["code"] == "invalid_mail_file_type"
    closed = create_app(local_browser_configuration=True, settings=PlanRunSettings())
    with TestClient(closed) as client:
        refused = client.post(
            "/api/row-sources/mail_files/candidates",
            params={"filename": "mails.zip"},
            content=fixture_zip(),
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "mail_not_allowed"


def test_only_four_sets_are_kept_and_a_set_can_be_forgotten() -> None:
    with TestClient(local_app()) as client:
        dataset = upload(client, register_bytes())
        ids = [candidates_of(client) for _ in range(5)]
        gone = client.post(f"/api/datasets/{dataset}/rows/preview", json=body(ids[0]))
        assert gone.status_code == 404
        assert gone.json()["error"]["code"] == "rows_candidates_not_found"
        for kept in ids[1:]:
            assert client.post(f"/api/datasets/{dataset}/rows/preview", json=body(kept)).is_success
        assert client.delete(f"/api/row-sources/candidates/{ids[1]}").status_code == 204
        assert client.delete(f"/api/row-sources/candidates/{ids[1]}").status_code == 204
        assert client.delete("/api/row-sources/candidates/never-was").status_code == 204
        forgotten = client.post(f"/api/datasets/{dataset}/rows", json=run_body(ids[1]))
        assert forgotten.status_code == 404
        assert forgotten.json()["error"]["code"] == "rows_candidates_not_found"


def test_the_whole_path_on_a_hand_made_register() -> None:
    app = local_app()
    with TestClient(app) as client:
        source = upload(client, register_bytes())
        before = preview_of(client, source)
        candidates_id = candidates_of(client)
        previewed = client.post(f"/api/datasets/{source}/rows/preview", json=body(candidates_id))
        assert previewed.status_code == 200, previewed.text
        shown = previewed.json()
        assert shown["plan"] == {
            "source_id": "mail_files",
            "sheet_name": "Register",
            "range_ref": "A1:D4",
            "mapping": MAPPING,
            "key": KEY,
            "on_duplicate": "skip",
            "conditions": [],
        }
        assert shown["proposed"] == {"mapping": True, "key": True}
        assert shown["refused"] is None
        count = shown["count"]
        assert count["holds"] is True
        assert (count["in_source"], count["duplicates_in_table"], count["added"]) == (10, 3, 7)
        assert count["written"] == 7 and count["table_rows_without_key"] == 0
        # Nothing was written by the preview.
        assert len(stored_files(app)) == 1

        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 201, run.text
        done = run.json()
        new = done["dataset_id"]
        assert new != source and done["worksheets"][0]["name"] == "Register"
        assert done["rows"] == {
            "count": count,
            "first_row": 5,
            "last_row": 11,
            "sheet_name": "Register",
            "range_ref": "A1:D11",
        }
        assert len(stored_files(app)) == 2

        grown = preview_of(client, new, ref=done["rows"]["range_ref"])
        old_rows, new_rows = grown["rows"]["values"][:3], grown["rows"]["values"][3:]
        assert old_rows == before["rows"]["values"]
        assert len(new_rows) == 7 and grown["rows"]["row_numbers"][-1] == 11
        # The register's own column is left empty in the new rows.
        assert all(row[3] is None for row in new_rows)
        assert all(row[0] is not None and row[1] and row[2] for row in new_rows)
        assert [row[3] for row in old_rows] == [10, 20, 30]
        # The source is as it was.
        assert preview_of(client, source) == before

        # A second run on the result adds nothing.
        again = client.post(
            f"/api/datasets/{new}/rows/preview",
            json=body(candidates_id, range_ref="A1:D11"),
        ).json()
        assert again["count"]["added"] == 0 and again["count"]["duplicates_in_table"] == 10
        assert again["count"]["holds"] is True
        assert again["proposed"] == {"mapping": True, "key": True}


def test_a_given_mapping_and_key_are_used_as_they_are_and_not_proposed() -> None:
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        shown = client.post(
            f"/api/datasets/{source}/rows/preview",
            json=body(
                candidates_id,
                mapping={"received": "A", "sender": "B"},
                key=["received"],
                on_duplicate="add_marked",
            ),
        ).json()
        assert shown["proposed"] == {"mapping": False, "key": False}
        assert shown["plan"]["mapping"] == {"received": "A", "sender": "B"}
        assert shown["plan"]["key"] == ["received"]
        count = shown["count"]
        assert count["duplicates_in_table"] == 3 and count["written"] == 10
        assert count["holds"] is True


def test_a_register_with_text_dates_is_refused_by_the_preview_and_the_run() -> None:
    app = local_app()
    with TestClient(app) as client:
        source = upload(client, register_bytes(text_dates=True))
        candidates_id = candidates_of(client)
        shown = client.post(f"/api/datasets/{source}/rows/preview", json=body(candidates_id))
        assert shown.status_code == 200, shown.text
        assert shown.json()["count"] is None
        assert shown.json()["refused"] == {"code": "rows_key_not_comparable", "count": 3, "row": 2}
        assert shown.json()["plan"]["key"] == KEY
        before = stored_files(app)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 422
        assert run.json()["error"]["code"] == "rows_key_not_comparable"
        assert stored_files(app) == before
        assert preview_of(client, source)["profile"]["row_count"] == 3
        assert client.post(
            f"/api/datasets/{source}/rows/preview", json=body(candidates_id)
        ).is_success


def test_a_plan_the_core_refuses_is_an_answer_of_the_preview_and_an_error_of_the_run() -> None:
    both = {"received": "A", "sender": "B"}
    cases = [
        ({"mapping": both, "key": []}, "rows_key_empty"),
        ({"mapping": {"subject": "C"}}, "rows_key_empty"),
        ({"mapping": {"received": "A", "sender": "A"}, "key": KEY}, "rows_column_used_twice"),
        ({"mapping": {"received": "A", "sender": "H"}, "key": KEY}, "rows_column_outside_table"),
        ({"mapping": both, "key": [*KEY, "x"]}, "rows_unknown_field"),
        ({"mapping": {**both, "x": "C"}, "key": KEY}, "rows_unknown_field"),
        ({"mapping": {"received": "A"}, "key": KEY}, "rows_key_not_mapped"),
        ({"mapping": both, "key": [*KEY, "subject"]}, "rows_key_not_mapped"),
    ]
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        for extra, code in cases:
            answer = client.post(
                f"/api/datasets/{source}/rows/preview", json=body(candidates_id, **extra)
            )
            assert answer.status_code == 200, answer.text
            assert answer.json()["refused"] == {"code": code, "count": None, "row": None}
            assert answer.json()["count"] is None
            if "key" in extra:
                run = client.post(f"/api/datasets/{source}/rows", json=body(candidates_id, **extra))
                assert run.status_code == 422, run.text
                assert run.json()["error"]["code"] == code
        # A refused plan counts for nothing.
        kinds = [e["kind"] for e in client.get("/api/usage").json()["recent"]]
        assert kinds == ["upload"]


def test_a_missing_sheet_a_bad_range_and_a_range_past_the_limits_are_http_errors() -> None:
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        for route in ("rows/preview", "rows"):
            extra = {} if route == "rows/preview" else {"mapping": MAPPING, "key": KEY}
            for change, code in (
                # The preview reads the header row, so a bad sheet is a bad selection; the run
                # leaves it to the core.
                (
                    {"sheet_name": "Nowhere"},
                    "invalid_selection" if route == "rows/preview" else "rows_sheet_missing",
                ),
                ({"range_ref": "not a range"}, "invalid_selection"),
                ({"range_ref": "A1:D6000"}, "worksheet_limit"),
            ):
                answer = client.post(
                    f"/api/datasets/{source}/{route}", json=body(candidates_id, **extra, **change)
                )
                assert answer.status_code == 422, (route, change, answer.text)
                assert answer.json()["error"]["code"] == code, (route, change)
        for route in ("rows/preview", "rows"):
            extra = {} if route == "rows/preview" else {"mapping": MAPPING, "key": KEY}
            missing = client.post(f"/api/datasets/nope/{route}", json=body(candidates_id, **extra))
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "dataset_not_found"


def test_the_body_is_checked_and_a_run_needs_its_mapping_and_key() -> None:
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        bad = [
            body(candidates_id, extra=1),
            body(candidates_id, on_duplicate="replace"),
            body(candidates_id, conditions=[{"field_id": "received", "op": "x", "value": True}]),
            body(candidates_id, conditions=[{"field_id": "received", "op": "x", "value": 1.5}]),
        ]
        for item in bad:
            answer = client.post(f"/api/datasets/{source}/rows/preview", json=item)
            assert answer.status_code == 422, item
            assert answer.json()["error"]["code"] == "invalid_request"
        for item in (body(candidates_id), body(candidates_id, mapping=MAPPING)):
            answer = client.post(f"/api/datasets/{source}/rows", json=item)
            assert answer.status_code == 422
            assert answer.json()["error"]["code"] == "invalid_request"
        assert (
            client.post(f"/api/datasets/{source}/rows", json=run_body("a", key=None)).status_code
            == 422
        )


def test_no_room_below_the_table_is_refused_by_the_preview_and_the_run() -> None:
    app = local_app()
    with TestClient(app) as client:
        # Seven new rows would reach row 11; row 6 holds a value.
        source = upload(client, register_bytes(stray="B6"))
        candidates_id = candidates_of(client)
        shown = client.post(f"/api/datasets/{source}/rows/preview", json=body(candidates_id))
        assert shown.json()["refused"] == {"code": "rows_no_room", "count": None, "row": None}
        before = stored_files(app)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 422 and run.json()["error"]["code"] == "rows_no_room"
        assert stored_files(app) == before
        # A condition that leaves one row to add fits above the stray value.
        few = body(
            candidates_id,
            conditions=[{"field_id": "received", "op": "within_last_days", "value": 1}],
        )
        assert (
            client.post(
                f"/api/datasets/{source}/rows", json={**few, "mapping": MAPPING, "key": KEY}
            ).status_code
            == 201
        )


@pytest.mark.parametrize("limit,value", [("MAX_ROWS", 8), ("MAX_CELLS", 32)])
def test_a_table_may_not_grow_past_the_limits(
    monkeypatch: pytest.MonkeyPatch, limit: str, value: int
) -> None:
    monkeypatch.setattr(api_module, limit, value)
    app = local_app()
    with TestClient(app) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        shown = client.post(f"/api/datasets/{source}/rows/preview", json=body(candidates_id))
        assert shown.json()["refused"] == {"code": "rows_too_many", "count": None, "row": None}
        before = stored_files(app)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 422 and run.json()["error"]["code"] == "rows_too_many"
        assert stored_files(app) == before
        # Up to the limit it is allowed: five of the seven new rows end in row 8 and no further.
        five = [{"field_id": "received", "op": "on_or_after", "value": "2026-11-12"}]
        ok = client.post(
            f"/api/datasets/{source}/rows", json=run_body(candidates_id, conditions=five)
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["rows"]["last_row"] == 8


def test_conditions_are_given_in_json() -> None:
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)

        def counted(*conditions: dict) -> dict:
            answer = client.post(
                f"/api/datasets/{source}/rows/preview",
                json=body(candidates_id, conditions=list(conditions)),
            )
            assert answer.status_code == 200, answer.text
            return answer.json()

        recent = counted({"field_id": "received", "op": "within_last_days", "value": 4})
        # The clock is 2026-11-19 12:00: only the mails of 16 and 18 November are that recent.
        assert recent["count"]["added"] == 2 and recent["count"]["left_out"] == 8
        assert recent["plan"]["conditions"] == [
            {"field_id": "received", "op": "within_last_days", "value": 4}
        ]
        after = counted({"field_id": "received", "op": "on_or_after", "value": "2026-11-12"})
        assert after["count"]["added"] == 4 and after["count"]["left_out"] == 6
        before = counted({"field_id": "received", "op": "on_or_before", "value": "2026-11-03"})
        assert before["count"]["duplicates_in_table"] == 2 and before["count"]["added"] == 1
        word = counted({"field_id": "subject", "op": "contains", "value": "STREAMLY"})
        assert word["count"]["in_source"] == 10 and word["count"]["holds"] is True
        assert word["count"]["added"] + word["count"]["duplicates_in_table"] == 4
        both = counted(
            {"field_id": "received", "op": "on_or_after", "value": "2026-11-12"},
            {"field_id": "subject", "op": "contains", "value": "streamly"},
        )
        assert both["count"]["added"] == 3
        for bad in (
            {"field_id": "received", "op": "on_or_after", "value": "12.11.2026"},
            {"field_id": "received", "op": "on_or_after", "value": "2026-13-45"},
            {"field_id": "received", "op": "on_or_after", "value": "20261112"},
            {"field_id": "received", "op": "on_or_after", "value": 5},
            {"field_id": "received", "op": "within_last_days", "value": "5"},
            {"field_id": "received", "op": "within_last_days", "value": 0},
            {"field_id": "received", "op": "contains", "value": "x"},
            {"field_id": "subject", "op": "within_last_days", "value": 3},
        ):
            shown = counted(bad)
            assert shown["count"] is None
            assert shown["refused"] == {"code": "rows_bad_condition", "count": None, "row": None}
        unknown = counted({"field_id": "nothing", "op": "contains", "value": "x"})
        assert unknown["refused"]["code"] == "rows_unknown_field"
        run = client.post(
            f"/api/datasets/{source}/rows",
            json=run_body(
                candidates_id,
                conditions=[{"field_id": "received", "op": "on_or_after", "value": "12.11.2026"}],
            ),
        )
        assert run.status_code == 422 and run.json()["error"]["code"] == "rows_bad_condition"


def test_the_dataset_being_appended_to_is_never_dropped_to_make_room() -> None:
    app = local_app()
    with TestClient(app) as client:
        # The register is the oldest dataset: the one that would go first.
        source = upload(client, register_bytes())
        others = [upload(client, register_bytes((1,))) for _ in range(MAX_DATASETS - 1)]
        assert len(stored_files(app)) == MAX_DATASETS
        candidates_id = candidates_of(client)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 201, run.text
        assert len(stored_files(app)) == MAX_DATASETS
        # The register is still there, and the next oldest one went instead.
        assert preview_of(client, source)["profile"]["row_count"] == 3
        gone = client.post(
            f"/api/datasets/{others[0]}/preview",
            json={"sheet_name": "Register", "range_ref": "A1:D2"},
        )
        assert gone.status_code == 404
        assert preview_of(client, run.json()["dataset_id"], ref="A1:D11")


def test_a_refused_run_does_not_cost_another_table_its_place() -> None:
    app = local_app()
    with TestClient(app) as client:
        oldest = upload(client, register_bytes((1,)))
        for _ in range(MAX_DATASETS - 2):
            upload(client, register_bytes((1,)))
        source = upload(client, register_bytes(text_dates=True))
        before = stored_files(app)
        assert len(before) == MAX_DATASETS
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_of(client)))
        assert run.status_code == 422
        assert run.json()["error"]["code"] == "rows_key_not_comparable"
        # The workspace is full, and still nothing was dropped for a run that could not be.
        assert stored_files(app) == before
        still = client.post(
            f"/api/datasets/{oldest}/preview",
            json={"sheet_name": "Register", "range_ref": "A1:D2"},
        )
        assert still.status_code == 200


def test_when_nothing_else_can_be_dropped_the_workspace_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_module, "MAX_DATASETS", 1)
    app = local_app()
    with TestClient(app) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 409
        assert run.json()["error"]["code"] == "workspace_full"
        assert stored_files(app) == [f"{source}.xlsx"]
        assert preview_of(client, source)["profile"]["row_count"] == 3


def test_a_table_of_mails_stays_a_table_of_mails_and_another_does_not_become_one() -> None:
    app = local_app()
    with TestClient(app) as client:
        mail = send(client, fixture_zip()).json()["dataset_id"]
        plain = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        mail_run = client.post(
            f"/api/datasets/{mail}/rows",
            json=body(candidates_id, range_ref="A1:D11", mapping=MAPPING, key=KEY)
            | {"sheet_name": "Mail"},
        )
        assert mail_run.status_code == 201, mail_run.text
        assert mail_run.json()["rows"]["count"]["added"] == 0
        plain_run = client.post(f"/api/datasets/{plain}/rows", json=run_body(candidates_id))
        assert plain_run.status_code == 201
        assert app.state.plan_run.mail_datasets == {mail, mail_run.json()["dataset_id"]}


def test_the_routes_are_closed_without_the_access_code() -> None:
    code = "demo-code-1234"
    with TestClient(local_app(access_code=code)) as client:
        for method, path in (
            ("GET", "/api/row-sources"),
            ("POST", "/api/row-sources/mail_files/candidates?filename=a.zip"),
            ("DELETE", "/api/row-sources/candidates/x"),
            ("POST", "/api/datasets/x/rows/preview"),
            ("POST", "/api/datasets/x/rows"),
        ):
            answer = client.request(method, path)
            assert answer.status_code == 401, path
            assert answer.json()["error"]["code"] == "access_required"
        granted = {"X-Access-Code": code}
        source = upload(client, register_bytes(), **granted)
        candidates_id = candidates_of(client, **granted)
        assert client.get("/api/row-sources", headers=granted).status_code == 200
        shown = client.post(
            f"/api/datasets/{source}/rows/preview", json=body(candidates_id), headers=granted
        )
        assert shown.status_code == 200


def test_every_code_of_the_core_has_a_public_error() -> None:
    codes: set[str] = set()
    for path in (ROOT / "src" / "ai_data_enrichment" / "rows").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "RowsError":
                assert isinstance(node.args[0], ast.Constant)
                codes.add(str(node.args[0].value))
    assert len(codes) == 11
    for code in codes:
        assert rows_api.workspace_error_for(RowsError(code)).code == code
    with pytest.raises(RowsError):
        rows_api.workspace_error_for(RowsError("something_new"))


def test_a_finished_append_is_counted_with_numbers_only_and_leaves_no_trace_of_the_mails(
    tmp_path: Path, caplog
) -> None:
    kept = tmp_path / "usage.jsonl"
    app = local_app(settings=PlanRunSettings(allow_mail=True, usage_file=str(kept)))
    secrets = set()
    for path in FIXTURES.glob("*.eml"):
        secrets |= set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", path.read_text("utf-8")))
    for item in fixture_candidates():
        secrets |= {str(item.values["sender"]), str(item.values["subject"])}
    secrets |= {"Streamly", "PayRelay", "Cloudbox", "HostHill", "MARKER-ROWS-FILE"}
    assert len(secrets) > 20
    caplog.set_level(logging.DEBUG)
    answers: list[str] = []
    with TestClient(app) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client, "MARKER-ROWS-FILE.zip")
        for request in (
            client.post(f"/api/datasets/{source}/rows/preview", json=body(candidates_id)),
            client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id)),
            client.post(f"/api/datasets/{source}/rows", json=run_body("MARKER-ROWS-FILE")),
            client.post(
                f"/api/datasets/{source}/rows",
                json=run_body(candidates_id, key=["received", "MARKER-ROWS-FILE"]),
            ),
            client.post(
                f"/api/datasets/{source}/rows",
                json=run_body(candidates_id, sheet_name="MARKER-ROWS-FILE"),
            ),
            client.post(
                "/api/row-sources/MARKER-ROWS-FILE/candidates",
                params={"filename": "MARKER-ROWS-FILE.zip"},
                content=b"x",
            ),
            client.post(
                "/api/row-sources/mail_files/candidates",
                params={"filename": "MARKER-ROWS-FILE.txt"},
                content=b"x",
            ),
        ):
            answers.append(request.text)
        summary = client.get("/api/usage").json()
    events = [e for e in summary["recent"] if e["kind"] == "rows_append"]
    assert len(events) == 1
    assert set(events[0]) == {"at", "who", "kind", "in_source", "added", "written", "duplicates"}
    assert (events[0]["in_source"], events[0]["added"], events[0]["written"]) == (10, 7, 7)
    assert events[0]["duplicates"] == 3
    assert summary["visitors"][0]["rows_appends"] == 1 and summary["visitors"][0]["uploads"] == 1
    assert [e["kind"] for e in reversed(summary["recent"])] == ["upload", "rows_append"]
    text = "\n".join(
        [
            json.dumps(summary),
            kept.read_text("utf-8"),
            *answers,
            *(r.getMessage() for r in caplog.records if not r.name.startswith("httpx")),
        ]
    )
    for secret in secrets:
        assert secret not in text, secret
