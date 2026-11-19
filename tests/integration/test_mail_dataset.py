"""A table built from e-mails: the route, its gates and bounds, and that no model sees it."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from tests.support.planning import ScriptedPlanClient

from ai_data_enrichment import api as api_module
from ai_data_enrichment.api import create_app
from ai_data_enrichment.mail import upload as mail_upload
from ai_data_enrichment.plan_run import PlanRunSettings
from ai_data_enrichment.workspace_errors import WorkspaceError

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"
MAIL = PlanRunSettings(allow_mail=True)


def fixture_zip(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(FIXTURES.glob("*.eml")):
            archive.write(path, path.name)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def zip_of(entries: dict[str, bytes], *, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def local_app(**options):
    options.setdefault("settings", MAIL)
    return create_app(local_browser_configuration=True, **options)


def send(client: TestClient, content: bytes, name: str = "mails.zip"):
    return client.post("/api/mail-datasets", params={"filename": name}, content=content)


def test_a_zip_of_the_fixtures_becomes_a_table_with_one_row_per_mail() -> None:
    with TestClient(local_app()) as client:
        answer = send(client, fixture_zip(extra={"notes.txt": b"x", "sub/": b""}))
        assert answer.status_code == 201, answer.text
        body = answer.json()
        assert body["filename"] == "mail.xlsx"
        assert body["mail"]["read"] == 10 and body["mail"]["skipped"] == 1
        assert body["mail"]["truncated"] == 0
        assert set(body["mail"]["masked"]) == {"card", "phone", "me"}
        assert body["mail"]["masked"]["card"] >= 1 and body["mail"]["masked"]["me"] >= 1
        sheet = body["worksheets"][0]
        assert sheet["name"] == "Mail" and sheet["columns"] == [
            "Received",
            "From",
            "Subject",
            "Text",
        ]
        assert sheet["row_count"] == 10
        preview = client.post(
            f"/api/datasets/{body['dataset_id']}/preview",
            json={"sheet_name": "Mail", "range_ref": "A1:D11"},
        )
        assert preview.status_code == 200, preview.text
        shown = preview.json()
        assert shown["mail"] is True and len(shown["rows"]["values"]) == 10
        assert shown["profile"]["row_count"] == 10
        # Cells are cut to the profiling limit, so a long text is never shown whole.
        assert all(len(cell) <= 200 for row in shown["rows"]["values"] for cell in row if cell)
        assert json.dumps(shown["profile"]["sample_records"]).count("Subject:") >= 1


def test_the_generated_workbook_is_the_only_thing_stored() -> None:
    app = local_app()
    with TestClient(app) as client:
        dataset_id = send(client, fixture_zip()).json()["dataset_id"]
        stored = list(app.state.directory.rglob("*"))
        assert [p.name for p in stored] == [f"{dataset_id}.xlsx"]
        sheet = load_workbook(stored[0])["Mail"]
        assert sheet.max_row == 11
        assert sheet["A2"].is_date


def test_a_single_eml_file() -> None:
    with TestClient(local_app()) as client:
        raw = (FIXTURES / "10_payrelay_card_phone_me.eml").read_bytes()
        body = send(client, raw, "one.EML").json()
        assert body["mail"]["read"] == 1 and body["worksheets"][0]["row_count"] == 1
        assert body["mail"]["masked"] == {"card": 3, "phone": 1, "me": 1}


def test_the_mail_table_is_a_dataset_like_any_other() -> None:
    with TestClient(local_app()) as client:
        dataset_id = send(client, fixture_zip()).json()["dataset_id"]
        assert client.delete(f"/api/datasets/{dataset_id}").status_code == 204
        gone = client.post(
            f"/api/datasets/{dataset_id}/preview",
            json={"sheet_name": "Mail", "range_ref": "A1:D3"},
        )
        assert gone.status_code == 404


def test_an_ordinary_upload_still_works_and_is_not_a_mail_dataset() -> None:
    app = local_app()
    with TestClient(app) as client:
        answer = client.post(
            "/api/datasets",
            params={"filename": "capitals.xlsx"},
            content=Path("samples/capitals.xlsx").read_bytes(),
        )
        assert answer.status_code == 201 and "mail" not in answer.json()
        dataset_id = answer.json()["dataset_id"]
        assert dataset_id not in app.state.plan_run.mail_datasets
        preview = client.post(
            f"/api/datasets/{dataset_id}/preview",
            json={"sheet_name": "Sheet1", "range_ref": "A1:E11"},
        )
        assert preview.status_code == 200 and "mail" not in preview.json()


# --- the gate -----------------------------------------------------------------------------


def test_with_the_setting_off_the_route_is_refused_before_the_body_is_read() -> None:
    with TestClient(local_app(settings=PlanRunSettings())) as client:
        assert client.get("/api/limits").json()["mail"] is False
        answer = send(client, b"not even looked at", "mails.txt")
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "mail_not_allowed"


def test_the_limits_say_when_mail_is_available() -> None:
    with TestClient(local_app()) as client:
        assert client.get("/api/limits").json()["mail"] is True


def test_the_hosted_app_never_offers_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ai_data_enrichment.hosted import create_hosted_app

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setenv("ENRICHMENT_ACCESS_CODE", "visitor-code-1")
    monkeypatch.delenv("ENRICHMENT_ACCESS_CODES", raising=False)
    monkeypatch.delenv("ENRICHMENT_ADMIN_CODE", raising=False)
    monkeypatch.setenv("ENRICHMENT_STATIC_DIR", str(tmp_path))
    monkeypatch.setenv("ENRICHMENT_ALLOW_MAIL", "1")
    headers = {"x-access-code": "visitor-code-1"}
    with TestClient(create_hosted_app()) as client:
        assert client.get("/api/limits", headers=headers).json()["mail"] is False
        answer = client.post(
            "/api/mail-datasets",
            params={"filename": "mails.zip"},
            content=fixture_zip(),
            headers=headers,
        )
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "mail_not_allowed"


def test_the_environment_variable_switches_the_setting_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENRICHMENT_ALLOW_MAIL", "1")
    assert PlanRunSettings.from_environment().allow_mail is True
    monkeypatch.setenv("ENRICHMENT_ALLOW_MAIL", "true")
    assert PlanRunSettings.from_environment().allow_mail is False
    monkeypatch.delenv("ENRICHMENT_ALLOW_MAIL")
    assert PlanRunSettings.from_environment().allow_mail is False


# --- bounds --------------------------------------------------------------------------------


def error_of(answer) -> tuple[int, str, str]:
    error = answer.json()["error"]
    return answer.status_code, error["code"], error["message"]


def test_the_wrong_type_is_refused() -> None:
    with TestClient(local_app()) as client:
        status, code, _ = error_of(send(client, b"x", "mails.xlsx"))
        assert (status, code) == (422, "invalid_mail_file_type")
        assert error_of(send(client, b"x", "mails"))[1] == "invalid_mail_file_type"


def test_an_upload_over_32_mib_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail_upload, "MAX_UPLOAD_BYTES", 1000)
    with TestClient(local_app()) as client:
        assert error_of(send(client, b"x" * 1001, "m.eml")) == (
            413,
            "mail_upload_too_large",
            "Upload up to 32 MiB.",
        )


def test_the_real_upload_limit_is_32_mib() -> None:
    assert mail_upload.MAX_UPLOAD_BYTES == 32 * 1024 * 1024
    assert mail_upload.MAX_ENTRIES == 500
    assert mail_upload.MAX_ENTRY_BYTES == 10 * 1024 * 1024
    assert mail_upload.MAX_TOTAL_BYTES == 64 * 1024 * 1024


def test_more_than_500_mail_files_are_refused() -> None:
    raw = (FIXTURES / "04_streamly_trial_ends.eml").read_bytes()
    entries = {f"{i}.eml": raw for i in range(501)}
    with TestClient(local_app()) as client:
        assert error_of(send(client, zip_of(entries))) == (
            422,
            "mail_too_many",
            "Upload at most 500 .eml files in one .zip.",
        )
        # Other entries do not count towards the limit.
        entries = {f"{i}.eml": raw for i in range(500)} | {f"{i}.txt": b"x" for i in range(100)}
        answer = send(client, zip_of(entries))
        assert answer.status_code == 201
        assert answer.json()["mail"]["read"] == 500 and answer.json()["mail"]["skipped"] == 100


def test_a_mail_over_10_mib_is_refused_as_a_small_zip_bomb() -> None:
    bomb = zip_of({"big.eml": b"\0" * (10 * 1024 * 1024 + 1)})
    assert len(bomb) < 100_000
    with TestClient(local_app()) as client:
        assert error_of(send(client, bomb)) == (
            413,
            "mail_entry_too_large",
            "A mail in the upload is larger than 10 MiB.",
        )
        single = send(client, b"\0" * (10 * 1024 * 1024 + 1), "big.eml")
        assert error_of(single)[1] == "mail_entry_too_large"


def test_mails_adding_up_to_more_than_64_mib_are_refused_as_a_small_zip_bomb() -> None:
    chunk = b"\0" * (9 * 1024 * 1024)
    bomb = zip_of({f"{i}.eml": chunk for i in range(8)})
    assert len(bomb) < 200_000
    with TestClient(local_app()) as client:
        assert error_of(send(client, bomb)) == (
            413,
            "mail_total_too_large",
            "The mails in the upload add up to more than 64 MiB.",
        )


def test_an_archive_that_declares_less_than_it_holds_is_not_expanded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reader stops at the declared size, and the code reads no more than a bound anyway:
    # three real mails that declare one byte each leave nothing readable and nothing expanded.
    raw = (FIXTURES / "04_streamly_trial_ends.eml").read_bytes()
    data = zip_of({"a.eml": raw, "b.eml": raw, "c.eml": raw})
    declared = zipfile.ZipFile(io.BytesIO(data)).infolist()
    for entry in declared:
        entry.file_size = 1
    monkeypatch.setattr(zipfile.ZipFile, "infolist", lambda self: declared)
    with pytest.raises(WorkspaceError) as caught:
        mail_upload.read_mail_upload(data, archive=True)
    assert caught.value.code == "no_mail_found"


def test_no_readable_mail_is_an_error_and_a_broken_entry_is_only_counted() -> None:
    with TestClient(local_app()) as client:
        status, code, _ = error_of(send(client, zip_of({"a.eml": b"\x00\x01\x02", "b.txt": b"x"})))
        assert (status, code) == (422, "no_mail_found")
        assert error_of(send(client, zip_of({"readme.txt": b"x"})))[1] == "no_mail_found"
        assert error_of(send(client, b"garbage", "m.eml"))[1] == "no_mail_found"
        assert error_of(send(client, b"garbage", "m.zip"))[1] == "invalid_mail_archive"
        good = (FIXTURES / "04_streamly_trial_ends.eml").read_bytes()
        answer = send(client, zip_of({"a.eml": b"\x00\x01", "b.eml": good}))
        assert answer.status_code == 201
        assert answer.json()["mail"]["read"] == 1 and answer.json()["mail"]["skipped"] == 1


def test_an_encrypted_or_unsupported_entry_is_skipped_not_fatal() -> None:
    good = (FIXTURES / "04_streamly_trial_ends.eml").read_bytes()
    data = bytearray(zip_of({"a.eml": b"payload", "b.eml": good}, method=zipfile.ZIP_STORED))
    # Flag the first entry as encrypted in its central directory record.
    position = data.index(b"PK\x01\x02")
    data[position + 8] |= 1
    with TestClient(local_app()) as client:
        answer = send(client, bytes(data))
        assert answer.status_code == 201
        assert answer.json()["mail"] == {
            "read": 1,
            "skipped": 1,
            "truncated": 0,
            "masked": answer.json()["mail"]["masked"],
        }


# --- no model sees such a table -------------------------------------------------------------


def refused(answer) -> None:
    assert answer.status_code == 409, answer.text
    assert answer.json()["error"] == {
        "code": "mail_dataset_no_planner",
        "message": "A table built from e-mails is not planned by the AI.",
    }


def test_the_planner_task_ideas_and_web_search_refuse_a_mail_table_without_a_model_call() -> None:
    model = ScriptedPlanClient()
    app = local_app(
        settings=PlanRunSettings(allow_mail=True, allow_web_lookup=True), offline_plan_client=model
    )
    with TestClient(app) as client:
        dataset_id = send(client, fixture_zip()).json()["dataset_id"]
        table = {"dataset_id": dataset_id, "sheet_name": "Mail", "range_ref": "A1:D11"}
        refused(
            client.post(
                "/api/plans",
                json={**table, "instruction": "x", "output_columns": {"Service": "E"}},
            )
        )
        refused(
            client.post(
                "/api/plans",
                json={
                    **table,
                    "instruction": "x",
                    "output_columns": {"Service": "E"},
                    "plan": json.loads(Path("demo/capitals_api_plan.json").read_text("utf-8")),
                    "allow_network": True,
                },
            )
        )
        refused(client.post("/api/task-ideas", json={**table, "language": "en"}))
        refused(client.post("/api/task-ideas", json={**table, "language": "en", "again": True}))
        refused(
            client.post(
                "/api/fallback-plans",
                json={
                    **table,
                    "question": "q",
                    "context_columns": ["Text"],
                    "targets": [{"column": "Service", "kind": "text"}],
                    "output_columns": {"Service": "E"},
                },
            )
        )
        # An unknown id is still an unknown id.
        missing = client.post(
            "/api/task-ideas", json={**table, "dataset_id": "0" * 32, "language": "en"}
        )
        assert missing.status_code != 409
    assert model.calls == []


def test_eviction_and_removal_forget_the_mail_flag() -> None:
    app = local_app()
    with TestClient(app) as client:
        mail_id = send(client, fixture_zip()).json()["dataset_id"]
        assert app.state.plan_run.mail_datasets == {mail_id}
        book = Workbook()
        buffer = io.BytesIO()
        book.save(buffer)
        for _ in range(api_module.MAX_DATASETS - 1):
            assert (
                client.post(
                    "/api/datasets", params={"filename": "a.xlsx"}, content=buffer.getvalue()
                ).status_code
                == 201
            )
        assert app.state.plan_run.mail_datasets == {mail_id}
        # The ninth dataset evicts the oldest, the mail table.
        assert (
            client.post(
                "/api/datasets", params={"filename": "a.xlsx"}, content=buffer.getvalue()
            ).status_code
            == 201
        )
        assert app.state.plan_run.mail_datasets == set()
        assert mail_id not in app.state.plan_run.datasets
        second = send(client, fixture_zip()).json()["dataset_id"]
        assert app.state.plan_run.mail_datasets == {second}
        assert client.delete(f"/api/datasets/{second}").status_code == 204
        assert app.state.plan_run.mail_datasets == set()


def test_a_mail_import_is_counted_with_numbers_only() -> None:
    app = local_app()
    with TestClient(app) as client:
        send(client, fixture_zip())
        usage = client.get("/api/usage").json()
    event = next(e for e in usage["recent"] if e["kind"] == "mail_import")
    assert event["read"] == 10 and set(event["masked"]) == {"card", "phone", "me"}
    assert set(event) == {"at", "who", "kind", "read", "skipped", "truncated", "masked"}
    assert usage["visitors"][0]["mail_imports"] == 1
    assert usage["visitors"][0]["uploads"] == 0
    json.dumps(usage)


def test_a_mail_table_replaces_nothing_when_the_upload_is_refused() -> None:
    app = local_app()
    with TestClient(app) as client:
        first = send(client, fixture_zip()).json()["dataset_id"]
        assert send(client, b"garbage", "m.eml").status_code == 422
        assert list(app.state.plan_run.datasets) == [first]
