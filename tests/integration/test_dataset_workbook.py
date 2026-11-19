"""The stored workbook of a dataset as a download: how a table with new rows is taken away."""

import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from tests.integration.test_mail_dataset import fixture_zip, local_app, send
from tests.integration.test_rows_api import (
    candidates_of,
    register_bytes,
    run_body,
    upload,
)

from ai_data_enrichment.api import create_app

CODE = "demo-code-1234"


def test_the_workbook_with_the_appended_rows_opens_as_a_workbook() -> None:
    with TestClient(local_app()) as client:
        source = upload(client, register_bytes())
        candidates_id = candidates_of(client)
        run = client.post(f"/api/datasets/{source}/rows", json=run_body(candidates_id))
        assert run.status_code == 201, run.text
        new = run.json()["dataset_id"]
        answer = client.get(f"/api/datasets/{new}/workbook")
        assert answer.status_code == 200
        assert answer.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        # A fixed neutral name: the client names the file it saves.
        assert answer.headers["content-disposition"].startswith("attachment;")
        assert 'filename="workbook.xlsx"' in answer.headers["content-disposition"]
        sheet = load_workbook(io.BytesIO(answer.content))["Register"]
        assert sheet.max_row == 11
        assert [cell.value for cell in sheet[1]] == ["Received", "From", "Subject", "Amount"]
        assert [sheet.cell(row, 4).value for row in (2, 3, 4)] == [10, 20, 30]
        assert all(sheet.cell(row, 1).value and sheet.cell(row, 2).value for row in range(5, 12))
        # The source workbook downloads as it was uploaded.
        original = client.get(f"/api/datasets/{source}/workbook")
        assert load_workbook(io.BytesIO(original.content))["Register"].max_row == 4


def test_a_dataset_that_is_not_there_is_not_found() -> None:
    with TestClient(local_app()) as client:
        answer = client.get("/api/datasets/never-was/workbook")
        assert answer.status_code == 404
        assert answer.json()["error"]["code"] == "dataset_not_found"


def test_a_table_built_from_e_mails_is_not_handed_out() -> None:
    with TestClient(local_app()) as client:
        made = send(client, fixture_zip()).json()
        mails, sheet = made["dataset_id"], made["worksheets"][0]["name"]
        answer = client.get(f"/api/datasets/{mails}/workbook")
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "mail_dataset_no_download"
        # What grows out of it is a table of e-mails too.
        grown = client.post(
            f"/api/datasets/{mails}/rows",
            json=run_body(
                candidates_of(client),
                sheet_name=sheet,
                range_ref="A1:D11",
                mapping={"received": "A", "sender": "B", "subject": "C"},
            ),
        )
        assert grown.status_code == 201, grown.text
        refused = client.get(f"/api/datasets/{grown.json()['dataset_id']}/workbook")
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "mail_dataset_no_download"


def test_a_download_is_counted_once_with_no_details() -> None:
    app = local_app()
    with TestClient(app) as client:
        source = upload(client, register_bytes())
        before = app.state.usage.summary()["events"].get("download", 0)
        assert client.get(f"/api/datasets/{source}/workbook").status_code == 200
        assert client.get("/api/datasets/never-was/workbook").status_code == 404
    summary = app.state.usage.summary()
    assert summary["events"]["download"] == before + 1
    last = [e for e in summary["recent"] if e["kind"] == "download"]
    assert len(last) == 1 and set(last[0]) == {"at", "who", "kind"}


def test_the_access_code_covers_the_route() -> None:
    with TestClient(create_app(access_code=CODE)) as client:
        assert client.get("/api/datasets/x/workbook").status_code == 401
        granted = {"X-Access-Code": CODE}
        up = client.post(
            "/api/datasets",
            params={"filename": "register.xlsx"},
            content=register_bytes(),
            headers=granted,
        )
        dataset = up.json()["dataset_id"]
        assert client.get(f"/api/datasets/{dataset}/workbook").status_code == 401
        assert client.get(f"/api/datasets/{dataset}/workbook", headers=granted).status_code == 200
