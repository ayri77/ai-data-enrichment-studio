"""Shared fakes for the text extraction tests: a task, replies and responses."""

import json
from types import SimpleNamespace as NS
from typing import Any

from ai_data_enrichment.providers.text_extraction import ExtractionColumn, ExtractionTask

TEXT = (
    "Subject: Your receipt\n\n"
    "Cloudbox Plus, monthly.\n"
    "Total: 9,99 EUR\n"
    "Paid on 3 Nov 2026\n"
    "Renews on 01.12.2026\n"
    "Thank you."
)


def mixed_task() -> ExtractionTask:
    return ExtractionTask(
        question="Payments and subscriptions",
        columns=(
            ExtractionColumn("Service", "text"),
            ExtractionColumn("Amount", "number"),
            ExtractionColumn("Paid on", "date"),
            ExtractionColumn("Auto renews", "yes_no"),
            ExtractionColumn("Event", "one_of", ("payment", "ended")),
        ),
    )


def entry(value: Any = None, quote: str | None = None) -> dict[str, Any]:
    return {"value": value, "quote": quote}


def answer(applies: bool = True, **entries: dict[str, Any]) -> dict[str, Any]:
    """A reply for ``mixed_task``; slots not given are null."""
    values = {f"value_{i}": entry() for i in range(1, 6)}
    values.update(entries)
    return {"applies": applies, "values": values}


def good_answer() -> dict[str, Any]:
    return answer(
        value_1=entry("Cloudbox Plus", "Cloudbox Plus, monthly."),
        value_2=entry(9.99, "Total: 9,99 EUR"),
        value_3=entry("2026-11-03", "Paid on 3 Nov 2026"),
        value_4=entry("Yes", "Renews on 01.12.2026"),
        value_5=entry("payment", "Total: 9,99 EUR"),
    )


def response(
    reply: Any,
    *,
    status: str = "completed",
    input_tokens: int = 700,
    output_tokens: int = 90,
    refusal: bool = False,
) -> Any:
    text = reply if isinstance(reply, str) else json.dumps(reply)
    content = [NS(type="refusal", refusal="no")] if refusal else [NS(type="output_text", text=text)]
    return NS(
        status=status,
        output=[NS(type="message", content=content)],
        output_text="" if refusal else text,
        usage=NS(input_tokens=input_tokens, output_tokens=output_tokens),
    )
