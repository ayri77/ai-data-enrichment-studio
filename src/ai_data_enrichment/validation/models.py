"""Domain models for plan validation results."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field


class ValidationDiagnostic(BaseModel):
    """Describe a single plan validation error."""

    category: Literal["contract", "intent"] = "contract"
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

    @computed_field
    @property
    def contract_valid(self) -> bool:
        """Separate contract failures from instruction-backed intent conflicts."""
        return not any(error.category == "contract" for error in self.errors)
