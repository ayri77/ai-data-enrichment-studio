"""Domain models for plan validation results."""

from pydantic import BaseModel, Field, computed_field


class ValidationDiagnostic(BaseModel):
    """Describe a single plan validation error."""

    code: str
    message: str
    step_id: str | None = None
    field: str | None = None


class ValidationResult(BaseModel):
    """Collect plan validation diagnostics."""

    errors: list[ValidationDiagnostic] = Field(default_factory=list)

    @computed_field
    @property
    def valid(self) -> bool:
        """Return True when no validation errors exist."""
        return not self.errors
