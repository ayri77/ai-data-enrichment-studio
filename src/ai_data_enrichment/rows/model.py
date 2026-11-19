"""What a source of rows is made of. Import these names from ``rows.sources``.

They live in their own module so that a source (``rows.mail_files``) can be described
without importing the registry that lists it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

FieldKind = Literal["moment", "text"]


@dataclass(frozen=True)
class SourceField:
    """One value a source gives for a row."""

    field_id: str
    kind: FieldKind
    # For people, in English.
    label: str
    # Lower-case header names this field is proposed for.
    headers: tuple[str, ...]


@dataclass(frozen=True)
class RowSource:
    """What the rest of the code has to know about one source of rows."""

    source_id: str
    label: str
    connect: Literal["files"]
    file_suffixes: tuple[str, ...]
    fields: tuple[SourceField, ...]
    # The fields that, taken together, tell one row from another.
    default_key: tuple[str, ...]
    # "given": the source hands over its rows, so they can be counted. A later kind
    # will be "found".
    listing: Literal["given"]
    # Whether the rows are read on this machine only.
    local_only: bool

    def __post_init__(self) -> None:
        ids = [item.field_id for item in self.fields]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate field id in source {self.source_id}")
        if any(part not in ids for part in self.default_key):
            raise ValueError(f"Default key of source {self.source_id} names an unknown field")

    def field(self, field_id: str) -> SourceField | None:
        """The field with this id, or None."""
        return next((item for item in self.fields if item.field_id == field_id), None)


@dataclass(frozen=True)
class Candidate:
    """One would-be row: a value per field id.

    A ``moment`` value is a naive datetime in UTC cut to the minute; a ``text`` value is a
    str. Nothing about where it came from is kept.
    """

    values: Mapping[str, datetime | str | None]
