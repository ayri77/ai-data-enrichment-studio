"""Which column of a table a field goes to, and how two rows are told to be the same."""

import unicodedata
from collections.abc import Collection, Hashable, Sequence
from datetime import UTC, datetime

from ai_data_enrichment.rows.model import FieldKind, RowSource


def _normal(header: object) -> str:
    return " ".join(str(header).split()).casefold()


def propose_mapping(source: RowSource, headers: Sequence[str]) -> dict[str, int]:
    """Field id -> index in ``headers``, from the names of the headers.

    A field that matches two columns is left out, not guessed. A column goes to at most one
    field (the first in the order of the source).
    """
    normal = [_normal(header) for header in headers]
    mapping: dict[str, int] = {}
    for field in source.fields:
        found = [index for index, name in enumerate(normal) if name in field.headers]
        if len(found) == 1 and found[0] not in mapping.values():
            mapping[field.field_id] = found[0]
    return mapping


def propose_key(source: RowSource, mapped_fields: Collection[str]) -> tuple[str, ...]:
    """The default key of the source when all its fields are mapped, else ``()``."""
    if all(part in mapped_fields for part in source.default_key):
        return source.default_key
    return ()


def key_part(kind: FieldKind, value: object) -> Hashable | None:
    """The part of a key a value makes, or None when it cannot be compared.

    A moment is a datetime cut to the minute (a time zone is converted to UTC and dropped).
    Text is normalised (NFKC), its spaces collapsed, its case ignored; empty text is None.
    """
    if kind == "moment":
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value.replace(second=0, microsecond=0)
    if value is None:
        return None
    text = " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()
    return text or None
