"""The mail probe with a local model: the new arguments end to end, against a fake Ollama."""

import json
import os
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from scripts.experiment_mail_extraction import run
from tests.support.ollama import Call, FakeOpener, chat_payload, http_error
from tests.support.text_extraction import entry

FIXTURES = Path(__file__).parents[2] / "samples" / "probe" / "mail"
QUESTION = "Payments and subscriptions: what was paid, or what changes"
TARGETS = [
    "--target", "Service:text",
    "--target", "Amount:number",
    "--target", "Paid on:date",
    "--target", "Event:one_of:payment|renewal notice|ended",
]  # fmt: skip
MODEL = "testmodel"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"
TAGS = {
    "models": [
        {
            "name": "testmodel:latest",
            "model": "testmodel:latest",
            "digest": DIGEST,
            "size": 4_000_000,
        }
    ]
}


def args(out: Path, *extra: str, eml: Path = FIXTURES) -> list[str]:
    return [
        "--eml-dir", str(eml), "--task", QUESTION, *TARGETS, "--out-dir", str(out),
        "--ollama-model", MODEL, *extra,
    ]  # fmt: skip


def reply(applies: bool = True, **slots: dict[str, Any]) -> dict[str, Any]:
    values = {f"value_{i}": entry() for i in range(1, 5)}
    values.update(slots)
    return {"applies": applies, "values": values}


def server(call: Call) -> Any:
    """A fake Ollama: version, tags, and a reader that depends on the mail it is given."""
    if call.url.endswith("/api/version"):
        return {"version": "0.12.6"}
    if call.url.endswith("/api/tags"):
        return TAGS
    assert call.body is not None
    prompt = call.body["messages"][1]["content"]
    if "Subject: Ihre Rechnung" in prompt:
        return chat_payload(
            reply(
                value_1=entry("Cloudbox Plus", "Tarif: Cloudbox Plus (2 TB Speicher)"),
                value_2=entry(10.0, "Gesamt: 10,00 EUR"),
                value_3=entry("2026-11-01", "Bezahlt am 01.11.2026"),
                value_4=entry("payment", "Bezahlt am 01.11.2026"),
            ),
            prompt=900,
            evals=50,
            thinking="reasoning that must not matter",
        )
    if "Subject: Price change" in prompt:
        return chat_payload(reply(), prompt=8192, evals=0)  # the prompt did not fit
    if "Subject: Payment failed" in prompt:
        return http_error(500)
    if "Subject: Receipt for your payment" in prompt:
        wrong = entry(4.5, "Amount: 4.50 EUR")
        wrong["quote"] = "Amount: 99.00 EUR"  # not in the text: dropped by the reader
        return chat_payload(
            reply(
                value_1=entry("HostHill Starter", "Item: HostHill Starter, monthly"), value_2=wrong
            )
        )
    return chat_payload(reply(applies=False), prompt=500, evals=20)


@pytest.fixture(autouse=True)
def _no_openai(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """No OpenAI client may be built and the key may not be read, even if it is set."""
    import openai

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("an OpenAI client was constructed")

    monkeypatch.setattr(openai, "OpenAI", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-0123456789")
    asked: list[str] = []
    real = os.getenv

    def spy(name: str, *rest: Any) -> Any:
        asked.append(name)
        return real(name, *rest)

    monkeypatch.setattr(os, "getenv", spy)
    return asked


def test_full_run_with_a_local_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], _no_openai: list[str]
) -> None:
    opener = FakeOpener(choose=server)
    out = tmp_path / "out"
    assert run(args(out, "--ollama-num-ctx", "8192"), ollama_opener=opener) == 0
    console = capsys.readouterr().out

    assert "OPENAI_API_KEY" not in _no_openai
    # The version and the model list come first, then one chat request per mail, all local.
    kinds = [(c.method, c.url.rpartition("/api/")[2]) for c in opener.calls]
    assert kinds[:2] == [("GET", "version"), ("GET", "tags")]
    assert kinds[2:] == [("POST", "chat")] * 10
    assert {c.url.partition("/api/")[0] for c in opener.calls} == {"http://127.0.0.1:11434"}
    first = opener.calls[2].body
    assert first is not None
    assert first["model"] == MODEL and first["think"] is False and first["stream"] is False
    assert first["options"]["temperature"] == 0.0 and first["options"]["num_ctx"] == 8192
    assert opener.calls[2].timeout == 120.0

    rows = json.loads((out / "rows.json").read_text(encoding="utf-8"))
    by_source = {r["source"][:2]: r for r in rows}
    assert {k: v["outcome"] for k, v in by_source.items()} == {
        "01": "FOUND", "02": "NOT_APPLICABLE", "03": "NOT_APPLICABLE", "04": "NOT_APPLICABLE",
        "05": "PROVIDER_ERROR", "06": "NOT_APPLICABLE", "07": "PROVIDER_ERROR",
        "08": "NOT_APPLICABLE", "09": "NOT_APPLICABLE", "10": "FOUND",
    }  # fmt: skip
    assert by_source["01"]["values"]["Amount"] == {
        "value": 10.0, "mark": "literal", "quote": "Gesamt: 10,00 EUR"
    }  # fmt: skip
    assert by_source["01"]["input_tokens"] == 900 and by_source["01"]["output_tokens"] == 50
    assert by_source["10"]["dropped"][0]["reason"] == "quote_not_in_text"
    assert "PromptDidNotFit" in by_source["07"]["message"]
    assert [r["source"][:2] for r in rows if r["prompt_did_not_fit"]] == ["07"]
    assert "reasoning that must not matter" not in json.dumps(rows)

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["configuration_id"] == "ollama-text-extraction-p1-testmodel"
    assert summary["executor"] == "ollama" and summary["model"] == MODEL
    assert summary["reasoning_effort"] is None
    assert "estimated_cost" not in summary and "prices_used" not in summary
    local = summary["ollama"]
    assert {k: local[k] for k in ("name", "model_digest", "model_size_bytes", "version")} == {
        "name": "testmodel:latest", "model_digest": DIGEST, "model_size_bytes": 4_000_000,
        "version": "0.12.6",
    }  # fmt: skip
    assert (local["num_ctx"], local["temperature"], local["loopback"]) == (8192, 0.0, True)
    assert local["prompt_did_not_fit"] == 1
    seconds = sorted(r["seconds"] for r in rows)
    assert local["seconds_per_mail"] == {
        "median": round((seconds[4] + seconds[5]) / 2, 2),
        "max": seconds[-1],
    }
    assert summary["outcomes"] == {"FOUND": 2, "NOT_APPLICABLE": 6, "PROVIDER_ERROR": 2}
    assert summary["values_dropped_by_reason"] == {"quote_not_in_text": 1}
    assert sorted(p.name for p in out.iterdir()) == ["register.xlsx", "rows.json", "summary.json"]

    assert "Local model: testmodel:latest" in console and "No cost estimate" in console
    assert "Mails whose prompt did not fit the context: 1" in console
    assert "Estimated cost" not in console and "OpenAI" not in console


def test_no_key_is_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    assert run(args(tmp_path / "o", "--limit", "2"), ollama_opener=FakeOpener(choose=server)) == 0


def test_a_server_that_does_not_answer_stops_with_one_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = FakeOpener(urllib.error.URLError(ConnectionRefusedError(111, "refused")))
    assert run(args(tmp_path / "o"), ollama_opener=opener) == 1
    err = capsys.readouterr().err
    assert err == (
        "Ollama does not answer at http://127.0.0.1:11434; start it (ollama serve) "
        "or check --ollama-url.\n"
    )
    assert len(opener.calls) == 1 and not (tmp_path / "o").exists()


def test_a_model_that_is_not_pulled_stops_with_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = FakeOpener(
        choose=lambda call: {"version": "1"} if "version" in call.url else {"models": []}
    )
    assert run(args(tmp_path / "o"), ollama_opener=opener) == 1
    assert capsys.readouterr().err == (
        "The model testmodel is not available on this Ollama server; "
        "pull it first with: ollama pull testmodel\n"
    )
    assert [c.method for c in opener.calls] == ["GET", "GET"] and not (tmp_path / "o").exists()


def test_a_missing_model_during_the_run_stops_it_like_a_rejected_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def gone(call: Call) -> Any:
        return http_error(404) if call.method == "POST" else server(call)

    opener = FakeOpener(choose=gone)
    assert run(args(tmp_path / "o"), ollama_opener=opener) == 0
    console = capsys.readouterr().out
    assert "REQUEST_REFUSED" in console and "Ollama did not accept the request" in console
    summary = json.loads((tmp_path / "o" / "summary.json").read_text(encoding="utf-8"))
    assert (
        summary["messages"]["asked"] == 1
        and "REQUEST_REFUSED" in summary["messages"]["stopped_early"]
    )


def test_a_remote_host_is_refused_unless_allowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = FakeOpener(choose=server)
    with pytest.raises(SystemExit) as stop:
        run(args(tmp_path / "o", "--ollama-url", "http://192.168.1.20:11434"), ollama_opener=opener)
    assert stop.value.code == 2 and "loopback" in capsys.readouterr().err
    assert opener.calls == []

    allowed = args(
        tmp_path / "o", "--ollama-url", "http://192.168.1.20:11434", "--ollama-allow-remote"
    )
    assert run([*allowed, "--limit", "1"], ollama_opener=opener) == 0
    assert {c.url.partition("/api/")[0] for c in opener.calls} == {"http://192.168.1.20:11434"}
    summary = json.loads((tmp_path / "o" / "summary.json").read_text(encoding="utf-8"))
    assert summary["ollama"]["loopback"] is False


@pytest.mark.parametrize(
    "extra",
    [["--ollama-num-ctx", "100"], ["--ollama-timeout", "0"], ["--ollama-url", "ftp://127.0.0.1"]],
)
def test_option_errors(
    extra: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        run(args(tmp_path / "o", *extra), ollama_opener=FakeOpener(choose=server))
    assert stop.value.code == 2 and capsys.readouterr().err


@pytest.mark.parametrize(
    "option",
    [["--ollama-url", "http://127.0.0.1:11434"], ["--ollama-allow-remote"],
     ["--ollama-num-ctx", "4096"], ["--ollama-timeout", "30"]],
)  # fmt: skip
def test_the_other_ollama_options_need_the_model(
    option: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--eml-dir", str(FIXTURES), "--task", QUESTION, *TARGETS, "--out-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as stop:
        run([*base, *option])
    assert stop.value.code == 2 and "go with --ollama-model" in capsys.readouterr().err


def test_a_custom_timeout_reaches_the_request(tmp_path: Path) -> None:
    opener = FakeOpener(choose=server)
    run([*args(tmp_path / "o", "--ollama-timeout", "33.5"), "--limit", "1"], ollama_opener=opener)
    assert opener.calls[2].timeout == 33.5


def test_dry_run_prints_the_chat_body_and_contacts_nobody(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = FakeOpener(choose=server)
    assert run(args(tmp_path / "o", "--dry-run"), ollama_opener=opener) == 0
    assert opener.calls == [] and not (tmp_path / "o").exists()
    output = capsys.readouterr().out
    printed = json.loads(output[output.index("{") :])
    assert printed["model"] == MODEL and printed["options"]["num_ctx"] == 8192
    assert printed["timeout"] == 120.0 and printed["think"] is False
    assert printed["messages"][1]["content"].startswith("Task: Payments")


def test_thinking_can_be_left_to_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opener = FakeOpener(choose=server)
    assert run(args(tmp_path / "o", "--dry-run", "--ollama-think", "default")) == 0
    output = capsys.readouterr().out
    assert "think" not in json.loads(output[output.index("{") :])
    out = tmp_path / "run"
    run([*args(out, "--ollama-think", "default"), "--limit", "1"], ollama_opener=opener)
    assert "think" not in (opener.calls[2].body or {})
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert summary["ollama"]["think"] == "default"
