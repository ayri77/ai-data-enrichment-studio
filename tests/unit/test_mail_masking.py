"""Masking: each pattern, the counts, and that nothing else is touched."""

import pytest

from ai_data_enrichment.mail.masking import mask_text

OWN = ["sam.example@example.org"]


def mask(text: str, own: list[str] | None = None) -> tuple[str, dict[str, int]]:
    return mask_text(text, own_addresses=own if own is not None else OWN)


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Card •••• 0504 charged", "Card [card] charged"),
        ("Mastercard •••• 0504", "Mastercard [card]"),
        ("Card **** 0504.", "Card [card]."),
        ("**** **** **** 0504", "[card]"),
        ("•••• •••• •••• 0504", "[card]"),
        ("xxxx-xxxx-xxxx-0504", "[card]"),
        ("Visa-6464 on file", "Visa-[card] on file"),
        ("visa-6464", "visa-[card]"),
        ("a card ending in 1234", "a card ending [card]".replace("ending ", "ending in ")),
        ("ending with 1234", "ending with [card]"),
        ("Ihre Karte endet auf 1234.", "Ihre Karte endet auf [card]."),
        ("карта заканчивается на 1234", "карта заканчивается на [card]"),
        ("картка закінчується на 1234", "картка закінчується на [card]"),
        ("Mastercard ending in •••• 0504", "Mastercard ending in [card]"),
    ],
)
def test_card_tails(text: str, masked: str) -> None:
    out, counts = mask(text)
    assert out == masked
    assert counts == {"card": 1, "phone": 0, "me": 0}


@pytest.mark.parametrize(
    "number",
    ["+49 40 1234 5678", "+1 555 010 0199", "+44-20-7946-0000", "+380501234567", "+123456789"],
)
def test_phone_numbers(number: str) -> None:
    out, counts = mask(f"Call {number}, please.")
    assert out == "Call [phone], please."
    assert counts["phone"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Order +12345678 shipped",  # only 8 digits
        "Order +1234567890123456 shipped",  # 16 digits
        "Phone 040 1234 5678",  # no plus sign
        "Total +9,99 EUR",
        "Paid on 2026-11-18 and 2026-12-01",
        "x+49 40 1234 5678",  # a plus inside a word is not a phone prefix
    ],
)
def test_text_that_is_not_a_phone_number(text: str) -> None:
    assert mask(text) == (text, {"card": 0, "phone": 0, "me": 0})


def test_own_addresses() -> None:
    text = (
        "To Sam.Example@Example.org, again sam.example@example.org. Also a.sam.example@example.org"
    )
    out, counts = mask(text)
    assert out == "To [me], again [me]. Also a.sam.example@example.org"
    assert counts == {"card": 0, "phone": 0, "me": 2}


def test_several_own_addresses_and_none() -> None:
    out, counts = mask(
        "a@example.org b@example.org c@example.org", ["a@example.org", "b@example.org"]
    )
    assert out == "[me] [me] c@example.org" and counts["me"] == 2
    assert mask("a@example.org", [])[0] == "a@example.org"
    assert mask("a@example.org", [""])[0] == "a@example.org"


def test_an_address_before_a_full_stop_and_inside_brackets() -> None:
    out, _ = mask("Sent to sam.example@example.org. (sam.example@example.org)")
    assert out == "Sent to [me]. ([me])"


def test_counts_per_kind_in_one_text() -> None:
    text = (
        "Mastercard •••• 0504, Visa-6464, ending in 1234; +49 40 1234 5678; sam.example@example.org"
    )
    out, counts = mask(text)
    assert counts == {"card": 3, "phone": 1, "me": 1}
    assert out == "Mastercard [card], Visa-[card], ending in [card]; [phone]; [me]"


def test_nothing_else_is_touched() -> None:
    text = (
        "Amount: 1.234,56 EUR on 2026-11-04, order PR-5521907, VAT 19 %.\n"
        "Sam Example, 12 Sample Street, 20095 Exampletown\n"
        "Year 2024, number 1234, Visa and Mastercard accepted."
    )
    assert mask(text) == (text, {"card": 0, "phone": 0, "me": 0})
