"""Cell values a lookup cannot use.

A workbook is user data: one cell that cannot be used must cost one row, never
the run. A resolution service raises `UnusableInput` for such a value; the
audited handler turns it into an ordinary failed operation with its own audit
entry, and no request is made to the source.

It subclasses `ValueError` so that direct callers of a service, which already
treat bad inputs as a `ValueError`, keep working unchanged.
"""


class UnusableInput(ValueError):
    def __init__(self, *, error_code: str, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.reason_code = reason_code
        self.message = message


# The longest name a lookup accepts; the audit model has the same bound.
MAX_NAME_LENGTH = 512


def check_name_length(name: str) -> None:
    if len(name.strip()) > MAX_NAME_LENGTH:
        raise UnusableInput(
            error_code="NAME_TOO_LONG",
            reason_code="name_too_long",
            message=f"The name is longer than {MAX_NAME_LENGTH} characters and was not looked up.",
        )


def unrecognized_country() -> UnusableInput:
    return UnusableInput(
        error_code="UNRECOGNIZED_COUNTRY",
        reason_code="country_not_recognized",
        message=(
            "The country was not recognized. Use its English name or its ISO code, "
            "and separate several countries with a slash."
        ),
    )
