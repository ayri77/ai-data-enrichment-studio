"""Mask what must not leave the machine, before a text is sent to a model.

Three kinds are replaced by a fixed placeholder: the tail of a payment card (``[card]``),
a phone number (``[phone]``) and the mailbox owner's own e-mail address (``[me]``).

This is a small, pattern-based filter and nothing more. It does not find names, postal
addresses, account or tax numbers, or anything written in a form the patterns do not
cover. Amounts, dates and order numbers stay, because they are what the table is built
from.
"""

import re
from collections.abc import Iterable

KINDS = ("card", "phone", "me")

_CARD = "[card]"
_PHONE = "[phone]"
_ME = "[me]"

_BRANDS = (
    r"Visa|Mastercard|Master\s+Card|Maestro|American\s+Express|Amex|Discover|Diners(?:\s+Club)?"
    r"|JCB|UnionPay|Girocard"
)
_MASK_CHARS = r"(?:[•●∙∗*]{2,}|[xX]{4})"
# Words before a card tail: "ending in", "ending with", "endet auf", "заканчивается на" ...
_ENDING = (
    r"(?:ending(?:\s+(?:in|with))?|endet\s+auf|endend\s+auf|endung|"
    r"заканчива\w+\s+на|оканчива\w+\s+на|закінчу\w+\s+на)"
)

# The digits are replaced; in the last two the text before them (the card brand word, "ending
# in") is group 1 and stays.
_CARD_PATTERNS = (
    # •••• 0504, **** **** **** 0504, xxxx 0504
    re.compile(rf"(?<![\w•●∙∗*])(?:{_MASK_CHARS}[ \-]?)+\d{{4}}(?!\d)"),
    # Visa-6464
    re.compile(rf"(\b(?:{_BRANDS})[-–])\d{{4}}(?!\d)", re.IGNORECASE),
    # ending in 1234, endet auf 1234
    re.compile(rf"(\b{_ENDING}\s+)\d{{4}}(?!\d)", re.IGNORECASE),
)
_PHONE_PATTERN = re.compile(r"(?<![\w+])\+(?:\d[ \-]?){8,14}\d(?!\d)")


def _mask_cards(text: str) -> tuple[str, int]:
    count = 0
    # Pattern 1 swallows the mask characters with the digits.
    text, n = _CARD_PATTERNS[0].subn(_CARD, text)
    count += n
    for pattern in _CARD_PATTERNS[1:]:
        text, n = pattern.subn(rf"\1{_CARD}", text)
        count += n
    return text, count


def mask_text(text: str, *, own_addresses: Iterable[str]) -> tuple[str, dict[str, int]]:
    """The text with card tails, phone numbers and own addresses replaced, and counts per kind."""
    counts: dict[str, int] = dict.fromkeys(KINDS, 0)
    for address in sorted(
        {a.strip() for a in own_addresses if a and a.strip()}, key=len, reverse=True
    ):
        pattern = re.compile(rf"(?<![\w.+-]){re.escape(address)}(?![\w-]|\.\w)", re.IGNORECASE)
        text, n = pattern.subn(_ME, text)
        counts["me"] += n
    text, counts["card"] = _mask_cards(text)
    text, counts["phone"] = _PHONE_PATTERN.subn(_PHONE, text)
    return text, counts
