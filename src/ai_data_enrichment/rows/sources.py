"""The sources new rows can come from, in one table.

A new source is one more ``RowSource`` in ``ROW_SOURCES``: what it is called, how it is
connected, what fields it gives and which of them tell one row from another. Nothing else
in the rows core compares source ids.
"""

from ai_data_enrichment.rows.errors import RowsError
from ai_data_enrichment.rows.mail_files import MAIL_FILES
from ai_data_enrichment.rows.model import Candidate, FieldKind, RowSource, SourceField

__all__ = [
    "ROW_SOURCES",
    "Candidate",
    "FieldKind",
    "RowSource",
    "SourceField",
    "source_for",
]

ROW_SOURCES: tuple[RowSource, ...] = (MAIL_FILES,)


def source_for(source_id: str) -> RowSource:
    """The source with this id; ``rows_unknown_source`` when there is none."""
    for source in ROW_SOURCES:
        if source.source_id == source_id:
            return source
    raise RowsError("rows_unknown_source")
