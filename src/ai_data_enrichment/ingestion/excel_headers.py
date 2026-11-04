"""Shared validation for Excel worksheet headers."""

from collections.abc import Iterable


def validate_excel_headers(values: Iterable[object]) -> tuple[str, ...]:
    """Validate unique, non-empty column names."""

    headers: list[str] = []
    seen: set[str] = set()

    for index, value in enumerate(values, start=1):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid column name at position {index}")

        if value in seen:
            raise ValueError(f"Duplicate column name: {value}")

        headers.append(value)
        seen.add(value)

    if not headers:
        raise ValueError("Worksheet has no header row")

    return tuple(headers)
