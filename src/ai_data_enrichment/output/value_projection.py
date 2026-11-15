"""Project typed operation results into Excel scalar values."""

from pydantic import BaseModel

from ai_data_enrichment.execution.models import OperationResult


def field_is_empty(result: OperationResult, value_field: str) -> bool:
    """Whether a successful structured result declares a field but holds no value for it.

    A source may not record every fact about a place. That is a gap in the
    source, not a failure of the step: the other fields of the same result are
    still written.
    """

    value = result.value
    if result.status != "success" or not isinstance(value, BaseModel):
        return False
    return value_field in type(value).model_fields and getattr(value, value_field) is None


def project_operation_field(
    result: OperationResult,
    value_field: str,
) -> str | float:
    """Extract a scalar field from a successful operation result."""

    if result.status != "success" or result.value is None:
        raise ValueError("Cannot project an unsuccessful operation result")

    value = result.value

    # Primitive runtime values expose a virtual "value" field.
    if isinstance(value, (str, float)):
        if value_field != "value":
            raise ValueError(f"Unsupported output field: {value_field}")
        return value

    # Structured runtime values must be Pydantic models.
    if isinstance(value, BaseModel):
        fields = value.model_dump()
        projected = fields.get(value_field)

        if isinstance(projected, (str, float)):
            return projected

        # A count is an integer in the model and a number in the cell.
        if type(projected) is int:
            return float(projected)

    raise ValueError(f"Unsupported output field: {value_field}")
