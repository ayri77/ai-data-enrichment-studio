"""Which claimed links are kept or dropped (and why), and tracking parameters; offline."""

from typing import Any

import pytest
from tests.support.web_lookup import PAGE, PAGE_2, good_reply, mixed_task, response, slot

from ai_data_enrichment.providers.web_lookup import (
    is_tracking_parameter,
    normalize_url,
    strip_tracking,
)
from ai_data_enrichment.providers.web_lookup_reply import ParsedReply, ReplyFailure, parse_reply

PHONE = "https://example.org/phone"


def read(reply: Any, **options: Any) -> ParsedReply | ReplyFailure:
    return parse_reply(response(reply, **options), mixed_task())


def parsed(reply: Any, **options: Any) -> ParsedReply:
    result = read(reply, **options)
    assert isinstance(result, ParsedReply), result
    return result


def with_sources(sources: list[str]) -> dict[str, Any]:
    reply = good_reply()
    reply["value_1"]["sources"] = sources
    return reply


# --- drop reasons ----------------------------------------------------------------------


def test_each_drop_reason_and_the_total() -> None:
    many = tuple(f"https://example.org/p{i}" for i in range(5))
    claimed = [
        many[0],
        many[0],  # duplicate of a kept link
        "https://invented.example/x",  # not_reported
        "ftp://example.org/x",  # not_a_link
        "not a link",  # not_a_link (spaces)
        "https://example.org/" + "a" * 2000,  # not_a_link (too long)
        many[1],
        many[2],
        many[3],  # over_limit
        many[4],  # over_limit
    ]
    result = parsed(with_sources(claimed), reported=many)
    diagnostic = result.diagnostics[0]
    assert result.record.slots["value_1"].sources == many[:3]
    assert [reason for _, reason in diagnostic.dropped] == [
        "duplicate",
        "not_reported",
        "not_a_link",
        "not_a_link",
        "not_a_link",
        "over_limit",
        "over_limit",
    ]
    assert diagnostic.links_dropped == len(diagnostic.dropped) == 7
    assert diagnostic.dropped[1] == ("https://invented.example/x", "not_reported")


def test_a_duplicate_is_a_duplicate_not_unreported() -> None:
    # The same page named twice, once with tracking and once in another spelling.
    reply = with_sources([PAGE, "HTTPS://www.example.org/everest/?utm_source=a#x", PAGE])
    diagnostic = parsed(reply).diagnostics[0]
    assert [reason for _, reason in diagnostic.dropped] == ["duplicate", "duplicate"]


def test_a_full_slot_calls_a_repeat_a_duplicate_before_over_limit() -> None:
    many = tuple(f"https://example.org/p{i}" for i in range(4))
    diagnostic = parsed(with_sources([*many[:3], many[0], many[3]]), reported=many).diagnostics[0]
    assert [reason for _, reason in diagnostic.dropped] == ["duplicate", "over_limit"]


def test_the_stored_link_is_cleaned_and_cut() -> None:
    claimed = ["  https://invented.example/\x00x\n", "https://invented.example/" + "b" * 400]
    reply = good_reply()
    reply["value_1"]["sources"] = [*claimed, PAGE]
    diagnostic = parsed(reply).diagnostics[0]
    (first, first_reason), (second, second_reason) = diagnostic.dropped
    assert first == "https://invented.example/x" and first_reason == "not_reported"
    assert len(second) == 300 and second.startswith("https://invented.example/bbb")
    assert second_reason == "not_reported"


def test_dropped_links_are_kept_for_a_slot_that_loses_all_of_them() -> None:
    reply = good_reply()
    reply["value_1"]["sources"] = ["https://invented.example/a", "mailto:x@example.org"]
    diagnostic = parsed(reply).diagnostics[0]
    assert diagnostic.reason == "no_reported_link" and diagnostic.links_dropped == 2
    assert diagnostic.dropped == (
        ("https://invented.example/a", "not_reported"),
        ("mailto:x@example.org", "not_a_link"),
    )


def test_a_kept_slot_with_nothing_dropped_has_an_empty_list() -> None:
    assert all(d.dropped == () for d in parsed(good_reply()).diagnostics)


def test_no_link_is_in_a_nothing_found_message() -> None:
    reply = {
        name: slot(value=1, unit="m", basis="stated", sources=["https://invented.example/secret"])
        for name in ("value_1",)
    }
    reply["value_2"] = slot(value="a", basis="stated", sources=["https://invented.example/secret2"])
    reply["value_3"] = slot(value=True, basis="stated", sources=["ftp://invented.example/x"])
    result = read(reply)
    assert isinstance(result, ReplyFailure) and result.code == "NOTHING_FOUND"
    assert "invented" not in result.message and "http" not in result.message
    assert "no_reported_link: 3" in result.message
    assert result.diagnostics[0].dropped == (("https://invented.example/secret", "not_reported"),)


# --- tracking parameters -----------------------------------------------------------------


def test_a_tracking_parameter_on_the_reported_side_is_exact_and_the_kept_link_is_clean() -> None:
    result = parsed(with_sources([PHONE]), reported=(PHONE + "?msockid=ABC123", PAGE_2))
    assert result.record.slots["value_1"].sources == (PHONE,)
    assert result.diagnostics[0].link_matches == ("exact",)
    assert Evidence_refs(result) == [PHONE, PAGE_2]


def Evidence_refs(result: ParsedReply) -> list[str]:  # noqa: N802
    return [e.reference for e in result.evidence]


def test_a_tracking_parameter_on_the_claimed_side_is_exact_too() -> None:
    result = parsed(with_sources([PHONE + "?gclid=1&UTM_Medium=x"]), reported=(PHONE, PAGE_2))
    assert result.record.slots["value_1"].sources == (PHONE,)
    assert result.diagnostics[0].link_matches == ("exact",)


def test_other_parameters_stay_in_the_kept_link_in_order() -> None:
    reported = PHONE + "?b=2&fbclid=z&a=1"
    result = parsed(with_sources([PHONE + "?b=2&a=1"]), reported=(reported, PAGE_2))
    assert result.record.slots["value_1"].sources == (PHONE + "?b=2&a=1",)
    assert result.diagnostics[0].link_matches == ("exact",)


def test_a_parameter_that_is_not_tracking_still_needs_an_exact_match() -> None:
    reported = (PHONE + "?sku=B", PHONE + "?sku=A")
    result = parsed(with_sources([PHONE + "?sku=A"]), reported=reported)
    assert result.record.slots["value_1"].sources == (PHONE + "?sku=A",)
    gone = parsed(with_sources([PHONE + "?sku=A"]), reported=(PHONE + "?sku=B", PAGE))
    assert gone.diagnostics[0].reason == "no_reported_link"
    assert gone.diagnostics[0].dropped == ((PHONE + "?sku=A", "not_reported"),)


def test_the_address_level_remains_for_a_parameter_not_on_the_list() -> None:
    result = parsed(with_sources([PHONE]), reported=(PHONE + "?ref=1",))
    assert result.record.slots["value_1"].sources == (PHONE + "?ref=1",)
    assert result.diagnostics[0].link_matches == ("address",)


def test_reported_links_that_differ_only_by_tracking_are_one_link() -> None:
    reported = (PHONE + "?msockid=1", PHONE + "?msockid=2", PHONE)
    reply = good_reply()
    reply["value_1"]["sources"] = [PHONE, PHONE + "?msockid=2"]
    reply["value_2"]["sources"] = [PHONE + "?msockid=1"]
    result = parsed(reply, reported=reported)
    assert result.record.slots["value_1"].sources == (PHONE,)
    assert result.diagnostics[0].dropped == ((PHONE + "?msockid=2", "duplicate"),)
    assert Evidence_refs(result).count(PHONE) == 1


def test_no_link_is_in_any_operation_message() -> None:
    from tests.support.web_lookup import FakeApi

    from ai_data_enrichment.providers.web_lookup import WebLookupUsage
    from ai_data_enrichment.providers.web_lookup_handler import WebLookupHandler

    reply = {
        "value_1": slot(value=1, unit="m", basis="stated", sources=["https://invented.example/s"]),
        "value_2": slot(),
        "value_3": slot(),
    }
    handler = WebLookupHandler(
        FakeApi(response(reply)), model="m", reasoning_effort=None, usage=WebLookupUsage()
    )
    task = mixed_task()
    result = handler.execute(inputs={"task": task.to_json(), "ctx_1": "Everest"}, parameters={})
    assert result.error_code == "NOTHING_FOUND"
    assert "invented" not in (result.message or "")


# --- strip_tracking ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://Example.ORG/Path/?b=2&a=1#Frag",
        "HTTPS://www.example.org",
        "https://example.org/x?ref=a&source=b&id=1&sku=2&lang=de",
        "https://example.org/x?a=%C3%A9&b=+&&c",
        "https://example.org/x?",
        "https://example.org/x#a?utm_source=1",
        "not even a link",
    ],
)
def test_a_link_without_tracking_comes_back_unchanged(url: str) -> None:
    assert strip_tracking(url) == url


@pytest.mark.parametrize(
    ("url", "clean"),
    [
        ("https://Example.ORG/x?msockid=1", "https://Example.ORG/x"),
        (
            "https://example.org/x?b=2&utm_source=a&a=1&gclid=9#Top",
            "https://example.org/x?b=2&a=1#Top",
        ),
        ("https://example.org/x?UTM_Campaign=a&MSCLKID=2", "https://example.org/x"),
        ("https://example.org/x?_ga=1&_gl=2&a=%20b", "https://example.org/x?a=%20b"),
        ("https://example.org/x?utm%5Fsource=1&a=1", "https://example.org/x?a=1"),
        ("https://example.org/x?fbclid&a=1", "https://example.org/x?a=1"),
    ],
)
def test_tracking_is_removed_and_the_rest_is_left_as_written(url: str, clean: str) -> None:
    assert strip_tracking(url) == clean


def test_the_tracking_list() -> None:
    for name in (
        "utm_source", "UTM_X", "msockid", "msclkid", "gclid", "gclsrc", "dclid", "gbraid",
        "wbraid", "gad_source", "fbclid", "igshid", "yclid", "srsltid", "mc_cid", "mc_eid",
        "_ga", "_gl", "MsockID",
    ):  # fmt: skip
        assert is_tracking_parameter(name), name
    for name in ("ref", "source", "id", "sku", "lang", "utm", "gad", "ga", "q"):
        assert not is_tracking_parameter(name), name
    assert normalize_url("https://a.org/x?msockid=1&sku=2&FBCLID=3") == "https://a.org/x?sku=2"
