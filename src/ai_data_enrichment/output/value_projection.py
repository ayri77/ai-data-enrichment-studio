"""Project typed operation results into Excel scalar values."""

from pydantic import BaseModel

from ai_data_enrichment.execution.models import OperationResult


def project_operation_field(
    result: OperationResult,
    value_field: str,
) -> str | float:
    """Extract a scalar field from a successful operation result."""

    if result.status != "success" or result.value is None:
        raise ValueError(
            "Cannot project an unsuccessful operation result"
        )

    value = result.value

    # Primitive runtime values expose a virtual "value" field.
    if isinstance(value, (str, float)):
        if value_field != "value":
            raise ValueError(
                f"Unsupported output field: {value_field}"
            )
        return value

    # Structured runtime values must be Pydantic models.
    if isinstance(value, BaseModel):
        fields = value.model_dump()
        projected = fields.get(value_field)

        if isinstance(projected, (str, float)):
            return projected

    raise ValueError(
        f"Unsupported output field: {value_field}"
    )
