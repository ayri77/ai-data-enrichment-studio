"""Unit tests for profiling limits."""

import pytest
from pydantic import ValidationError

from ai_data_enrichment.ingestion.profiling_limits import ProfilingLimits


@pytest.mark.parametrize("value", [0, -1])
def test_profiling_limits_rejects_non_positive_sample_count(
    value: int,
) -> None:
    """ProfilingLimits should require a positive sample record limit."""
    with pytest.raises(ValidationError) as exc_info:
        ProfilingLimits(max_sample_records=value)

    assert exc_info.value.errors()[0]["loc"] == ("max_sample_records",)
    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"


def test_profiling_limits_uses_default_values() -> None:
    """ProfilingLimits should provide sensible default limits."""
    limits = ProfilingLimits()

    assert limits.max_sample_records == 5
    assert limits.max_value_length == 200
    assert limits.max_profile_bytes == 12_000


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("max_value_length", 0),
        ("max_value_length", -1),
        ("max_profile_bytes", 0),
        ("max_profile_bytes", -1),
    ],
)
def test_profiling_limits_rejects_non_positive_limits(
    field_name: str,
    value: int,
) -> None:
    """ProfilingLimits should reject non-positive configuration limits."""
    with pytest.raises(ValidationError) as exc_info:
        ProfilingLimits(**{field_name: value})

    assert exc_info.value.errors()[0]["loc"] == (field_name,)
    assert exc_info.value.errors()[0]["type"] == "greater_than_equal"
