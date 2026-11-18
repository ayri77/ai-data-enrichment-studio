"""The fake model's answers for the ten invented fixture mails (``samples/probe/mail``).

For the targets ``Service`` (text), ``Amount`` (number) and ``Paid on`` (date) of the acceptance
script's tests. A top-level function, so that it can be pickled into the run worker. Every quote
is a passage of the text it is given, except the one of the second mail with a date, which is
not: the reader drops that value.
"""

import re
from typing import Any

from tests.support.extraction_api import answer
from tests.support.text_extraction import entry, response

ADVERTISEMENTS = ("New this week on Streamly", "Black Friday")
AMOUNT = re.compile(r"\b\d+[.,]\d{2}\b")


def fixture_answer(kwargs: dict[str, Any]) -> Any:
    text: str = kwargs["input"]
    if any(marker in text for marker in ADVERTISEMENTS):
        return response(answer(applies=False))
    values: dict[str, dict[str, Any]] = {}
    if found := AMOUNT.search(text):
        values["value_2"] = entry(float(found[0].replace(",", ".")), found[0])
    if "Gesamt: 10,00 EUR" in text:
        values["value_1"] = entry("Cloudbox Plus", "Tarif: Cloudbox Plus (2 TB Speicher)")
        values["value_3"] = entry("2026-11-01", "Bezahlt am 01.11.2026")
    if "Receipt for your payment to HostHill" in text:
        # Not in the text: dropped as ``quote_not_in_text``.
        values["value_3"] = entry("2026-11-03", "Paid on the third of November")
    return response(answer(True, **values))
