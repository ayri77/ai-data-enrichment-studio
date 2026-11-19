"""The error of the rows core: a fixed code and numbers, never the content of a cell."""


class RowsError(Exception):
    """A plan or a condition that cannot be carried out; ``code`` says why."""

    def __init__(self, code: str, *, count: int | None = None, row: int | None = None) -> None:
        super().__init__(code)
        self.code, self.count, self.row = code, count, row
