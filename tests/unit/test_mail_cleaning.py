"""Cleaning the body of a mail: markup, links, invisible characters, length."""

from ai_data_enrichment.mail.cleaning import clean_text, html_to_text


def test_html_becomes_text_with_line_breaks_and_without_hidden_parts() -> None:
    html = (
        "<html><head><title>T</title><style>p {color: red}</style></head><body>"
        "<script>var x = 'Total 1.00';</script>"
        "<p>First</p><div>Second<br>Third</div>"
        "<table><tr><td>Amount</td><td>$9.99</td></tr></table></body></html>"
    )
    text, truncated = clean_text("", html)
    assert text.splitlines() == ["First", "", "Second", "Third", "", "Amount", "$9.99"]
    assert not truncated
    assert "Total 1.00" not in text and "color" not in text and "T\n" not in text


def test_link_text_stays_and_the_target_goes() -> None:
    html = '<p>See <a href="https://track.example.com/c/abc123?x=1">your receipt</a> now.</p>'
    assert clean_text(None, html)[0] == "See your receipt now."


def test_urls_are_removed_from_plain_text_too() -> None:
    plain = (
        "Open https://example.com/a/very/long/path?x=1&y=2 now.\n"
        "[Pay now](https://pay.example.com/x) or [](https://pay.example.com/y) done.\n"
        "<https://example.com/z> end (see https://example.com/q)."
    )
    text, _ = clean_text(plain, None)
    assert text.splitlines() == ["Open now.", "Pay now or done.", "end (see )."]
    assert "http" not in text


def test_a_link_whose_text_is_the_address_leaves_nothing() -> None:
    html = '<p>Copy: <a href="https://a.example.com/x">https://a.example.com/x</a></p>'
    assert clean_text(None, html)[0] == "Copy:"


def test_invisible_padding_and_special_spaces() -> None:
    padding = "͏​­⠀﻿‌‍⁠"
    plain = f"Hello{padding} world again now.\n{padding * 20}\nTotal:  9,99 EUR"
    text, _ = clean_text(plain, None)
    assert text == "Hello world again now.\n\nTotal: 9,99 EUR"


def test_whitespace_runs_lines_and_blank_lines() -> None:
    plain = "  a   b \t c  \r\n\r\n\r\n\r\n\r\n   d  \n\n\n\n"
    assert clean_text(plain, None)[0] == "a b c\n\nd"


def test_control_characters_are_dropped_but_not_line_breaks() -> None:
    assert clean_text("a\x00b\x0cc\nd\x7f", None)[0] == "abc\nd"


def test_plain_is_preferred_when_it_has_real_content() -> None:
    plain = "This is the plain version of the receipt for your monthly plan."
    html = "<p>This is the HTML version of the receipt for your monthly plan, longer.</p>"
    assert clean_text(plain, html)[0] == plain


def test_html_is_used_when_the_plain_part_is_only_a_stub() -> None:
    plain = "View this email in your browser: https://example.com/v/1"
    html = "<p>Your trial of Streamly Premium ends on 20 November 2026, then 9.99 per month.</p>"
    assert clean_text(plain, html)[0].startswith("Your trial of Streamly Premium")


def test_a_short_plain_part_is_kept_when_there_is_no_html() -> None:
    assert clean_text("Paid.", None) == ("Paid.", False)
    assert clean_text("Paid.", "") == ("Paid.", False)


def test_nothing_in_either_part() -> None:
    assert clean_text(None, None) == ("", False)
    assert clean_text("", "<html></html>") == ("", False)


def test_cut_at_a_line_boundary() -> None:
    plain = "\n".join(f"line {i:02d} " + "x" * 10 for i in range(20))
    text, truncated = clean_text(plain, None, max_chars=100)
    assert truncated
    assert len(text) <= 100
    assert all(line.startswith("line ") and len(line) == 18 for line in text.splitlines())
    assert plain.startswith(text)


def test_exactly_at_the_limit_is_not_cut() -> None:
    text, truncated = clean_text("a" * 50 + "\n" + "b" * 49, None, max_chars=100)
    assert (len(text), truncated) == (100, False)
    text, truncated = clean_text("a" * 50 + "\n" + "b" * 50, None, max_chars=100)
    assert truncated and text == "a" * 50


def test_a_single_long_line_is_cut_hard() -> None:
    text, truncated = clean_text("w" * 500, None, max_chars=100)
    assert (len(text), truncated) == (100, True)


def test_broken_markup_does_not_raise() -> None:
    assert html_to_text("<p>One<div>Two</p></span><b>").split() == ["One", "Two"]
    assert html_to_text("<script>never closed Total 5").strip() == ""
