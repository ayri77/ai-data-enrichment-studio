"""HTTP contract and source-preservation checks for the local workspace."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from ai_data_enrichment import api


@pytest.fixture
def client():
    with TestClient(api.create_app(), raise_server_exceptions=False) as client:
        yield client


def workbook_bytes(*, duplicate: bool = False, empty: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Offset"
    if not empty:
        sheet.append(["Title"])
        sheet.append([None, "name", "name" if duplicate else "value"])
        for i in range(12):
            sheet.append([None, f"Item {i}", i])
    workbook.create_sheet("Other").append(["header"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def upload(client: TestClient, content: bytes, filename: str = "book.xlsx"):
    return client.post("/api/datasets", params={"filename": filename}, content=content)


@pytest.mark.parametrize(
    ("name", "range_ref", "headers"),
    [
        (
            "capitals",
            "A1:E11",
            ["Capital_From", "Country_From", "Capital_To", "Country_To", "distance"],
        ),
        ("mountains", "A1:C11", ["Mountain", "Country", "height"]),
    ],
)
def test_real_sample_preview_and_source_preservation(client, name, range_ref, headers):
    path = Path("samples") / f"{name}.xlsx"
    original = path.read_bytes()
    result = upload(client, original, path.name)
    assert result.status_code == 201
    dataset = result.json()
    assert dataset["worksheets"][0]["name"] == "Sheet1"
    assert dataset["worksheets"][0]["row_count"] == 10
    stored_path = client.app.state.directory / f"{dataset['dataset_id']}.xlsx"
    assert stored_path.read_bytes() == original
    preview = client.post(
        f"/api/datasets/{dataset['dataset_id']}/preview",
        json={"sheet_name": "Sheet1", "range_ref": range_ref},
    )
    assert preview.status_code == 200
    profile = preview.json()["profile"]
    assert [column["name"] for column in profile["columns"]] == headers
    assert profile["row_count"] == 10
    assert len(profile["sample_records"]) == 5
    assert list(profile["sample_records"][0]) == headers
    # The whole table is there to look through; the profile keeps its sample.
    rows = preview.json()["rows"]
    assert rows["row_numbers"] == list(range(2, 12))
    assert len(rows["values"]) == 10 and all(len(row) == len(headers) for row in rows["values"])
    assert rows["values"][0] == [profile["sample_records"][0][name] for name in headers]
    assert sha256(path.read_bytes()).digest() == sha256(original).digest()
    assert stored_path.read_bytes() == original


def test_offset_range_and_sheet_selection(client):
    dataset_id = upload(client, workbook_bytes(), "../../source.xlsx").json()["dataset_id"]
    endpoint = f"/api/datasets/{dataset_id}/preview"
    response = client.post(endpoint, json={"sheet_name": "Offset", "range_ref": "B2:C14"})
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["row_count"] == 12
    assert profile["sample_records"][0] == {"name": "Item 0", "value": 0}
    other = client.post(endpoint, json={"sheet_name": "Other", "range_ref": "A1:A1"})
    assert other.json()["profile"]["sample_records"] == []


@pytest.mark.parametrize("range_ref", ["bad", "A1:XFD1048576", "A1:A5002", "A1:C2"])
def test_invalid_selection_is_structured_and_path_free(client, range_ref):
    dataset_id = upload(client, workbook_bytes()).json()["dataset_id"]
    response = client.post(
        f"/api/datasets/{dataset_id}/preview",
        json={"sheet_name": "Offset", "range_ref": range_ref},
    )
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert str(client.app.state.directory) not in response.text


def test_missing_sheet_duplicate_headers_and_invalid_request(client):
    dataset_id = upload(client, workbook_bytes(duplicate=True)).json()["dataset_id"]
    endpoint = f"/api/datasets/{dataset_id}/preview"
    for body in [
        {"sheet_name": "Missing", "range_ref": "B2:C14"},
        {"sheet_name": "Offset", "range_ref": "B2:C14"},
        {"sheet_name": "Offset", "range_ref": "B2:C14", "path": "/secret"},
        {},
    ]:
        response = client.post(endpoint, json=body)
        assert response.status_code == 422
        assert "error" in response.json()


@pytest.mark.parametrize("content", [b"", b"not excel", b"PK corrupt"])
def test_malformed_workbooks_are_removed(client, content):
    response = upload(client, content)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_workbook"
    assert not list(client.app.state.directory.iterdir())


def test_upload_extension_size_and_cleanup(client, monkeypatch):
    assert upload(client, b"fake", "file.xls").status_code == 422
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 5)
    response = upload(client, b"123456")
    assert response.status_code == 413
    assert not list(client.app.state.directory.iterdir())


@pytest.mark.parametrize(
    ("filename", "xml"),
    [
        ("xl/worksheets/sheet1.xml", '<worksheet><row r="1048576"/></worksheet>'),
        ("xl/unusual.bin", '<worksheet><c r="XFD1"/></worksheet>'),
        ("xl/worksheets/sheet1.xml", '<worksheet><dimension ref="A1:A1048576"/></worksheet>'),
    ],
)
def test_archive_resource_limits(client, filename, xml):
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(filename, xml)
    response = upload(client, output.getvalue())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "worksheet_limit"


def test_expanded_size_limit(client, monkeypatch):
    monkeypatch.setattr(api, "MAX_EXPANDED_BYTES", 10)
    assert upload(client, workbook_bytes()).json()["error"]["code"] == "workbook_limit"


def test_repeated_long_headers_are_bounded_in_metadata(client):
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["x" * 13_000])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    assert upload(client, output.getvalue()).json()["error"]["code"] == "workbook_limit"


def test_delete_unknown_ids_capacity_and_shutdown_cleanup(monkeypatch):
    monkeypatch.setattr(api, "MAX_DATASETS", 1)
    workspace_app = api.create_app()
    with TestClient(workspace_app) as client:
        directory = workspace_app.state.directory
        dataset_id = upload(client, workbook_bytes()).json()["dataset_id"]
        # At the limit the oldest workbook gives way and its file is removed.
        newer = upload(client, workbook_bytes())
        assert newer.status_code == 201
        assert [p.stem for p in directory.glob("*.xlsx")] == [newer.json()["dataset_id"]]
        assert client.delete(f"/api/datasets/{dataset_id}").status_code == 204
        response = client.post(
            f"/api/datasets/{dataset_id}/preview",
            json={"sheet_name": "Offset", "range_ref": "B2:C14"},
        )
        assert response.status_code == 404
        assert upload(client, workbook_bytes()).status_code == 201
    assert not directory.exists()


def test_unexpected_errors_do_not_leak_paths(client, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("C:/private/internal.xlsx")

    dataset_id = upload(client, workbook_bytes()).json()["dataset_id"]
    monkeypatch.setattr(api, "profile_worksheet", fail)
    response = client.post(
        f"/api/datasets/{dataset_id}/preview",
        json={"sheet_name": "Offset", "range_ref": "B2:C14"},
    )
    assert response.status_code == 500
    assert "private" not in response.text
